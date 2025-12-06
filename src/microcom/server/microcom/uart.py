from machine import UART # type: ignore # pylint: disable=E0401
import asyncio
import gc
import sys
from microcom.log_manager import logging_handler
from microcom._bus import MicrocomBus
from microcom.msg._base import MSG_TYPE_BUS_CMD
from microcom.exceptions import MicrocomBusInitializationError


class MicrocomUARTBus(MicrocomBus):
    BUS_TYPE = 'UART'
    BUS_INIT_FIELDS = ('bus_id', 'tx', 'rx', 'baudrate', 'flow_control')
    BUS_CMD_FIELDS = {
        'write': ('data', ),
        'txdone': (),
        'flush': ()
    }

    def __init__(self, config:dict, _logger:logging_handler):
        super().__init__(config, _logger)
        self.read_thread = None
        initial_config = {'tx': self._config['tx'], 'rx': self._config['rx'], 'baudrate': self._config['baudrate']}
        if self._config.get('flow_control', False) in [True, 'True', 'TRUE', 'true', 1]:
            if 'cts' not in self._config or 'rts' not in self._config:
                raise MicrocomBusInitializationError(f"UART{self._config['bus_id']} flow control enabled, but 'cts' and 'rts' pin required.")
            initial_config['cts'] = self._config['cts']
            initial_config['rts'] = self._config['rts']
            initial_config['flow'] = UART.RTS | UART.CTS
        self._logger.info(f"UART{self._config['bus_id']}: Initializing with config: {initial_config}")
        try:
            self._bus = UART(self._config['bus_id'], **initial_config)
        except Exception as e:
            error_msg = f"UART{self._config['bus_id']} error initializing: {e.__class__.__name__}: {e}"
            self._logger.error(error_msg)
            if self._logger.console_level == 7:
                sys.print_exception(e) # pyright: ignore[reportAttributeAccessIssue] # pylint: disable=E1101
            raise MicrocomBusInitializationError(error_msg) from e
    

    async def async_read(self, callback, msg_class):
        ''' Run an async read thread to read data from the remote device '''
        # Exception loop
        while True:
            self._logger.info(f"UART{self._config['bus_id']}: Starting read thread...")
            stream_reader = asyncio.StreamReader(self._bus) # pylint: disable=e1101
            data = b''
            try:
                # read loop
                while True:
                    self._logger.debug(f"UART{self._config['bus_id']}: Waiting for input...")
                    gc.collect()
                    data = await stream_reader.readline()
                    self._logger.debug(f"UART{self._config['bus_id']}: Received data: {data[0:100]}{'...' if len(data) > 100 else ''}")

                    # send the received data using the callback function and MicrocomMsg
                    asyncio.create_task(callback(msg_class(msg_type=MSG_TYPE_BUS_CMD, data=data)))

            except Exception as e:
                self._logger.error(f"UART{self._config['bus_id']}: Error in read thread: {e.__class__.__name__}: {e}")
                if self._logger.console_level == 7:
                    sys.print_exception(e) # pyright: ignore[reportAttributeAccessIssue] # pylint: disable=E1101
