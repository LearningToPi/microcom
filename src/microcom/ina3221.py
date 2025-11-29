from logging_handler import create_logger, INFO
from time import time, sleep
from typing import Callable
from collections import namedtuple
from microcom.exceptions import *


ADDRESS_LIST = (64, 65, 66, 67)

# Address pointer register
REG_PTR_LEN             = 1
REG_PTR_CONF            = 0x00 # R/W
REG_PTR_1_SHUNT         = 0x01 # R
REG_PTR_1_BUS           = 0x02 # R
REG_PTR_2_SHUNT         = 0x03 # R
REG_PTR_2_BUS           = 0x04 # R
REG_PTR_3_SHUNT         = 0x05 # R
REG_PTR_3_BUS           = 0x06 # R
REG_PTR_1_ALERT_CRIT    = 0x07 # R/W
REG_PTR_1_ALERT_WARN    = 0x08 # R/W
REG_PTR_2_ALERT_CRIT    = 0x09 # R/W
REG_PTR_2_ALERT_WARN    = 0x0A # R/W
REG_PTR_3_ALERT_CRIT    = 0x0B # R/W
REG_PTR_3_ALERT_WARN    = 0x0C # R/W
REG_PTR_SHUNT_SUM       = 0x0D # R
REG_PTR_SHUNT_SUM_LIMIT = 0x0E # R/W
REG_PTR_MASK            = 0x0F # R/W
REG_PTR_POWER_VALID_UPPER = 0x10 # R/W
REG_PTR_POWER_VALID_LOWER = 0x11 # R/W
REG_PTR_MFG_ID          = 0xFE # R
REG_PTR_DIE_ID          = 0xFF # R

REG_MEM_LEN             = 2

REG_PTR_BUSES = (None, REG_PTR_1_BUS, REG_PTR_2_BUS, REG_PTR_3_BUS)
REG_PTR_SHUNTS = (None, REG_PTR_1_SHUNT, REG_PTR_2_SHUNT, REG_PTR_3_SHUNT)
REG_PTR_THRESH = ((None, None), (REG_PTR_1_ALERT_WARN, REG_PTR_1_ALERT_CRIT),
                  (REG_PTR_2_ALERT_WARN, REG_PTR_2_ALERT_WARN), (REG_PTR_3_ALERT_WARN, REG_PTR_3_ALERT_CRIT))

# Conversion Register - two's complement
REG_CONV_LEN            = 2
REG_CONV_MAX_VAL        = 32767 # 0x8000
REG_CONV_MIN_VAL        = -32768 # 0x7FFF

# Config register masks
REG_CONF_RESET          = 0b10000000_00000000   # rw, set to 1 to force a reset
REG_CONF_CH1_ENABLE     = 0b01000000_00000000   # >
REG_CONF_CH2_ENABLE     = 0b00100000_00000000   # >> rw, 0 = channel disabled, 1 = channel enabled (default)
REG_CONF_CH3_ENABLE     = 0b00010000_00000000   # >
REG_CONF_AVG_MODE       = 0b00001110_00000000   # Sets the number of samples to collect and average
                                                #   000 = 1 (default)
                                                #   001 = 4
                                                #   010 = 16
                                                #   011 = 64
                                                #   100 = 128
                                                #   101 = 256
                                                #   110 = 512
                                                #   111 = 1024
REG_CONF_BUS_CONV_TIME  = 0b00000001_11000000   # Sets the conversion time for bus voltage
                                                #   000 = 140 us
                                                #   001 = 204 us
                                                #   010 = 332 us
                                                #   011 = 558 us
                                                #   100 = 1.1 ms (default)
                                                #   101 = 2.116 ms
                                                #   110 = 4.156 ms
                                                #   111 = 8.244 ms
REG_CONF_SHUNT_CONV_TIME = 0b00000000_00111000   # Sets the conversion time for shunt voltage (same as bus voltage above)
REG_CONF_MODE           = 0b00000000_00000111   # Sets operating mode
                                                #   000 = power down
                                                #   001 = Shunt voltage, single-shot (triggered)
                                                #   010 = bus voltage, single-shot (triggered)
                                                #   011 = shunt and bus voltage, single-shot (triggered)
                                                #   100 = power down
                                                #   101 = shunt voltage, continuous
                                                #   110 = bus voltage, continuous
                                                #   111 = shunt and bus voltage, continuous (default)

# Mask register bit masks
REG_MASK_LEN            = 2
REG_MASK_SUM_CHANNELS   = 0b01110000_00000000   # Select the bits corresponding to the channel to include in the register SUM
                                                #   1xx - 1st bit for channel 1, 0 for exclude (default), 1 for include
                                                #   x1x - 2st bit for channel 2, 0 for exclude (default), 1 for include
                                                #   xx1 - 3st bit for channel 3, 0 for exclude (default), 1 for include
REG_MASK_WARN_ENABLE    = 0b00001000_00000000   # Warning alert latch enable
                                                #   0 - Transparent (default)
                                                #   1 - Latch enabled
REG_MASK_CRIT_ENABLE    = 0b00000100_00000000   # Critical alert latch enable
                                                #   0 - Transparent (default)
                                                #   1 - Latch enabled
REG_MASK_CRIT_CHANNELS  = 0b00000011_10000000   # Critical alert flag for each channel. Critical alerts are cleared when read
REG_MASK_SUM_ALERT      = 0b00000000_01000000   # Summation alert triggered when sum of channels exceeds sum register
REG_MASK_WARN_CHANNELS  = 0b00000000_00111000   # Warn alert flag for each channel. Warn alerts are cleared when read
REG_MASK_PV_ALERT       = 0b00000000_00000100   # Power Valid alert, corresponds to PV Pin, does not clear until issue resolved
REG_MASK_TIMING_ALERT   = 0b00000000_00000010   # Timing-control-alert, corresponds to TC Pin, does not clear until a full RESET
REG_MASK_CONV_READY     = 0b00000000_00000001   # Conversion Ready flag, clears when writing config reg or reading mask reg

ALERT_TUPLE = namedtuple('ALERT_TUPLE', ('sum', 'timing', 'ch1warn', 'ch1critical', 'ch2warn', 'ch2critical', 'ch3warn', 'ch3critical'))
CRITICAL_ALERTS = ('timing', 'sum', 'ch1critical', 'ch2critical', 'ch3critical')

AVG_SAMPLES = (0, 4, 16, 64, 128, 512, 1024)
CONVERSION_TIMES = (0.00014, .000204, .000332, .000558, .0011, .002116, .004156, .008244)

MODE_OFF = 0
MODE_SHUNT_SINGLE = 1
MODE_BUS_SINGLE = 2
MODE_BOTH_SINGLE = 3
MODE_SHUNT = 5
MODE_BUS = 6
MODE_BOTH = 7
MODE_LIST = (MODE_OFF, MODE_SHUNT_SINGLE, MODE_BUS_SINGLE, MODE_BOTH_SINGLE, MODE_OFF, MODE_SHUNT, MODE_BUS, MODE_BOTH)

# SHUNT Voltage Register values - two's compliment
VOLTAGE_RESERVED_BITS = 3
VOLTAGE_BITS = 13
SHUNT_VOLTAGE_MAX = 163.8 # Maximum reading in mV - 0x7FF8 (excludes 1st bit for sign and last 3 reserved bits)
SHUNT_VOLTAGE_INCREMET = .04 # mV for each increment - 4095 / SHUNT voltage max 163.8 (204)

# BUS Voltage register values - two's compliment
BUS_VOLTAGE_MAX = 32.76 # Maximum reading in V - 0x7ff8 NOTE: do not exceed 26V!
BUS_VOLTAGE_INCREMENT = 0.008 # V for each increment - 4095 / BUS voltage max 32.76


# Other constants
READ_TIMEOUT = 2

def mask_offset(mask:int) -> int:
    ''' Return the number of bits to offset for the given mask.  Assumes bits are contiguous, only finds trailing 0's '''
    mask_str = str(bin(mask))
    mask_1 = mask_str.rfind('1')
    return len(mask_str) - mask_1 - 1


class ShuntConfig:
    ''' Class to represent a Shunt and hold the values as well as handle calculations '''
    def __init__(self, amps:int, milli_volts:int):
        self.amps, self.milli_volts = amps, milli_volts

    def get_amps(self, milli_volts:float, round_count:int=2) -> float:
        ''' Calculate the amperage across the shunt based on the voltage drop '''
        return round(milli_volts * self.amps / self.milli_volts, round_count)

    def get_register_value(self, amps:float, increment:float) -> int:
        ''' Calculate the register value based on the amps and register increment provided '''
        return int(round(amps * self.milli_volts / self.amps / increment, 0)) << VOLTAGE_BITS

    def __repr__(self):
        return f"{self.__class__.__name__}(amps={self.amps}, volts={self.milli_volts})"


class Ina3221I2C:
    ''' Class to represent and INA3221 I2C based 3 channel high side current and bus voltage monitor '''
    def __init__(self, address:int, readmem_func:Callable, writemem_func:Callable, alert_callback:Callable|None=None, log_level=INFO,
                 shunt1:ShuntConfig|tuple|None=None, shunt2:ShuntConfig|tuple|None=None, shunt3:ShuntConfig|tuple|None=None):
        self._logger = create_logger(log_level, name=f"INA3221({address})")
        self._address, self.readmem, self.writemem, self.alert_callback = address, readmem_func, writemem_func, alert_callback
        self._shunts = [shunt1 if not isinstance(shunt1, tuple) else ShuntConfig(shunt1[0], shunt1[1]),
                        shunt2 if not isinstance(shunt2, tuple) else ShuntConfig(shunt2[0], shunt2[1]),
                        shunt3 if not isinstance(shunt3, tuple) else ShuntConfig(shunt3[0], shunt3[1]),]
        self._conf_reg = 0
        self._mask_reg = 0
        self._config_changed = False
        self._mask_changed = False
        self.refresh()
        self._mfg_id = self._die_id = None

    def _read_config(self) -> int:
        ''' Read the configuration from the sensor '''
        return int.from_bytes(self.readmem(self._address, REG_PTR_CONF, REG_MEM_LEN), 'big')

    def _read_mask(self) -> int:
        ''' Read the mask/enable register which clears any alerts '''
        mask = int.from_bytes(self.readmem(self._address, REG_PTR_MASK, REG_MEM_LEN), 'big')
        alerts = ina3221_alerts(mask)
        if alerts.count(True) > 0:
            if isinstance(self.alert_callback, Callable):
                self.alert_callback(alerts)
            for alert_name, alert_value in alerts._asdict().items():
                if alert_name in CRITICAL_ALERTS and alert_value:
                    self._logger.error(f"Alert {str(alert_name).upper()} triggered")
                elif alert_value:
                    self._logger.warning(f"Warning {str(alert_name).upper()} triggered")
        return mask

    def refresh(self):
        ''' Refresh the config and mask from the INA3221 '''
        self._config_changed = False
        self._mask_changed = False
        self._conf_reg = self._read_config()
        self._mask_reg = self._read_mask()

    @property
    def up_to_date(self) -> bool:
        ''' Return TRUE if the config/mask is up to date, FALSE if the config or mask needs to be pushed '''
        if self._config_changed or self._mask_changed:
            return False
        return True

    @property
    def mfg_id(self) -> int:
        ''' Read the manufacturer ID from the chip '''
        if self._mfg_id is not None:
            return self._mfg_id
        self._mfg_id = int.from_bytes(self.readmem(self._address, REG_PTR_MFG_ID, REG_MEM_LEN), 'big')
        return self._mfg_id

    @property
    def die_id(self) -> int:
        ''' Read the die ID from the chip '''
        if self._die_id is not None:
            return self._die_id
        self._die_id = int.from_bytes(self.readmem(self._address, REG_PTR_DIE_ID, REG_MEM_LEN), 'big')
        return self._die_id

    def reset(self):
        ''' Send a reset to the global address of 0. NOTE: resets all registers, and may affect other devices '''
        # set the config reset value
        self._conf_reg |= REG_CONF_RESET
        self.push_config(verify=False) # Can't verify since the config will be reset
        self.refresh()

    def push_config(self, verify:bool=True):
        ''' Push the configuration and mask to the device (verify is done by I2C object) '''
        if not self.writemem(self._address, REG_PTR_CONF, self._conf_reg.to_bytes(REG_MEM_LEN, 'big'), verify=verify):
            if verify: # only raise the write failure of verify is enabled
                raise MicrocomBusWriteFail("I2C write to device failed writing INA3221 config")
        if not self.writemem(self._address, REG_PTR_MASK, self._mask_reg.to_bytes(REG_MEM_LEN, 'big'), verify=verify):
            if verify: # only raise the write failure of verify is enabled
                raise MicrocomBusWriteFail("I2C write to device failed writing INA3221 mask")
        # reload the config
        self.refresh()

    @property
    def mode(self) -> int:
        ''' RetMODE_LISTurrent mode of the INA3221 '''
        return MODE_LIST[self._conf_reg & REG_CONF_MODE]

    @mode.setter
    def mode(self, value:int):
        ''' Set the mode, if str is passed, convert to int '''
        if value not in MODE_LIST:
            raise ValueError(f"Expecting a valid config mode, got '{value}'. INT values must correspond to index of valid values: {MODE_LIST}")
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_MODE
        self._conf_reg |= value
        self._config_changed = True

    @property
    def ch1_enabled(self) -> bool:
        ''' Return TRUE if CH1 is enabled '''
        return self._conf_reg & REG_CONF_CH1_ENABLE > 0

    @ch1_enabled.setter
    def ch1_enabled(self, value:bool):
        ''' Set CH1 to enabled or disabled '''
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_CH1_ENABLE
        self._conf_reg |= value << mask_offset(REG_CONF_CH1_ENABLE)
        self._config_changed = True

    @property
    def ch2_enabled(self) -> bool:
        ''' Return TRUE if CH1 is enabled '''
        return self._conf_reg & REG_CONF_CH2_ENABLE > 0

    @ch2_enabled.setter
    def ch2_enabled(self, value:bool):
        ''' Set CH1 to enabled or disabled '''
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_CH2_ENABLE
        self._conf_reg |= value << mask_offset(REG_CONF_CH2_ENABLE)
        self._config_changed = True

    @property
    def ch3_enabled(self) -> bool:
        ''' Return TRUE if CH1 is enabled '''
        return self._conf_reg & REG_CONF_CH3_ENABLE > 0

    @ch3_enabled.setter
    def ch3_enabled(self, value:bool):
        ''' Set CH1 to enabled or disabled '''
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_CH3_ENABLE
        self._conf_reg |= value << mask_offset(REG_CONF_CH3_ENABLE)
        self._config_changed = True

    @property
    def supported_sample_rates(self) -> tuple:
        ''' Return the list of supported sample rates '''
        return AVG_SAMPLES

    @property
    def samples(self) -> int:
        ''' Returns the number of samples that are averaged for each reading '''
        return AVG_SAMPLES[(self._conf_reg & REG_CONF_AVG_MODE) >> mask_offset(REG_CONF_AVG_MODE)]

    @samples.setter
    def samples(self, value:int):
        ''' Set the number of samples taken, uses the actual number, not the binary flag value '''
        if value not in AVG_SAMPLES:
            raise ValueError(f"Expecting a valid number of samples to average, got '{value}', valid values: {AVG_SAMPLES}")
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_AVG_MODE
        self._conf_reg |= AVG_SAMPLES.index(value) << mask_offset(REG_CONF_AVG_MODE)
        self._config_changed = True

    @property
    def supported_conversion_times(self) -> tuple:
        ''' Return the supported conversion times for BUS or SHUNT '''
        return CONVERSION_TIMES

    @property
    def shunt_conversion_time(self) -> float:
        ''' Return the current SHUNT conversion time '''
        return CONVERSION_TIMES[(self._conf_reg & REG_CONF_SHUNT_CONV_TIME) >> mask_offset(REG_CONF_SHUNT_CONV_TIME)]

    @property
    def bus_conversion_time(self) -> float:
        ''' Return the current BUS conversion time '''
        return CONVERSION_TIMES[(self._conf_reg & REG_CONF_BUS_CONV_TIME) >> mask_offset(REG_CONF_BUS_CONV_TIME)]

    @shunt_conversion_time.setter
    def shunt_conversion_time(self, value:float):
        ''' Set the conversion time for the shunt '''
        if value not in CONVERSION_TIMES:
            raise ValueError(f"Expecting a valid conversion time, got '{value}', valid values: {CONVERSION_TIMES}")
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_SHUNT_CONV_TIME
        self._conf_reg |= CONVERSION_TIMES.index(value) << mask_offset(REG_CONF_SHUNT_CONV_TIME)
        self._config_changed = True

    @bus_conversion_time.setter
    def bus_conversion_time(self, value:float):
        ''' Set the conversion time for the shunt '''
        if value not in CONVERSION_TIMES:
            raise ValueError(f"Expecting a valid conversion time, got '{value}', valid values: {CONVERSION_TIMES}")
        # reset bits before setting
        self._conf_reg &= ~REG_CONF_BUS_CONV_TIME
        self._conf_reg |= CONVERSION_TIMES.index(value) << mask_offset(REG_CONF_BUS_CONV_TIME)
        self._config_changed = True

    @property
    def summation_channels(self) -> tuple[bool, bool, bool]:
        ''' Return which channels are included for the summation register '''
        channels = (self._mask_reg & REG_MASK_SUM_CHANNELS) >> mask_offset(REG_MASK_SUM_CHANNELS)
        return (channels & 0b100 > 0, channels & 0b010 > 0, channels & 0b001 > 0)

    @summation_channels.setter
    def summation_channels(self, value:list|tuple):
        ''' Set the channels used for summation. List or Tuple must include True/False for channels '''
        if len(value) != 3:
            raise ValueError(f"Expecting True/False for 3 channels got '{value}'")
        for x in value:
            if not isinstance(x, bool):
                raise ValueError(f"Expecting True/False got '{x}'")
        # reset bits before setting
        self._mask_reg &= ~REG_MASK_SUM_CHANNELS
        self._mask_reg |= ((int(value[0]) << 2) + (int(value[1]) << 1) + int(value[2])) << mask_offset(REG_MASK_SUM_CHANNELS)
        self._mask_changed = True

    @property
    def warn_latch_enabled(self) -> bool:
        ''' Return True if the latch is enabled, False if set to transparent '''
        return (self._mask_reg & REG_MASK_WARN_ENABLE) > 0

    @warn_latch_enabled.setter
    def warn_latch_enabled(self, value:bool):
        ''' Set the Warn Latch to enabled if true, transparent if false '''
        self._mask_reg &= ~REG_MASK_WARN_ENABLE
        if value:
            self._mask_reg |= REG_MASK_WARN_ENABLE
        self._mask_changed = True

    @property
    def critical_latch_enabled(self) -> bool:
        ''' Return True if the latch is enabled, False if set to transparent '''
        return (self._mask_reg & REG_MASK_WARN_ENABLE) > 0

    @critical_latch_enabled.setter
    def critical_latch_enabled(self, value:bool):
        ''' Set the Warn Latch to enabled if true, transparent if false '''
        self._mask_reg &= ~REG_MASK_CRIT_ENABLE
        if value:
            self._mask_reg |= REG_MASK_CRIT_ENABLE
        self._mask_changed = True

    @property
    def active_alerts(self) -> ALERT_TUPLE:
        ''' Return a dict contain all active alerts '''
        return ina3221_alerts(self._mask_reg)

    @property
    def conversion_ready(self) -> bool:
        ''' Return True if conversion data is ready '''
        return (self._mask_reg & REG_MASK_CONV_READY) > 0

    def __repr__(self):
        return f"{self.__class__.__name__}({', '.join([x + '=' + str(getattr(self,x)) for x in dir(self) if not x.startswith('_') and not isinstance(getattr(self, x), Callable)])})"

    def _read_voltage_register(self, mem_address:int) -> int:
        ''' Read the voltage from the specified address, offset for reserved bits and convert from two's compliment, return as int '''
        reading = int.from_bytes(self.readmem(self._address, mem_address, REG_MEM_LEN), 'big')
        reading = reading >> VOLTAGE_RESERVED_BITS
        return twos_comp(reading, VOLTAGE_BITS)

    def get_bus_volts(self, bus_id:int) -> float:
        ''' Return the bus voltage for the designated channel '''
        if 0 < bus_id <= 3:
            reading = self._read_voltage_register(REG_PTR_BUSES[bus_id]) # type: ignore
            self._logger.debug(f"Bus {bus_id} voltage: {reading}")
            return reading * BUS_VOLTAGE_INCREMENT
        raise ValueError(f"Expecting valid bus_id between 1 and 3 (inclusive), got '{bus_id}'")

    def get_shunt_milli_volts(self, bus_id:int) -> float:
        ''' Return the shunt voltage for the designated channel '''
        if 0 < bus_id <= 3:
            reading = self._read_voltage_register(REG_PTR_SHUNTS[bus_id]) # type: ignore
            self._logger.debug(f"Shunt {bus_id} voltage register: {reading}, voltage reading: {reading * SHUNT_VOLTAGE_INCREMET}")
            return reading * SHUNT_VOLTAGE_INCREMET
        raise ValueError(f"Expecting valid bus_id between 1 and 3 (inclusive), got '{bus_id}'")

    def get_shunt_amps(self, bus_id:int) -> float:
        ''' Return the amperage based on the voltage drop on the shunt '''
        if 0 < bus_id <= 3:
            shunt = self._shunts[bus_id - 1]
            if isinstance(shunt, ShuntConfig):
                reading = self.get_shunt_milli_volts(bus_id=bus_id)
                amps = shunt.get_amps(reading)
                self._logger.debug(f"Shunt {bus_id} amperage converted: {amps}")
                return amps
            raise ValueError(f"No shunt configuration present for bus {bus_id}. Unable to calculate bus amperage.")
        raise ValueError(f"Expecting valid bus_id between 1 and 3 (inclusive), got '{bus_id}'")

    def get_bus_amp_thresholds(self, bus_id:int) -> tuple[float, float]:
        ''' Get the current thresholds for warn and critical for a bus '''
        if 0 < bus_id <= 3:
            warn_reading = self._read_voltage_register(REG_PTR_BUSES[bus_id]) # type: ignore
            crit_reading = self._read_voltage_register(REG_PTR_BUSES[bus_id]) # type: ignore
            shunt = self._shunts[bus_id]
            if isinstance(shunt, ShuntConfig):
                return (shunt.get_amps(warn_reading * SHUNT_VOLTAGE_INCREMET),
                        shunt.get_amps(crit_reading * SHUNT_VOLTAGE_INCREMET))
            raise ValueError(f"No shunt configuration present for bus {bus_id}. Unable to calculate bus amperage.")
        raise ValueError(f"Expecting valid bus_id between 1 and 3 (inclusive), got '{bus_id}'")

    def set_bus_amp_thresholds(self, bus_id:int, warn_amps:float, crit_amps:float):
        ''' Set the thresholds for warning and critical alerts for a bus, values are in amps, the proper value will be calculated '''
        if 0 < bus_id <= 3:
            shunt = self._shunts[bus_id]
            if isinstance(shunt, ShuntConfig):
                warn_thresh = shunt.get_register_value(warn_amps, SHUNT_VOLTAGE_INCREMET)
                crit_thresh = shunt.get_register_value(crit_amps, SHUNT_VOLTAGE_INCREMET)
                self.writemem(self._address, REG_PTR_THRESH[bus_id][0], warn_thresh.to_bytes(2, 'big'), verify=True)
                self.writemem(self._address, REG_PTR_THRESH[bus_id][1], crit_thresh.to_bytes(2, 'big'), verify=True)
                return
            raise ValueError(f"No shunt configuration present for bus {bus_id}. Unable to calculate bus amperage.")
        raise ValueError(f"Expecting valid bus_id between 1 and 3 (inclusive), got '{bus_id}'")



def ina3221_alerts(mask_reg:int) -> ALERT_TUPLE:
    ''' Return a dict contain all active alerts '''
    return ALERT_TUPLE(
        sum=(mask_reg & REG_MASK_SUM_ALERT) > 0,
        timing=(mask_reg & REG_MASK_TIMING_ALERT) == 0, # timing is inverse, high is False, low is True
        ch1warn=(((mask_reg & REG_MASK_WARN_CHANNELS) >> mask_offset(REG_MASK_WARN_CHANNELS)) & 0b100) > 1,
        ch1critical=(((mask_reg & REG_MASK_CRIT_CHANNELS) >> mask_offset(REG_MASK_CRIT_CHANNELS)) & 0b100) > 1,
        ch2warn=(((mask_reg & REG_MASK_WARN_CHANNELS) >> mask_offset(REG_MASK_WARN_CHANNELS)) & 0b10) > 1,
        ch2critical=(((mask_reg & REG_MASK_CRIT_CHANNELS) >> mask_offset(REG_MASK_CRIT_CHANNELS)) & 0b10) > 1,
        ch3warn=(((mask_reg & REG_MASK_WARN_CHANNELS) >> mask_offset(REG_MASK_WARN_CHANNELS)) & 0b1) > 1,
        ch3critical=(((mask_reg & REG_MASK_CRIT_CHANNELS) >> mask_offset(REG_MASK_CRIT_CHANNELS)) & 0b1) > 1
    )


def twos_comp(val, bits):
    """returns the 2's complement of int value val with n bits

    - https://stackoverflow.com/questions/1604464/twos-complement-in-python"""

    if (val & (1 << (bits - 1))) != 0:  # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << bits)        # compute negative value
    return val & ((2 ** bits) - 1)     # return positive value as is
