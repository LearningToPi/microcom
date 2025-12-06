from logging_handler import create_logger, INFO
from typing import Callable
from microcom.msg._base import MicrocomMsg, MSG_TYPE_BUS_CMD, MSG_TYPE_BUS_INIT
from microcom.exceptions import MicrocomBusWriteFail, MicrocomBusInitializationError, MicrocomBusInvaldParam



class MicrocomUART:
    ''' Class to represent a UART port on the microcom server '''
    def __init__(self, bus_id:int, send:Callable, tx:int, rx:int, baudrate:int, message_class:MicrocomMsg, log_level:str=INFO, flow_control:bool=False, cts:int|None=None, rts:int|None=None):
        self._bus_id, self._send, self._message_class, self._tx, self._rx, self._baudrate, self._flow_control, self._cts, self._rts = bus_id, send, message_class, tx, rx, baudrate, flow_control, cts, rts
        self._logger = create_logger(log_level, name=f"UART{self._bus_id}")
        message = self._message_class(msg_type=MSG_TYPE_BUS_INIT, data={'bus_type': 'uart', 'bus_id': bus_id, 'tx': tx, 'rx': rx, 'baudrate': baudrate, 'flow_control': flow_control, 'cts': cts, 'rts': rts}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0):
            raise MicrocomBusInitializationError(f"Error initializing UART{self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")

    def write(self, data:bytes) -> int:
        ''' Write data to the UART port '''
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'UART', 'bus_id': self._bus_id, 'bus_cmd': 'write', 'data': data}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0) or not isinstance(message.reply_msg.data, list):
            raise MicrocomBusWriteFail(f"Error writing to UART{self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data[0]

    def txdone(self) -> bool:
        ''' Returns True if the last transmit was completed (requires flow control, otherwise always True) '''
        message = self._message_class(msg_type=MSG_TYPE_BUS_CMD, data={'bus_type': 'UART', 'bus_id': self._bus_id, 'bus_cmd': 'txdone'}) # type: ignore
        self._send(message, wait_for_reply=True)
        if not message.reply_received() or message.reply_msg is None or (isinstance(message.reply_msg, MicrocomMsg) and message.reply_msg.return_code != 0) or not isinstance(message.reply_msg.data, list) or not isinstance(message.reply_msg.data[0], bool):
            raise MicrocomBusInvaldParam(f"Error getting txdone from UART{self._bus_id}. Code: {message.reply_msg.return_code if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}, error: {message.reply_msg.data if isinstance(message.reply_msg, MicrocomMsg) else 'n/a'}")
        return message.reply_msg.data[0]
