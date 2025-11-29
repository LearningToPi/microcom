import asyncio
from json import load
import os
from libs.logging_handler import create_logger, INFO, DEBUG
from microcom.server import TEXT_ENCODING
#, TEXT_ENCODING
#, TEXT_ENCODING
__VERSION__ = (0,0,3)

import sys
sys.path.append('lib')

logger = create_logger(INFO)
config_file = 'config.json'
with open (config_file, 'r', encoding=TEXT_ENCODING) as input_file:
    config = load(input_file)[os.uname().nodename]


def set_global_exception():
    ''' Function to call if an unhandled exception occurs '''
    def handle_exception(loop, context):
        sys.print_exception(context["exception"]) # type: ignore # pylint: disable=E1101
        sys.exit()
    loop = asyncio.get_event_loop()
    loop.set_exception_handler(handle_exception)

async def main():
    set_global_exception()  # Debug aid
    if config.get('server', None) == 'wifi_udp':
        from microcom.wifi_udp import MicrocomServerUDP
        config.pop('server')
        server = MicrocomServerUDP(**config)
    elif config.get('server', None) == 'ble':
        from microcom.ble import MicrocomServerBLE
        config.pop('server')
        server = MicrocomServerBLE(**config)
    else:
        from microcom.serial import MicrocomServerSerial
        if 'server' in config:
            config.pop('server')
        uart0 = MicrocomServerSerial(**config)  # Constructor might create tasks
    logger.info('Starting Microcom processing loop...')

    while True:
        await asyncio.sleep(2)

try:
    asyncio.run(main())
finally:
    asyncio.new_event_loop()  # Clear retained state
