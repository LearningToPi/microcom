
from logging_handler import create_logger, INFO
from typing import Callable

class MicrocomBus:
    ''' Generic class to act as the underlay for other bus protocols (I2C, SPI, UART, etc) 
        Class is generated from the MicrocomClient class. '''
    REQ_FIELDS = ('type', 'bus_id', 'pins', 'settings')

    def __init__(self, readmem:Callable, writemem:Callable, log_level=INFO, name=''):
        self._microcom_readmem, self._microcom_writemem = readmem, writemem
        self._logger = create_logger(log_level, name=f"{self.__class__.__name__}{(':' + name) if name != '' else ''}")

    def readmem(self):
        ''' Read from a device '''
        raise NotImplementedError("READ function must be overriden by inheritting class")

    def writemem(self):
        ''' Write to a devices '''
        raise NotImplementedError("READ function must be overriden by inheritting class")
