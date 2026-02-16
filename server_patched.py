import threading
import logging
import requests

# Configure logging for better visibility
logging.basicConfig(level=logging.INFO)

# A lock object for thread safety
lock = threading.Lock()

class YoutubeMP3Downloader:
    def __init__(self, url):
        self.url = url

    def validate_url(self):
        # Check if the URL is in a valid format (basic validation)
        if not self.url.startswith('https://') or 'youtube.com/watch' not in self.url:
            logging.error('Invalid YouTube URL provided.')
            raise ValueError('Invalid YouTube URL.')
        logging.info('Valid URL: %s', self.url)

    def download_video(self):
        try:
            self.validate_url()  # Validate the URL first
            # Simulated downloading process with a request
            with lock:
                logging.info('Starting download for: %s', self.url)
                response = requests.get(self.url)
                response.raise_for_status()  # Raise an error for bad responses

            # Simulate saving the downloaded content
            self.save_content(response.content)
        except requests.exceptions.RequestException as e:
            logging.error('Error during the download: %s', e)
            raise
        except Exception as e:
            logging.error('An unexpected error occurred: %s', e)
            raise

    def save_content(self, content):
        # Logic to save the content to a file
        file_name = 'downloaded_video.mp3'
        with open(file_name, 'wb') as f:
            f.write(content)
        logging.info('Content saved as: %s', file_name)

# Example usage
if __name__ == '__main__':
    url = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
    downloader = YoutubeMP3Downloader(url)
    downloader.download_video()