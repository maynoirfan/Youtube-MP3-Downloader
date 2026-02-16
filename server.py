import os
import json
import threading
import pyperclip
import requests
import paramiko
import io
import re
import datetime
import webbrowser
from PIL import Image, ImageTk
import yt_dlp as youtube_dl
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Optional ID3 tagging (graceful fallback)
try:
    from mutagen.id3 import ID3, TIT2, TPE1, APIC
    from mutagen.mp3 import MP3
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False

CONFIG_FILE = "yt_uploader_cms_config.json"


class YouTubeMediaPipeline:
    class YTDLPLogger:
        def __init__(self, log_method):
            self.log_method = log_method

        def debug(self, msg):
            self.log_method("YTDLP", msg)

        def warning(self, msg):
            self.log_method("YTDLP", f"WARNING: {msg}")

        def error(self, msg):
            self.log_method("ERROR", f"yt-dlp: {msg}")

    def __init__(self, root):
        self.root = root
        self.root.title("YouTube Media Pipeline - Pro CMS Edition v2")
        self.root.geometry("1120x860")
        self.is_dark = True
        self.current_metadata = None
        self.bulk_processing = False

        self.config = self.load_config()
        self.setup_ui()
        self.apply_theme(self.config.get("theme", "dark") == "dark")
        self.load_history()
        self.set_server_status("idle", "Idle - Configure credentials")
        self.set_status("Ready")

    def load_config(self):
        default = {
            "host": "", "user": "", "pass": "",
            "audio_path": "/", "image_path": "/",
            "quality": "128", "ffmpeg_path": "",
            "theme": "dark",
            "history": []
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    return {**default, **loaded}
            except:
                return default
        return default

    def save_current_config(self):
        if hasattr(self, "host_ent"):
            self.config["host"] = self.host_ent.get()
            self.config["user"] = self.user_ent.get()
            self.config["pass"] = self.pass_ent.get()
        if hasattr(self, "ffmpeg_var"):
            self.config["ffmpeg_path"] = self.ffmpeg_var.get()
        if hasattr(self, "quality_var"):
            self.config["quality"] = self.quality_var.get()
        if hasattr(self, "audio_path_var"):
            self.config["audio_path"] = self.audio_path_var.get()
        if hasattr(self, "image_path_var"):
            self.config["image_path"] = self.image_path_var.get()
        if hasattr(self, "theme_var"):
            self.config["theme"] = self.theme_var.get()
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    def log(self, tag, message):
        self.root.after(0, lambda: self._log_to_ui(tag, message))

    def _log_to_ui(self, tag, message):
        self.log_area.config(state="normal")
        self.log_area.insert(tk.END, f"[{tag}] {message}\n")
        self.log_area.see(tk.END)
        self.log_area.config(state="disabled")

    def set_status(self, text):
        self.root.after(0, lambda: self.status_bar.config(text=text))

    def set_server_status(self, state, message=""):
        colors = {
            "idle": "#666666",
            "negotiating": "#ffaa00",
            "connected": "#00ff00",
            "error": "#ff4444"
        }
        color = colors.get(state, "#666666")
        def _update():
            self.status_canvas.itemconfig(self.circle, fill=color)
            self.status_msg.config(text=message)
        self.root.after(0, _update)

    # ====================== URL CLEANING & VALIDATION ======================
    def clean_youtube_url(self, url):
        url = url.strip()
        for param in ['&list=', '&index=', '&t=', '?si=', '&pp=', '&ab_channel=', '&feature=']:
            if param in url:
                url = url.split(param)[0]
        if 'youtu.be/' in url:
            vid = url.split('youtu.be/')[-1].split('?')[0].split('&')[0]
            url = f"https://www.youtube.com/watch?v={vid}"
        return url

    def validate_url(self, url):
        return bool(re.search(r'(youtube\.com/watch\?v=|youtu\.be/)', url))

    # ====================== SANITIZATION ======================
    def sanitize_filename(self, title):
        if not title:
            return "untitled"
        emoji_pattern = re.compile(
            "["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            "]+", flags=re.UNICODE)
        title = emoji_pattern.sub(r'', title)
        title = title.lower().strip()
        title = re.sub(r'[^a-z0-9\s\-_]', '', title)
        title = re.sub(r'[\s\-_]+', '-', title).strip('-')
        return title or "untitled"

    # ====================== ID3 TAGGING ======================
    def _tag_mp3(self, mp3_path, info, img_path):
        if not MUTAGEN_AVAILABLE:
            self.log("TAG", "Mutagen not installed → skipping ID3 tags (pip install mutagen)")
            return
        try:
            audio = MP3(mp3_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=info.get('title', '')))
            artist = info.get('uploader') or info.get('channel') or 'Unknown Channel'
            audio.tags.add(TPE1(encoding=3, text=artist))
            if os.path.exists(img_path):
                with open(img_path, 'rb') as f:
                    audio.tags.add(APIC(
                        encoding=3,
                        mime='image/jpeg',
                        type=3,  # Front cover
                        desc='Cover',
                        data=f.read()
                    ))
            audio.save()
            self.log("TAG", f"ID3 tags embedded: {info.get('title', '')} / {artist}")
        except Exception as e:
            self.log("TAG", f"ID3 error: {e}")

    # ====================== DOWNLOAD PROGRESS ======================
    def _dl_progress_hook(self, d):
        if d['status'] == 'downloading':
            percent = d.get('_percent_str', '0%').strip()
            speed = d.get('_speed_str', '')
            self.log("PROGRESS", f"Downloading {percent} {speed}")
        elif d['status'] == 'finished':
            self.log("DL", "Download finished → converting to MP3...")
        elif d['status'] == 'error':
            self.log("ERROR", "Download error")

    # ====================== CORE PROCESSING ======================
    def _process_url(self, url, show_success=True):
        if not url or not self.validate_url(url):
            return
        temp = "yt_temp"
        os.makedirs(temp, exist_ok=True)
        slug = None
        info = None
        try:
            self.set_status("Downloading audio...")
            self.log("START", f"Processing → {url}")

            opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'{temp}/%(id)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': self.quality_var.get(),
                }],
                'logger': self.YTDLPLogger(self.log),
                'progress_hooks': [self._dl_progress_hook],
                'quiet': False,
            }
            if self.ffmpeg_var.get():
                opts['ffmpeg_location'] = self.ffmpeg_var.get()

            with youtube_dl.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)

            # Rename to sanitized slug
            id_mp3 = os.path.join(temp, f"{info['id']}.mp3")
            slug = self.sanitize_filename(info.get('title', info['id']))
            local_mp3 = os.path.join(temp, f"{slug}.mp3")
            if os.path.exists(id_mp3):
                os.rename(id_mp3, local_mp3)

            # Thumbnail
            thumb_url = info.get('thumbnail')
            local_img = None
            if thumb_url:
                try:
                    thumb_data = requests.get(thumb_url, timeout=10).content
                    id_img = os.path.join(temp, f"{info['id']}.jpg")
                    with open(id_img, "wb") as f:
                        f.write(thumb_data)
                    local_img = os.path.join(temp, f"{slug}.jpg")
                    os.rename(id_img, local_img)
                except:
                    pass

            # ID3 Tagging
            self.set_status("Embedding ID3 tags...")
            if local_img:
                self._tag_mp3(local_mp3, info, local_img)

            # SSH Upload
            self.set_status("Uploading via SCP Shell...")
            ssh = self.get_ssh_client()
            if ssh:
                audio_remote = f"{self.audio_path_var.get().rstrip('/')}/{slug}.mp3"
                img_remote = f"{self.image_path_var.get().rstrip('/')}/{slug}.jpg" if local_img else None

                if self.upload_via_shell(ssh, local_mp3, audio_remote):
                    self.log("UPLOAD", f"Audio → {audio_remote}")
                    if local_img and self.upload_via_shell(ssh, local_img, img_remote):
                        self.log("UPLOAD", f"Thumbnail → {img_remote}")

                ssh.close()
                self.set_server_status("connected", f"Connected to {self.config['host']}")

                # History
                history_entry = {
                    "title": info.get('title', slug),
                    "slug": slug,
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "audio_path": audio_remote,
                    "image_path": img_remote or ""
                }
                if "history" not in self.config:
                    self.config["history"] = []
                self.config["history"].append(history_entry)
                if len(self.config["history"]) > 200:
                    self.config["history"] = self.config["history"][-200:]
                self.save_current_config()
                self.refresh_history()

                self.log("DONE", f"✅ Pipeline complete → {slug}")
                if show_success:
                    self.root.after(0, lambda t=info.get('title', slug):
                        messagebox.showinfo("Success", f"Uploaded to CMS:\n{t}"))
            else:
                self.log("ERROR", "SSH connection failed")

        except Exception as e:
            self.log("ERROR", f"Pipeline failed: {e}")
            self.set_status("Error")
        finally:
            try:
                for f in os.listdir(temp):
                    try:
                        os.remove(os.path.join(temp, f))
                    except:
                        pass
            except:
                pass
            self.set_status("Ready")

    # ====================== SSH ======================
    def get_ssh_client(self):
        self.set_server_status("negotiating", "Negotiating SSH...")
        self.log("SSH", f"Connecting to {self.config['user']}@{self.config['host']}...")
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(
                hostname=self.config['host'],
                port=22,
                username=self.config['user'],
                password=self.config['pass'],
                timeout=15
            )
            self.log("SSH", "Authentication successful")
            self.set_server_status("connected", f"Connected to {self.config['host']}")
            return ssh
        except Exception as e:
            self.log("ERROR", f"SSH Failed: {e}")
            self.set_server_status("error", "SSH Error")
            return None

    def upload_via_shell(self, ssh, local_path, remote_path):
        try:
            file_size = os.path.getsize(local_path)
            ssh.exec_command(f'mkdir -p "{os.path.dirname(remote_path)}"')
            with open(local_path, "rb") as f:
                chan = ssh.get_transport().open_session()
                chan.exec_command(f'scp -t "{remote_path}"')
                chan.send(f'C0644 {file_size} {os.path.basename(remote_path)}\n')
                if chan.recv(1) == b'\x00':
                    chan.sendall(f.read())
                    chan.send('\x00')
                    chan.recv(1)
                chan.close()
            return True
        except Exception as e:
            self.log("SCP", f"Failed: {e}")
            return False

    # ====================== UI SETUP ======================
    def setup_ui(self):
        # Top status frame
        top_frame = ttk.Frame(self.root)
        top_frame.pack(fill="x", padx=10, pady=8)
        ttk.Label(top_frame, text="Server:").pack(side="left")
        self.status_canvas = tk.Canvas(top_frame, width=26, height=26, highlightthickness=0)
        self.status_canvas.pack(side="left", padx=8)
        self.circle = self.status_canvas.create_oval(3, 3, 23, 23, fill="#666666")
        self.status_msg = ttk.Label(top_frame, text="Idle")
        self.status_msg.pack(side="left")

        # Tabs
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=5)

        # ==================== DOWNLOADER TAB ====================
        dl_tab = ttk.Frame(self.tabs)
        self.tabs.add(dl_tab, text="Downloader")

        ttk.Label(dl_tab, text="YouTube URL:", font=("Segoe UI", 11, "bold")).pack(pady=(12, 2), anchor="w", padx=20)

        url_frame = ttk.Frame(dl_tab)
        url_frame.pack(fill="x", padx=20, pady=6)

        self.url_var = tk.StringVar()
        self.url_var.trace_add("write", self.on_url_changed)
        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=("Segoe UI", 10), width=80)
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Control-Return>", lambda e: self.run_pipeline())
        self.url_entry.bind("<Return>", lambda e: self.test_current_url())  # Enter = test

        ttk.Button(url_frame, text="📋 Paste", width=8, command=self.paste_clipboard).pack(side="left", padx=4)
        ttk.Button(url_frame, text="🧹 Clean", width=10, command=self.clean_current_url).pack(side="left", padx=4)
        ttk.Button(url_frame, text="🔍 Test", width=10, command=self.test_current_url).pack(side="left", padx=4)

        self.url_status = ttk.Label(dl_tab, text="Waiting for URL...", foreground="#888888")
        self.url_status.pack(anchor="w", padx=20, pady=(2, 8))

        # Settings row
        row2 = ttk.Frame(dl_tab)
        row2.pack(fill="x", padx=20, pady=8)

        self.ffmpeg_var = tk.StringVar(value=self.config.get("ffmpeg_path", ""))
        ttk.Label(row2, text="FFmpeg:").pack(side="left")
        ttk.Entry(row2, textvariable=self.ffmpeg_var, width=35).pack(side="left", padx=5)
        ttk.Button(row2, text="...", width=3, command=self.select_ffmpeg).pack(side="left")

        # FIXED: padx moved to .pack()
        ttk.Label(row2, text="Quality:").pack(side="left", padx=(20, 5))
        self.quality_var = tk.StringVar(value=self.config.get("quality", "128"))
        qmenu = ttk.OptionMenu(row2, self.quality_var, "128", "64", "96", "128", "192", "256", "320")
        qmenu.pack(side="left", padx=5)
        ttk.Label(row2, text="kbps").pack(side="left")

        self.preview_btn = ttk.Button(row2, text="🎧 Preview Audio", command=self.preview_audio, state="disabled")
        self.preview_btn.pack(side="right", padx=20)

        # Metadata Preview
        m_f = ttk.LabelFrame(dl_tab, text="Metadata Preview", padding=12)
        m_f.pack(fill="x", padx=20, pady=10)

        ttk.Label(m_f, text="Title:").grid(row=0, column=0, sticky="nw", pady=4, padx=(0,8))
        self.title_box = tk.Text(m_f, height=3, width=70, font=("Segoe UI", 10))
        self.title_box.grid(row=0, column=1, pady=4, padx=5)
        btn_frame_title = ttk.Frame(m_f)
        btn_frame_title.grid(row=0, column=2, sticky="nw", pady=4)
        ttk.Button(btn_frame_title, text="📋 Copy Title", command=lambda: self.copy_single(self.title_box)).pack()

        ttk.Label(m_f, text="Description:").grid(row=1, column=0, sticky="nw", pady=4, padx=(0,8))
        self.desc_box = tk.Text(m_f, height=7, width=70, font=("Segoe UI", 10))
        self.desc_box.grid(row=1, column=1, pady=4, padx=5)
        btn_frame_desc = ttk.Frame(m_f)
        btn_frame_desc.grid(row=1, column=2, sticky="nw", pady=4)
        ttk.Button(btn_frame_desc, text="📋 Copy Desc", command=lambda: self.copy_single(self.desc_box)).pack()

        ttk.Button(m_f, text="📋 Copy Title + Desc", command=self.copy_all_metadata).grid(row=2, column=1, sticky="w", pady=10)

        self.thumb_label = ttk.Label(m_f, text="Waiting for URL...")
        self.thumb_label.grid(row=3, column=1, pady=10, columnspan=2, sticky="w")

        ttk.Progressbar(dl_tab, variable=tk.DoubleVar(), maximum=100).pack(fill="x", padx=20, pady=5)

        self.start_btn = ttk.Button(dl_tab, text="🚀 RUN SINGLE PIPELINE", command=self.run_pipeline, state="disabled")
        self.start_btn.pack(pady=12)

        # Bulk tab
        bulk_tab = ttk.Frame(self.tabs)
        self.tabs.add(bulk_tab, text="Bulk")
        ttk.Label(bulk_tab, text="Paste multiple YouTube URLs (one per line)", font=("Segoe UI", 11, "bold")).pack(pady=10)
        self.bulk_text = tk.Text(bulk_tab, height=18, font=("Consolas", 10))
        self.bulk_text.pack(fill="both", expand=True, padx=20, pady=5)
        ttk.Button(bulk_tab, text="🚀 Process Bulk Sequentially", command=self.start_bulk).pack(pady=12)

        # Server Settings tab
        cfg_tab = ttk.Frame(self.tabs)
        self.tabs.add(cfg_tab, text="Server Settings")
        c_f = ttk.LabelFrame(cfg_tab, text="SSH Credentials", padding=12)
        c_f.pack(fill="x", padx=15, pady=10)
        self.host_ent = self.create_row(c_f, "Host:", "host", 0)
        self.user_ent = self.create_row(c_f, "User:", "user", 1)
        self.pass_ent = self.create_row(c_f, "Password:", "pass", 2, show="*")
        ttk.Button(c_f, text="Test Connection", command=self.test_connection).grid(row=3, column=1, pady=10, sticky="e")

        p_f = ttk.LabelFrame(cfg_tab, text="Remote CMS Paths", padding=12)
        p_f.pack(fill="x", padx=15, pady=10)
        self.audio_path_var = tk.StringVar(value=self.config["audio_path"])
        ttk.Label(p_f, text="Audio Folder:").grid(row=0, column=0, sticky="w")
        ttk.Entry(p_f, textvariable=self.audio_path_var, width=60).grid(row=0, column=1, padx=8)
        ttk.Button(p_f, text="Browse", command=lambda: self.browse_remote(self.audio_path_var)).grid(row=0, column=2)

        self.image_path_var = tk.StringVar(value=self.config["image_path"])
        ttk.Label(p_f, text="Image Folder:").grid(row=1, column=0, sticky="w")
        ttk.Entry(p_f, textvariable=self.image_path_var, width=60).grid(row=1, column=1, padx=8)
        ttk.Button(p_f, text="Browse", command=lambda: self.browse_remote(self.image_path_var)).grid(row=1, column=2)

        # History tab
        history_tab = ttk.Frame(self.tabs)
        self.tabs.add(history_tab, text="History")
        tree_frame = ttk.Frame(history_tab)
        tree_frame.pack(fill="both", expand=True, padx=15, pady=10)
        self.history_tree = ttk.Treeview(
            tree_frame,
            columns=("Title", "Slug", "Date", "Audio Path"),
            show="headings",
            height=18
        )
        self.history_tree.heading("Title", text="Title")
        self.history_tree.heading("Slug", text="Slug")
        self.history_tree.heading("Date", text="Date")
        self.history_tree.heading("Audio Path", text="Server Audio Path")
        self.history_tree.column("Title", width=320)
        self.history_tree.column("Slug", width=180)
        self.history_tree.column("Date", width=130)
        self.history_tree.column("Audio Path", width=380)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=vsb.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        btn_f = ttk.Frame(history_tab)
        btn_f.pack(pady=8)
        ttk.Button(btn_f, text="Refresh", command=self.load_history).pack(side="left", padx=5)
        ttk.Button(btn_f, text="Clear History", command=self.clear_history).pack(side="left", padx=5)

        # Settings tab
        settings_tab = ttk.Frame(self.tabs)
        self.tabs.add(settings_tab, text="Settings")
        ttk.Label(settings_tab, text="Theme", font=("Segoe UI", 14, "bold")).pack(pady=(30, 10))
        self.theme_var = tk.StringVar(value=self.config.get("theme", "dark"))
        ttk.Radiobutton(settings_tab, text="🌙 Dark Mode (Pro Default)", variable=self.theme_var, value="dark",
                        command=self.toggle_theme).pack(pady=6)
        ttk.Radiobutton(settings_tab, text="☀️ Light Mode", variable=self.theme_var, value="light",
                        command=self.toggle_theme).pack(pady=6)
        ttk.Label(settings_tab, text="All changes apply instantly.\nConfig saved automatically.",
                  foreground="#888888").pack(pady=40)

        # Command Palette
        log_f = ttk.LabelFrame(self.root, text="Command Palette (Real-time yt-dlp + SSH)")
        log_f.pack(fill="both", expand=False, padx=10, pady=(0, 8), ipady=4)
        self.log_area = tk.Text(log_f, height=11, bg="#0d0d0d", fg="#00FF00",
                               font=("Consolas", 9), wrap="word")
        self.log_area.pack(fill="both", expand=True, padx=8, pady=6)
        self.log_area.config(state="disabled")

        # Status bar
        self.status_bar = ttk.Label(self.root, text="Ready", relief="sunken", anchor="w", padding=(8, 4))
        self.status_bar.pack(fill="x", side="bottom")

    def create_row(self, parent, label, key, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=6)
        ent = ttk.Entry(parent, width=48, show=show)
        ent.insert(0, self.config.get(key, ""))
        ent.grid(row=row, column=1, pady=6, padx=10)
        return ent

    def apply_theme(self, dark):
        self.is_dark = dark
        bg = "#1e1e1e" if dark else "#f8f8f8"
        fg = "#ffffff" if dark else "#111111"
        entry_bg = "#2d2d2d" if dark else "#ffffff"
        console_bg = "#0d0d0d" if dark else "#ffffff"
        console_fg = "#00ff00" if dark else "#000000"
        self.root.configure(bg=bg)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=bg, foreground=fg)
        style.configure("TFrame", background=bg)
        style.configure("TLabelframe", background=bg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TButton", background=bg if not dark else "#333333", foreground=fg)
        style.configure("TEntry", fieldbackground=entry_bg, foreground=fg, insertcolor=fg)
        style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg)
        style.configure("TNotebook", background=bg)
        style.configure("TNotebook.Tab", background=bg if not dark else "#333333", foreground=fg)
        self.log_area.config(bg=console_bg, fg=console_fg)
        self.title_box.config(bg=entry_bg, fg=fg)
        self.desc_box.config(bg=entry_bg, fg=fg)
        self.bulk_text.config(bg=entry_bg, fg=fg)
        self.status_canvas.config(bg=bg)
        self.status_bar.config(background=bg, foreground=fg)

    def toggle_theme(self):
        dark = self.theme_var.get() == "dark"
        self.apply_theme(dark)
        self.save_current_config()

    # ====================== CALLBACKS ======================
    def select_ffmpeg(self):
        path = filedialog.askopenfilename(title="Select ffmpeg.exe")
        if path:
            self.ffmpeg_var.set(path)

    def test_connection(self):
        self.save_current_config()
        ssh = self.get_ssh_client()
        if ssh:
            messagebox.showinfo("Success", "SSH Connection OK")
            ssh.close()

    def on_url_changed(self, *args):
        url = self.url_var.get().strip()
        if not url:
            self.url_status.config(text="Waiting for URL...", foreground="#888888")
            self.preview_btn.config(state="disabled")
            self.start_btn.config(state="disabled")
            return
        cleaned = self.clean_youtube_url(url)
        if cleaned != url:
            self.url_var.set(cleaned)
        if self.validate_url(cleaned):
            self.url_status.config(text="✅ Valid YouTube video", foreground="#00cc44")
            threading.Thread(target=self.fetch_metadata, args=(cleaned,), daemon=True).start()
        else:
            self.url_status.config(text="❌ Not a valid YouTube video URL", foreground="#ff4444")
            self.preview_btn.config(state="disabled")
            self.start_btn.config(state="disabled")

    def clean_current_url(self):
        url = self.url_var.get().strip()
        cleaned = self.clean_youtube_url(url)
        if cleaned != url:
            self.url_var.set(cleaned)
            self.log("CLEAN", "URL cleaned (removed playlist / tracking parameters)")
        else:
            self.log("CLEAN", "URL already clean")

    def test_current_url(self):
        url = self.clean_youtube_url(self.url_var.get().strip())
        if self.validate_url(url):
            self.url_var.set(url)
            threading.Thread(target=self.fetch_metadata, args=(url,), daemon=True).start()
            self.log("TEST", "URL validation & metadata fetch started")
        else:
            self.url_status.config(text="❌ Invalid URL", foreground="#ff4444")
            messagebox.showwarning("Invalid", "Please enter a valid YouTube video URL")

    def paste_clipboard(self):
        try:
            text = pyperclip.paste().strip()
            if text:
                self.url_var.set(text)
                self.log("CLIPBOARD", "Pasted from clipboard")
        except:
            self.log("CLIPBOARD", "Clipboard access failed")

    def fetch_metadata(self, url):
        try:
            ydl_opts = {'quiet': True}
            if self.ffmpeg_var.get():
                ydl_opts['ffmpeg_location'] = self.ffmpeg_var.get()
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                self.current_metadata = info
                self.root.after(0, self.update_meta_ui)
        except Exception as e:
            self.log("META", f"Metadata fetch failed: {str(e)}")

    def update_meta_ui(self):
        if not self.current_metadata:
            return
        self.title_box.delete(1.0, tk.END)
        self.title_box.insert(tk.END, self.current_metadata.get('title', ''))
        self.desc_box.delete(1.0, tk.END)
        self.desc_box.insert(tk.END, self.current_metadata.get('description', ''))
        try:
            resp = requests.get(self.current_metadata.get('thumbnail'), timeout=8)
            img = Image.open(io.BytesIO(resp.content)).resize((260, 146))
            self.photo = ImageTk.PhotoImage(img)
            self.thumb_label.config(image=self.photo, text="")
        except:
            self.thumb_label.config(text="Thumbnail not available")
        self.preview_btn.config(state="normal")
        self.start_btn.config(state="normal")

    def preview_audio(self):
        if not self.current_metadata:
            messagebox.showinfo("Info", "No video loaded yet")
            return
        url = self.current_metadata.get('webpage_url') or self.url_var.get()
        self.log("PREVIEW", "Opening preview in browser...")
        webbrowser.open(url)

    def copy_all_metadata(self):
        title = self.title_box.get(1.0, tk.END).strip()
        desc = self.desc_box.get(1.0, tk.END).strip()
        if title or desc:
            pyperclip.copy(f"{title}\n\n{desc}")
            self.log("INFO", "Copied Title + Description")
        else:
            messagebox.showwarning("Empty", "Nothing to copy")

    def copy_single(self, widget):
        text = widget.get(1.0, tk.END).strip()
        if text:
            pyperclip.copy(text)
            self.log("INFO", "Copied to clipboard")
        else:
            messagebox.showwarning("Empty", "Field is empty")

    def run_pipeline(self):
        self.save_current_config()
        self.start_btn.config(state="disabled")
        threading.Thread(target=self._proc, daemon=True).start()

    def _proc(self):
        url = self.clean_youtube_url(self.url_var.get().strip())
        self._process_url(url, show_success=True)
        self.root.after(0, lambda: self.start_btn.config(state="normal"))

    def start_bulk(self):
        raw = self.bulk_text.get(1.0, tk.END).strip()
        urls = [self.clean_youtube_url(line.strip()) for line in raw.splitlines()
                if line.strip() and self.validate_url(line.strip())]
        if not urls:
            messagebox.showwarning("Bulk", "No valid YouTube video URLs found")
            return
        self.save_current_config()
        self.set_status(f"Bulk: {len(urls)} items queued...")
        threading.Thread(target=self._bulk_proc, args=(urls,), daemon=True).start()

    def _bulk_proc(self, urls):
        self.log("BULK", f"Starting bulk processing of {len(urls)} URLs")
        for i, url in enumerate(urls, 1):
            self.log("BULK", f"[{i}/{len(urls)}] {url}")
            self._process_url(url, show_success=False)
        self.log("BULK", "Bulk processing finished")
        self.root.after(0, lambda: messagebox.showinfo("Bulk Complete", f"Processed {len(urls)} videos."))

    def browse_remote(self, target_var):
        ssh = self.get_ssh_client()
        if not ssh:
            return
        win = tk.Toplevel(self.root)
        win.title("Remote Folder Browser - Improved")
        win.geometry("580x520")
        current_path = tk.StringVar(value=target_var.get().strip() or "/")

        def normalize_path(p):
            p = p.replace('\\', '/').strip()
            p = '/'.join(part for part in p.split('/') if part)
            return '/' + p if p else '/'

        current_path.set(normalize_path(current_path.get()))

        path_lbl = ttk.Label(win, textvariable=current_path, font=("Consolas", 10))
        path_lbl.pack(pady=6, padx=12, anchor="w")

        lb = tk.Listbox(win, font=("Consolas", 10), height=20)
        lb.pack(fill="both", expand=True, padx=12, pady=6)

        def refresh(path):
            lb.delete(0, tk.END)
            lb.insert(tk.END, ".. (go to parent directory)")
            cmd = f'cd "{path}" 2>/dev/null && ls -1 -F --group-directories-first 2>/dev/null | grep "/$" || echo ""'
            try:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                error = stderr.read().decode().strip()
                if error:
                    self.log("BROWSE", f"Error listing {path}: {error}")

                dirs = []
                for line in stdout:
                    line = line.strip()
                    if line.endswith('/'):
                        dir_name = line[:-1]
                        if dir_name:
                            dirs.append(dir_name)

                for d in sorted(dirs):
                    lb.insert(tk.END, d)

            except Exception as e:
                self.log("BROWSE", f"Refresh failed: {e}")
                lb.insert(tk.END, "(error reading directory)")

        def on_select(event):
            if not lb.curselection():
                return
            sel = lb.get(lb.curselection()[0]).strip()

            current = normalize_path(current_path.get())

            if sel.startswith(".."):
                parent = '/'.join(current.split('/')[:-1]) or '/'
                current_path.set(normalize_path(parent))
            else:
                new_path = f"{current.rstrip('/')}/{sel}".rstrip('/')
                current_path.set(normalize_path(new_path))

            refresh(current_path.get())

        lb.bind("<<ListboxSelect>>", on_select)
        lb.bind("<Double-Button-1>", on_select)

        btn_frame = ttk.Frame(win)
        btn_frame.pack(pady=10)

        ttk.Button(btn_frame, text="Refresh Current Folder",
                   command=lambda: refresh(normalize_path(current_path.get()))).pack(side="left", padx=8)

        ttk.Button(btn_frame, text="Select This Folder",
                   command=lambda: [
                       target_var.set(normalize_path(current_path.get())),
                       self.log("BROWSE", f"Selected remote path: {current_path.get()}"),
                       ssh.close(),
                       win.destroy()
                   ]).pack(side="left", padx=8)

        refresh(current_path.get())

    # ====================== HISTORY ======================
    def load_history(self):
        for item in self.history_tree.get_children():
            self.history_tree.delete(item)
        for entry in reversed(self.config.get("history", [])):
            self.history_tree.insert("", "end", values=(
                entry.get("title", ""),
                entry.get("slug", ""),
                entry.get("date", ""),
                entry.get("audio_path", "")
            ))

    def refresh_history(self):
        self.root.after(0, self.load_history)

    def clear_history(self):
        if messagebox.askyesno("Clear History", "Delete all upload records?"):
            self.config["history"] = []
            self.save_current_config()
            self.load_history()


if __name__ == "__main__":
    root = tk.Tk()
    app = YouTubeMediaPipeline(root)
    root.mainloop()
