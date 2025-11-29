# pylint: disable=W0401
import asyncio
import os
from time import time
import gc
import machine  # type: ignore # pylint: disable=import-error
from microcom.server import MicrocomServer
from microcom.msg._base import MicrocomMsg, SER_START_HEADER, SER_END, TEXT_ENCODING
from microcom.async_lock import AsyncLock
from microcom.exceptions import *


if os.uname().nodename == 'rp2':
    DATA_UART_TX_PIN = 4
    DATA_UART_RX_PIN = 5
elif os.uname().nodename == 'esp32':
    DATA_UART_TX_PIN = 10
    DATA_UART_RX_PIN = 9
else:
    raise MicrocomUnsupported(f"Platform {os.uname().nodename} is not recognized by the Microcom serial server module.")

DATA_UART = 1
DATA_UART_BAUDRATE = 9600
DATA_UART_SUPPORTED_BUADRATES = [9600, 115200]
DATA_UART_BITS = 8
DATA_UART_PARITY = None
DATA_UART_STOP = 1
DATA_UART_RTS_PIN = None
DATA_UART_CTS_PIN = None
DATA_UART_TX_BUFFER = 256
DATA_UART_RX_BUFFER = 256
DATA_UART_TIMEOUT = 1
MAX_MSG_LENGTH = 384


class MicrocomServerSerial(MicrocomServer):
    ''' Class to represent a serial connected client '''
    def __init__(self, port:int=DATA_UART, tx_pin=DATA_UART_TX_PIN, rx_pin=DATA_UART_RX_PIN, baudrate=DATA_UART_BAUDRATE, bits=DATA_UART_BITS, parity=DATA_UART_PARITY,
                 stop=DATA_UART_STOP, rts_pin=DATA_UART_RTS_PIN, cts_pin=DATA_UART_CTS_PIN, txbuf=DATA_UART_TX_BUFFER, rxbuf=DATA_UART_RX_BUFFER,
                 timeout=DATA_UART_TIMEOUT, max_input_len=MAX_MSG_LENGTH, **kwargs):
        super().__init__(stream_stats_client=f"UART{port} {baudrate} baud", **kwargs)
        self.__uart_write_lock = AsyncLock()
        self.__uart_send_lock = AsyncLock()
        self._max_input_len = max_input_len
        self.__uart = machine.UART(port, baudrate=baudrate, tx=machine.Pin(tx_pin), rx=machine.Pin(rx_pin))
        self.serial_update_port(baudrate=baudrate, bits=bits, parity=parity, stop=stop, rts=machine.Pin(rts_pin) if rts_pin is not None else None,
                                cts=machine.Pin(cts_pin) if cts_pin is not None else None, txbuf=txbuf, rxbuf=rxbuf, timeout=timeout,
                                flow=0 if cts_pin is None else machine.UART.RTS | machine.UART.CTS)
        self._receive_task = asyncio.create_task(self._receive_thread())

    def close(self):
        super().close()
        self.__uart.deinit()

    def serial_update_port(self, **kwargs):
        ''' Reconfigure the serial port using the provided parameters '''
        self._logger.info(f"Updating serial data port: {kwargs}")
        self.__uart.init(**{key:value for key, value in kwargs.items() if value is not None})

    async def send_ack(self, message: MicrocomMsg, timeout: None | int = None, retry: None | int = None):
        ''' Send ACK messages as soon as the port is writable '''
        writer = asyncio.StreamWriter(self.__uart, {}) # type: ignore # pylint: disable=E1120
        async with self.__uart_write_lock:
            header, data, footer = MicrocomMsg.ack(message=message).serialize()
            self._logger.debug(f"Sending ACK: {header}")
            for x in (header, data, footer):
                writer.write(x)
            await writer.drain()

    async def send(self, message:MicrocomMsg, timeout:None|int=None, retry:None|int=None, wait_for_ack=True, wait_for_reply=False, data=None):
        ''' Function to send a message '''
        timeout = timeout if timeout is not None else self.send_timeout
        retry = retry if retry is not None else self.retry
        if data is not None:
            message.data = data # if data was passed to the function, fill it in
        writer = asyncio.StreamWriter(self.__uart, {}) # type: ignore # pylint: disable=E1120

        # lock the writer to block any other send functon
        async with self.__uart_send_lock:
            if wait_for_ack or wait_for_reply:
                self._last_sent_message = message
            message.send_time = time()
            header, data, footer = message.serialize()

            # lock the write to the UART
            async with self.__uart_write_lock:
                self._logger.debug(f"Sending data: {header} {data[0:100]}{'...' if len(data)> 100 else ''}")
                for x in (header, data, footer):
                    writer.write(x)
                await writer.drain()

            # if we aren't waiting for an ACK, then we don't need to retrans
            if not wait_for_ack:
                return

            while not message.ack_received() and time() < (message.send_time + timeout): # type: ignore
                await asyncio.sleep(.1)
            if message.ack_received(): # type: ignore
                if wait_for_ack and not wait_for_reply:
                    return
                # if we are waiting for a reply
                while not message.reply_received() and time() < (message.send_time + timeout): # type: ignore
                    await asyncio.sleep(.1)
                return # return regardless of if a reply was received

        # if we didn't get an ACK
        self._logger.warning(f"ID: {message.pkt_id}, Type: {message.msg_type}, Send timeout after {timeout} seconds waiting for ACK. Retry {message.retries} / {retry}") # type: ignore
        self._logger.debug(f"ID: {message.pkt_id}, Type: {message.msg_type}, Send timeout packet details: {bytes(message.data_bytes[0:100])}") # type: ignore
        if message.retries < retry: # type: ignore
            # increment the retry counter and call the send function again
            message.retries += 1 # type: ignore
            return await self.send(message=message, timeout=timeout, retry=retry, wait_for_ack=wait_for_ack, wait_for_reply=wait_for_reply)
        else:
            self._logger.error(f"ID: {message.pkt_id}, Type: {message.msg_type}, Retry exceeded with no ACK. Message discarded.") # type: ignore

    def send_hello(self):
        ''' Send a hello to initialize the connection with supported parameters '''

    async def _receive_thread(self):
        ''' background thread to receive '''
        self._logger.info("Starting receive thread...")
        stream_reader = asyncio.StreamReader(self.__uart) # pylint: disable=e1101
        data = b''
        while True:
            self._logger.debug("Serial waiting for input...")
            gc.collect()
            data = await stream_reader.readline()
            self._logger.debug(f"Received from UART: {data[0:100]}{'...' if len(data) > 100 else ''}")

            # if we have the serial end string match, process the message, otherwise append and continue
            if SER_START_HEADER in data and SER_END in data:
                try:
                    received_msg = MicrocomMsg.from_bytes(data)
                    self._logger.debug(f"Message Details: {str(received_msg)[0:100]}...")

                    # create a background task to run async to process the message
                    asyncio.create_task(self._receive_message(received_msg))

                except MicrocomException as e:
                    self._logger.error(f"Receive Error: {e.__class__.__name__}: {e}")
