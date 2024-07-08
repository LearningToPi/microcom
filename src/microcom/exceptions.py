

class MicrocomException(Exception):
    ''' General Exception '''

class MicrocomRetryExceeded(MicrocomException):
    ''' Exception for retries exceeded '''

class MicrocomChecksumFailed(MicrocomException):
    ''' Exception when the checksum of a file fails '''

class MicrocomFileNotFound(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomUploadFailed(MicrocomException):
    ''' Exception when a remote file requested was not found '''

class MicrocomUpdateFailed(MicrocomException):
    ''' Exception when a Microcom update failes '''

class MicrocomUnsupported(MicrocomException):
    ''' Exception when a request for an unsupported option (IE Micropython update)'''

class MicrocomMicroPyUpdateFailed(MicrocomException):
    ''' Exception when a Micropython OTA update fails '''

class MicrcomDeserializeError(MicrocomException):
    ''' Exception when a message cannot be deserialized '''