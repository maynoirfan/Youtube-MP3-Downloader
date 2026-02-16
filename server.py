# Updated server.py with Security Fixes and Code Quality Improvements
import logging
import threading
import os
import paramiko
from keyring import get_password, set_password

# Setup logging
logging.basicConfig(filename='app.log', level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
lock = threading.Lock()

class SSHClient:
    def __init__(self, hostname, username):
        self.hostname = hostname
        self.username = username
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._connect()

    def _connect(self):
        try:
            private_key_path = os.path.expanduser('~/.ssh/id_rsa')  # Path to private key
            self.client.connect(self.hostname, username=self.username, key_filename=private_key_path)
            logging.info('SSH connection established successfully.')
        except paramiko.SSHException as e:
            logging.error(f'Failed to connect via SSH: {e}')
            raise

    def execute_command(self, command):
        try:
            with lock:
                stdin, stdout, stderr = self.client.exec_command(command)
                return stdout.read(), stderr.read()
        except Exception as e:
            logging.error(f'Error executing command: {e}')
            raise

    def close(self):
        self.client.close()

# SFTP implementation
class SFTPClient:
    def __init__(self, ssh_client):
        self.sftp = ssh_client.client.open_sftp()

    def upload_file(self, local_path, remote_path):
        try:
            self.sftp.put(local_path, remote_path)
            logging.info('File uploaded successfully.')
        except Exception as e:
            logging.error(f'Failed to upload file: {e}')
            raise

    def close(self):
        self.sftp.close()

# Main execution block
if __name__ == '__main__':
    hostname = 'your.hostname.com'
    username = 'your_username'

    ssh_client = SSHClient(hostname, username)
    sftp_client = SFTPClient(ssh_client)

    # Example command execution
    command = 'ls -l'
    try:
        output, error = ssh_client.execute_command(command)
        logging.info(f'Command output: {output}')
        logging.error(f'Command error: {error}')
    finally:
        ssh_client.close()
        sftp_client.close()