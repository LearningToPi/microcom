# Base Micrcom Client class
from time import sleep, time
from threading import Lock, Thread
import os
from copy import deepcopy
from hashlib import md5
from logging_handler import create_logger, INFO
from typing import Callable
from microcom.message._base import MicrocomMessage, MicrocomMessageFile, MSG_TYPE_INFO, MSG_TYPE_LIST_DIR, MSG_TYPE_CHECKSUM, MSG_TYPE_GET
from microcom.exceptions import *
from microcom.return_codes import *

SEND_QUEUE_MAX = 10
RECEIVE_QUEUE_MAX = 10
SEND_TIMEOUT = 3
RETRY = 3
RESTART_TIMEOUT = 10
SERVER_NAME = ''
STATS_STREAMING = True
STATS_INTERVAL = 5
STATS_HISTORY = 100
LOG_LEVEL = INFO

class MicrocomClient:
    def __init__(self, receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_streaming:bool=STATS_STREAMING, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        ''' If receive_callback calls the function, otherwise queues in receive queue '''
        self.receive_queue_max, self.send_queue_max, self.send_timeout, self.retry = receive_queue_max, send_queue_max, send_timeout, retry
        self.stream_stats, self.stats_interval, self.stats_history = stats_streaming, stats_interval, stats_history
        self.receive_callback = receive_callback
        self._logger = create_logger(log_level, name=f"self.__class__.__name__{(':' + name) if name != '' else ''}")
        self.__send_queue = []  ###### Make this a queuemanager object??
        self.__send_queue_lock = Lock()
        self.__send_thread = Thread()
        self.__receive_queue = [] ###### Make this a queuemanager object??
        self.__receive_queue_lock = Lock()
        self.__server_stats = []
        self.__server_stats_lock = Lock() # used to lock access while writing to the stats
        self.__packet_stats = [] # used to store send / receive statistics and timestamps
        self.__packet_stats_lock = Lock()
        self.__server = None # object to hold the server connection (serial or socket)

        if self.stream_stats:
            self.start_streaming_stats()

    def __del__(self):
        pass

    def close(self):
        ''' Close active sessions and objects '''
        pass

    def is_open(self):
        ''' Return True / False if connected to the server '''

    def send(self, message:MicrocomMessage, wait_for_ack:bool=True, wait_for_reply:bool=False, timeout:None|int=None, retry:None|int=None) -> MicrocomMessage:
        ''' Serialize and send a microcom message. If not wait_for_reply, returns original message, else returns reply
            Raises:
                MicrocomRetryExceeded()
                MicrocomException() '''
        # apply default timeout and retry
        timeout = timeout if timeout is not None else self.send_timeout
        retry = retry if retry is not None else self.retry
        message.send_time = time()
        with self.__send_queue_lock:
            self.__send_queue.append(message)
        if wait_for_ack or wait_for_reply:
            return_data = self.send_thread(timeout=timeout, retry=retry)
            if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.ack_received:
                raise MicrocomException('Send message expected an ACK and received None')
        else:
            # if not wait for ack, start a background thread to send all messages if non are running
            if not self.__send_thread.is_alive():
                self.__send_thread = Thread(target=self.send_thread, kwargs={'timeout': timeout, 'retry': retry})
            return message
        if wait_for_reply:
            # if wait for reply
            while not return_data.reply_received and time() < message.ack_time + (timeout * retry):
                sleep(timeout / 4)
            if not return_data.reply_received or not isinstance(message.reply_message, MicrocomMessage):
                raise MicrocomException('Wait for reply specified but no reply received')
            return message.reply_message
        return message
        
    def send_thread(self, timeout:None|int=None, retry:None|int=None) -> None | MicrocomMessage:
        ''' Send all messages in the queue - override on a per client type basis
            Raises:
                MicrocomRetryExceeded() '''

    def receive_waiting(self) -> bool:
        ''' Return True / False if a message is waiting in the queue '''
        return len(self.__receive_queue) > 0

    def _receive_message(self, message:MicrocomMessage):
        ''' Process an incoming message '''

    def ping(self, count:int=5, timeout:None|int=None, data:bytes|str|None=None) -> dict:
        ''' Ping the Microcom server and return a dict with the following: (latency is in ms)
                {'sent': int, 'received': int, 'success_rate': float (percent), 'max_latency': int, 'avg_latency': int, 'min_latency': int}
            If data is provided, send the specified bytes or string '''
        ping_replies = []
        for x in range(count):
            ping_replies.append(self.send(message=MicrocomMessage(data=data if data is not None else os.urandom(64)), wait_for_reply=True))

        # calculate stats
        successful_replies = [x for x in ping_replies if x is not None and x.reply_received]
        return {
            'sent': len(ping_replies),
            'received': len(successful_replies),
            'success_rate': len(successful_replies) / len(ping_replies),
            'max_latency': max([x.rtt() for x in successful_replies]),
            'avg_latency': sum([x.rtt() for x in successful_replies]) / len(successful_replies),
            'min_latency': min([x.rtt() for x in successful_replies])
        }

    def server_info(self, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' Query the server for information, returns as a dict
            Raises:
                MicrocomRetryExceeded() '''
        return_data = self.send(MicrocomMessage(msg_type=MSG_TYPE_INFO), wait_for_reply=True, timeout=timeout, retry=retry)
        if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.is_reply or not isinstance(return_data.data, dict):
            raise MicrocomException(f'Expected server info response and received {return_data}')
        return return_data.data

    def list_files(self, path:str='/', subdirs:bool=True, timeout:None|int=None, retry:None|int=None) -> list:
        ''' List all files on the microcom server
            Data sent as {'path': '[dir path]', subdirs: bool}'''
        return_data = self.send(MicrocomMessage(msg_type=MSG_TYPE_LIST_DIR, data={'path': path, 'subdirs': subdirs}), wait_for_reply=True, timeout=timeout, retry=retry)
        if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.is_reply:
            raise MicrocomException(f'Expected server info response and received {return_data}')
        if return_data.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"Error occured on microcom server: {return_data.data}")
        if return_data.return_code == MICROCOM_FILE_NOT_FOUND or not isinstance(return_data.data, list):
            raise MicrocomFileNotFound(f"Path '{path}' not found")
        return return_data.data

    def get_checksum(self, remote:str, timeout:None|int=None, retry:None|int=None) -> str:
        ''' Get the checksum of the file, returns None or raises an exception
            Data passed a string with the path/filename
            Raises:
                MicrocomRetryExceeded() 
                MicrocomFileNotFound() '''
        return_data = self.send(MicrocomMessage(msg_type=MSG_TYPE_CHECKSUM, data=remote), wait_for_reply=True, timeout=timeout, retry=retry)
        if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.is_reply:
            raise MicrocomException(f'Expected server info response and received {return_data}')
        if return_data.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"Error occured on microcom server: {return_data.data}")
        if return_data.return_code == MICROCOM_FILE_NOT_FOUND or not isinstance(return_data.data, str):
            raise MicrocomFileNotFound(f"Path '{remote}' not found")
        return return_data.data

    def download_files(self, remote:str|list, save_path:None|str=None, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' Copy one or more files from the microcom srever, if save_path, save to the path specified, otherwise return as:
            {'[filename]': [file_contents], ...}
            Raises:
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomFileNotFound() '''
        remote = [remote] if isinstance(remote, str) else remote
        return_data = self.send(MicrocomMessage(msg_type=MSG_TYPE_GET, data=remote), wait_for_reply=True, timeout=timeout, retry=retry)
        if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.is_reply:
            raise MicrocomException(f'Expected server info response and received {return_data}')
        if return_data.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"Error occured on microcom server: {return_data.data}")
        if return_data.return_code == MICROCOM_FILE_NOT_FOUND or not isinstance(return_data.data, dict):
            raise MicrocomFileNotFound(f"Path '{remote}' not found")
        # get the checksum of the file
        for key, value in return_data.data:
            remote_checksum = self.get_checksum(remote=key)
            local_checksum = str(md5(value))
            if remote_checksum != local_checksum:
                raise MicrocomChecksumFailed(f"Checksum failed for file '{key}'")
        return return_data.data

    def upload_files(self, path:str, remote:str, subdir=False, timeout:None|int=None, retry:None|int=None):
        ''' Copy one or more files to the microcom server, if a file name is specified, just that file is loaded. If a dir is specified then all files in that dir (and subdir if selected)
            are uploaded
            Raises:
             MicrocomUploadFailed() if the upload fails '''
        def get_files(path:str, subdir:bool) -> list:
            files = []
            if os.path.isdir(path):
                contents = os.listdir(path)
                for item in contents:
                    if os.path.isfile(os.path.join(path, item)):
                        files.append(os.path.join(path, item))
                    elif subdir:
                        files += get_files(os.path.join(path, item), subdir=subdir)
            elif os.path.isfile(path):
                files.append(path)
            else:
                raise MicrocomFileNotFound(f"Local file '{path}' not found")
            return files
        files = get_files(path=path, subdir=subdir)
        for file in files:
            with open(file, 'r', encoding='utf-8') as input_file:
                return_data = self.send(MicrocomMessageFile(data=input_file.read(), path=remote), wait_for_ack=True, timeout=timeout, retry=retry)
                if return_data is None or not isinstance(return_data, MicrocomMessage) or not return_data.ack_received:
                    raise MicrocomUploadFailed(f"Failed to upload '{file}'")

    def reset_server(self, wait=True, timeout=RESTART_TIMEOUT):
        ''' Execute a reset on the server, wait the specified timeout for the server to reconnet '''

    def server_up_to_date(self, timeout:None|int=None, retry:None|int=None) -> bool:
        ''' Return True / False if the server is running the same version as the client '''

    def update_server(self, wait=True, overwrite=False, restart_timeout=RESTART_TIMEOUT, timeout:None|int=None, retry:None|int=None):
        ''' copies all current microcom files to the server.  If overwrite is False, files are copied to a new folder and falls back if failure
            NOTE: set main.py to try loading from the new folder, then fall back to original?
            Raises:
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomUpdateFailed() '''

    def micropython_version(self, timeout:None|int=None, retry:None|int=None) -> tuple:
        ''' Get the micropython version running on the microcom server '''

    def micropython_update(self, wait=True, restart_timeout=RESTART_TIMEOUT, timeout:None|int=None, retry:None|int=None):
        ''' Perform an OTA update of micropython on the Microcom server IF SUPPORTED
            Raises:
                MicrocomUnsupported() - If OTA update is not supported
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomMicroPyUpdateFailed() '''

    def server_stats(self) -> dict:
        ''' Get the latest stats (if streaming enabled) or query from the device if streaming is disabled '''

    def start_streaming_stats(self):
        ''' Start the microcom server sending streaming stats '''

    def stop_streaming_stats(self):
        ''' Stop the microcom server sending streaming stats '''