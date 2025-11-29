from logging_handler import create_logger, INFO
from time import time, sleep
from typing import Callable
from microcom.exceptions import *


# Address pointer register
REG_PTR_LEN             = 1
REG_PTR_CONV            = 0x00
REG_PTR_CONF            = 0x01
REG_PTR_LOW_THRESH      = 0x02
REG_PTR_HIGH_THRESH     = 0x03

REG_PTR_RESET           = 0x06

# Conversion Register - two's complement
REG_CONV_LEN            = 2
REG_CONV_MAX_VAL        = 32767 # 0x8000
REG_CONV_MIN_VAL        = -32768 # 0x7FFF

# Config register masks
REG_CONF_LEN            = 2
REG_CONF_STATUS         = 0b10000000_00000000   # read: 0=conversion in process, 1=idle  write: 0=no change, 1=start single converstion (when in idle state)
REG_CONF_MUX            = 0b01110000_00000000   # ADS1115 only, input multiplexer
                                                #   000 : AINP = AIN0 and AINN = AIN1 (default)
                                                #   001 : AINP = AIN0 and AINN = AIN3
                                                #   010 : AINP = AIN1 and AINN = AIN3
                                                #   011 : AINP = AIN2 and AINN = AIN3
                                                #   100 : AINP = AIN0 and AINN = GND
                                                #   101 : AINP = AIN1 and AINN = GND
                                                #   110 : AINP = AIN2 and AINN = GND
                                                #   111 : AINP = AIN3 and AINN = GND
REG_CONF_GAIN           = 0b00001110_00000000   # Programmable gain amplifier (no effect on ADS1113)
                                                #   000 : FSR = ±6.144 V (1)
                                                #   001 : FSR = ±4.096 V (1)
                                                #   010 : FSR = ±2.048 V (default)
                                                #   011 : FSR = ±1.024 V
                                                #   100 : FSR = ±0.512 V
                                                #   101 : FSR = ±0.256 V
                                                #   110 : FSR = ±0.256 V
                                                #   111 : FSR = ±0.256 V
REG_CONF_MODE           = 0b00000001_00000000   # Operating mode, 0=continuous conversion, 1=single-shot or power-down (idle) (default)
REG_CONF_SPS            = 0b00000000_11100000   # Data rate (samples per second)
                                                #   000 : 8 SPS
                                                #   001 : 16 SPS
                                                #   010 : 32 SPS
                                                #   011 : 64 SPS
                                                #   100 : 128 SPS (default)
                                                #   101 : 250 SPS
                                                #   110 : 475 SPS
                                                #   111 : 860 SPS
        # Comparator settings for ADS1114 and ADS1115 only (no effect on ADS1113)
REG_CONF_COMP_MODE      = 0b00000000_00010000   # Comparator mode 0=traditional (default), 1=window comparator
                                                #   traditional - ALERT triggered when conversion exceeds high threshold
                                                #   window - ALERT triggered when exceeding high or dropping below low threshold
REG_CONF_COMP_POL       = 0b00000000_00001000   # Comparator polarity 0=active low (default), 1=active high
REG_CONF_COMP_LATCH     = 0b00000000_00000100   # Latching comparator 0=non-latching (default, no ALERT/READY), 1=latching (ALERT/READY enabled)
REG_CONF_COMP_QUEUE     = 0b00000000_00000011   # Comparator queue and disable
                                                #   00 : Assert after one conversion
                                                #   01 : Assert after two conversions
                                                #   10 : Assert after four conversions
                                                #   11 : Disable comparator and set ALERT/RDY pin to high-impedance (default)

ADS_BUSY                = 'busy'
ADS_IDLE                = 'idle'
ADS_CONTINUOUS          = 0
ADS_SINGLE              = 1
ADS_MUX                 = [{'pos': 0, 'neg': 1},
                           {'pos': 0, 'neg': 3},
                           {'pos': 1, 'neg': 3},
                           {'pos': 2, 'neg': 3},
                           {'pos': 0, 'neg': None},
                           {'pos': 1, 'neg': None},
                           {'pos': 2, 'neg': None},
                           {'pos': 3, 'neg': None}]
ADS_GAIN_VOLT_RANGE     = [(-6.144, 6.144),
                           (-4.906, 4.906),
                           (-2.048, 2.048),
                           (-1.024, 1.024),
                           (-0.512, 0.512),
                           (-0.256, 0.256)]
ADS_SPS                 = [8, 16, 32, 64, 128, 250, 475, 860]
ADS_COMP_TRADITIONAL    = 0
ADS_COMP_WINDOW         = 1
ADS_COMP_POL_LOW        = 0
ADS_COMP_POL_HIGH       = 1
ADS_COMP_QUEUE          = (1, 2, 4, 0)

# Low threshold register - two's complement
REG_LOW_THRESH_LEN      = 2
REG_LOW_THRESH_MAX_VAL  = 32768 # 0x8000
REG_LOW_THRESH_MIN_VAL  = -32767 # 0x7FFF

# High threshold reguster - two's complement
REG_HIGH_THRESH_LEN     = 2
REG_HIGH_THRESH_MAX_VAL = 32768 # 0x8000
REG_HIGH_THRESH_MIN_VAL = -32767 # 0x7FFF


# Other constants
READ_TIMEOUT = 2

class Ads111xCompConfig:
    def __init__(self, conf_reg):
        self._conf_reg = conf_reg

    @property
    def mode(self):
        ''' Return the current mode of the ADS111x comparator '''
        return ADS_COMP_WINDOW if self._conf_reg[0] & REG_CONF_COMP_MODE else ADS_COMP_TRADITIONAL

    @mode.setter
    def mode(self, value:int):
        ''' Set the comparator mode, 0=traditional, 1=window
            traditional - ALERT triggered when conversion exceeds high threshold
            window - ALERT triggered when exceeding high or dropping below low threshold
           '''
        if value in [0, 1]:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_COMP_MODE
            self._conf_reg[0] |= value << 4
        else:
            raise ValueError(f"Value {value} not supported for comparator mode. Pass either 0=traditional, 1=window")

    @property
    def polarity(self):
        ''' Return the current polarity of the ADS111x comparator '''
        return ADS_COMP_POL_HIGH if self._conf_reg[0] & REG_CONF_COMP_POL else ADS_COMP_POL_LOW

    @polarity.setter
    def polarity(self, value:int):
        ''' Set the comparator polarity mode, 0=active low, 1=active high '''
        if value in [0, 1]:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_COMP_POL
            self._conf_reg[0] |= value << 3
        else:
            raise ValueError(f"Value {value} not supported for comparator polarity. Pass either 0=active low, 1=active high")

    @property
    def queue(self):
        ''' Return the queue for asserting ALERT for ADS111x (disabled moved to 'disabled' property) '''
        return ADS_COMP_QUEUE[self._conf_reg[0] & REG_CONF_COMP_QUEUE]

    @queue.setter
    def queue(self, value:int):
        ''' Set the comparator polarity mode, 0=active low, 1=active high '''
        if value in ADS_COMP_QUEUE:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_COMP_QUEUE
            self._conf_reg[0] |= value
        else:
            raise ValueError(f"Value {value} not supported for comparator queue. Pass {ADS_COMP_QUEUE} (0 means turn off)")

    @property
    def disabled(self):
        ''' Return TRUE if the comparator is disabled for the ADS111x '''
        return (self._conf_reg[0] & REG_CONF_COMP_QUEUE) == 3

    @disabled.setter
    def disabled(self, disable:bool):
        ''' Set the comparator to disabled or enabled. If setting enabled and currently disabled, sets queue to 1 '''
        if self.disabled and not disable:
            self._conf_reg[1] = True
            self.queue = 1
        elif not self.disabled and disable:
            self._conf_reg[1] = True
            self.queue = 0
        elif self.disabled != disable:
            raise ValueError(f"Value must be a boolean, got {disable}")

    def __repr__(self):
        return f"{self.__class__.__name__}({', '.join([x + '=' + str(getattr(self,x)) for x in dir(self) if not x.startswith('_') and not isinstance(getattr(self, x), Callable)])})"


class Ads111xI2C:
    ''' Class to represent and ADS111x I2C based sensor (i.e. ADS1115) '''
    def __init__(self, address:int, readmem_func:Callable, writemem_func:Callable, write_func:Callable, log_level=INFO):
        self._logger = create_logger(log_level, name=f"ADS111x({address})")
        self._address, self.readmem, self.writemem, self.write = address, readmem_func, writemem_func, write_func
        self._read_config()
        self._comparator = Ads111xCompConfig(self._conf_reg)

    def _read_config(self):
        self._conf_reg = [int.from_bytes(self.readmem(self._address, REG_PTR_CONF, REG_CONF_LEN), 'big'), False]

    def reset(self):
        ''' Send a reset to the global address of 0. NOTE: resets all registers, and may affect other devices '''
        # send the reset command
        self.write(0, REG_PTR_RESET.to_bytes(1, 'big'))
        # update the config
        self._read_config()
        # check to me sure we are back to default
        if not self.idle or self.mux != ADS_MUX[0] or self.range != ADS_GAIN_VOLT_RANGE[2] or self.rate != 128:
            raise MicrocomBusWriteFail("Reset sent but device is not at default state.")

    def push_config(self, conf_reg:bytes|None=None):
        ''' Push the configuration to the device (verify is done by I2C object) '''
        if not self.writemem(self._address, REG_PTR_CONF, self._conf_reg[0].to_bytes(REG_CONF_LEN, 'big') if conf_reg is None else conf_reg, verify=True):
            raise MicrocomBusWriteFail("I2C write to device failed")
        # reload the config
        self._read_config()

    def single_read(self):
        ''' Perform a single read '''
        if not self.idle:
            raise MicrocomBusDeviceNotReady("Device is not ready")
        start_time = time()
        self.writemem(self._address, REG_PTR_CONF, self._conf_reg[0] & REG_CONF_STATUS, verify=False)
        # wait until the device is back to idle to read the result
        self._read_config()
        while not self.idle and start_time + READ_TIMEOUT > time():
            sleep(.5)
            self._read_config()
        if not self.idle:
            raise MicrocomBusDeviceNotReady("Read request sent, but device still busy after timeout.")
        return self.readmem(self._address, REG_PTR_CONV, REG_CONV_LEN)

    @property
    def idle(self):
        ''' Return the status of the ADS111x '''
        return True if self._conf_reg[0] & REG_CONF_STATUS else False

    @property
    def busy(self):
        ''' Return the status of the ADS111x '''
        return False if self._conf_reg[0] & REG_CONF_STATUS else True

    @property
    def mux(self):
        ''' Return the current MUX setting for the ADS111x '''
        return ADS_MUX[(self._conf_reg[0] & REG_CONF_MUX) >> 12]

    @mux.setter
    def mux(self, value:int|dict):
        ''' Set the MUX config, pass either an int matching the index of ADS_MUX, or the ADS_MUX dict '''
        if value in ADS_MUX:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_MUX
            self._conf_reg[0] |= (ADS_MUX.index(value) << 12)
        elif isinstance(value, int) and value >= 0 and value < len(ADS_MUX):
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_MUX
            self._conf_reg[0] |= (value << 12)
        else:
            raise ValueError(f"Value {value} not supported for MUX. Pass either the value or index from: {ADS_MUX}")

    @property
    def range(self):
        ''' Return the voltage range (based on the gain) for the ADS111x '''
        return ADS_GAIN_VOLT_RANGE[(self._conf_reg[0] & REG_CONF_GAIN) >> 9]

    @range.setter
    def range(self, value:int|tuple):
        ''' Set the voltage range/gain, pass either an int matching the index of ADS_GAIN_VOLT_RANGE or the ADS_GAIN_VOLT_RANGE tuple (min,max) '''
        if value in ADS_GAIN_VOLT_RANGE:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_GAIN
            self._conf_reg[0] |= (ADS_GAIN_VOLT_RANGE.index(value) << 9)
        elif isinstance(value, int) and value >= 0 and value < len(ADS_GAIN_VOLT_RANGE):
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_GAIN
            self._conf_reg[0] |= (value << 9)
        else:
            raise ValueError(f"Value {value} not supported for range. Pass either the value or index from: {ADS_GAIN_VOLT_RANGE}")

    @property
    def mode(self):
        ''' Return the current mode of the ADS111x '''
        return ADS_SINGLE if self._conf_reg[0] & REG_CONF_MODE else ADS_CONTINUOUS

    @mode.setter
    def mode(self, value:int):
        ''' Set the ADS111x mode (0=continuous conversion, 1=single-shot) '''
        if value in [0, 1]:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_MODE
            self._conf_reg[0] |= value << 8
        else:
            raise ValueError(f"Value {value} not supported for mode. 0=continuous conversion, 1=single-shot")

    @property
    def rate(self):
        ''' Return the current rate (SPS) for the ADS111x in continuous mode '''
        return ADS_SPS[(self._conf_reg[0] & REG_CONF_SPS) >> 5]

    @rate.setter
    def rate(self, value:int):
        ''' Set the ADS111x sample rate, pass the value in samples per second '''
        if value in ADS_SPS:
            self._conf_reg[1] = True
            # reset bits before setting
            self._conf_reg[0] &= ~REG_CONF_SPS
            self._conf_reg[0] |= ADS_SPS.index(value) << 5
        else:
            raise ValueError(f"Value {value} not supported rate. supported values: {ADS_SPS}")

    @property
    def comparator(self):
        ''' Return the ADS111x comparator config object '''
        return Ads111xCompConfig(self._conf_reg)

    @property
    def up_to_date(self):
        ''' Return TRUE if the config has not been modified, FALSE if the config has been modified '''
        return not self._conf_reg[1]

    def __repr__(self):
        return f"{self.__class__.__name__}({', '.join([x + '=' + str(getattr(self,x)) for x in dir(self) if not x.startswith('_') and not isinstance(getattr(self, x), Callable)])})"

    def get_readings(self):
        ''' get the readings from the device '''
        #self.