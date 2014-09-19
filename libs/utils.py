#!/usr/bin/env python
# -*- coding:utf-8 -*-
import uuid
import time
import hashlib
import base64
import random
import string

def getPhotoName(prefix, ext):
	name = prefix + '_' + str(uuid.uuid1()).replace('-','') + "." + ext
	return name

def time2stamp(timestr, format_type='%Y-%m-%d'):
    return int(time.mktime(time.strptime(timestr, format_type)))


def stamp2time(stamp, format_type='%Y-%m-%d'):
    return time.strftime(format_type, time.localtime(stamp))

def encrypt(password):
	return hashlib.sha1(password).hexdigest()

def createSessionId():
	return encrypt(base64.b64encode(uuid.uuid4().bytes + uuid.uuid4().bytes))

def randomword(length=10):
   return ''.join(random.choice(string.lowercase) for i in range(length))

def randomnum(length=6):
	return ''.join(str(random.choice(range(1, 9))) if i == 0 else str(random.choice(range(0, 9)))\
	  	for i in range(length))

def decode_base64(data):
    """Decode base64, padding being optional.

    :param data: Base64 data as an ASCII byte string
    :returns: The decoded byte string.

    """
    missing_padding = 4 - len(data) % 4
    if missing_padding:
        data += b'='* missing_padding
    return base64.decodestring(data)

def getTableName(table, operId):
    return table + '_' + str(operId)