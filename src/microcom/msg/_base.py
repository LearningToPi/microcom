from json import dumps, loads
from random import randint
from binascii import crc32
from time import time
import re
#from microcom.crc import crc32
from microcom.exceptions import *

# regex works slightly different in Micropython vs CPython.  '.' doesn't include '\n' in CPython, but IS included in Micropython.
# For CPython we need to add re.DOTALL to the re.search(..., ..., re.DOTALL) to make sure we account for newlines that are present
# based on the binary data (i.e. timestamp or crc).  We can check for 'DOTALL' in the re object to determine if this is needed.
include_dotall = True if 'DOTALL' in dir(re) else False


DIR_SEND = 0
DIR_REPLY = 1
DIR_ACK = 2
DIR_REPLY_FRAG = 3 # final will be DIR_REPLY
DIR_SEND_FRAG = 5 # final will be DIR_SEND
DIR_IRQ = 4
SUPPORTED_DIR = (DIR_SEND, DIR_REPLY, DIR_ACK, DIR_REPLY_FRAG, DIR_IRQ, DIR_SEND_FRAG)
SUPPORTED_DIR_TEXT = ('send', 'reply', 'ack', 'frag_reply', 'irq', 'frag_send')

MSG_TYPE_INIT             = 0
MSG_TYPE_PING             = 1
MSG_TYPE_INFO             = 2
MSG_TYPE_STATS            = 3
MSG_TYPE_STATS_ENABLE     = 4
MSG_TYPE_STATS_DISABLE    = 5
MSG_TYPE_RESET            = 6
MSG_TYPE_LIST_DIR         = 7
MSG_TYPE_CHECKSUM         = 8
MSG_TYPE_READ_FILE        = 9
MSG_TYPE_WRITE_FILE       = 10
MSG_TYPE_RENAME_FILE      = 11
MSG_TYPE_DEL_FILE         = 12
MSG_TYPE_ERROR            = 13
MSG_TYPE_PIN_READ         = 14
MSG_TYPE_PIN_SET          = 15
MSG_TYPE_TIME_SYNC        = 16
MSG_TYPE_BUS_INIT         = 17
MSG_TYPE_BUS_CMD          = 18
MSG_TYPE_GET_VAR          = 19
MSG_TYPE_ASYNC_REPLY      = 20
MSG_TYPE_SET_VAR          = 21
MSG_TYPE_GET_BYTE_ARR     = 22
MSG_TYPE_CREATE_BYTE_ARR  = 23
MSG_TYPE_IMPORT_CLASS     = 24
MSG_TYPE_PWM_READ         = 25
MSG_TYPE_PWM_SET          = 26
MSG_TYPE_IRQ              = 27
MSG_TYPE_EXEC_FUNC        = 28
MSG_TYPE_PLATFORM_SPEC    = 29
MSG_TYPE_MONITOR_UPDATE   = 30
MSG_TYPE_NEOPIXEL_CMD     = 31
MSG_TYPE_DISPLAY_INIT     = 32
MSG_TYPE_DISPLAY_CMD      = 33

SUPPORTED_MSG_TYPE = (MSG_TYPE_INIT, MSG_TYPE_PING, MSG_TYPE_INFO, MSG_TYPE_STATS, MSG_TYPE_STATS_ENABLE, MSG_TYPE_STATS_DISABLE,
                      MSG_TYPE_RESET, MSG_TYPE_LIST_DIR, MSG_TYPE_CHECKSUM, MSG_TYPE_READ_FILE, MSG_TYPE_WRITE_FILE,
                      MSG_TYPE_RENAME_FILE, MSG_TYPE_DEL_FILE, MSG_TYPE_ERROR, MSG_TYPE_PIN_READ, MSG_TYPE_PIN_SET,
                      MSG_TYPE_TIME_SYNC, MSG_TYPE_BUS_INIT, MSG_TYPE_BUS_CMD, MSG_TYPE_GET_VAR, MSG_TYPE_ASYNC_REPLY,
                      MSG_TYPE_SET_VAR, MSG_TYPE_GET_BYTE_ARR, MSG_TYPE_CREATE_BYTE_ARR, MSG_TYPE_IMPORT_CLASS,
                      MSG_TYPE_PWM_READ, MSG_TYPE_PWM_SET, MSG_TYPE_IRQ, MSG_TYPE_EXEC_FUNC, MSG_TYPE_PLATFORM_SPEC,
                      MSG_TYPE_MONITOR_UPDATE, MSG_TYPE_NEOPIXEL_CMD, MSG_TYPE_DISPLAY_INIT, MSG_TYPE_DISPLAY_CMD)
SUPPORTED_MSG_TYPE_TEXT = ('init', 'ping', 'info', 'stats', 'stats_enable', 'stats_disable', 'reset', 'list_dir',
                           'checksum', 'read_file', 'write_file', 'rename_file', 'delete_file', 'error', 'pin_read',
                           'pin_write', 'time_sync', 'bus_init', 'bus_cmd', 'get_var', 'async_reply', 'set_var',
                           'get_byte_arr', 'create_byte_arr', 'import_class', 'pwm_read', 'pwm_set', 'irq', 'exec_func',
                           'platform_specific', 'monitor_update', 'neo_pixel_cmd', 'display_init', 'display_cmd')
SUPPORTED_STATS_TYPE = (MSG_TYPE_PIN_READ, MSG_TYPE_BUS_CMD, MSG_TYPE_GET_BYTE_ARR, MSG_TYPE_EXEC_FUNC)

DATA_NULL         = 99
DATA_BOOL         = 0
DATA_INT          = 1
DATA_FLOAT        = 2
DATA_STR          = 3
DATA_BYTES        = 4
DATA_JSON         = 5
DATA_BYTEARRAY    = 6

DATA_SUPPORTED_FORMATS = (bool, int, float, str, bytes, (dict, list, tuple), bytearray)
DATA_SUPPORTED_FORMATS_TEXT = ('bool', 'int', 'float', 'str', 'bytes', 'json', 'bytearray')

FILE_CHUNK_SIZE = 512

SER_START_HEADER = b'\x01\x01\x01'
SER_START_DATA   = b'\x02\x02\x02'
SER_END_DATA     = b'\x03\x03\x03'
SER_END          = b'\x04\x04\x04'
SER_SEPARATOR    = b'\x1d'

# '\n' substituted in data to prevent issues with readline() function in micropython not looping back quickly enough to read from the buffer
SER_SUBSTITUTIONS = {
    '\n': b'\x02\x0b\x02',
    '\x03': b'\x02\x0c\x02'
}

HASH_NONE = (0).to_bytes(1, 'big') #, signed=True)
HASH_MD5 = 1
HASH_SHA1 = 2
HASH_SHA256 = 3
HASH_SHA384 = 4
HASH_SHA512 = 5

CRYPTO_NONE = 0
SUPPORTED_CRYPTO = (CRYPTO_NONE,)
SUPPORTED_CRYPTO_TEXT = ('none',)

TEXT_ENCODING = 'utf-8'


MICROCOM_NO_ERROR = 0
MICROCOM_GENERAL_ERROR = 1
MICROCOM_FILE_ERROR = 2
MICROCOM_UNSUPPORTED = 3

SUPPORTED_RETURN_CODES = (MICROCOM_NO_ERROR, MICROCOM_GENERAL_ERROR, MICROCOM_FILE_ERROR, MICROCOM_UNSUPPORTED)
SUPPORTED_RETURN_CODES_TEXT = ('no_error', 'general_error', 'file_error', 'unsupported')

# NOTE: Micropython does not support {x,y} number of instances regex, so number of digits must be specified manually
MICROCOM_MSG_RE_HEADER = SER_START_HEADER + b'([^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d][^\x1d][^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'(..)' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'(..)' + SER_SEPARATOR + \
    b'(....)' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d])' + SER_SEPARATOR + \
    b'([^\x1d][^\x1d])' + SER_START_DATA
    #b'([\s\S]+)' + SER_END_DATA
MICROCOM_MSG_RE_CRC = SER_END_DATA + b'(.....)' + SER_END + b'\n$'
MICROCOM_MSG_RE_GROUP = ('ver', 'id', 'retries', 'crypto', 'msg_type', 'direction', 'frag_number', 'timestamp', 'return_code', 'data_type', 'data')

def rjust(value, min_width, fill_char='0'):
    ''' convert value to a string of a min width using the fill char '''
    ret_val = str(value)
    if len(ret_val) < min_width:
        ret_val = (fill_char * (min_width - len(ret_val))) + ret_val
    return ret_val



class MicrocomMsg:
    ''' Represent a message that is sent to or received from a Micrcom Server '''
    _version = 0

    def __init__(self, direction=DIR_SEND, msg_type=MSG_TYPE_PING, data:object=None, return_code:int=0, crypto:int=CRYPTO_NONE,
                 pkt_id:int|None=None, timestamp:int|None=None, retries:int=0, ip:str|None=None, port:int|None=None, frag_number:int=0,
                 data_type:int|None=None):
        if direction not in SUPPORTED_DIR:
            raise MicrocomUnsupported(f"Unsupported direction '{direction}'. Must be from {SUPPORTED_DIR}")
        if isinstance(msg_type, str):
            msg_type = SUPPORTED_MSG_TYPE[SUPPORTED_MSG_TYPE_TEXT.index(msg_type)] # added to allow text as a msg type instead of int
        if msg_type not in SUPPORTED_MSG_TYPE:
            raise MicrocomUnsupported(f"Unsupported type '{msg_type}'. Must be from {SUPPORTED_MSG_TYPE}")
        if return_code is not None and return_code not in SUPPORTED_RETURN_CODES:
            raise MicrocomUnsupported(f"Upsupported return code '{return_code}. Must be from {SUPPORTED_RETURN_CODES}")
        self.send_time = 0.0
        self.ack_time = 0.0
        self.received_time = 0.0
        self.reply_time = 0.0
        self.reply_msg = None
        self.final_msg = None # last message in a fragment
        self.frag_number = frag_number
        self.frag_time = 0.0
        self.retries = retries
        self.canceled = False
        self.timestamp = timestamp if timestamp is not None else int(time())
        self.ip, self.port = ip, port

        self.pkt_id = randint(0, 99999) if not isinstance(pkt_id, int) else pkt_id
        self.direction = direction
        self.msg_type = msg_type
        self.return_code = return_code
        self.crypto = crypto

        # Set the data type and store the data in a byte array
        self.data_type = DATA_NULL
        self._data = b'\x00'
        self._data_formatted = None

        # override the data type to account for fragments that get reset to bytes rather than retaining their original type
        if data_type is not None:
            self._data = data
            self.data_type = data_type
        else:
            for x in range(len(DATA_SUPPORTED_FORMATS)): # pylint: disable=C0200
                if isinstance(data, DATA_SUPPORTED_FORMATS[x]):
                    self.data_type = x
                    if not isinstance(data, bytearray):
                        self._data = data_to_bytes(data, x)
                    else:
                        self._data = data
                    break

        # if our data is not None and we are still set to DATA_NULL then raise an error
        if data is not None and self.data_type == DATA_NULL:
            raise MicrocomUnsupported(f"Data format '{type(data)} is not in supported list: {DATA_SUPPORTED_FORMATS}")

    @classmethod
    def ack(cls, message): # type: ignore # pylint: disable=E0602
        ''' Create an ACK for a message setting the ID and type to match the received message '''
        return cls(direction=DIR_ACK, msg_type=message.msg_type, crypto=message.crypto, pkt_id=message.pkt_id, ip=message.ip, port=message.port, frag_number=message.frag_number)

    @classmethod
    def reply(cls, message, data=None, direction=DIR_REPLY, return_code:int=0): # type: ignore # pylint: disable=E0602
        ''' Create a reply to the passed message '''
        data = message.data if message.msg_type == MSG_TYPE_PING else data
        return cls(pkt_id=message.pkt_id, msg_type=message.msg_type, direction=direction, data=data, crypto=message.crypto, return_code=return_code,
                   ip=message.ip, port=message.port)

    @classmethod
    def from_bytes(cls, data:bytes, source:tuple|None=None):
        ''' Create a message object from bytes data received '''
        if include_dotall: # CPython doesn't include \n in . unless re.DOTALL is added (see above)
            data_header_re = re.search(MICROCOM_MSG_RE_HEADER, data, re.DOTALL)
            data_crc_re = re.search(MICROCOM_MSG_RE_CRC, data, re.DOTALL)
        else: # Micropython includes \n in .
            data_header_re = re.search(MICROCOM_MSG_RE_HEADER, data)
            data_crc_re = re.search(MICROCOM_MSG_RE_CRC, data)
        if not data_header_re or not data_crc_re:
            raise MicrocomMessageDecodeError(f"Error occured decoding bytes: {data}")
        # check the CRC
        header_crc = crc32(data[data_header_re.start():data_header_re.end()])
        data_crc = crc32(data[data_header_re.end():data_crc_re.start()])
        crc = crc32((header_crc + data_crc).to_bytes(5, 'big')).to_bytes(5, 'big')
        if crc != data_crc_re.groups()[0]:
            raise MicrocomMessageDecodeError(f"CRC error decoding bytes: {data[0:100]}{'...' if len(data) > 100 else ''}, CRC: {crc}")

        # load message
        new_msg = cls(direction=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('direction')].decode('utf-8')),
                      retries=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('retries')].decode('utf-8')),
                      msg_type=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('msg_type')].decode('utf-8')),
                      timestamp=int.from_bytes(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('timestamp')], 'big'),
                      return_code=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('return_code')].decode('utf-8')),
                      crypto=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('crypto')].decode('utf-8')),
                      ip=source[0] if isinstance(source, tuple) and len(source) == 2 else None,
                      port=source[1] if isinstance(source, tuple) and len(source) == 2 else None,
                      frag_number=int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('frag_number')].decode('utf-8')))
        new_msg._version = int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('ver')].decode('utf-8'))
        new_msg.pkt_id = int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('id')].decode('utf-8'))
        new_msg.received_time = time()

        # perform the bytes substitutions and set the data type
        new_msg._data = data[data_header_re.end():data_crc_re.start()]
        for sub, value in SER_SUBSTITUTIONS.items():
            new_msg._data = new_msg._data.replace(value, sub.encode('utf-8'))
        new_msg.data_type = int(data_header_re.groups()[MICROCOM_MSG_RE_GROUP.index('data_type')].decode('utf-8'))

        return new_msg

    @property
    def data_bytes(self) -> bytes|memoryview:
        ''' Return the data in bytes format '''
        if isinstance(self._data, bytes):
            data = self._data
            for sub, value in SER_SUBSTITUTIONS.items():
                data = data.replace(sub.encode('utf-8'), value)
            return data
        elif isinstance(self._data, bytearray):
            return memoryview(self._data)
        raise ValueError(f"Internal data field must contain bytes or bytearray, has data of type: {type(self._data)}")
   
    @property
    def total_length(self) -> int:
        ''' return the total length of the packet '''
        header, data, footer = self.serialize()
        return len(header + data + footer)

    @property
    def data_length(self) -> int:
        ''' Return the number of bytes in the data field '''
        return len(self.data_bytes)

    @property
    def data(self) -> object:
        ''' Return the data in casted format (i.e. int, float, dict, list) '''
        # perform substitutions
        if self.data_type == DATA_NULL or self._data == b'\x00':
            return None

        if DATA_SUPPORTED_FORMATS_TEXT[self.data_type] == 'json' and self.frag_number == 0:
            return json_reverse_fixup(loads(self._data.decode('utf-8')))
        if DATA_SUPPORTED_FORMATS_TEXT[self.data_type] in ['bytes', 'bytearray'] or self.frag_number > 0:
            return self._data
        return DATA_SUPPORTED_FORMATS[self.data_type](self._data.decode('utf-8')) # type:ignore

    @data.setter
    def data(self, value):
        ''' Convert the data to bytes and save the datatype '''
        for x in range(len(DATA_SUPPORTED_FORMATS)): # pylint: disable=C0200
            if isinstance(value, DATA_SUPPORTED_FORMATS[x]):
                self.data_type = x
                self._data = data_to_bytes(value, x)
                break

    def append_data(self, data):
        ''' Convert the data to bytes and append it to the existing data '''
        for x in range(len(DATA_SUPPORTED_FORMATS)): # pylint: disable=C0200
            if isinstance(data, DATA_SUPPORTED_FORMATS[x]):
                self.data_type = x
                self._data += data_to_bytes(data, x)
                break

    def sent(self) -> bool:
        ''' Return True if the message has been sent '''
        return self.send_time > 0

    def ack_received(self) -> bool:
        ''' Return True if an ACK was received '''
        return self.ack_time > 0

    def reply_received(self) -> bool:
        ''' Return True if a reply was received '''
        return self.reply_time > 0 and self.final_msg is not None

    def ack_rtt(self) -> float:
        ''' Return the RTT for the ACK '''
        return self.ack_time - self.send_time if self.ack_received() else -1

    def rtt(self) -> float:
        ''' Return the round trip time of the message in milliseconds '''
        if self.reply_time is None:
            raise MicrocomException('Unable to calulate RTT of message, no reply time found')
        # if there are fragments, use the time from the last fragment
        return (self.reply_time if self.frag_time == 0 else self.frag_time) - self.send_time

    def serialize(self) -> tuple[bytes, bytes|memoryview, bytes]:
        ''' Return the Microcom message as bytes 
            Packet Format:
            \x01 [mc version] \x1d [id] \x1d [crypto_ver] \x1d [msg_type] \x1d [msg_dir] \x1d [data_type] \x02 [data] \x03 [crc32] \x04 '''
        ### The timestamp is in binary and can cause problems with the "readline()" if a '\n' is in the outputs
        #time_bytes = bytes.fromhex(hex(int(self.timestamp)).replace('0x', ''))
        ### Switch timestamp to convert to bytes from int
        time_bytes = int(time()).to_bytes(4, 'big')
        if b'\n' in time_bytes:
            time_bytes = b'\x00\x00\x00\x00'
        header = SER_START_HEADER + \
            bytes(rjust(str(self._version)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.pkt_id)[0:5], 5), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.retries)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(CRYPTO_NONE)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.msg_type)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.direction)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.frag_number)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            time_bytes + SER_SEPARATOR + \
            bytes(rjust(str(self.return_code)[0:2], 2), TEXT_ENCODING) + SER_SEPARATOR + \
            bytes(rjust(str(self.data_type)[0:2], 2), TEXT_ENCODING) + SER_START_DATA
        crc = (crc32((crc32(header) + crc32(self.data_bytes)).to_bytes(5, 'big'))).to_bytes(5, 'big')
        #crc.update(SER_END_DATA)
        #crc = bytes(rjust(str(crc.crc), 12), TEXT_ENCODING)
            #self.data_to_bytes() + SER_END_DATA
        return header, self.data_bytes, SER_END_DATA + crc + SER_END + b'\n'
        #return data + bytes(rjust(str(crc32(data)), 12), TEXT_ENCODING) + SER_END + b'\n'

    def frag(self, size:int):
        ''' Return an generator that will split the data into bytes based on the size provided '''
        data_length = self.data_length
        if data_length < size:
            yield self
        else:
            fragments = (data_length // size) + (1 if data_length % size > 0 else 0)
            data = self.data_bytes
            for x in range(1, fragments + 1):
                if x < fragments:
                    direction = DIR_REPLY_FRAG if self.direction == DIR_REPLY else DIR_SEND_FRAG
                else:
                    direction = self.direction
                # if we are on the last fagment, need to send as a reply rather than a reply fragment
                yield MicrocomMsg(direction=direction, msg_type=self.msg_type, return_code=self.return_code, crypto=self.crypto, pkt_id=self.pkt_id,
                                data=data[(x-1)*size:(x-1)*size+size], ip=self.ip, port=self.port, frag_number=x, data_type=self.data_type)

    def dict(self, text:bool=False) -> dict:
        ''' Return the packet as a dict object '''
        return {
            'ver': self._version,
            'id': self.pkt_id,
            'crypto': self.crypto if not text else SUPPORTED_CRYPTO_TEXT[self.crypto],
            'msg_type': self.msg_type if not text else SUPPORTED_MSG_TYPE_TEXT[self.msg_type],
            'direction': self.direction if not text else SUPPORTED_DIR_TEXT[self.direction],
            'fragment': self.frag_number,
            'data_type': self.data_type if not text else ('none' if self.data_type == 99 else DATA_SUPPORTED_FORMATS_TEXT[self.data_type]),
            'data': self.data,
            'sent': self.sent(),
            'ack_received': self.ack_received(),
            'ack_rtt': self.ack_rtt(),
            'retries': self.retries
        }

    def __iter__(self):
        yield from self.dict().items()

    def __str__(self) -> str:
        ''' Return the packet as a str '''
        return str(self.dict(text=True))
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({', '.join([str(key) + '=' + str(val) for key, val in self.dict().items()])})"

    def __eq__(self, other) -> bool:
        ''' Check if packets match '''
        if self._version == other._version and self.pkt_id == other.pkt_id and self.crypto == other.crypto and \
            self.msg_type == other.msg_type and self.direction == other.direction and self.data_type == other.data_type and \
            self.data == other.data:
            return True
        return False

    def is_ack(self, other) -> bool:
        ''' Returns True if the passed message is an ACK message of this message '''
        if self.pkt_id == other.pkt_id and other.direction == DIR_ACK and self.msg_type == other.msg_type:
            return True
        return False

    def is_reply(self, other) -> bool:
        ''' Returns True if the passed message is a reply to this message '''
        if self.pkt_id == other.pkt_id and self.direction == DIR_SEND and other.direction == DIR_REPLY and self.msg_type == other.msg_type:
            return True
        return False


def json_fixup(data):
    ''' Micropython json doesn't support "default" formatting '''
    if data is None:
        return data
    if isinstance(data, list):
        return_data = [] # use a new VAR so we don't change the original data
        for x in range(len(data)): # pylint: disable=C0200
            return_data.append(json_fixup(data[x])) # pylint: disable=E1128
        return return_data
    if isinstance(data, dict):
        return_data = {} # use a new VAR so we don't change the original data
        for key, value in data.items():
            return_data[key] = json_fixup(value)
        return return_data
    # return values that are ok
    if isinstance(data, (int, float, str, bool, tuple)):
        return data
    if isinstance(data, (bytes, bytearray)):
        return "bytes(" + data.hex() + ")"
    # convert and return everything else as a string
    return str(data)


def json_reverse_fixup(data):
    ''' Reverse the 'fixup' that was done for data sent in a json '''
    if data is None:
        return data
    if isinstance(data, list):
        for x in range(len(data)): # pylint: disable=C0200
            data[x] = json_reverse_fixup(data[x]) # pylint: disable=E1128
        return data
    if isinstance(data, dict):
        for key, value in data.items():
            data[key] = json_reverse_fixup(value)
        return data
    if isinstance(data, str) and data.startswith('bytes(') and data.endswith(')'):
        # reverse the bytes encoding to create a bytearray
        return bytearray.fromhex(data[6:-1])
    return data


def data_to_bytes(data, data_type) -> bytes:
    ''' return the data as bytes '''
    if data_type == DATA_NULL:
        data = b'\x00'
    #elif data_type == DATA_BOOL:
    #    data =  # type: ignore
    elif data_type in [DATA_INT, DATA_FLOAT, DATA_STR, DATA_BOOL]: # convert numbers to strings then send
        data = bytes(str(data), TEXT_ENCODING)
    elif data_type == DATA_BYTEARRAY:
        data = data # type: ignore
    elif data_type == DATA_BYTES:
        data = data # type: ignore
    elif data_type == DATA_JSON:
        data =  bytes(dumps(json_fixup(data)), TEXT_ENCODING) # type: ignore
    else:
        raise MicrocomUnsupported(f"Data type not supported: {data_type}")
    return data
