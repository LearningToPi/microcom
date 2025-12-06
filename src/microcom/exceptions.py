

class MicrocomException(Exception):
    ''' General Exception '''

class MicrocomRetryExceeded(MicrocomException):
    ''' Exception for retries exceeded '''

class MicrocomResetTimeout(MicrocomException):
    ''' Exception for timeout waiting for server to respond after a reset '''

class MicrocomChecksumFailed(MicrocomException):
    ''' Exception when the checksum of a file fails '''

class MicrocomFileNotFound(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomUploadFailed(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomRenameFailed(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomDeleteFailed(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomUpdateFailed(MicrocomException):
    ''' Exception when a Microcom update failes '''

class MicrocomUnsupported(MicrocomException):
    ''' Exception when a request for an unsupported option (IE Micropython update)'''

class MicrocomMicroPyUpdateFailed(MicrocomException):
    ''' Exception when a Micropython OTA update fails '''

class MicrcomDeserializeError(MicrocomException):
    ''' Exception when a message cannot be deserialized '''

class MicrocomStatsError(MicrocomException):
    ''' Exception when streaming stats are no longer being received '''

class MicrocomMessageDecodeError(MicrocomException):
    ''' Exception when decoding a message '''

class MicrocomBusNotInitialized(MicrocomException):
    ''' Exception when attempting to access a bus that has not been initialized '''

class MicrocomBusInitializationError(MicrocomException):
    ''' Exception when attempting to initialize a bus '''

class MicrocomBusDeviceNotFound(MicrocomException):
    ''' Exception when a requested device address is not found on a bus i.e. I2C '''

class MicrocomBusWriteFail(MicrocomException):
    ''' Exception when a write fails '''

class MicrocomBusInvaldParam(MicrocomException):
    ''' Exception when an invalid parameter is passed '''

class MicrocomPIOException(MicrocomException):
    ''' Exception when operating with a PIO on an RP2 '''

class MicrocomDisplayException(MicrocomException):
    ''' Exception using the display '''

class MicrocomFontException(MicrocomException):
    ''' Exception loading or using fonts '''

class MicrocomConnectFailed(MicrocomException):
    ''' Exception when a failure to connect occurs '''

class MicrocomDBusError(MicrocomException):
    ''' Exception in the DBus connection to get/disconnect devices '''
