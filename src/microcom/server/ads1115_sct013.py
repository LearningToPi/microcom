from machine import I2C
from gc import collect
from microcom.log_manager import MicrocomLogManager
from time import sleep


class Ads1115_Config:
    ''' Class to represent the config for a reading '''
    def __init__(self, addr:int, conf_reg:bytes, readings:int, conf_addr=0x01, read_addr=0x00, calc_abs:bool=False, calc_avg:bool=False):
        self.addr, self.conf_reg, self.conf_addr, self.calc_abs, self.calc_avg, self.readings, self.read_addr = addr, conf_reg, conf_addr, calc_abs, calc_avg, readings, read_addr

    def __str__(self):
        return f"I2C addr: {self.addr}, conf_reg: {self.conf_reg}, read_addr: {self.read_addr}, abs: {self.calc_abs}, avg: {self.calc_avg}"

class Ads1115_Sct013:
    ''' Class to read differential readings from AC SCT-013 meters '''
    def __init__(self, _vars:dict, _byte_arr:dict, i2c_bus:int, logger:MicrocomLogManager, config:list):
        self.configs = [Ads1115_Config(**x) for x in config]
        self._logger = logger
        self.i2c = _vars['buses']['i2c'][i2c_bus]

    def run(self) -> list:
        ''' Run the data collection '''
        return_data = []
        for x in self.configs:
            self._logger.info(f"Ads1115_Sct013 Start: {x}")
            # write the config reg
            self.i2c.writeto_mem(x.addr, x.conf_addr, bytearray(x.conf_reg))
            # initialize the data buffer
            collect()
            data_buffer = bytearray(x.readings * 2)
            temp_buffer = bytearray(2)
            for y in range(x.readings):
                temp_buffer = self.i2c.readfrom_mem(x.addr, x.read_addr, 2)
                if x.calc_abs and bytes(int.from_bytes(temp_buffer, 'big') >> 15):
                    # check 1st bit to see if negative
                    data_buffer[y*2:y*2+2] = (~int.from_bytes(bytes(temp_buffer), 'big') & 0xFFFF).to_bytes(2, 'big')
                else:
                    data_buffer[y*2:y*2+2] = temp_buffer
                sleep(.001)

            self._logger.debug(f"Ads1115_Sct013 data buffer: {data_buffer}")
            if x.calc_avg:
                return_data.append(sum([int.from_bytes(data_buffer[y*2:y*2+1], 'big') for y in range(x.readings)]) / x.readings)
            else:
                return_data.append(data_buffer)
        return return_data
