''' Serial based Microcom Client (TTL or RS232) '''
from typing import Callable
from logging_handler import INFO
from serial import Serial
from time import time, sleep
from src.microcom.client._base import MicrocomClient, RECEIVE_QUEUE_MAX, RETRY, SEND_QUEUE_MAX, SEND_TIMEOUT, SERVER_NAME, STATS_HISTORY, STATS_INTERVAL, STATS_STREAMING, LOG_LEVEL
from src.microcom.msg._base import *
from src.microcom.exceptions import *
import re
from threading import Thread, Lock

from microcom.msg._base import MicrocomMsg

BAUD_DEFAULT = 9600
BAUD_AUTO = False
BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
HW_FLOW_CONTROL = False
WRITE_DELAY = .2

class MicrocomClientSerial(MicrocomClient):
    def __init__(self, port:str, baudrate=BAUD_DEFAULT, baud_auto=BAUD_AUTO, hw_flow_control:bool=HW_FLOW_CONTROL,
                 receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        self.__serial_write_lock = Lock()
        self.__serial_send_lock = Lock()
        super().__init__(receive_queue_max, send_queue_max, log_level, name, send_timeout, retry, stats_interval, stats_history, receive_callback)
        if not baud_auto:
            self.__server = Serial(port=port, baudrate=baudrate, timeout=SEND_TIMEOUT, rtscts=hw_flow_control)
            self.__server.reset_input_buffer()
        self.receive_stop = False
        self._receive_background_thread = Thread(target=self._receive_thread)
        self._receive_background_thread.start()

    def _baud_autosense(self):
        ''' If not connected, start scanning each baud rate to find the server, if connected let the microcom server know to start at the default 
            For each speed, connect then send a ping with a long data string to validate '''

    def send_ack(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None):
        ''' Send ACK messages as soon as the serial port is writable '''
        with self.__serial_write_lock:
            header, data, footer = MicrocomMsg.ack(message=message).serialize()
            self._logger.debug(f"Sending ACK: {header}")
            for x in (header, data, footer):
                self.__server.write(x)
            self.__server.flush()
            sleep(WRITE_DELAY)

    def send(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None, wait_for_ack=True, wait_for_reply=False):
        ''' Send a message. Blocking if wait_for_ack or wait_for_reply '''
        timeout = timeout if timeout is not None else self.timeout
        retry = retry if retry is not None else self.retry

        # lock the writer to block any other send function
        with self.__serial_send_lock:
            if wait_for_ack or wait_for_reply:
                self._last_sent_message = message
            message.send_time = time()
            header, data, footer = message.serialize()

            # lock the write to serial, separate from send so ACK messages can be sent without waiting in the queue
            with self.__serial_write_lock:
                self._logger.debug(f"Sending data: {header} {data[0:100]}{'...' if len(data)> 100 else ''}")
                for x in (header, data, footer):
                    self.__server.write(x)
                self.__server.flush()
                sleep(WRITE_DELAY)
            
            # if we aren't waiting for an ACK, then we don't need to retrans
            if not wait_for_ack and not wait_for_reply:
                self._logger.debug("Returning from send, not waiting for ACK or REPLY")
                return

            while not message.ack_received() and time() < (message.send_time + timeout): # type: ignore
                sleep(.1)
            if message.ack_received(): # type: ignore
                self._logger.debug(f"ACK Received for {message.pkt_id}")
                if wait_for_ack and not wait_for_reply:
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

    def _receive_thread(self):
        ''' Wait for incoming message, deserialize and send for processing '''
        self._logger.info("Starting receive thread...")
        while not self.receive_stop:
            try:
                # read new data from the buffer (blocking)
                data = self.__server.readline()
                #self._logger.debug(f"Received from serial: {data}")

                if SER_START_HEADER in data and SER_END in data:
                    try:
                        received_msg = MicrocomMsg.from_bytes(data)
                        self._logger.debug(f"Message Details: {str(received_msg)}")

                        # create a background task to run async to process the message
                        Thread(target=self._receive_message, args=[received_msg]).start()

                    except MicrocomException as e:
                        self._logger.error(f"Receive Error: {e.__class__.__name__}: {e}")

            except Exception as e:
                self._logger.error(f"Receive Serial Error: {e.__class__.__name__}: {e}")
        self._logger.info("Receive Thread Stopped.")
