# Base Micrcom Client class
from time import sleep, time
from threading import Lock, Thread
from datetime import datetime
from typing import List, Dict
import os
import hashlib
import io
from logging_handler import create_logger, INFO
from typing import Callable
from src.microcom.msg._base import *
from src.microcom.exceptions import *
from src.microcom.stats._base import *
from src.microcom.utils import supported_hash_algs
from microcom.pin import *
from microcom.pwm import *
from microcom.gpio import MicrocomGPIO
from microcom.i2c import MicrocomI2C
from microcom.bmx280 import Bmx280I2C
from microcom.pwm_in import MicrocomRP2PwmIn, MicrocomRP2TachIn
from microcom.neo import MicrocomNeoPixel

SEND_QUEUE_MAX = 10
RECEIVE_QUEUE_MAX = 10
SEND_TIMEOUT = 3
RETRY = 3
RESTART_TIMEOUT = 10
SERVER_NAME = ''
STATS_STREAMING = False
STATS_INTERVAL = 30
STATS_HISTORY = 100
LOG_LEVEL = INFO
SERVER_TIME_DRIFT_SEC = 2
SERVER_TIME_USE_LOCALTIME = True

class MicrocomClient:
    def __init__(self, receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        ''' If receive_callback calls the function, otherwise queues in receive queue '''
        self.receive_queue_max, self.send_queue_max, self.timeout, self.retry = receive_queue_max, send_queue_max, timeout, retry
        self.stats_interval, self.stats_history = stats_interval, stats_history
        self.receive_callback = receive_callback
        self._irq_handlers = {}
        self._logger = create_logger(log_level, name=f"{self.__class__.__name__}{(':' + name) if name != '' else ''}")
        self._send_queue = []  ###### Make this a queuemanager object??
        self._send_queue_lock = Lock()
        self._send_background_thread = Thread()
        self._last_sent_message = None
        self._last_sent_frag = None
        self._received_frag = None
        self._stats_lock = Lock()
        self._stats = []
        self._server_stats = []
        self._server_stats_lock = Lock() # used to lock access while writing to the stats
        self._packet_stats = [] # used to store send / receive statistics and timestamps
        self._packet_stats_lock = Lock()
        self._server = None # object to hold the server connection (serial or socket)

    def __del__(self):
        pass

    def close(self):
        ''' Close active sessions and objects '''
        pass

    @property
    def stream_stats(self) -> bool:
        ''' Return TRUE if stats are currently streaming '''
        with self._stats_lock:
            if len(self._stats) > 0 and isinstance(self._stats[-1], MicrocomStats):
                if self._stats[-1].timestamp > time() - self.stats_interval * 3:
                    return True
        return False

    @stream_stats.setter
    def stream_stats(self, value):
        if not isinstance(value, bool):
            raise ValueError("Expecting a bool for stream_stats")
        if value:
            self._logger.info(f"Starting stats steaming. Interval {self.stats_interval}...")
            self.start_streaming_stats(interval=self.stats_interval)
        elif not value:
            self._logger.info("Stopping streaming stats...")
            self.stop_streaming_stats()

    def is_open(self) -> bool:
        ''' Return True / False if connected to the server '''
        try:
            return self.ping(count=1, data=b'1', timeout=1, retry=0).get('success_rate', False) == True
        except Exception:
            return False

    def send(self, message:MicrocomMsg, wait_for_ack:bool=True, wait_for_reply:bool=False, timeout:None|int=None, retry:None|int=None) -> MicrocomMsg|None:
        ''' Serialize and send a microcom message. If not wait_for_reply returns none, otherwise returns the reply message
            Raises:
                MicrocomRetryExceeded()
                MicrocomException() '''
        raise NotImplementedError("Function 'send' must be overriden by an inheriting class")

    def send_ack(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None):
        ''' Send an ACK for a packet that was received '''
        raise NotImplementedError("Function '_receive_thread' must be overriden by inheriting class")

    def _receive_message(self, received_msg:MicrocomMsg):
        ''' Process an incoming message '''
        # if handle an ACK first
        if received_msg.direction == DIR_ACK and received_msg.frag_number == 0:
            # if the frag number is 0, use the last sent message
            if not isinstance(self._last_sent_message, MicrocomMsg):
                self._logger.warning(f"Received ACK message but no message waiting for ACK: {str(received_msg)} (Last sent message type: {type(self._last_sent_message)})")
                return
            if not self._last_sent_message.is_ack(received_msg):
                self._logger.warning(f"Received ACK message that does not match message waiting for ACK. Received: {str(received_msg)}, last sent: {self._last_sent_message}")
                return
            self._last_sent_message.ack_time = time()
            self._logger.debug(f"Received ACK for ID {received_msg.pkt_id}. ACK RTT: {self._last_sent_message.ack_rtt()}")
        elif received_msg.direction == DIR_ACK and received_msg.frag_number != 0:
            # if the frag number is NOT 0, use the last frag
            if not isinstance(self._last_sent_frag, MicrocomMsg):
                self._logger.warning(f"Received ACK FRAG message but no message waiting for ACK: {str(received_msg)} (Last sent message type: {type(self._last_sent_frag)})")
                return
            if not self._last_sent_frag.is_ack(received_msg):
                self._logger.warning(f"Received ACK FRAG message that does not match message waiting for ACK. Received: {str(received_msg)}, last sent: {self._last_sent_frag}")
            self._last_sent_frag.ack_time = time()
            self._logger.debug(f"Received ACK FRAG for ID ({received_msg.pkt_id}). ACK RTT: {self._last_sent_frag.ack_rtt()}")

        elif received_msg.direction == DIR_SEND_FRAG:
            self.send_ack(message=received_msg)
            # if we received a fragment that isn't a reply save the data until all fragments received
            if isinstance(self._received_frag, MicrocomMsg) and self._received_frag.pkt_id == received_msg.pkt_id:
                self._logger.debug(f"RECEIVED send fragment for pkt: {self._received_frag.pkt_id}")
                if self._received_frag.frag_number +1 != received_msg.frag_number:
                    self._logger.error(f"RECEIVED a fragment that should not be next. pkt_id {self._received_frag.pkt_id} last frag {self._received_frag.frag_number}, got frag {received_msg.frag_number}")
                    return
                else:
                    # if the fragment was the expected fragment, save the data (prevent double saves or missing fragments)
                    self._received_frag.frag_time = time()
                    self._received_frag.frag_number = received_msg.frag_number
                    self._received_frag.append_data(received_msg.data)
            elif isinstance(self._received_frag, MicrocomMsg):
                self._logger.warning(f"RECEIVED send fragment with a different id, previous id: {self._received_frag.pkt_id}, new id: {received_msg.pkt_id}, discarding old fragment")
                return
            else:
                # save the new fragment
                self._logger.debug(f"RECEIVED new send fragment for pkt: {received_msg.pkt_id}")
                self._received_frag = received_msg
                self._received_frag.frag_time = time()

        elif received_msg.direction == DIR_REPLY_FRAG:
            self.send_ack(message=received_msg)
            # if we received a fragment as a reply to a packet we sent, save until all the fragments are received
            if not isinstance(self._last_sent_message, MicrocomMsg):
                self._logger.warning(f"RECEIVED reply of type {received_msg.msg_type}, but no message waiting for reply: {str(received_msg)}")
                return
            if not self._last_sent_message.pkt_id == received_msg.pkt_id:
                self._logger.warning(f"RECEIVED reply of type {received_msg.msg_type} with id: {received_msg.pkt_id}, " \
                                     f"but response does not match last sent {self._last_sent_message.msg_type} id: {str(self._last_sent_message.pkt_id)}")
                return
            else:
                # if this is the 1st fagment, save as the reply
                if received_msg.frag_number == 1:
                    self._logger.debug(f"RECEIVED first fragment pkt_id: {received_msg.pkt_id}")
                    self._last_sent_message.reply_msg = received_msg
                    self._last_sent_message.frag_time = time()
                    self._last_sent_message.frag_number = received_msg.frag_number
                else:
                    if self._last_sent_message.frag_number +1 != received_msg.frag_number:
                        self._logger.error(f"RECEIVED a fragment that should not be next. pkt_id {self._last_sent_message.pkt_id} last frag {self._last_sent_message.frag_number}, got frag {received_msg.frag_number}")
                        return
                    # if the fragment was the expected fragment, save the data (prevent double saves or missing fragments)
                    self._logger.debug(f"RECEIVED fragment {received_msg.frag_number} pkt_id: {received_msg.pkt_id}")
                    self._last_sent_message.frag_time = time()
                    self._last_sent_message.reply_msg.append_data(received_msg.data)
                    self._last_sent_message.reply_msg.retries += received_msg.retries
                    self._last_sent_message.frag_number = received_msg.frag_number

        elif received_msg.direction == DIR_REPLY:
            # if this wasn't an ACK or a stats message, send back an ACK
            if not received_msg.msg_type == MSG_TYPE_STATS:
                self.send_ack(message=received_msg)

            # check the timestamp of the recieved packet. If the EPOCH time is off on the device, send a time sync
            # but ONLY apply to packets that have no retries (retry packets are not setup to update their timestamp)
            self._logger.debug(f"Receive Message Timestamp debug: {received_msg.retries}, {received_msg.timestamp > self.time_w_tz_offset() + SERVER_TIME_DRIFT_SEC}, {received_msg.timestamp}, {received_msg.timestamp < self.time_w_tz_offset() - SERVER_TIME_DRIFT_SEC}")
            if received_msg.retries == 0 and (received_msg.timestamp > self.time_w_tz_offset() + SERVER_TIME_DRIFT_SEC or received_msg.timestamp < self.time_w_tz_offset() - SERVER_TIME_DRIFT_SEC):
                if received_msg.timestamp == 0:
                    self._logger.debug("Received empty timestamp!")
                else:
                    self._logger.info("Sending timesync message...")
                    self.send(message=MicrocomMsg(msg_type=MSG_TYPE_TIME_SYNC, data=int(self.time_w_tz_offset())), wait_for_ack=False, wait_for_reply=False)

            # if receive a reply, check the last sent packet for a match
            if not isinstance(self._last_sent_message, MicrocomMsg):
                self._logger.warning(f"RECEIVED reply of type {received_msg.msg_type}, but no message waiting for reply: {str(received_msg)}")
                return
            if not self._last_sent_message.pkt_id == received_msg.pkt_id:
                self._logger.warning(f"RECEIVED reply of type {received_msg.msg_type} with id: {received_msg.pkt_id}, " \
                                        f"but response does not match last sent {self._last_sent_message.msg_type} id: {str(self._last_sent_message.pkt_id)}")
                return

            # if this is last fragment, combine the packets and process otherwise process accordingly
            if self._last_sent_message.reply_msg is not None:
                self._last_sent_message.reply_msg.append_data(received_msg.data)
            else:
                self._last_sent_message.reply_msg = received_msg
            self._last_sent_message.final_msg = received_msg
            self._last_sent_message.reply_time = time()
            self._logger.debug(f"Marking packet {self._last_sent_message} as received")

        elif received_msg.msg_type == MSG_TYPE_STATS:
            # if we receieved a stats message, remove stats > than the max and save the new stats
            with self._stats_lock:
                while len(self._stats) >= self.stats_history:
                    self._stats.pop(0)
                self._stats.append(received_msg)
        elif received_msg.msg_type == MSG_TYPE_PING:
            # we received a ping, so reply
            self.send(MicrocomMsg(msg_type=MSG_TYPE_PING, data=received_msg.data), wait_for_ack=True, wait_for_reply=False)

        elif received_msg.msg_type == MSG_TYPE_IRQ:
            # handle an IRQ message that was sent
            if not isinstance(received_msg.data, dict) or 'pin_info' not in received_msg.data:
                self._logger.warning(f"IRQ Message received but no pin information: {received_msg.data}")
                return
            self._logger.info(f"Received IRQ message data: {received_msg.data}")
            for handler in self._irq_handlers.get(str(received_msg.data['pin_info'].get('pin')), []):
                if isinstance(handler, Callable):
                    self._logger.debug(f"IRQ Pin {received_msg.data['pin_info'].get('pin')}: Calling {handler}")
                    handler(received_msg.data)

        else:
            self._logger.error(f"RECEIVED unsupported message of direction {received_msg.direction}, {received_msg}")

    def get_stats(self, qty:int=1) -> list:
        ''' Get the last 'qty' stats and return them '''
        stats = []
        with self._stats_lock:
            stats_data = self._stats[qty * -1:] # get the last 'qty' records
            for stats_record in stats_data:
                if not stats_record.__class__.__name__ == 'MicrocomMsg' or not isinstance(stats_record.data, dict):
                    self._logger.error(f"Get Stats: Expecting a MicrocomMsg for stats and have: {stats_record}")
                    raise ValueError(f"Expecting a MicrocomMsg for stats and have: {stats_record}")
                # convert to a readable dataset and save
                stats.append({
                    'timestamp': stats_record.received_time,
                    'datetime': datetime.fromtimestamp(stats_record.received_time),
                    'cpu_freq': stats_record.data.get('cpu_freq', None),
                    'temp': stats_record.data.get('temp', {}),
                    'memory': {
                        'total': stats_record.data.get('memory', [None, None, None])[0],
                        'free': stats_record.data.get('memory', [None, None, None])[1],
                        'used': stats_record.data.get('memory', [None, None, None])[2]
                    },
                    'disk': {
                        path:{'total': stats_record.data['disk'].get(path, [None, None, None])[0],
                              'free': stats_record.data['disk'].get(path, [None, None, None])[1],
                              'used': stats_record.data['disk'].get(path, [None, None, None])[2]} for path in stats_record.data.get('disk', {})
                    }
                })
                # add other stats in
                stats[len(stats)-1].update(**{x:y for x,y in stats_record.data.items() if x not in ('cpu_freq', 'temp', 'memory', 'disk')})
        return stats

    def get_last_stat(self) -> dict:
        ''' Get just the last stat '''
        return self.get_stats(qty=1)[0] if len(self._stats) > 0 else {}

    def register_irq_handler(self, pin:int, handler:Callable):
        ''' Register a handler function for any IRQ received associated to a pin '''
        self._logger.debug(f"Pin {pin}: Registering IRQ handler: {handler}")
        if str(pin) not in self._irq_handlers:
            self._irq_handlers[str(pin)] = [handler]
        else:
            self._irq_handlers[str(pin)].append(handler)

    def clear_irq_handler(self, pin:int):
        ''' Clear all the IRQ handlers for a pin '''
        if str(pin) in self._irq_handlers:
            self._logger.debug(f"Pin {pin}: Clearing IRQ handlers: {self._irq_handlers[str(pin)]}")
            self._irq_handlers[str(pin)] = []

    def ping(self, count:int=5, data:bytes|str|None=None, random:int=32, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' Ping the Microcom server and return a dict with the following: (latency is in ms)
                {'sent': int, 'received': int, 'success_rate': float (percent), 'max_latency': int, 'avg_latency': int, 'min_latency': int}
            If data is provided, send the specified bytes or string '''
        ping_messages = []
        self._logger.info(f"Starting ping, count {count}, timeout: {timeout if timeout is not None else self.timeout}, retry: {retry if retry is not None else retry}")
        for x in range(count):
            ping_message = MicrocomMsg(data=data if data is not None else os.urandom(random))
            ping_messages.append(ping_message)
            self._logger.debug(f"Sending ping {x+1}/{count}, data: {ping_message.data}")
            self.send(message=ping_message, wait_for_reply=True, timeout=timeout, retry=retry)

        # calculate stats
        successful_replies = [x for x in ping_messages if x is not None and x.reply_received() and x.data == x.reply_msg.data]
        ping_data = {
            'sent': len(ping_messages),
            'received': len(successful_replies),
            'success_rate': len(successful_replies) / len(ping_messages),
            'retries': sum(x.retries for x in successful_replies),
            'max_latency': max(x.rtt() for x in successful_replies) if len(successful_replies) > 0 else 0,
            'avg_latency': (sum(x.rtt() for x in successful_replies) / len(successful_replies)) if len(successful_replies) > 0 else 0,
            'min_latency': min(x.rtt() for x in successful_replies) if len(successful_replies) > 0 else 0
        }
        self._logger.debug(f"Ping data: {ping_data}")
        return ping_data

    def server_info(self, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' Query the server for information, returns as a dict
            Raises:
                MicrocomRetryExceeded() '''
        message = MicrocomMsg(msg_type=MSG_TYPE_INFO)
        self._logger.info("Sending server info request...")
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or not isinstance(message.reply_msg, MicrocomMsg) or not isinstance(message.reply_msg.data, dict):
            raise MicrocomException(f'Expected server info response and received {str(message.reply_msg)}')
        return message.reply_msg.data

    def list_dir(self, path:str='/', subdirs:bool=True, checksums:bool=False, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' List all files on the microcom server
            Data sent as {'path': '[dir path]', subdirs: bool}'''
        message = MicrocomMsg(msg_type=MSG_TYPE_LIST_DIR, data={'path': path, 'subdirs': subdirs, 'checksums': checksums})
        self._logger.info(f"Requesting list dir from server: {message.data}")
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or not isinstance(message.reply_msg, MicrocomMsg):
            raise MicrocomRetryExceeded("Retry exceeded for messge.")
        if message.reply_msg.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"General error occured on microcom server: {message.reply_msg.data}")
        if message.reply_msg.return_code == MICROCOM_FILE_ERROR or not isinstance(message.reply_msg.data, dict):
            raise MicrocomFileNotFound(f"Path '{path}' not found")
        self._logger.debug(f"List dir response from server: {message.data}: {message.reply_msg.data}")
        return message.reply_msg.data

    def get_checksum(self, remote:str, timeout:None|int=None, retry:None|int=None) -> dict:
        ''' Get the checksum of the file, returns None or raises an exception
            Data passed a string with the path/filename
            Raises:
                MicrocomRetryExceeded() 
                MicrocomFileNotFound() '''
        message = MicrocomMsg(msg_type=MSG_TYPE_CHECKSUM, data={'path': remote, 'alg_list': supported_hash_algs()})
        self._logger.info(f"Requesting checksum from server: {message.data}")
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or not isinstance(message.reply_msg, MicrocomMsg):
            raise MicrocomRetryExceeded("Retry exceeded for messge.")
        if message.reply_msg.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"General error occured on microcom server: {message.reply_msg.data}")
        if message.reply_msg.return_code == MICROCOM_FILE_ERROR or not isinstance(message.reply_msg.data, dict):
            raise MicrocomFileNotFound(f"Path '{remote}' not found")
        self._logger.debug(f"Checksum response from server: {message.data}: {message.reply_msg.data}")
        return message.reply_msg.data

    def download_file(self, remote:str, alg_list:None|list=None, timeout:None|int=None, retry:None|int=None) -> io.BytesIO:
        ''' Copy one or more files from the microcom srever, if save_path, save to the path specified, otherwise return as:
            {'[filename]': [file_contents], ...}
            Raises:
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomFileNotFound() '''
        message = MicrocomMsg(msg_type=MSG_TYPE_READ_FILE, data={'path': remote, 'alg_list': alg_list})
        self._logger.info(f"Requesting file from server: {message.data}")
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or not isinstance(message.reply_msg, MicrocomMsg):
            raise MicrocomRetryExceeded("Retry exceeded for messge.")
        if message.reply_msg.return_code == MICROCOM_GENERAL_ERROR:
            raise MicrocomException(f"General error occured on microcom server: {message.reply_msg.data}")
        if message.reply_msg.return_code == MICROCOM_FILE_ERROR or not isinstance(message.reply_msg.data, bytes):
            raise MicrocomFileNotFound(f"Path '{remote}' not found")
        if not isinstance(message.final_msg, MicrocomMsg) or not isinstance(message.final_msg.data, dict):
            raise MicrocomException("Did not receive checksum data with file.")
        if not isinstance(message.reply_msg.data, bytes):
            raise MicrocomException(f"Did not receive a byte stream for file. Received: {message.reply_msg.data}")
        if message.final_msg.data.get('alg') not in supported_hash_algs():
            raise MicrocomChecksumFailed(f"Received checksum using {message.final_msg.data.alg}. Supported algorithms: {supported_hash_algs()}")
        # validate the checksum
        checksum = getattr(hashlib, message.final_msg.data.get('alg'))(message.reply_msg.data).hexdigest()
        if checksum != bytes(message.final_msg.data.get('checksum')).decode('utf-8'):
            raise MicrocomChecksumFailed(f"file: {remote}, received checksum as {message.final_msg.data}, calculated: {checksum}")
        self._logger.debug(f"Download of {remote} complete.")

        # return a bytestream object
        return io.BytesIO(message.reply_msg.data) # type: ignore

    def upload_file(self, stream:io.BufferedReader, path:str, timeout:None|int=None, retry:None|int=None):
        ''' Send a single file based on a bytes stream to the microcom server '''
        # clear the temp file on the server
        self._logger.info("Uploading file to server using io stream...")
        self._logger.debug("Clearing temp write file on server...")
        message = MicrocomMsg(msg_type=MSG_TYPE_WRITE_FILE, data=None)
        self.send(message=message, wait_for_reply=True)
        read_buf = None

        # find a hashing algorithm that is supported on the client and server
        server_info = self.server_info(timeout=timeout, retry=retry)
        hash_alg = [x for x in supported_hash_algs() if x in server_info['hash_algs']][0]
        hash_obj = getattr(hashlib, hash_alg)()
        while read_buf != b'':
            # read a chunk from the file and send the message
            read_buf = stream.read(FILE_CHUNK_SIZE)
            hash_obj.update(read_buf)
            message = MicrocomMsg(msg_type=MSG_TYPE_WRITE_FILE, data=read_buf)
            self.send(message=message, wait_for_reply=True, timeout=timeout, retry=retry)
            # if we didn't get the reply, raise an exception
            if not message.reply_received():
                raise MicrocomRetryExceeded("Retry exceeded for message.")

        # verify the checksum on tie file
        server_checksum = self.get_checksum('.temp_file', timeout=timeout, retry=retry)
        if bytes(server_checksum['checksum']).decode('utf-8') != hash_obj.hexdigest():
            raise MicrocomChecksumFailed("Checksum of copied data did not match. File {path} not uploaded.")

        # rename the file
        self.rename_file('/.temp_file', path, timeout=timeout, retry=retry)
        self._logger.info(f"File uploaded to {path}")

    def rename_file(self, old:str, new:str, timeout:None|int=None, retry:None|int=None):
        ''' Rename a file on the server '''
        self._logger.info(f"Renaming file on server '{old}' to '{new}'...")
        message = MicrocomMsg(msg_type=MSG_TYPE_RENAME_FILE, data={'old':old, 'new': new})
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomRenameFailed(f"Attempt to rename file '{old}' to '{new}' failed. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        self._logger.info(f"File renamed from {old} to {new}")

    def delete_file(self, path:str, timeout:None|int=None, retry:None|int=None):
        ''' Rename a file on the server '''
        self._logger.info(f"Deleting file on server '{path}'...")
        message = MicrocomMsg(msg_type=MSG_TYPE_DEL_FILE, data=path)
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomRenameFailed(f"Attempt to delete file '{path}' failed. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def reset_server(self, wait=True, timeout=RESTART_TIMEOUT) -> bool:
        ''' Execute a reset on the server, wait the specified timeout for the server to reconnet
            Raises:
                MicrocomResetTimeout() if the server doesn't respond in the specified interval '''
        self.send(MicrocomMsg(msg_type=MSG_TYPE_RESET), wait_for_ack=True)
        if wait:
            sleep(1)
            start_time = time()
            while start_time + timeout > time():
                if self.is_open():
                    return True
                sleep(.100)
            raise MicrocomResetTimeout(f"Server did not respond within {timeout} seconds")
        return True # Return True if we we received an ACK and aren't set to wait for the reset

    def get_pin(self, pin:int, log_level=INFO, mode=IN, pull=PULL_NONE, value:int|None=None) -> MicrocomGPIO:
        ''' Get a GPIO pin '''
        return MicrocomGPIO(pin=pin, send=self.send, message_class=MicrocomMsg, log_level=log_level, mode=mode, pull=pull, irq_register=self.register_irq_handler, value=value) # type: ignore

    def get_pwm(self, pin:int, log_level=INFO, freq=10_000, duty_u16=0, invert=False) -> MicrocomPWM:
        ''' Return a PWM pin object '''
        return MicrocomPWM(pin=pin, send=self.send, message_class=MicrocomMsg, log_level=log_level, freq=freq, duty_u16=duty_u16, invert=invert) # type: ignore

    def get_rp2_pwm_in(self, pin:int, log_level=INFO, freq=10_000, sm:int|None=None, active:bool=True, invert:bool=False) -> MicrocomRP2PwmIn:
        ''' Return a RP2 PWM input object using PIO '''
        return MicrocomRP2PwmIn(pin=pin, log_level=log_level, freq=freq, sm=sm, active=active, invert=invert, send=self.send)

    def get_rp2_tach_in(self, pin:int, pulse_per_rev:int, log_level=INFO, freq=10_000, sm:int|None=None, active:bool=True, invert:bool=False) -> MicrocomRP2TachIn:
        ''' Return a RP2 Tach object using PIO '''
        return MicrocomRP2TachIn(pin=pin, pulse_per_rev=pulse_per_rev, log_level=log_level, freq=freq, sm=sm, active=active, invert=invert, send=self.send)

    def get_neopixel(self, name:str, devices:list[dict], log_level=INFO) -> MicrocomNeoPixel:
        ''' Return a NeoPixel object '''
        return MicrocomNeoPixel(devices=devices, name=name, send=self.send, log_level=log_level)

    def get_neopixel_from_server(self, name:str, log_level=INFO) -> MicrocomNeoPixel:
        ''' return a neopixel object that already exists on the server '''
        return MicrocomNeoPixel.from_server(name=name, send=self.send, log_level=log_level)

    def bus_list(self) -> dict:
        ''' Get the list of initialized buses from the server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_GET_VAR, data='buses')
        self.send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error getting bus list. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, dict):
            raise ValueError(f"Expected a dict representing the bus list but received: {message.reply_msg.data}")
        return message.reply_msg.data

    def i2c_init(self, bus_id:int, sda_pin:int, scl_pin:int, hardware:bool=True, freq:int=20_000, log_level=INFO):
        ''' Initiate an I2C bus '''
        message = MicrocomMsg(msg_type=MSG_TYPE_BUS_INIT, data={'bus_type': I2C, 'bus_id': bus_id, 'scl_pin': scl_pin, 'sda_pin': sda_pin, 'freq': freq, 'hardware': hardware})
        self.send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error initializing i2c bus {bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        if not isinstance(message.reply_msg.data, dict):
            raise ValueError(f"Expected a dict but received: {message.reply_msg.data}")
        return MicrocomI2C(bus_id=bus_id, send=self.send, log_level=log_level, message_class=MicrocomMsg, irq_register=self.register_irq_handler) # type: ignore

    def get_var(self, var_name:str|None=None, var_type:str|None='saved'):
        ''' Get the specified variable from the MicrocomServer '''
        message = MicrocomMsg(msg_type=MSG_TYPE_GET_VAR, data={'var_name': var_name, 'var_type': var_type})
        self.send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error getting var {var_name}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    def set_var(self, var_data:list|dict, timeout:None|int=None, retry:None|int=None):
        ''' Set a variable in memory on the microcom server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_SET_VAR, data=var_data)
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or message.reply_msg is None or (message.reply_msg.__class__.__name__ == 'MicrocomMsg' and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error setting vars {var_data}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def exec_func(self, function:str, obj:str|None=None, saved:str|None=None, save_return:str|None=None, args:list|None=None, kwargs:dict|None=None, timeout:None|int=None, retry:None|int=None):
        ''' Execute a function associated to a class or object '''
        if obj is None and saved is None:
            raise MicrocomException("Must pass either an object or name of a saved object to execute a function.")
        data = {'function': function, 'obj': obj, 'saved': saved, 'save_return': save_return, 'args': args, 'kwargs': kwargs}
        # remove any keys that are None
        null_keys = []
        for key, value in data.items():
            if value is None:
                null_keys.append(key)
        for key in null_keys:
            data.pop(key)
        message = MicrocomMsg(msg_type=MSG_TYPE_EXEC_FUNC, data=data)
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or message.reply_msg is None or (message.reply_msg.__class__.__name__ == 'MicrocomMsg' and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error executing function '{function}'. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def get_bytearray(self, var_name:str, timeout:None|int=None, retry:None|int=None):
        ''' Get the specified byte array from the MicrocomServer '''
        message = MicrocomMsg(msg_type=MSG_TYPE_GET_BYTE_ARR, data=var_name)
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error getting byte array {var_name}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    def create_bytearray(self, var_name:str, length:int):
        ''' Set a variable in memory on the microcom server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_CREATE_BYTE_ARR, data={'key': var_name, 'length': length})
        self.send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error setting var {var_name}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def remote_class(self, path:str, class_name:str, save_instance:str|None=None, save_return:str|None=None, init_params:dict|None=None, run_params:dict|None=None, timeout:None|int=None, retry:None|int=None):
        ''' Load and execute a remote class on the Microcom server '''
        message = MicrocomMsg(msg_type=MSG_TYPE_IMPORT_CLASS, data={'path': path,
                                                                    'class_name': class_name,
                                                                    'save_instance': save_instance,
                                                                    'save_return': save_return,
                                                                    'init_params': {} if init_params is None else init_params,
                                                                    'run_params': {} if run_params is None else run_params})
        self.send(message, wait_for_reply=True, timeout=timeout, retry=retry)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomException(f"Error call class {path}.{class_name}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data

    def server_up_to_date(self, timeout:None|int=None, retry:None|int=None, server_info=None|dict) -> bool:
        ''' Return True / False if the server is running the same version as the client '''
        server_info = self.server_info(timeout=timeout, retry=retry) if server_info is None else server_info
        if not isinstance(server_info, dict):
            raise ValueError(f"Expected a dict object and received: {type(server_info)}. Data: {server_info}")
        if 'version' not in server_info or not isinstance(server_info['version'], dict):
            raise ValueError(f"'version' section missing from server info: {server_info}")
        if 'microcom' not in server_info['version'] or 'microcom_message' not in server_info['version'] or 'micropython' not in server_info['version']:
            raise ValueError(f"server info 'version' section should contain 'microcom', 'microcom_message' and 'micropython', received {server_info}")

        ################## Need to add comparison logic!

        return False        

    def update_server(self, wait=True, overwrite=False, restart_timeout=RESTART_TIMEOUT, timeout:None|int=None, retry:None|int=None):
        ''' copies all current microcom files to the server.  If overwrite is False, files are copied to a new folder and falls back if failure
            NOTE: set main.py to try loading from the new folder, then fall back to original?
            Raises:
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomUpdateFailed() '''

    def micropython_version(self, timeout:None|int=None, retry:None|int=None) -> tuple:
        ''' Get the micropython version running on the microcom server '''
        # call server_up_to_date() to raise errors if values are mossing
        server_info = self.server_info()
        self.server_up_to_date(server_info=server_info)
        return server_info['version']['micropython']

    def microcom_version(self, timeout:None|int=None, retry:None|int=None) -> tuple:
        ''' Get the micropython version running on the microcom server '''
        # call server_up_to_date() to raise errors if values are mossing
        server_info =self.server_info()
        self.server_up_to_date(server_info=server_info)
        return server_info['version']['microcom']

    def micropython_update(self, wait=True, restart_timeout=RESTART_TIMEOUT, timeout:None|int=None, retry:None|int=None):
        ''' Perform an OTA update of micropython on the Microcom server IF SUPPORTED
            Raises:
                MicrocomUnsupported() - If OTA update is not supported
                MicrocomRetryExceeded()
                MicrocomChecksumFailed()
                MicrocomMicroPyUpdateFailed() '''

    def server_stats(self) -> MicrocomStats:
        ''' Get the latest stats (if streaming enabled) or query from the device if streaming is disabled '''
        with self._stats_lock:
            if self.stream_stats and len(self._stats) > 0 and isinstance(self._stats[-1], MicrocomStats):
                if self._stats[-1].timestamp > time() - self.stats_interval * 3:
                    return self._stats[-1]
                raise MicrocomStatsError(f"Streaming Stats enabled but not being received. It has been {time() - self._stats[-1].timestamp} since last update. Interval is {self.stats_interval}")

        # if streaming not enabled, get an instance of the stats
        data = self.send(MicrocomMsg(msg_type=MSG_TYPE_STATS), wait_for_reply=True)
        if isinstance(data.data, MicrocomStats):
            return data.data
        raise MicrocomStatsError(f"Did not receive stats as reply. Received: {data.data}")

    def clear_stats(self):
        ''' Clears all stored stats '''
        with self._stats_lock:
            self._stats = []

    def start_streaming_stats(self, interval:int|None=None, timeout:None|int=None, retry:None|int=None):
        ''' Clears the existing stats and Starts the microcom server sending streaming stats '''
        self.clear_stats()
        self.stats_interval = interval if interval is not None else self.stats_interval
        self.send(MicrocomMsg(msg_type=MSG_TYPE_STATS_ENABLE, data={'interval': self.stats_interval}), wait_for_ack=True, timeout=timeout, retry=retry)

    def stop_streaming_stats(self, timeout:None|int=None, retry:None|int=None):
        ''' Stop the microcom server sending streaming stats '''
        self.send(MicrocomMsg(msg_type=MSG_TYPE_STATS_DISABLE), wait_for_ack=True, timeout=timeout, retry=retry)


    def time_w_tz_offset(self) -> int:
        ''' Return the timestamp adjusted to account for the local system timezone. Micropython does not support TZ setting'''
        try:
            timezone = datetime.now().astimezone().tzinfo.utcoffset(None)
            new_time = time() + (timezone.days * 3600 * 24) + (timezone.seconds)
        except Exception as e:
            self._logger.warning(f"Error getting local timezone info to update the server: {e.__class__.__name__}: {e}")
            return int(time())
        return int(new_time)