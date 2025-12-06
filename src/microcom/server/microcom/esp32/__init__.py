''' Platform specific file for esp32 based microcontrollers '''
import esp32 # pylint: disable=E0401
from microcom.gpio import GPIO_GLOBAL_CONSTANTS

# Global platform variables
PLATFORM_SUPPORTED_BUSES = ('i2c', 'uart')
PLATFORM_SUPPORTED_FUNCTIONS = ()

PLATFORM_SUPPORTED_IRQ_FUNCTIONS = ()

# GPIO Constants
class GPIO_CONSTANTS(GPIO_GLOBAL_CONSTANTS):
    ''' Class to hold platform specific constants '''
    IN = 1
    OUT = 3
    OPEN_DRAIN = 7
    ALT = None
    PULL_NONE = None
    PULL_UP = 1
    PULL_DOWN = 2
    VALID_PIN_MODE = (IN, OUT, OPEN_DRAIN)
    PIN_MODE_TEXT = ('IN', 'OUT', 'OPEN_DRAIN')
    VALID_PIN_ALT = (None,)
    PIN_ALT_TEXT = (None,)

def esp32_base_cpu_temp() -> dict:
    ''' return the temp of the CPU for the base ESP32 '''
    temp_in_f = esp32.raw_temperature() # pyright: ignore[reportAttributeAccessIssue]
    return {'cpu': (temp_in_f - 64) * (5.0/9.0), 'cpu_temp_scale': 'c'}

def esp32_c3_c6_s2_s3_cpu_temp() -> dict:
    ''' Return the temp of the CPU for C3, C6, S2 and S3 ESP32 chips in C '''
    return {'cpu': esp32.mcu_temperature(), 'cpu_temp_scale': 'c'} # pyright: ignore[reportAttributeAccessIssue]
