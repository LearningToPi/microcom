''' Serial based Microcom Client (TTL or RS232) '''
from typing import Callable
from logging_handler import INFO
from _base import MicrocomClient
from serial import Serial
from microcom.client._base import RECEIVE_QUEUE_MAX, RETRY, SEND_QUEUE_MAX, SEND_TIMEOUT, SERVER_NAME, STATS_HISTORY, STATS_INTERVAL, STATS_STREAMING, LOG_LEVEL

BAUD_DEFAULT = 9600
BAUD_AUTO = False
BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
HW_FLOW_CONTROL = False

class MicrocomClientSerial(MicrocomClient):
    def __init__(self, port:str, baud=BAUD_DEFAULT, baud_auto=BAUD_AUTO, hw_flow_control:bool=HW_FLOW_CONTROL,
                 receive_queue_max:int=RECEIVE_QUEUE_MAX, send_queue_max:int=SEND_QUEUE_MAX, log_level:str=LOG_LEVEL, name:str=SERVER_NAME,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_streaming:bool=STATS_STREAMING, stats_interval:int=STATS_INTERVAL,
                 stats_history:int=STATS_HISTORY, receive_callback:None|Callable=None):
        super().__init__(receive_queue_max, send_queue_max, log_level, name, send_timeout, retry, stats_streaming, stats_interval, stats_history, receive_callback)
        if not baud_auto:
            self.__server = Serial(port=port, baudrate=baud, timeout=SEND_TIMEOUT, rtscts=hw_flow_control)

    def _baud_autosense(self):
        ''' If not connected, start scanning each baud rate to find the server, if connected let the microcom server know to start at the default 
            For each speed, connect then send a ping with a long data string to validate '''
