''' Serial based Microcom Client (TTL or RS232) '''
from typing import Callable
from logging_handler import INFO
from serial import Serial
from time import time, sleep
from src.microcom.client._base import MicrocomClient, RECEIVE_QUEUE_MAX, RETRY, SEND_QUEUE_MAX, SEND_TIMEOUT, SERVER_NAME, STATS_HISTORY, STATS_INTERVAL, STATS_STREAMING, LOG_LEVEL
from src.microcom.msg._base import *
from src.microcom.exceptions import *
import re
import socket
from threading import Thread, Lock

from microcom.msg._base import MicrocomMsg

UDP_PORT = 2099
MAX_MSG_LENGTH = 384
DATA_MAX_BUFFER_SIZE = 801


class MicrocomClientUDP(MicrocomClient):
    def __init__(self, ip:str, port:int=UDP_PORT,
                 receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        self.ip, self.port, self._client = ip, port, None
        self.__udp_write_lock = Lock()
        self.__udp_send_lock = Lock()
        super().__init__(receive_queue_max, send_queue_max, log_level, name, send_timeout, retry, stats_interval, stats_history, receive_callback)
        self.receive_stop = False
        self.connect()
        self._receive_background_thread = Thread(target=self._receive_thread)
        self._receive_background_thread.start()

    def send_ack(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None):
        ''' Send ACK messages as soon as the serial port is writable '''
        with self.__udp_write_lock:
            header, data, footer = MicrocomMsg.ack(message=message).serialize()
            self._logger.debug(f"Sending ACK: {header}")
            if isinstance(self._client, socket.socket):
                self._client.send(header + data + footer)
            else:
                self._logger.error(f"SEND_ACK client not connected. Discarding message id: {message.pkt_id}")
                Thread(target=self.connect).start()

    def send(self, message:MicrocomMsg, wait_for_ack:bool=True, wait_for_reply:bool=False, timeout:None|int=None, retry:None|int=None):
        ''' Send a message. Blocking if wait_for_ack or wait_for_reply '''
        timeout = timeout if timeout is not None else self.timeout
        retry = retry if retry is not None else self.retry
        message.ip = self.ip if message.ip is None else message.ip
        message.port = self.port if message.port is None else message.port

        # if the message is to enable streaming stats, add the local IP/port
        if message.msg_type == MSG_TYPE_STATS_ENABLE and isinstance(self._client, socket.socket):
            client_data = {'ip': self._client.getsockname()[0], 'port': self._client.getsockname()[1]}
            if isinstance(message.data, dict):
                client_data.update(**message.data)
                message.data = client_data
            else:
                message.data = client_data

        if message.data_length > DATA_MAX_BUFFER_SIZE:
            # if we need to fragment, break up the packet and send
            frag_count = 1
            fragment = None
            # set the last saved message to the original
            self._last_sent_message = message
            message.send_time = time()
            for fragment in message.frag(DATA_MAX_BUFFER_SIZE):
                self._logger.debug(f"SEND {frag_count} fragments for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) size: {fragment.data_length}")
                # save the fragment we are sending for ACK purposes
                self._last_sent_frag = fragment
                self.send(fragment, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=False)
                if not fragment.ack_received():
                    self._logger.error(f"SEND {frag_count} fragments for pkt_id: {fragment.pkt_id} to ({fragment.ip}:{fragment.port}) failed.")
                    return
                frag_count += 1
                message.retries += fragment.retries # capture any retries that occured for a fragment in the original packet

            # check and see if the message was received
            while not self._last_sent_message.reply_received() and (time() < (self._last_sent_message.send_time + timeout) or time() < (self._last_sent_message.frag_time + timeout)): # type: ignore
                sleep(.1)

            if self._last_sent_message.reply_received():
                self._logger.debug(f"REPLY received for {self._last_sent_message.pkt_id}")
            else:
                self._logger.error(f"REPLY was NOT received for {self._last_sent_message.pkt_id}")
            return

        # lock the writer to block any other send function
        with self.__udp_send_lock:
            if (wait_for_ack or wait_for_reply) and message.frag_number == 0:
                # don't replace the last sent message for fragments, the root message will be saved here
                self._last_sent_message = message
            message.send_time = time()
            header, data, footer = message.serialize()

            # lock the write to serial, separate from send so ACK messages can be sent without waiting in the queue
            with self.__udp_write_lock:
                self._logger.debug(f"Sending data: {header} {data[0:100]}{'...' if len(data)> 100 else ''}")
                if isinstance(self._client, socket.socket):
                    self._client.send(header + data + footer)
                else:
                    self._logger.error(f"SEND_ACK client not connected. Discarding message id: {message.pkt_id}")
                    Thread(target=self.connect).start()
            
            # if we aren't waiting for an ACK, then we don't need to retrans
            if not wait_for_ack and not wait_for_reply:
                self._logger.debug("Returning from send, not waiting for ACK or REPLY")
                return

            while not message.ack_received() and time() < (message.send_time + timeout): # type: ignore
                sleep(.1)
            if message.ack_received(): # type: ignore
                self._logger.debug(f"ACK Received for {message.pkt_id}")
                if not wait_for_reply:
                    return
                # if we are waiting for a reply
                while not message.reply_received() and (time() < (message.send_time + timeout) or time() < (message.frag_time + timeout)): # type: ignore
                    sleep(.1)
                self._logger.debug(f"REPLY {'received' if message.reply_received() else 'NOT received'} for {message.pkt_id}")
                return # return regardless of if a reply was received

        # if we didn't get an ACK
        self._logger.warning(f"ID: {message.pkt_id}, Type: {message.msg_type}, Send timeout after {timeout} seconds waiting for ACK. Retry {message.retries} / {retry}") # type: ignore
        self._logger.debug(f"ID: {message.pkt_id}, Type: {message.msg_type}, Send timeout packet details: {str(message)}") # type: ignore
        if message.retries < retry: # type: ignore
            # increment the retry counter and call the send function again
            message.retries += 1 # type: ignore
            return self.send(message=message, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply)
        else:
            self._logger.error(f"ID: {message.pkt_id}, Type: {message.msg_type}, Retry exceeded with no ACK. Message discarded.") # type: ignore

    def connect(self):
        ''' Make the initial connection '''
        self._logger.info(f"Connecting to ({self.ip}:{self.port}) on UDP...")
        with self.__udp_write_lock:
            self._client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._client.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._client.setblocking(True)
            self._client.connect((self.ip, self.port))

    def _receive_thread(self):
        ''' Wait for incoming message, deserialize and send for processing '''
        self._logger.info("Starting receive thread...")
        if self._client is None:
            self.connect()

        while not self.receive_stop:
            try:
                if not isinstance(self._client, socket.socket):
                    self.connect()
                else:
                    # read new data from the buffer (blocking)
                    data, source = self._client.recvfrom(1500)
                    self._logger.debug(f"RECEIVE_THREAD received {len(data)} bytes from {source}, bytes: {data}")

                    if SER_START_HEADER in data and SER_END in data:
                        # if we have the start and end, create a message to process
                        received_msg = MicrocomMsg.from_bytes(data=data, source=source)
                        self._logger.debug(f"RECEIVE_THREAD message details: {received_msg}")
                        Thread(target=self._receive_message, args=[received_msg]).start()

            except Exception as e:
                self._logger.error(f"RECEIVE_THREAD Error: {e.__class__.__name__}: {e}")
        self._logger.info("Receive Thread Stopped.")
