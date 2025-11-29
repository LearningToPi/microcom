from machine import I2C, SoftI2C # type: ignore # pylint: disable=E0401
from microcom.log_manager import logging_handler
from microcom._bus import MicrocomBus


class MicrocomI2CBus(MicrocomBus):
    BUS_INIT_FIELDS = ('bus_id', 'sda_pin', 'scl_pin', 'freq', 'hardware')
    BUS_CMD_FIELDS = {
        'scan': (),
        'readfrom': ('addr', 'nbytes'),
        'readfrom_mem': ('addr', 'memaddr', 'nbytes'),
        'writeto_mem': ('addr', 'memaddr', 'buf'),
        'writeto': ('addr', 'buf', 'stop')
    }

    def __init__(self, config: dict, _logger: logging_handler):
        super().__init__(config, _logger)
        if self._config.get('hardware', True):
            self._bus = I2C(self._config['bus_id'],
                            scl=self._config['scl_pin'],
                            sda=self._config['sda_pin'],
                            freq=self._config['freq'])
        else:
            self._bus = SoftI2C(self._config['bus_id'],
                            scl=self._config['scl_pin'],
                            sda=self._config['sda_pin'],
                            freq=self._config['freq'])
