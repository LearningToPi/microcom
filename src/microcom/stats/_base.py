from time import time

class MicrocomStats:
    ''' Class to represent a single set of stats received from the server '''
    def __init__(self, data:dict):
        self.timestamp = time()
        self.data = data