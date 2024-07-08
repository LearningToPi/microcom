from json import dumps, loads
from json.decoder import JSONDecodeError
from hashlib import md5
from microcom.exceptions import *
from microcom.return_codes import *


DIR_OUT = 'out'
DIR_IN = 'in'
SUPPORTED_DIR = [DIR_IN, DIR_OUT]

MSG_TYPE_PING = 'ping'
MSG_TYPE_INFO = 'info'
MSG_TYPE_STATS = 'stats'
MSG_TYPE_STATS_ENABLE = 'stats-enable'
MSG_TYPE_STATS_DISABLE = 'stats-disable'
MSG_TYPE_LIST_DIR = 'list'
MSG_TYPE_CHECKSUM = 'checksum'
MSG_TYPE_GET = 'get'
MSG_TYPE_PUSH = 'push'
SUPPORTED_MSG_TYPE = (MSG_TYPE_PING, MSG_TYPE_INFO, MSG_TYPE_STATS, MSG_TYPE_STATS_ENABLE, MSG_TYPE_STATS_DISABLE, MSG_TYPE_LIST_DIR, MSG_TYPE_GET, MSG_TYPE_INFO, MSG_TYPE_CHECKSUM)

DATA_SUPPORTED_FORMATS = (str, bytes, dict, list)

SUPPORTED_RETURN_CODES = (MICROCOM_GENERAL_ERROR, MICROCOM_FILE_NOT_FOUND, MICROCOM_FILE_UPLOAD_FAIL)

class MicrocomMessage:
    ''' Represent a message that is sent to or received from a Micrcom Server '''
    __version = 0

    def __init__(self, direction=DIR_OUT, msg_type=MSG_TYPE_PING, data:str|bytes|dict|list|None=None, return_code:int|None=None):
        if direction not in SUPPORTED_DIR:
            raise MicrocomUnsupported(f"Unsupported direction '{direction}'. Must be from {SUPPORTED_DIR}")
        if msg_type not in SUPPORTED_MSG_TYPE:
            raise MicrocomUnsupported(f"Unsupported type '{msg_type}'. Must be from {SUPPORTED_MSG_TYPE}")
        if return_code not in SUPPORTED_RETURN_CODES:
            raise MicrocomUnsupported(f"Upsupported return code '{return_code}. Must be from {SUPPORTED_RETURN_CODES}")
        self.sent = False
        self.ack_received = False
        self.reply_received = False
        self.reply_message = None
        self.is_reply = False
        self.direction = direction
        self.type = msg_type
        self.return_code = return_code
        self.send_time = 0.0
        self.ack_time = 0.0
        self.reply_time = 0.0
        self.retries = 0

        if data is not None and not isinstance(data, DATA_SUPPORTED_FORMATS):
            raise MicrocomUnsupported(f"Data format '{type(data)} is not in supported list: {DATA_SUPPORTED_FORMATS}")

        # if bytes, convert first then load from json
        if isinstance(data, bytes):
            try:
                temp = data.decode('utf-8')
                self.data = loads(temp) if data is not None else None
            except JSONDecodeError as e:
                raise MicrcomDeserializeError(f"Error deserializing data: {e}") from e
        else:
            self.data = data

    def __bytes__(self) -> bytes:
        ''' return the message as bytes for transmission '''
        return self.__str__().encode()

    def __str__(self) -> str:
        ''' return the message as a json dumped string '''
        return dumps(self.__dict__())

    def __dict__(self) -> dict:
        ''' return the message as a dict '''
        return {
            'version': self.__version,
            'type': self.type,
            'dir': self.direction,
            'data': self.data,
            'return_code': self.return_code
            }

    def rtt(self) -> int:
        ''' Return the round trip time of the message in milliseconds '''
        if self.reply_time is None:
            raise MicrocomException('Unable to calulate RTT of message, no reply time found')
        return int(self.reply_time - self.send_time * 1000)
        

class MicrocomMessageFile(MicrocomMessage):
    ''' Represent a file to send or receive '''
    def __init__(self, data: str|bytes, path:str):
        super().__init__(direction=DIR_OUT, msg_type=MSG_TYPE_PUSH, data=data)
        self.checksum = str(md5(data.encode() if isinstance(data, str) else data))
        self.path = path
    
    def __dict__(self) -> dict:
        return super().__dict__().update(checksum=self.checksum)
