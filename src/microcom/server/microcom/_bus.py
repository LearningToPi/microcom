from microcom.log_manager import logging_handler
from microcom.exceptions import MicrocomBusInvaldParam


class MicrocomBus:
    BUS_TYPE = 'n/a'        # Bus type name, i.e. 'i2c' or 'spi'
    BUS_INIT_FIELDS = ()    # override per class (tuple of init parameters)
    BUS_CMD_FIELDS = {}     # override per class (dict if commands with a tuple of parameters)
    _bus = None             # object to hold the initialized bus

    ''' Class to perform generic functions on a bus.  I.e. SPI / I2C '''
    def __init__(self, config:dict, _logger:logging_handler):
        self._config, self._logger = config, _logger
        for req_field in self.BUS_INIT_FIELDS:
            if req_field not in config:
                raise MicrocomBusInvaldParam(f"BUS_INIT: Bus type {self.BUS_TYPE} requires field: {req_field}, got {config}")

        # create functions

    def exec(self, bus_cmd:str, **kwargs):
        ''' Execute a command on the bus '''
        bus_id = str(self._config['bus_id'])

        # check that we have valid command and parameters for supported commands
        if bus_cmd not in self.BUS_CMD_FIELDS:
            self._logger.error(f"{self.BUS_TYPE}_EXEC_CMD: {self.BUS_TYPE}{bus_id} command '{bus_cmd}' not supported. Supported list: {self.BUS_CMD_FIELDS.keys()}")
        for field in self.BUS_CMD_FIELDS[bus_cmd]:
            if field not in kwargs:
                self._logger.error(f"{self.BUS_TYPE}_EXEC_CMD: message missing field {field}, got {kwargs}")
                raise MicrocomBusInvaldParam(f"{self.BUS_TYPE}_EXEC_CMD: message missing field {field}, got {kwargs}")

        # check that the command exists in the selected object
        if bus_cmd not in dir(self._bus): # type: ignore
            self._logger.error(f"{self.BUS_TYPE}_EXEC_CMD: {self.BUS_TYPE}{bus_id} does not support {bus_cmd}")
            raise NotImplementedError(f"{self.BUS_TYPE}_EXEC_CMD: {self.BUS_TYPE}{bus_id} does not support {bus_cmd}")

        # execute the command
        self._logger.debug(f"{self.BUS_TYPE}_EXEC_CMD: Executing {self.BUS_TYPE}{bus_id} {bus_cmd}: {kwargs}")
        cmd_func = getattr(self._bus, bus_cmd)
        return cmd_func(*[kwargs[x] for x in self.BUS_CMD_FIELDS[bus_cmd]])
