import hashlib


def supported_hash_algs() -> list:
    ''' Return a list of platform supported hashing algorithms '''
    return [x for x in dir(hashlib) if not x.startswith('_')]
