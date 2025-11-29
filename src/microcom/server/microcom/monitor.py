# pylint: disable=C0415
import sys
import os
from gc import mem_free, mem_alloc, collect # type: ignore # pylint: disable=E0611
from _thread import allocate_lock
import machine # type: ignore # pylint: disable=E0401
from libs.logging_handler import create_logger, INFO
from microcom.utils import supported_hash_algs
from microcom.version import VERSION


LOG_LEVEL = INFO

if os.uname().sysname == 'esp32':
    import esp32 # pylint: disable=E0401
    import microcom.esp32
elif os.uname().sysname == 'rp2':
    import microcom.rp2

class MicroPyMonitor:
    ''' Class to represent a monitor object to collect statistics '''
    def __init__(self, server, log_level=LOG_LEVEL, fs_list=('/',)):
        self._logger = create_logger(log_level)
        self.__lock = allocate_lock()
        self._server = server
        self.fs_list = fs_list
        self.wlan = None # wlan object can be populated after the monitor is started
        # import platform specific code
        if os.uname().sysname == 'rp2':
            self._temp_func = microcom.rp2.rp2_cpu_temp
        elif os.uname().sysname == 'esp32':
            # break out the differences between the platforms
            if os.uname().machine[-2:] in ['C3', 'C6', 'S2', 'S3']:
                self._temp_func = microcom.esp32.esp32_c3_c6_s2_s3_cpu_temp
            else:
                self._temp_func = microcom.esp32.esp32_base_cpu_temp
        else:
            self._temp_func = None

    def get_stats(self, monitor_data:dict={}) -> dict:
        ''' Collect the system statistics and return - this function contains generic micropython and platform specific can override and add additional as needed '''
        # disk_stats
        system_stats = {
            'disk': {}, 'memory': (), 'cpu_freq': machine.freq()
        }
        for path in self.fs_list:
            stats = os.statvfs(path)
            system_stats['disk'][path] = (round(stats[0] * stats[2] / 1024, 2), round(stats[0] * stats[3] / 1024, 2), round(stats[0] * (stats[2] - stats[3]) / 1024, 2))

        # mem
        with self.__lock:
            collect() # garbage collect
            free, alloc = mem_free(), mem_alloc()
            system_stats['memory'] = (round((free + alloc) / 1024, 2), round(free / 1024, 2), round(alloc / 1024, 2))

        # platform specific
        if self._temp_func is not None:
            system_stats['temp'] = self._temp_func()

        # return results from any recurring tasks
        system_stats['monitor'] = monitor_data

        return system_stats

    def get_sys_info(self, server) -> dict:
        ''' Collect the system statistics and return - this function contains generic and platform specific can override and add additional as needed '''
        sys_info = {
                'platform': sys.platform,
                'system_id': machine.unique_id(),
                'cpu_freq': machine.freq(),
                'version': {
                    'microcom': VERSION,
                    'microcom_message': None,
                    'micropython': sys.version
                },
                'supported_messages': server.MESSAGE_TYPE,
                'supported_buses': server.PLATFORM_SUPPORTED_BUSES,
                'hash_algs': supported_hash_algs(),
                'crypto_algs': [],
                'gpio_const': self._server.GPIO_CONSTANTS.dict()
            }

        # Add display font information if enabled
        for display in server._vars.get('display', {}):
            sys_info['displays'][display] = {
                'type': sys_info['displays'][display].display_type,
                'bus': sys_info['displays'][display].display_bus,
                'x': sys_info['displays'][display].res_x,
                'y': sys_info['displays'][display].res_y,
                'fonts' : sys_info['displays'][display].font_list() if 'font_list' in dir(sys_info['displays'][display]) else 'n/a'
            }

        if os.uname().sysname == 'rp2':
            import microcom.rp2
            sys_info['platform_specific'] = {'supported_functions': microcom.rp2.PLATFORM_SUPPORTED_FUNCTIONS}

        # if network is present
        if self.wlan is not None:
            with self._server._wifi_lock:
                sys_info['network'] = {'mac': self.wlan.config('mac'),
                                        'ifconfig': self.wlan.ifconfig(),
                                        'ssid': self.wlan.config('ssid'),
                                        'channel': self.wlan.config('channel'),
                                        'powermgmt': self.wlan.config('pm')}


        return sys_info
