from random import seed, randint, randbytes, random, choice
import string
import json
from microcom.msg._base import *

seed()

# create a file containing different message definitions for testing
microcom_messages = []

MC_VERSIONS = range(10)

TYPE_BOOL = [True, False]
TYPE_INT = [0, 10, 100] + [randint(0, 1000000000) for _ in range(10)]
TYPE_FLOAT = [0] + [random() for _ in range(10)]
TYPE_STR = [''.join(choice(string.printable) for _ in range(x)) for x in [randint(0,2000) for _ in range(10)]]
TYPE_BYTES = [(randbytes(x) for _ in range(x)) for x in [randint(0,2000) for _ in range(10)]]
TYPE_JSON = [{'str': "str", 'int': 123, 'float': 123.4, 'bool': True, 'None': None},
             [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]

for ver in MC_VERSIONS:
    for msg_type in SUPPORTED_MSG_TYPE:
        for direction in SUPPORTED_DIR:
            for data_type in DATA_SUPPORTED_FORMATS:
                # add a message with None as the data
                microcom_messages.append(MicrocomMsg(direction=direction, msg_type=msg_type, data=None))

                if data_type == bool:
                    for value in TYPE_BOOL:
                        microcom_messages.append(MicrocomMsg(direction=direction, msg_type=msg_type,
                                                             data=value))
                        # set the version in the message
                        microcom_messages[-1].__version = ver

with open('test_messages.json', 'wb') as output_file:
    for message in microcom_messages:
        output_file.write(message.serialize())
