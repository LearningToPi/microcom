# pylint: disable=W0221,W0718,C0415
import asyncio
#from functools import partial
from time import time, gmtime # type: ignore # pylint:disable=E0611
import os
import sys
import hashlib
import binascii
from machine import reset, soft_reset, RTC # type: ignore # pylint: disable=E0401
from gc import collect # type: ignore # pylint: disable=E0611

from microcom.async_lock import AsyncLock
from microcom.msg._base import *
from microcom.monitor import MicroPyMonitor, supported_hash_algs
from microcom.msg._base import MicrocomMsg
from microcom.log_manager import LOG_LIST_MAX, MicrocomLogManager, INFO
from microcom.worker_thread import WorkerThread
#from microcom.neopixel_controller import NeoPixel_Controller

SEND_TIMEOUT = 3
RETRY = 3
SERVER_NAME = ''
STATS_STREAMING = True
STATS_INTERVAL = 30
LOG_LEVEL = INFO

ASYNC_TIMEOUT = 5

VFATFS_FILE = 32768
VFATFS_DIR = 16384

FILE_HASH_PREFERED = ['sha1', 'sha256']

PWM = 'pwm'
I2C = 'i2c'
UART = 'uart'

IRQ_COMMANDS = ('send_message', 'write_file', 'pin_set', 'bus_cmd', 'set_vars', 'create_byte_arr', 'pwm_set', 'reset', 'exec_func', 'platform_specific')


class MicrocomServer:
    ''' Base server class to handle Microcom operations '''
    def __init__(self, log_level:int=LOG_LEVEL,
                 send_timeout:int=SEND_TIMEOUT, retry:int=RETRY, stats_streaming:bool=STATS_STREAMING, stats_interval:int=STATS_INTERVAL,
                 log_list_max:int=LOG_LIST_MAX, tz_str:str="GMT", tz_offset:int=0, stream_stats_client:str|None=None, initial_config:list|None=None):
        self.send_timeout, self.retry = send_timeout, retry
        self.stats_interval = stats_interval
        self._logger = MicrocomLogManager(console_level=log_level, send_level=log_level, log_max_len=log_list_max, tz_str=tz_str, tz_offset=tz_offset)
        self._send_queue = []
        self._send_queue_lock = AsyncLock()
        self._wifi_lock = AsyncLock()
        self._last_sent_message = None
        self._received_frag = None
        self._send_task = None
        self._error_log = []
        self._irq = {}
        self._stream_stats_task = None
        self._logger.info(f"Microcom Starting, log level: {self._logger.console_level}...")
        self._stream_stats_client = stream_stats_client
        self.stream_stats = stats_streaming # start or stop the stats streaming
        self._vars = {'i2c': {}, 'spi': {}, 'pwm': {}, 'saved':{}, 'platform': {}, 'irq': {}, 'display': {}, 'uart': {}}
        self._byte_arr = {}
        self._worker_thread = WorkerThread(logger=self._logger, reply_func=self.send)

        # import platform specific functionality
        if os.uname().sysname == 'rp2':
            from microcom.rp2 import PLATFORM_SUPPORTED_BUSES, GPIO_CONSTANTS
            self.PLATFORM_SUPPORTED_BUSES = PLATFORM_SUPPORTED_BUSES
            self.GPIO_CONSTANTS = GPIO_CONSTANTS()
        elif os.uname().sysname == 'esp32':
            from microcom.esp32 import PLATFORM_SUPPORTED_BUSES, GPIO_CONSTANTS
            self.PLATFORM_SUPPORTED_BUSES = PLATFORM_SUPPORTED_BUSES
            self.GPIO_CONSTANTS = GPIO_CONSTANTS()
        else:
            self._logger.error("Unable to load platform specific functionality, BUSES and other functions will not be available.")
            self.PLATFORM_SUPPORTED_BUSES = ()
            from microcom.gpio import GPIO_GLOBAL_CONSTANTS
            self.GPIO_CONSTANTS = GPIO_GLOBAL_CONSTANTS

        # initiate monitor - start after platform specific
        self._monitor = MicroPyMonitor(server=self, log_level=log_level)

        # setup mapping for message types
        self.MESSAGE_TYPE =     (MSG_TYPE_INFO, MSG_TYPE_LIST_DIR, MSG_TYPE_CHECKSUM, MSG_TYPE_READ_FILE, MSG_TYPE_WRITE_FILE, MSG_TYPE_RENAME_FILE, MSG_TYPE_DEL_FILE,
                                 MSG_TYPE_PIN_READ, MSG_TYPE_PIN_SET, MSG_TYPE_TIME_SYNC, MSG_TYPE_BUS_INIT, MSG_TYPE_BUS_CMD, MSG_TYPE_GET_VAR, MSG_TYPE_SET_VAR,
                                 MSG_TYPE_GET_BYTE_ARR, MSG_TYPE_CREATE_BYTE_ARR, MSG_TYPE_PWM_READ, MSG_TYPE_PWM_SET, MSG_TYPE_RESET,
                                 MSG_TYPE_EXEC_FUNC, MSG_TYPE_PLATFORM_SPEC, MSG_TYPE_MONITOR_UPDATE, MSG_TYPE_NEOPIXEL_CMD, MSG_TYPE_DISPLAY_INIT, MSG_TYPE_DISPLAY_CMD,
                                 MSG_TYPE_IMPORT_CLASS)
        self.MONITOR_TYPES =    (MSG_TYPE_PIN_READ, MSG_TYPE_BUS_CMD, MSG_TYPE_GET_BYTE_ARR, MSG_TYPE_PWM_READ, MSG_TYPE_EXEC_FUNC, MSG_TYPE_PLATFORM_SPEC)
        self.MESSAGE_FUNCTION = (self.get_sysinfo, self.list_dir, self.file_checksum, self.read_file, self.write_file, self.rename_file, self.delete_file,
                                 self.pin_read, self.pin_set, self.time_sync, self.bus_init, self.bus_cmd, self.get_vars, self.set_vars,
                                 self.get_byte_arr, self.create_byte_arr, self.pwm_read, self.pwm_set, self.reset,
                                 self.exec_func, self.platform_specific, self.monitor_update, self.neopixel_cmd, self.display_init, self.display_cmd,
                                 self.import_class)

        # read initial config if present and initiate
        if initial_config is not None:
            self._logger.info("Processing initial config...")
            asyncio.create_task(self.initialize(config=initial_config))

    def __del__(self):
        self._logger.info("Microcom Server object deletion...")
        self.close()

    def close(self):
        ''' Shutdown the microcom server '''
        self._logger.info("Closing Microcom Server...")

    async def initialize(self, config:list):
        ''' Initialize the initial configs in the config.json file '''
        try:
            for initial_config in config:
                initial_msg = MicrocomMsg(**initial_config)
                self._logger.debug(f"Processing initial config messge: {initial_msg}")
                if initial_msg.msg_type in self.MESSAGE_TYPE:
                    try:
                        return_data = await asyncio.create_task(self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(initial_msg.msg_type)](initial_msg))
                        self._logger.info(f"Initial config messge: {initial_msg}, response {return_data}")
                    except Exception as e:
                        self._logger.warning(f"Vars: {self._vars}")
                        err_msg = f"Error in initial config {initial_msg.data}: {self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(initial_msg.msg_type)].__name__}({initial_msg.data}): " \
                                  f"{e.__class__.__name__}: {e}"
                        self._logger.error(err_msg)
                        if self._logger.console_level >= 7:
                            sys.print_exception(e) # type: ignore # pylint: disable=E1101
        except Exception as e:
            self._logger.error(f"General error with initial config: {e.__class__.__name__}: {e}")
            if self._logger.console_level >= 7:
                sys.print_exception(e) # type: ignore # pylint: disable=E1101
        # output the currently configured VARS
        self._logger.debug(f"VARS Configured: {self._vars}")

    @property
    def stream_stats(self) -> bool:
        ''' Return TRUE if stats are currently streaming '''
        return isinstance(self._stream_stats_task, asyncio.Task) and not self._stream_stats_task.done()

    @stream_stats.setter
    def stream_stats(self, value):
        if not isinstance(value, bool):
            raise ValueError("Expecting a bool for stream_stats")
        if value and not self.stream_stats:
            self._logger.info(f"Starting stats steaming. Interval {self.stats_interval}, client info: {self._stream_stats_client}...")
            self._stream_stats_task = asyncio.create_task(self.send_streaming_stats())
        elif not value and self.stream_stats:
            self._logger.info("Stopping streaming stats...")
            self._stream_stats_task.cancel() # type: ignore

    async def send(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None, wait_for_ack=True, wait_for_reply=False, data=None):
        ''' Serialize and send a microcom message. If not wait_for_reply, returns original message, else returns reply
            Raises:
                MicrocomRetryExceeded()
                MicrocomException() '''
        raise NotImplementedError("Function 'send' must be overriden by an inheriting class")

    async def send_ack(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None):
        ''' Send an ACK for a packet that was received - override per class '''
        raise NotImplementedError("Function 'send_ack' must be overriden by an inheriting class")

    async def get_sysinfo(self, message:MicrocomMsg):
        ''' Return they system info '''
        return self._monitor.get_sys_info(self)

    async def list_dir(self, message:MicrocomMsg):
        ''' reply with list of files '''
        if not isinstance(message.data, dict):
            raise ValueError(f"Message data must be a dict, received {message.data}")
        self._logger.info(f"{message.data}: Getting directory list...")
        return await list_dir(path=message.data.get('path', '/'),
                              subdirs=message.data.get('subdirs', True),
                              checksums=message.data.get('checksums', False))

    async def file_checksum(self, message:MicrocomMsg):
        ''' Repy with the checsum of a file '''
        if not isinstance(message.data, dict):
            raise ValueError(f"Message data must be a dict, received {message.data}")

        self._logger.info(f"{message.data}: Getting file checksum...")
        return await get_file_checksum(path=message.data.get('path', ''), alg_list=message.data.get('alg_list', FILE_HASH_PREFERED))

    async def read_file(self, message:MicrocomMsg):
        ''' Read a file and return it to the client '''
        if not isinstance(message.data, dict):
            raise ValueError(f"Message data must be a dict, received {message.data}")
        self._logger.info(f"{message.data}: Reading and sending file to client...")
        with open(message.data.get('path', None), 'rb') as input_file:
            in_buf = None
            while in_buf != b'':
                # read a chunk from the file and send the message
                in_buf = input_file.read(FILE_CHUNK_SIZE)
                await self.send(MicrocomMsg.reply(message, data=in_buf, direction=DIR_REPLY_FRAG))
                await asyncio.sleep(0) # insert a break to allow other tasks
                if not isinstance(self._last_sent_message, MicrocomMsg) or not self._last_sent_message.ack_received():
                    self._logger.error(f"Failed to receive ACK for file fragment for {message.data}. Cancelling transmit.")
                    return

            # after we reach the end, send final message with the checksum
            return await self.file_checksum(message)

    async def write_file(self, message:MicrocomMsg):
        ''' Write a block of data to the temp file location, if data is None, then delete the file '''
        if message.data is None:
            self._logger.info("Deleting temp file '.temp_file'...")
            os.remove('.temp_file')
        else:
            if not isinstance(message.data, bytes):
                raise ValueError(f"Message data must include bytes data, received {type(message.data)}")
            self._logger.info("Writing file block to '.temp_file'...")
            with open('.temp_file', 'ab') as output_file:
                output_file.write(message.data)

    async def rename_file(self, message:MicrocomMsg):
        ''' Rename a file, expecting a dict data field with {'old': '[filename]', 'new': '[filename]'} '''
        if message.data is None or not isinstance(message.data, dict) or 'old' not in message.data or 'new' not in message.data:
            raise ValueError(f"Expecting a dict of {{'old': '[filename]', 'new': '[filename]'}} and got {message.data}")
        self._logger.info(f"Renaming file '{message.data['old']}' to '{message.data['new']}'...")
        os.rename(message.data['old'], message.data['new'])

    async def delete_file(self, message:MicrocomMsg):
        ''' Delete a specified file. Expecting data to be a string with the file name only '''
        if message.data is None or not isinstance(message.data, str):
            raise ValueError(f"Expecting a string with the filename to delete, got {message.data}")
        self._logger.info(f"Deleting file {message.data}...")
        os.remove(message.data)

    async def reset(self, message:MicrocomMsg):
        ''' Perform a reset '''
        delay = message.data.get('delay', 0) if isinstance(message.data, dict) else 0
        soft = message.data.get('soft', True) if isinstance(message.data, dict) else True
        self._logger.warning(f"Reset {'SOFT' if soft else 'HARD'} request initiated. Reset will start in {delay} seconds...")
        await asyncio.sleep(delay)
        self._logger.warning(f"Initiating {'SOFT' if soft else 'HARD'} reset...")
        await asyncio.sleep(.5) # give enough time for logging to occur
        if soft:
            soft_reset()
        else:
            reset()

    async def send_streaming_stats(self):
        ''' Collect stats at the defined interval and send '''
        while self.send_streaming_stats:
            try:
                self._logger.debug("Generating streaming stats to send...")
                # run any monitors configured
                monitor_data = {}
                for mon_key, mon_msg in self._vars.get('monitor', {}).items():
                    if mon_msg.msg_type in self.MONITOR_TYPES:
                        try:
                            monitor_data[mon_key] = await asyncio.create_task(self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(mon_msg.msg_type)](mon_msg))
                        except Exception as e:
                            err_msg = f"Error in {self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(mon_msg.msg_type)].__name__}({mon_msg.data}): {e.__class__.__name__}: {e}"
                            self._logger.error(err_msg)
                            asyncio.create_task(self.send(MicrocomMsg.reply(mon_msg, data=err_msg, return_code=MICROCOM_FILE_ERROR if e.__class__.__name__ == 'OSError' else MICROCOM_GENERAL_ERROR)))
                    else:
                        self._logger.error(f"SEND_STREAMING_STATS message type '{mon_msg.msg_type}' is not supported. Supported message types are: {self.MONITOR_TYPES}")
                stats = self._monitor.get_stats(monitor_data=monitor_data)
                await self.send(MicrocomMsg(msg_type=MSG_TYPE_STATS, data=stats), wait_for_ack=False)
                await asyncio.sleep(self.stats_interval)
            except Exception as e:
                err_msg = f"Error in SEND_STREAMING_STATS: {e.__class__.__name__}: {e}"
                if self._logger.console_level >= 7:
                    sys.print_exception(e) # type: ignore # pylint: disable=E1101
                self._logger.error(err_msg)
            await asyncio.sleep(1)

    async def pin_read(self, message:MicrocomMsg):
        ''' Read the value of a pin '''
        from microcom.gpio import gpio_get
        if not isinstance(message.data, (list, int)):
            raise ValueError(f"Expected a list or int and got '{message.data}'")
        return await asyncio.wait_for(gpio_get(message.data if isinstance(message.data, list) else [message.data], self._logger, self.GPIO_CONSTANTS), timeout=ASYNC_TIMEOUT) # pyright: ignore[reportArgumentType]

    async def pin_set(self, message:MicrocomMsg):
        ''' Write the config for a Pin '''
        from microcom.gpio import gpio_set, PinIRQ
        if not isinstance(message.data, dict):
            raise ValueError(f"Expected a dict representing a pin config and got: {message.data}")
        # call return the pin data
        return_data = await asyncio.wait_for(gpio_set(message.data, self._logger, self.GPIO_CONSTANTS), timeout=ASYNC_TIMEOUT) # pyright: ignore[reportArgumentType]
        # populate IRQ task list and create IRQ's
        if message.data.get('irq', None) is not None:
            tasks = [(self.send, {'message': MicrocomMsg(msg_type=MSG_TYPE_IRQ)}, 'data')]
            tasks += [(x['function'], x['kwargs']) for x in message.data['irq']['tasks']]
            self._vars['irq'][str(message.data['pin'])] = PinIRQ(pin=str(message.data['pin']), _logger=self._logger,
                                                                rising=message.data['irq'].get('rising', True),
                                                                falling=message.data['irq'].get('falling', True),
                                                                tasks=tasks,
                                                                CONST=self.GPIO_CONSTANTS, # pyright: ignore[reportArgumentType]
                                                                debounce_ms=message.data['irq'].get('debounce', None))
        return return_data

    async def pwm_read(self, message:MicrocomMsg):
        ''' Read a PWM Pin, if the Pin is not configured for PWM returns an error '''
        from microcom.pwm import pwm_read
        if not isinstance(message.data, (list, int)):
            raise ValueError(f"Expected a list or int and got '{message.data}'")
        return await asyncio.wait_for(pwm_read(message.data if isinstance(message.data, list) else [message.data], self._logger, self._vars['pwm']), timeout=ASYNC_TIMEOUT)

    async def pwm_set(self, message:MicrocomMsg):
        ''' Write the config for a Pin '''
        from microcom.pwm import pwm_set
        if not isinstance(message.data, dict):
            raise ValueError(f"Expected a dict representing a pin config and got: {message.data}")
        return await asyncio.wait_for(pwm_set(message.data, self._logger, self._vars['pwm']), timeout=ASYNC_TIMEOUT)

    async def time_sync(self, message:MicrocomMsg):
        ''' Process and update the time on the server '''
        if not isinstance(message.data, int):
            self._logger.warning(f"Received Time sync message with invalid data: {message.data}")
            raise ValueError
        tt = gmtime(int(message.data))
        old_time = RTC().datetime()
        RTC().datetime((tt[0], tt[1], tt[2], tt[6], tt[3], tt[4], tt[5], 0))
        self._logger.info(f"Updated time from {old_time} to {RTC().datetime()}")

    async def bus_init(self, message:MicrocomMsg):
        ''' Initialize or change the config of a bus (I2C, SPI, UART) '''
        if not isinstance(message.data, dict) or 'bus_type' not in message.data or 'bus_id' not in message.data:
            raise ValueError(f"Bus Init message data must be a dict with 'bus_type' and 'bus_id', got {message.data}")
        if message.data['bus_type'] not in self.PLATFORM_SUPPORTED_BUSES:
            raise ValueError(f"Bus type {message.data['bus_type']} not in platform supported list: {self.PLATFORM_SUPPORTED_BUSES}")
        elif message.data['bus_type'] == I2C:
            from microcom.i2c import MicrocomI2CBus
            self._vars['i2c'][str(message.data['bus_id'])] = MicrocomI2CBus(message.data, self._logger)
            # return a I2C bus scan
            return self._vars['i2c'][str(message.data['bus_id'])].exec(bus_cmd='scan')
        elif message.data['bus_type'] == UART:
            from microcom.uart import MicrocomUARTBus
            self._vars['uart'][str(message.data['bus_id'])] = MicrocomUARTBus(message.data, self._logger)
            # create async read task
            self._vars['uart'][str(message.data['bus_id'])].read_thread = asyncio.create_task(self._vars['uart'][str(message.data['bus_id'])].async_read(callback=self.send, msg_class=MicrocomMsg))
        else:
            raise MicrocomException(f"Unable to initialize bus: {message.data}")

    def exec_bus_cmd(self, commands:list):
        ''' Execute a command on a bus (not async) '''
        # if we received a list of commands, run each
        if not isinstance(commands, (list)):
            raise ValueError(f"EXEC_BUS_CMD is expecting a list and got: {commands}")

        return_data = []
        for cmd in (commands if isinstance(commands, list) else [commands]):
            if not isinstance(cmd, dict):
                raise ValueError(f"EXEC_BUS_CMD Expected a dict and got '{cmd}'")
            if str(cmd['bus_type']).lower() == 'i2c':
                if str(cmd['bus_id']) in self._vars['i2c']:
                    return_data.append(self._vars['i2c'][str(cmd['bus_id'])].exec(**cmd))
                else:
                    raise MicrocomBusNotInitialized(f"EXEC_BUS_CMD {cmd['bus_type']} id {cmd['bus_id']} is not initialized")
            elif str(cmd['bus_type']).lower() == 'uart':
                if str(cmd['bus_id']) in self._vars['uart']:
                    return_data.append(self._vars['uart'][str(cmd['bus_id'])].exec(**cmd))
            else:
                raise MicrocomBusInvaldParam(f"EXEC_BUS_CMD Invalid bus type '{cmd['bus_type']}' provided. Platform supports {self.PLATFORM_SUPPORTED_BUSES}")

        # return the list of results for each command, or if we only have a single command return as a dict
        return return_data

    async def bus_cmd(self, message:MicrocomMsg):
        ''' Execute a command on a bus '''
        # make sure we have a list of commands to run
        if not isinstance(message.data, (dict, list)):
            raise ValueError(f"Bus cmd message data must be a dict, got {message.data}")
        return self.exec_bus_cmd(message.data if isinstance(message.data, list) else [message.data])

    async def display_init(self, message:MicrocomMsg):
        ''' Initialize a display '''
        # make sure we have a vars to init a display
        from microcom.display import SUPPORTED_DISPLAY_TYPES
        if not isinstance(message.data, dict):
            raise ValueError(f"DISPLAY_INIT Expected a dict of {{'display_type': {','.join(SUPPORTED_DISPLAY_TYPES.keys())}, "
                             f"'bus_type: 'i2c', 'bus_id': int, 'config': dict(configuration display type dependent)}}")
        self._vars['display'][message.data.get('id', '0')] = SUPPORTED_DISPLAY_TYPES[message.data['display_type']][message.data['bus_type']](
            self._logger, self._vars[message.data['bus_type']][str(message.data['bus_id'])], message.data['config']
        )

    def display_cmd(self, message:MicrocomMsg):
        pass

    async def get_vars(self, message:MicrocomMsg):
        ''' Return a specific variable from memory '''
        if not isinstance(message.data, dict) and message.data is not None:
            raise ValueError(f"get var message data must be a dict, got {message.data}")
        # if var_type is not None
        if isinstance(message.data, dict) and message.data.get('var_type') in ['pwm', 'saved', 'display', 'platform', 'neo']:
            if message.data.get('var_name') is not None:
                return_var = {message.data.get('var_name'): self._vars[message.data['var_type']][message.data.get('var_name')]}
            else:
                return_var = self._vars[message.data['var_type']]
        else:
            raise ValueError(f"Var {message.data} not in variable storage.")
        return return_var

    async def set_vars(self, message:MicrocomMsg):
        ''' Set a variable in the var storage '''
        data = [message.data] if isinstance(message.data, dict) else message.data
        if not isinstance(data, list):
            self._logger.error(f"SET_VAR function requires a list or a dict, recieved {message.data}")
            raise MicrocomException(f"SET_VAR function requires a list or a dict, recieved {message.data}")
        for new_var in data:
            if not isinstance(new_var, dict) or 'key' not in new_var:
                self._logger.error(f"SET_VAR variables must be a dict with a 'key' name, recieved {new_var}")
                raise MicrocomException(f"SET_VAR variables must a dict with a 'key', recieved {new_var}")
            self._logger.debug(f"SET_VAR creating variable {new_var}")
            # if a value is passed, assign it as is
            if 'value' in new_var:
                self._vars['saved'][new_var['key']] = new_var['value']
            # if a class, create an instance of the class and pass args / kwargs
            elif 'class' in new_var:
                if new_var['class'] not in globals():
                    self._logger.error(f"Failed to create variable {new_var['key']}. Class {new_var['class']} not imported.")
                    raise MicrocomException(f"Failed to create variable {new_var['key']}. Class {new_var['class']} not imported.")
                self._vars['saved'][new_var['key']] = globals()[new_var['class']](*new_var.get('a`rgs', []), **new_var.get('kwargs', {}))
            else:
                self._logger.error(f"Uknown variable type: {new_var}")
                raise MicrocomException(f"Uknown variable type: {new_var}")
        # send back an empy message as confirmation

    async def get_byte_arr(self, message:MicrocomMsg):
        ''' Get a byte array stored in memory '''
        if not isinstance(message.data, str) and message.data is not None:
            raise ValueError(f"get byte arr message data must be a str, got {message.data}")
        if message.data is None:
            raise ValueError(f"get byte arr message data must be a str, got {message.data}")
        if message.data not in self._byte_arr:
            raise ValueError(f"Byte arr '{message.data}' not in variable storage.")
        else:
            return_var = self._byte_arr[message.data]
            self._logger.info(f"Returning byte arr {message.data}")
        return return_var

    async def create_byte_arr(self, message:MicrocomMsg) -> None:
        ''' Create a byte array in memory '''
        if not isinstance(message.data, dict):
            raise ValueError(f"create byte arr message data must be a dict containting 'key' and 'length', got {message.data}")
        self._logger.info(f"Allocating byte array '{message.data['key']}' of length {message.data['length']}")
        self._byte_arr[message.data['key']] = bytearray(message.data['length']) # type: ignore
        # send back an empy message as confirmation

    async def import_class(self, message:MicrocomMsg):
        ''' Import a class to execute (class should have an __init__ with applicable variables and a run() method) '''
        try:
            if not isinstance(message.data, dict) or 'path' not in message.data or 'class_name' not in message.data:
                raise ValueError(f"import_class message data must be a dict containting 'path' and 'class_name', got {message.data}")
            class_path = __import__(message.data['path'])
            class_obj = getattr(class_path, message.data['class_name'])
            instance = class_obj(_vars=self._vars, _byte_arr=self._byte_arr, logger=self._logger, **message.data.get('init_params', {}))
            if isinstance(message.data.get('save_instance', None), str):
                self._vars['saved'][message.data['save_instance']] = instance
            self._logger.info(f"Import Class: Starting {message.data['path']}.{message.data['class_name']} with params: {message.data.get('run_params', {})}")
            if message.data.get('async', False):
                self._logger.info(f"Import Class: Scheduling async on core2 for {message.data['path']}.{message.data['class_name']} with params: {message.data.get('run_params', {})}")
                self._worker_thread.sync_add_task(instance.run, **message.data.get('run_params', {}))
                return_data = ""
            else:
                start_time = time()
                type_coro = type(_dummy()) # Get the type of a coroutine to check the return data
                run_function = getattr(instance, 'run')
                return_data = run_function(**message.data.get('run_params', {}))
                if isinstance(return_data, type_coro):
                    return_data = await asyncio.create_task(run_function(**message.data.get('run_params', {})))
                end_time = time()
                self._logger.debug(f"Import Class: took {end_time - start_time} seconds for {message.data['path']}.{message.data['class_name']} with params: {message.data.get('run_params', {})}")
            if isinstance(message.data.get('save_return', None), str):
                self._vars['saved'][message.data['save_return']] = return_data
            asyncio.create_task(self.send(MicrocomMsg.reply(message, data=return_data)))
        except Exception as e:
            self._logger.error(f"Error in IMPORT_CLASS({message.data}): {e.__class__.__name__}: {e}")
            if self._logger.console_level >= 7:
                sys.print_exception(e) # type: ignore # pylint: disable=E1101
            asyncio.create_task(self.send(MicrocomMsg.reply(message, data=str(e), return_code=MICROCOM_FILE_ERROR if e.__class__.__name__ == 'OSError' else MICROCOM_GENERAL_ERROR)))

    async def exec_func(self, message:MicrocomMsg):
        ''' Exec a function, this can be a saved variable, or a builtin function 
            params: (either class or saved required)
                - obj (optional) - builtin or imported class to executefrom
                - saved (optional) - named of saved var to execute
                - function (required) - Function to execute
                - args (optional) - list of positional arguments
                - kwargs (optional) - list of keyword arguments
                - save_return (optional) - name of variable to save the return to
            '''
        if not isinstance(message.data, dict) or ('object' not in message.data and 'saved' not in message.data):
            self._logger.error(f"EXEC_FUNC requires a dict with either a 'obj' or 'saved' object to call, got: {message.data}")
            raise MicrocomException(f"EXEC_FUNC requires a dict with either a 'obj' or 'saved' object to call, got: {message.data}")
        obj = globals()[message.data['obj']] if 'obj' in message.data else self._vars['saved'][message.data['saved']]
        func = getattr(obj, message.data['function'])
        self._logger.debug(f"EXEC_FUNC calling {obj}.{func}(*{message.data.get('args', [])}, **{message.data.get('kwargs', {})})")
        type_coro = type(_dummy()) # Get the type of a coroutine to check the return data
        return_data = func(*message.data.get('args', []), **message.data.get('kwargs', {}))
        if isinstance(return_data, type_coro):
            return_data = await asyncio.create_task(func(*message.data.get('args', []), **message.data.get('kwargs', {})))
        if 'save_return' in message.data:
            self._vars['saved'][message.data['save_return']] = return_data
        return return_data

    async def platform_specific(self, message:MicrocomMsg):
        ''' Class to execute platform specific functions, i.e. PIO for RP2 '''
        if os.uname().sysname == 'rp2':
            import microcom.rp2
            return await microcom.rp2.handler(message, self._vars, self._logger)
        raise MicrocomException(f"PLATFORM_SPECIFIC platform unsupported {os.uname().sysname}")

    async def monitor_update(self, message:MicrocomMsg):
        ''' Update the periodic monitor '''
        if not isinstance(message.data, list):
            self._logger.error(f"MONITOR_UPDATE requires a list, got: {message.data}")
            raise MicrocomException(f"MONITOR_UPDATE requires a list, got: {message.data}")
        for monitor in message.data:
            if not isinstance(monitor, dict) or 'msg_type' not in monitor or 'name' not in monitor or 'data' not in monitor:
                raise MicrocomException(f"MONITOR_UPDATE requires a list of dict, the monitor dict must include a 'msg_type', 'name' and 'data', got {monitor}")
            if 'monitor' not in self._vars:
                self._vars['monitor'] = {}
            elif isinstance(monitor, dict) and monitor.get('remove', False) and 'name' in monitor:
                if monitor['name'] not in self._vars['monitor']:
                    raise MicrocomException(f"MONITOR_UPDATE cannot delete monitor '{monitor['name']}'. Monitor not present")
                self._vars['monitor'].pop(monitor['name'])
            else:
                self._logger.info(f"MONITOR_UPDATE adding monitor {monitor}")
                self._vars['monitor'][monitor['name']] = MicrocomMsg(msg_type=monitor['msg_type'], data=monitor['data'])
        return self._vars.get('monitor', {})

    async def neopixel_cmd(self, message:MicrocomMsg):
        ''' Execute a command on a neopixel group '''
        if 'neo' not in self._vars:
            self._vars['neo'] = {}
        if not isinstance(message.data, dict):
            raise MicrocomException(f"NEOPIXEL_CMD requires a dict, got {message.data}")
        for key, item in message.data.items():
            if not isinstance(item, dict):
                raise MicrocomException(f"NEOPIXEL_CMD device requires a dict, got {key}, {item}")
            if 'cmd' not in item:
                raise MicrocomException(f"NEOPIXEL_CMD device requires a 'cmd' to execute, got {key}, {item}")
            if item['cmd'] == 'INIT':
                from microcom.neo import MicrocomNeoPixelGroup
                self._vars['neo'][key] = MicrocomNeoPixelGroup(**item.get('kwargs', {}))
                return
            if key not in self._vars.get('neo', {}):
                raise MicrocomException(f"NEOPIXEL_CMD device {key} not initialized. Initialize before sending CMD")
            if item['cmd'] == 'DELETE':
                self._vars.pop(key)
                return
            if item['cmd'] not in dir(self._vars['neo'][key]):
                raise MicrocomException(f"NEOPIXEL_CMD device command not recognized, got {key}, {item}")
            func = getattr(self._vars['neo'][key], item['cmd'])
            self._logger.info(f"NEOPIXEL_CMD executing {key}, {item}")
            func_return = func(**item.get('kwargs', {}))
            if type(func_return) == type(_dummy()): # check if we got a coro back
                self._logger.info("NEOPIXEL_CMD executing as coroutine")
                asyncio.create_task(func(**item.get('kwargs', {})))

    async def _receive_message(self, received_msg:MicrocomMsg):
        ''' Process an incoming message '''
        # process the received message
        if received_msg.direction == DIR_ACK:
            if not isinstance(self._last_sent_message, MicrocomMsg):
                self._logger.warning(f"Received ACK message but no message waiting for ACK: {str(received_msg)}")
                del received_msg
                collect()
                return
            if not self._last_sent_message.is_ack(received_msg):
                self._logger.warning(f"Received ACK message that does not match message waiting for ACK. Received: {str(received_msg)}, last sent: {self._last_sent_message}")
                del received_msg
                collect()
                return
            self._last_sent_message.ack_time = time()
            self._logger.debug(f"Received ACK for ID {received_msg.pkt_id}. ACK RTT: {self._last_sent_message.ack_rtt()}")
        elif received_msg.direction == DIR_SEND_FRAG:
            # send an ACK
            asyncio.create_task(self.send_ack(message=received_msg))
            await asyncio.sleep(.01)
            # if we received a fragment that isn't a reply save the data until all fragments received
            if isinstance(self._received_frag, MicrocomMsg) and self._received_frag.pkt_id == received_msg.pkt_id:
                self._logger.debug(f"RECEIVED send fragment {received_msg.frag_number} for pkt: {self._received_frag.pkt_id}")
                if self._received_frag.frag_number +1 != received_msg.frag_number:
                    self._logger.error(f"RECEIVED a fragment that should not be next. pkt_id {self._received_frag.pkt_id} last frag {self._received_frag.frag_number}, got frag {received_msg.frag_number}")
                    del received_msg
                    collect()
                    return
                else:
                    # if the fragment was the expected fragment, save the data (prevent double saves or missing fragments)
                    self._received_frag.frag_time = time()
                    self._received_frag.append_data(received_msg.data)
                    self._received_frag.frag_number = received_msg.frag_number
            elif isinstance(self._received_frag, MicrocomMsg):
                self._logger.warning(f"RECEIVED send fragment with a different id, previous id: {self._received_frag.pkt_id}, new id: {received_msg.pkt_id}, discarding old fragment")
                self._received_frag = received_msg
                del received_msg
                collect()
                return
            else:
                # save the new fragment
                self._logger.debug(f"RECEIVED new send fragment for pkt: {received_msg.pkt_id}")
                self._received_frag = received_msg
                self._received_frag.frag_time = time()
        else:
            # if this wasn't an ACK or Time sync, send back an ACK
            if received_msg.msg_type != MSG_TYPE_TIME_SYNC:
                asyncio.create_task(self.send_ack(message=received_msg))

            # if the received packet is a continuation of a previous fragment, combine the packets and process accordingly
            if isinstance(self._received_frag, MicrocomMsg) and self._received_frag.pkt_id == received_msg.pkt_id:
                self._received_frag.received_time = time()
                self._received_frag.append_data(received_msg.data)
                received_msg = self._received_frag
                self._received_frag = None

            if received_msg.msg_type == MSG_TYPE_PING:
                # if we received a ping, return a message with the same ID and data payload
                asyncio.create_task(self.send(MicrocomMsg.reply(received_msg)))
                await asyncio.sleep(.1)

            elif received_msg.msg_type in self.MESSAGE_TYPE:
                # if a timesync message, just run the sync
                if received_msg.msg_type == MSG_TYPE_TIME_SYNC:
                    asyncio.create_task(self.time_sync(received_msg))
                else:
                    try:
                        return_data = await asyncio.create_task(self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(received_msg.msg_type)](received_msg))
                        asyncio.create_task(self.send(MicrocomMsg.reply(received_msg, data=return_data)))
                    except Exception as e:
                        err_msg = f"Error in {self.MESSAGE_FUNCTION[self.MESSAGE_TYPE.index(received_msg.msg_type)].__name__}({received_msg.data}): {e.__class__.__name__}: {e}"
                        self._logger.error(err_msg)
                        if self._logger.console_level == 7:
                            sys.print_exception(e)  # pyright: ignore[reportAttributeAccessIssue] # pylint: disable=E1101
                        asyncio.create_task(self.send(MicrocomMsg.reply(received_msg, data=err_msg, return_code=MICROCOM_FILE_ERROR if e.__class__.__name__ == 'OSError' else MICROCOM_GENERAL_ERROR)))

            elif received_msg.msg_type == MSG_TYPE_STATS_ENABLE:
                self.stream_stats = False
                await asyncio.sleep(.1) # added delay to make sure the stream stats stop
                if isinstance(received_msg.data, dict):
                    self._logger.info(f"Enabling streaming stats: {received_msg.data}")
                    self.stats_interval = received_msg.data['interval'] if isinstance(received_msg.data, dict) and 'interval' in received_msg.data else self.stats_interval
                    if 'ip' in received_msg.data and 'port' in received_msg.data:
                        self._stream_stats_client = (received_msg.data['ip'], received_msg.data['port'])
                else:
                    self._logger.info(f"Enabling streaming stats interval: {self.stats_interval}")
                    self.stats_interval = self.stats_interval
                self.stream_stats = True

            elif received_msg.msg_type == MSG_TYPE_STATS_DISABLE:
                self._logger.info(f"Disabling streaming stats: {received_msg.data}")
                self.stream_stats = False

            else:
                # unknown message type, reply with an error
                asyncio.create_task(self.send(MicrocomMsg.reply(received_msg, return_code=MICROCOM_UNSUPPORTED, data=f"Message type {received_msg.msg_type} not supported")))

        del received_msg
        collect()


async def get_file_checksum(path:str, alg_list:str|list|None=None) -> dict:
    ''' Calculate the hash using the specified algorithm '''
    # get a list of the supported algorithms
    supported_algs = supported_hash_algs()
    # if a string, make it a list, if None, just use the supported algs
    alg_list = [alg_list] if isinstance(alg_list, str) else supported_algs if alg_list is None else alg_list
    # get the 1st matching alg
    matching_algs = [x for x in supported_algs if x in alg_list]
    if len(matching_algs) == 0:
        raise NotImplementedError(f"Requested hashing algorithm {alg_list} not in supported list: {supported_algs}")
    alg = matching_algs[0]

    collect()

    with open(path, 'rb') as input_file:
        file_hash = getattr(hashlib, alg)()
        in_buf = None
        while in_buf != b'':
            in_buf = input_file.read(FILE_CHUNK_SIZE)
            file_hash.update(in_buf)
            await asyncio.sleep(0) # insert point to allow other tasks
        return {'alg': alg, 'checksum': binascii.hexlify(file_hash.digest())}


async def list_dir(path:str='/', subdirs:bool=True, checksums:bool=False) -> dict:
    ''' Recursively list all files and folders in a given path '''
    path = path if isinstance(path,str) else '/'
    path = path + ('/' if path[-1] != '/' else '')
    file_list = {}
    list_iter = os.ilistdir(path) # type: ignore # pylint: disable=E1101
    for x in list_iter:
        collect()
        print(path, x)
        file_list[x[0]] = {
            'size': x[3],
            'dir': True if x[1] == VFATFS_DIR else False,
            'subfiles': await list_dir(path=path+x[0]+'/', subdirs=subdirs, checksums=checksums) if subdirs and x[1] == VFATFS_DIR else {},
            'checksum': await get_file_checksum(path=path+x[0]) if checksums and x[1] != VFATFS_DIR else None
        }
    return file_list

# if a generator was returned, call it as async
async def _dummy(): ## https://forum.micropython.org/viewtopic.php?t=10840
    pass
