#!/usr/bin/env python

""" redis.py - A client for the Redis daemon.

History:
        - 20100129 Magic removal. Objects of type int, float, Decimal, str
                   are stored natively (not pickled). Everything else
                   (including unicode strings) is pickled.
                   -- moe@signalbeam.net
        - 20091207 Redis conenctions are now thread safe.  Thanks Aaron Raddon
        - 20091115 implemented __getitem__, __setitem__, __delitem__ to make
          gets/sets/deletes easy and pythonic. better py 2.6 decimal support
          (thanks Brent Pedersen)
        - 20091106 added SPOP, SCARD, SRANDMEMBER, SDIFF, SDIFFSTORE
          and all sorted set (Z*) commands (Andy McCurdy)
        - 20091106 added connection retry handling when the client gets disconnected,
          perhaps via the server timing the client out.
        - 20090603 fix missing errno import, add sunion and sunionstore commands,
          generalize shebang (Jochen Kupperschmidt)

"""

__author__ = "Ludovico Magnocavallo <ludo\x40qix\x2eit>"
__copyright__ = "Copyright 2009, Ludovico Magnocavallo"
__license__ = "MIT"
__version__ = "0.6.1"
__revision__ = "$LastChangedRevision: 175 $"[22:-2]
__date__ = "$LastChangedDate: 2009-03-17 16:15:55 +0100 (Mar, 17 Mar 2009) $"[18:-2]


# TODO: Redis._get_multi_response

import socket
import decimal
import errno
import threading
import warnings
import cPickle as pickle

# global threading manager
connections = threading.local()


class RedisError(Exception): pass
class ConnectionError(RedisError): pass
class ResponseError(RedisError): pass
class InvalidResponse(RedisError): pass
class InvalidData(RedisError): pass

class Redis(object):
    """The main Redis client.

    >>> from redis import Redis, ConnectionError, ResponseError
    >>> r = Redis(db=9)

    # If you put a str...
    >>> r['some_string'] = "Hello World!"

    # ...it comes back as str
    >>> r['some_string']
    'Hello World!'

    # If you put an int...
    >>> r['some_int'] = 24

    # ...it also comes back as str
    >>> r['some_int']
    '24'

    # Same for floats:
    >>> r['some_float'] = 24.0
    >>> r['some_float']
    '24.0'

    # Same for Decimals:
    >>> from decimal import Decimal
    >>> r['some_dec'] = Decimal( '24.5' )
    >>> r['some_dec']
    '24.5'

    # ANY other type of object will be serialized using pickle.
    # This means if you store an unicode object...
    >>> r['some_unicode_str'] = u"Hello W\xC3\xB6rld!"

    # ...it comes back as unicode
    >>> r['some_unicode_str']
    u'Hello W\\xc3\\xb6rld!'

    # Just like any other picklable object,
    # such as dicts...
    >>> r['some_dict'] = { 'foo': 'bar', 'batz':'futz' }
    >>> r['some_dict']
    {'foo': 'bar', 'batz': 'futz'}

    # ...or lists...
    >>> r['some_list'] = [ 'foo', 'bar', 'batz', 'futz' ]
    >>> r['some_list']
    ['foo', 'bar', 'batz', 'futz']

    # Well, you get the idea.

    # By the way, this is how non-existing keys behave.
    >>> print r.get('unknown_key') # r['unknown_key'] will raise KeyError
    None

    # There's lots more, see the doctests below for more examples.

    """
    
    def __init__(self, host=None, port=None, timeout=None, use_pickle=True,
            db=None, nodelay=None, retry_connection=True ):
        self.host = host or 'localhost'
        self.port = port or 6379
        self.db = db
        self.connection_key = '%s:%s:%s' % (self.host, self.port, self.db)
        if timeout:
            socket.setdefaulttimeout(timeout)
        self.nodelay = nodelay
        self.use_pickle = use_pickle
        if retry_connection:
            self.send_command = self._send_command_retry
        else:
            self.send_command = self._send_command
        self.retry_connection = retry_connection
        self._sock = None
        self._fp = None

    def _serialize( self, value ):

        if isinstance( value, str ) and chr(0) != value[0]:
            # str objects are stored natively if their
            # value doesn't start with a null-byte
            return str(value)
        elif isinstance( value, int ) or \
             isinstance( value, float ) or \
             isinstance( value, decimal.Decimal ):
            # numeric objects are always stored natively
            return str(value)

        # everything else, including unicode strings, gets pickled
        # (if pickle is enabled)
        if self.use_pickle:
            return chr(0) + pickle.dumps( value, pickle.HIGHEST_PROTOCOL )

        raise InvalidData( "Can not serialize object of type '%s', \
                            use_pickle is set to False" % ( type( value ) ) )

    def _deserialize( self, value ):
        # a leading null-byte is our marker for pickled values
        if 0 == ord( value[0] ) and self.use_pickle:
            try:
                return pickle.loads( value[1:] )
            except:
                raise ValueError( "This looks like a pickled value but isn't: %s" % ( value.encode('utf-8', 'replace') ) )

        # it's not a pickled object, so we return the plain string
        return value

    def _send_command(self, s, raw_response=False):
        """
        >>> r = Redis(db=9)
        >>> r.connect()
        >>> r._sock.close()
        >>> try:
        ...     r._send_command('pippo')
        ... except ConnectionError, e:
        ...     print e
        Error 9 while writing to socket. Bad file descriptor.
        >>>
        >>> 
        """
        self.connect()
        try:
            self._sock.sendall(s)
        except socket.error, e:
            if e.args[0] == 32:
                # broken pipe
                self.disconnect()
            raise ConnectionError("Error %s while writing to socket. %s." % tuple(e.args))
        return self._get_response( raw=raw_response )
            
    def _send_command_retry(self, s, raw_response=False):
        try:
            return self._send_command(s, raw_response=raw_response)
        except ConnectionError:
            self.disconnect()
            return self._send_command(s, raw_response=raw_response)
            
            
    def _read(self):
        try:
            line = self._fp.readline()
            return line
        except socket.error, e:
            if e.args and e.args[0] == errno.EAGAIN:
                return
            self.disconnect()
            raise ConnectionError("Error %s while reading from socket. %s." % tuple(e.args))
        if not data:
            self.disconnect()
            raise ConnectionError("Socket connection closed when reading.")
        return data
    
    def ping(self):
        """
        >>> r = Redis(db=9)
        >>> r.ping()
        'PONG'
        >>> 
        """
        return self.send_command('PING\r\n')
    
    def set(self, name, value, preserve=False, getset=False):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 'pippo')
        'OK'
        >>> r.set('a', u'pippo \u3235')
        'OK'
        >>> r.get('a')
        u'pippo \u3235'
        >>> r.set('b', 105.2 )
        'OK'
        >>> r.set('b', 'xxx', preserve=True)
        0
        >>> v = r.get('b')
        >>> v
        '105.2'
        >>> type( v )
        <type 'str'>
        >>> r.set('d', { 'a':10, 'b':'foo', 'c':0.5, 'd': {'x':'y' } } )
        'OK'
        >>> r.get('d')
        {'a': 10, 'c': 0.5, 'b': 'foo', 'd': {'x': 'y'}}
        """
        if getset: command = 'GETSET'
        elif preserve: command = 'SETNX'
        else: command = 'SET'
        value = self._serialize(value)
        return self.send_command('%s %s %s\r\n%s\r\n' % (
                command, name, len(value), value
            ))

    __setitem__ = set
    
    def get(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', u'pippo'), r.set('b', 15), r.set('c', ' \\r\\naaa\\nbbb\\r\\ncccc\\nddd\\r\\n '), r.set('d', '\\r\\n')
        ('OK', 'OK', 'OK', 'OK')
        >>> r.get('a')
        u'pippo'
        >>> r.get('b')
        '15'
        >>> r.get('d')
        '\\r\\n'
        >>> r.get('b')
        '15'
        >>> r.get('c')
        ' \\r\\naaa\\nbbb\\r\\ncccc\\nddd\\r\\n '
        >>> r.get('c')
        ' \\r\\naaa\\nbbb\\r\\ncccc\\nddd\\r\\n '
        >>> r.get('ajhsd')
        """
        return self.send_command('GET %s\r\n' % name)

    def __getitem__(self, name):
        val = self.get(name)
        if val is None:
            raise KeyError
        return val

    
    def getset(self, name, value):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 'pippo')
        'OK'
        >>> r.getset('a', 2)
        'pippo'
        >>> 
        """
        return self.set(name, value, getset=True)
        
    def mget(self, *args):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 'pippo'), r.set('b', 15), r.set('c', '\\r\\naaa\\nbbb\\r\\ncccc\\nddd\\r\\n'), r.set('d', '\\r\\n')
        ('OK', 'OK', 'OK', 'OK')
        >>> r.mget('a', 'b', 'c', 'd')
        ['pippo', '15', '\\r\\naaa\\nbbb\\r\\ncccc\\nddd\\r\\n', '\\r\\n']
        >>> 
        """
        return self.send_command('MGET %s\r\n' % ' '.join(args))
    
    def incr(self, name, amount=1):
        """
        >>> r = Redis(db=9)
        >>> r.delete('a')
        1
        >>> r.incr('a')
        1
        >>> r.incr('a')
        2
        >>> r.incr('a', 2)
        4
        >>>
        """
        if amount == 1:
            return self.send_command('INCR %s\r\n' % name)
        else:
            return self.send_command('INCRBY %s %s\r\n' % (name, amount))

    def decr(self, name, amount=1):
        """
        >>> r = Redis(db=9)
        >>> if r.get('a'):
        ...     r.delete('a')
        ... else:
        ...     print 1
        1
        >>> r.decr('a')
        -1
        >>> r.decr('a')
        -2
        >>> r.decr('a', 5)
        -7
        >>> 
        """
        if amount == 1:
            return self.send_command('DECR %s\r\n' % name)
        else:
            return self.send_command('DECRBY %s %s\r\n' % (name, amount))
    
    def exists(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.exists('dsjhfksjdhfkdsjfh')
        0
        >>> r.set('a', 'a')
        'OK'
        >>> r.exists('a')
        1
        >>>
        """
        return self.send_command('EXISTS %s\r\n' % name)

    def delete(self, *args):
        """
        >>> r = Redis(db=9)
        >>> r.delete('dsjhfksjdhfkdsjfh')
        0
        >>> r.set('a', 'a')
        'OK'
        >>> r.delete('a')
        1
        >>> r.exists('a')
        0
        >>> r.delete('a')
        0
        >>> r.set('a', 'a')
        'OK'
        >>> r.set('b', 'b')
        'OK'
        >>> r.delete('a', 'b')
        2
        >>> r.exists('a')
        0
        >>> r.exists('b')
        0
        >>> 
        """
        return self.send_command('DEL %s\r\n' % ' '.join(args))

    __delitem__ = delete

    def get_type(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 3)
        'OK'
        >>> r.get_type('a')
        'string'
        >>> r.get_type('zzz')
        >>> 
        """
        res = self.send_command('TYPE %s\r\n' % name)
        if res == 'none':
            return
        return res

    def keys(self, pattern):
        """
        >>> r = Redis(db=9)
        >>> r.flush()
        'OK'
        >>> r.set('a', 'a')
        'OK'
        >>> r.keys('a*')
        ['a']
        >>> r.set('a2', 'a')
        'OK'
        >>> keys = r.keys('a*')
        >>> 'a' in keys
        True
        >>> 'a2' in keys
        True
        >>> r.delete('a2')
        1
        >>> r.keys('sjdfhskjh*')
        []
        >>>
        """
        return self.send_command('KEYS %s\r\n' % pattern, raw_response=True).split()
    
    def randomkey(self):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 'a')
        'OK'
        >>> isinstance(r.randomkey(), str)
        True
        >>> 
        """
        #raise NotImplementedError("Implemented but buggy, do not use.")
        return self.send_command('RANDOMKEY\r\n')
    
    def rename(self, src, dst, preserve=False):
        """
        >>> r = Redis(db=9)
        >>> try:
        ...     r.rename('a', 'a')
        ... except ResponseError, e:
        ...     print e
        source and destination objects are the same
        >>> r.rename('a', 'b')
        'OK'
        >>> try:
        ...     r.rename('a', 'b')
        ... except ResponseError, e:
        ...     print e
        no such key
        >>> r.set('a', 1)
        'OK'
        >>> r.rename('b', 'a', preserve=True)
        0
        >>> 
        """
        if preserve:
            return self.send_command('RENAMENX %s %s\r\n' % (src, dst))
        else:
            return self.send_command('RENAME %s %s\r\n' % (src, dst))
        
    def dbsize(self):
        """
        >>> r = Redis(db=9)
        >>> type(r.dbsize())
        <type 'int'>
        >>> 
        """
        return self.send_command('DBSIZE\r\n')
    
    def ttl(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.ttl('a')
        -1
        >>> r.expire('a', 10)
        1
        >>> r.ttl('a')
        10
        >>> r.expire('a', 0)
        0
        >>> 
        """
        return self.send_command('TTL %s\r\n' % name)
    
    def expire(self, name, time):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 1)
        'OK'
        >>> r.expire('a', 1)
        1
        >>> r.expire('zzzzz', 1)
        0
        >>> 
        """
        return self.send_command('EXPIRE %s %s\r\n' % (name, time))
    
    def push(self, name, value, head=False, **kwargs):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.push('l', 'a')
        'OK'
        >>> r.set('a', 'a')
        'OK'
        >>> try:
        ...     r.push('a', 'a')
        ... except ResponseError, e:
        ...     print e
        Operation against a key holding the wrong kind of value
        >>> 
        """
        if "tail" in kwargs:
            tail = kwargs.pop("tail")
            warnings.warn(DeprecationWarning(
                "tail argument of push is deprecated, use head=False"))
            head = not tail
        if kwargs:
            raise TypeError(
                "push() got an unexpected keyword argument: %s" % (
                    kwargs.keys()[0]))

        value = self._serialize(value)
        command = 'RPUSH'
        if head: command = 'LPUSH'
        return self.send_command('%s %s %s\r\n%s\r\n' \
            % (command, name, len(value), value))

    def llen(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.push('l', 'a')
        'OK'
        >>> r.llen('l')
        1
        >>> r.push('l', 'a')
        'OK'
        >>> r.llen('l')
        2
        >>> 
        """
        return self.send_command('LLEN %s\r\n' % name)

    def lrange(self, name, start, end):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.lrange('l', 0, 1)
        []
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.lrange('l', 0, 1)
        ['aaa']
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.lrange('l', 0, 0)
        ['aaa']
        >>> r.lrange('l', 0, 1)
        ['aaa', 'bbb']
        >>> r.lrange('l', -1, 0)
        []
        >>> r.lrange('l', -1, -1)
        ['bbb']
        >>> 
        """
        return self.send_command('LRANGE %s %s %s\r\n' % (name, start, end))
        
    def ltrim(self, name, start, end):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.ltrim('l', 0, 1)
        'OK'
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.push('l', 'ccc')
        'OK'
        >>> r.ltrim('l', 0, 1)
        'OK'
        >>> r.llen('l')
        2
        >>> r.ltrim('l', 99, 95)
        'OK'
        >>> r.llen('l')
        0
        >>> 
        """
        return self.send_command('LTRIM %s %s %s\r\n' % (name, start, end))
    
    def lindex(self, name, index):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('l')
        >>> r.lindex('l', 0)
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.lindex('l', 0)
        'aaa'
        >>> r.lindex('l', 2)
        >>> r.push('l', 'ccc')
        'OK'
        >>> r.lindex('l', 1)
        'ccc'
        >>> r.lindex('l', -1)
        'ccc'
        >>> 
        """
        return self.send_command('LINDEX %s %s\r\n' % (name, index))

    def pop(self, name, tail=False):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.pop('l')
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.pop('l')
        'aaa'
        >>> r.pop('l')
        'bbb'
        >>> r.pop('l')
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.pop('l', tail=True)
        'bbb'
        >>> r.pop('l')
        'aaa'
        >>> r.pop('l')
        >>>
        """
        command = 'LPOP'
        if tail: command = 'RPOP'
        return self.send_command('%s %s\r\n' % (command, name))

    def poppush(self, src, dst):
        """
        >>> r = Redis(db=9)
        >>> r.delete('lsource')
        0
        >>> r.delete('ldestination')
        0
        >>> r.push('lsource', 'one', head=True)
        'OK'
        >>> r.push('lsource', 'two', head=True)
        'OK'
        >>> r.push('lsource', 'three', head=True)
        'OK'
        >>> r.poppush('lsource', 'ldestination')
        'one'
        >>> r.lrange('lsource', 0, -1)
        ['three', 'two']
        >>> r.lrange('ldestination', 0, -1)
        ['one']
        >>> r.poppush('lsource', 'ldestination')
        'two'
        >>> r.lrange('lsource', 0, -1)
        ['three']
        >>> r.lrange('ldestination', 0, -1)
        ['two', 'one']
        """
#        return self.send_command('RPOPLPUSH %s %s\r\n%s\r\n' % (
#            src, len(dst), dst
#        ))
        # This appears to no longer be a bulk command in redis 1.1+
        return self.send_command('RPOPLPUSH %s %s\r\n' % (
            src, dst
        ))

    
    def lset(self, name, index, value):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> try:
        ...     r.lset('l', 0, 'a')
        ... except ResponseError, e:
        ...     print e
        no such key
        >>> r.push('l', 'aaa')
        'OK'
        >>> try:
        ...     r.lset('l', 1, 'a')
        ... except ResponseError, e:
        ...     print e
        index out of range
        >>> r.lset('l', 0, 'bbb')
        'OK'
        >>> r.lrange('l', 0, 1)
        ['bbb']
        >>> 
        """
        value = self._serialize(value)
        return self.send_command('LSET %s %s %s\r\n%s\r\n' % (
            name, index, len(value), value
        ))
    
    def lrem(self, name, value, num=0):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.lrem('l', 'aaa')
        2
        >>> r.lrange('l', 0, 10)
        ['bbb']
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.lrem('l', 'aaa', 1)
        1
        >>> r.lrem('l', 'aaa', 1)
        1
        >>> r.lrem('l', 'aaa', 1)
        0
        >>> 
        """
        value = self._serialize(value)
        return self.send_command('LREM %s %s %s\r\n%s\r\n' % (
            name, num, len(value), value
        ))
    
    def sort(self, name, by=None, get=None, start=None, num=None, desc=False, alpha=False):
        """
        >>> r = Redis(db=9)
        >>> r.delete('l')
        1
        >>> r.push('l', 'ccc')
        'OK'
        >>> r.push('l', 'aaa')
        'OK'
        >>> r.push('l', 'ddd')
        'OK'
        >>> r.push('l', 'bbb')
        'OK'
        >>> r.sort('l', alpha=True)
        ['aaa', 'bbb', 'ccc', 'ddd']
        >>> r.delete('l')
        1
        >>> for i in range(1, 5):
        ...     res = r.push('l', 1.0 / i)
        >>> r.sort('l')
        ['0.25', '0.333333333333', '0.5', '1.0']
        >>> r.sort('l', desc=True)
        ['1.0', '0.5', '0.333333333333', '0.25']
        >>> r.sort('l', desc=True, start=2, num=1)
        ['0.333333333333']
        >>> r.set('weight_0.5', 10)
        'OK'
        >>> r.sort('l', desc=True, by='weight_*')
        ['0.5', '1.0', '0.333333333333', '0.25']
        >>> for i in r.sort('l', desc=True):
        ...     res = r.set('test_%s' % i, 100 - float(i))
        >>> r.sort('l', desc=True, get='test_*')
        ['99.0', '99.5', '99.6666666667', '99.75']
        >>> r.sort('l', desc=True, by='weight_*', get='test_*')
        ['99.5', '99.0', '99.6666666667', '99.75']
        >>> r.sort('l', desc=True, by='weight_*', get='missing_*')
        [None, None, None, None]
        >>> 
        """
        stmt = ['SORT', name]
        if by:
            stmt.append("BY %s" % by)
        if start is not None and num is not None:
            stmt.append("LIMIT %s %s" % (start, num))
        if get is None:
            pass
        elif isinstance(get, basestring):
            stmt.append("GET %s" % get)
        elif isinstance(get, list) or isinstance(get, tuple):
            for g in get:
                stmt.append("GET %s" % g)
        else:
            raise RedisError("Invalid parameter 'get' for Redis sort")
        if desc:
            stmt.append("DESC")
        if alpha:
            stmt.append("ALPHA")
        return self.send_command(' '.join(stmt + ["\r\n"]))
    
    def sadd(self, name, value):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s')
        >>> r.sadd('s', 'a')
        1
        >>> r.sadd('s', 'b')
        1
        >>> r.sismember( 's', 'a' )
        1
        >>> r.sismember( 's', 'b' )
        1
        """
        value = self._serialize(value)
        return self.send_command('SADD %s %s\r\n%s\r\n' % (
            name, len(value), value
        ))
        
    def srem(self, name, value):
        """
        >>> r = Redis(db=9)
        >>> r.delete('s')
        1
        >>> r.srem('s', 'aaa')
        0
        >>> r.sadd('s', 'b')
        1
        >>> r.srem('s', 'b')
        1
        >>> r.sismember('s', 'b')
        0
        >>> 
        """
        value = self._serialize(value)
        return self.send_command('SREM %s %s\r\n%s\r\n' % (
            name, len(value), value
        ))
        
    def spop(self, name):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s')
        >>> r.sadd('s', 'a')
        1
        >>> r.spop('s')
        'a'
        """
        return self.send_command('SPOP %s\r\n' % name)
        
    def smove(self, src, dst, member):
        """
        >>> r = Redis(db=9)
        >>> _ = r.delete('s1')
        >>> _ = r.delete('s2')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s2', 'b')
        1
        >>> r.sismember('s1', 'a')
        1
        >>> r.sismember('s2', 'b')
        1
        >>> r.smove('s1', 's2', 'a')
        1
        >>> src_set = r.smembers('s1')
        >>> 'a' in src_set
        False
        >>> dst_set = r.smembers('s2')
        >>> 'a' in dst_set
        True
        >>> 'b' in dst_set
        True
        """
        member = self._serialize(member)
        return self.send_command('SMOVE %s %s %s\r\n%s\r\n' % (
            src, dst, len(member), member
        ), raw_response=True )
 
    
    def scard(self, name):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s')
        >>> r.sadd('s', 'a')
        1
        >>> r.scard('s')
        1
        """
        return self.send_command('SCARD %s\r\n' % name)
    
    def sismember(self, name, value):
        """
        >>> r = Redis(db=9)
        >>> r.delete('s')
        1
        >>> r.sismember('s', 'b')
        0
        >>> r.sadd('s', 'a')
        1
        >>> r.sismember('s', 'b')
        0
        >>> r.sismember('s', 'a')
        1
        >>>
        """
        value = self._serialize(value)
        return self.send_command('SISMEMBER %s %s\r\n%s\r\n' % (
            name, len(value), value
        ))
    
    def sinter(self, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s2', 'a')
        1
        >>> r.sadd('s3', 'b')
        1
        >>> try:
        ...     r.sinter()
        ... except ResponseError, e:
        ...     print e
        wrong number of arguments for 'sinter' command
        >>> try:
        ...     r.sinter('l')
        ... except ResponseError, e:
        ...     print e
        Operation against a key holding the wrong kind of value
        >>> r.sinter('s1', 's2', 's3')
        set([])
        >>> r.sinter('s1', 's2')
        set(['a'])
        >>> 
        """
        return set(self.send_command('SINTER %s\r\n' % ' '.join(args)))
    
    def sinterstore(self, dest, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s2', 'a')
        1
        >>> r.sadd('s3', 'b')
        1
        >>> r.sinterstore('s_s', 's1', 's2', 's3')
        0
        >>> r.sinterstore('s_s', 's1', 's2')
        1
        >>> r.smembers('s_s')
        set(['a'])
        >>> 
        """
        return self.send_command('SINTERSTORE %s %s\r\n' % (dest, ' '.join(args)))

    def smembers(self, name):
        """
        >>> r = Redis(db=9)
        >>> r.delete('s')
        1
        >>> r.sadd('s', 'a')
        1
        >>> r.sadd('s', 'b')
        1
        >>> try:
        ...     r.smembers('l')
        ... except ResponseError, e:
        ...     print e
        Operation against a key holding the wrong kind of value
        >>> r.smembers('s')
        set(['a', 'b'])
        >>> 
        """
        return set(self.send_command('SMEMBERS %s\r\n' % name))

    def sunion(self, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s2', 'a')
        1
        >>> r.sadd('s3', 'b')
        1
        >>> r.sunion('s1', 's2', 's3')
        set(['a', 'b'])
        >>> r.sadd('s2', 'c')
        1
        >>> r.sunion('s1', 's2', 's3')
        set(['a', 'c', 'b'])
        >>> 
        """
        return set(self.send_command('SUNION %s\r\n' % ' '.join(args)))

    def sunionstore(self, dest, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s2', 'a')
        1
        >>> r.sadd('s3', 'b')
        1
        >>> r.sunionstore('s4', 's1', 's2', 's3')
        2
        >>> r.smembers('s4')
        set(['a', 'b'])
        >>> 
        """
        return self.send_command('SUNIONSTORE %s %s\r\n' % (dest, ' '.join(args)))
        
    def sdiff(self, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> r.sadd('s1', 'x')
        1
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s1', 'b')
        1
        >>> r.sadd('s1', 'c')
        1
        >>> r.sadd('s2', 'c')
        1
        >>> r.sadd('s3', 'a')
        1
        >>> r.sadd('s3', 'd')
        1
        >>> diff = r.sdiff('s1', 's2', 's3')
        >>> 'x' in diff
        True
        >>> 'b' in diff
        True
        """
        return set(self.send_command('SDIFF %s\r\n' % ' '.join(args)))
        
    def sdiffstore(self, dest, *args):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> res = r.delete('s2')
        >>> res = r.delete('s3')
        >>> res = r.delete('s4')
        >>> r.sadd('s1', 'x')
        1
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s1', 'b')
        1
        >>> r.sadd('s1', 'c')
        1
        >>> r.sadd('s2', 'c')
        1
        >>> r.sadd('s3', 'a')
        1
        >>> r.sadd('s3', 'd')
        1
        >>> r.sdiffstore('s4', 's1', 's2', 's3')
        2
        >>> diff = r.smembers('s4')
        >>> 'x' in diff
        True
        >>> 'b' in diff
        True
        """
        return self.send_command('SDIFFSTORE %s %s\r\n' % (dest, ' '.join(args)))
        
    def srandmember(self, key):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('s1')
        >>> r.sadd('s1', 'a')
        1
        >>> r.sadd('s1', 'b')
        1
        >>> rand = r.srandmember('s1')
        >>> rand in ['a', 'b']
        True
        """
        return self.send_command('SRANDMEMBER %s\r\n' % key)
        
    def zadd(self, key, member, score):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> r.zadd('z1', 'abc', 5)
        1
        >>> r.zadd('z1', 'def', 3)
        1
        >>> r.zadd('z1', 'abc', 2)
        0
        """
        member = self._serialize(member)
        return self.send_command('ZADD %s %s %s\r\n%s\r\n' % (
            key, score, len(member), member
        ))
        
    def zrem(self, key, member):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> r.zadd('z1', 'abc', 5)
        1
        >>> r.zrem('z1', 'abc')
        1
        >>> r.zrem('z1', 'def')
        0
        """
        member = self._serialize(member)
        return self.send_command('ZREM %s %s\r\n%s\r\n' % (
            key, len(member), member
        ))
        
    def zrange(self, key, start, end, desc=False):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> r.zadd('z1', 'a', 5)
        1
        >>> r.zadd('z1', 'b', 2)
        1
        >>> r.zadd('z1', 'c', 7)
        1
        >>> r.zrange('z1', 0, 2)
        ['b', 'a', 'c']
        >>> r.zrange('z1', 0, 1)
        ['b', 'a']
        >>> r.zrange('z1', 0, 2, desc=True)
        ['c', 'a', 'b']
        >>> r.zrange('z1', 0, 1, desc=True)
        ['c', 'a']
        >>>
        """
        command = 'ZRANGE'
        if desc: command = 'ZREVRANGE'
        return self.send_command('%s %s %s %s\r\n' \
            % (command, key, start, end))

    def zrangebyscore(self, key, min, max, offset=None, count=None):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> r.zadd('z1', 'a', 5)
        1
        >>> r.zadd('z1', 'b', 2)
        1
        >>> r.zadd('z1', 'c', 7)
        1
        >>> r.zadd('z1', 'd', 10)
        1
        >>> r.zrangebyscore('z1', 5, 7)
        ['a', 'c']
        >>> r.zadd('z1', 'e', 8)
        1
        >>> r.zrangebyscore('z1', 5, 8, offset=0, count=2)
        ['a', 'c']
        >>> r.zrangebyscore('z1', 5, 8, offset=1, count=2)
        ['c', 'e']
        """
        if offset is not None and count is not None:
            limit = " LIMIT %d %d" % (offset, count)
        else:
            limit = ""
        
        return self.send_command('ZRANGEBYSCORE %s %s %s%s\r\n' % (
            key, min, max, limit
        ))
        
    def zcard(self, key):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> res = r.delete('z2')
        >>> r.zadd('z1', 'a', 5)
        1
        >>> r.zadd('z1', 'b', 2)
        1
        >>> r.zadd('z1', 'c', 7)
        1
        >>> r.zcard('z1')
        3
        >>> r.zcard('z2')
        0
        """
        return self.send_command('ZCARD %s\r\n' % key)
        
    def zscore(self, key, member):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> res = r.delete('z2')
        >>> r.zadd('z1', 'a', 5)
        1
        >>> r.zscore('z1', 'a')
        '5'
        >>> r.zscore('z1', 'b')
        >>> r.zscore('z2', 'a')
        """
        member = self._serialize(member)
        return self.send_command('ZSCORE %s %s\r\n%s\r\n' % (
            key, len(member), member
        ))
        
    def zincr(self, key, member, value=1):
        """
        >>> r = Redis(db=9)
        >>> res = r.delete('z1')
        >>> r.zincr('z1', 'k1')
        '1'
        >>> r.zincr('z1', 'k1', 5)
        '6'
        >>> r.zincr('z1', 'k1', -4)
        '2'
        """
        member = self._serialize(member)
        return self.send_command('ZINCRBY %s %s %s\r\n%s\r\n' % (
            key, value, len(member), member
        ))
        
    def select(self, db):
        """
        >>> r = Redis(db=9)
        >>> r.delete('a')
        1
        >>> r.select(10)
        'OK'
        >>> r.set('a', 1)
        'OK'
        >>> r.select(9)
        'OK'
        >>> r.get('a')
        >>> 
        """
        return self.send_command('SELECT %s\r\n' % db)
    
    def move(self, name, db):
        """
        >>> r = Redis(db=9)
        >>> r.set('a', 'a')
        'OK'
        >>> r.select(10)
        'OK'
        >>> if r.get('a'):
        ...     r.delete('a')
        ... else:
        ...     print 1
        1
        >>> r.select(9)
        'OK'
        >>> r.move('a', 10)
        1
        >>> r.get('a')
        >>> r.select(10)
        'OK'
        >>> r.get('a')
        'a'
        >>> r.select(9)
        'OK'
        >>> 
        """
        return self.send_command('MOVE %s %s\r\n' % (name, db))
    
    def save(self, background=False):
        """
        >>> r = Redis(db=9)
        >>> r.save()
        'OK'
        >>> r.save(background=True)
        'Background saving started'
        >>> 
        """
        if background:
            return self.send_command('BGSAVE\r\n')
        else:
            return self.send_command('SAVE\r\n')
        
    def shutdown(self):
        """Close all client connections, dump database and quit the server."""
        try:  
            self.send_command('SHUTDOWN\r\n')
        except ConnectionError:
            return

    def lastsave(self):
        """
        >>> import time
        >>> r = Redis(db=9)
        >>> t = int(time.time())
        >>> r.save()
        'OK'
        >>> r.lastsave() >= t
        True
        >>> 
        """
        return self.send_command('LASTSAVE\r\n')
    
    def flush(self, all_dbs=False):
        """
        >>> r = Redis(db=9)
        >>> r.flush()
        'OK'
        >>> # r.flush(all_dbs=True)
        >>>
        """
        command = 'FLUSHDB'
        if all_dbs: command = 'FLUSHALL'
        return self.send_command('%s\r\n' % command)

    def info(self):
        """
        >>> r = Redis(db=9)
        >>> info = r.info()
        >>> info and isinstance(info, dict)
        True
        >>> isinstance(info.get('connected_clients'), int)
        True
        >>> 
        """
        info = dict()
        for l in self.send_command('INFO\r\n', raw_response=True).split('\r\n'):
            if not l:
                continue
            k, v = l.split(':', 1)
            if v.isdigit():
                info[k] = int(v)
            else:
                info[k] = v
        return info
    
    def auth(self, passwd):
        return self.send_command('AUTH %s\r\n' % passwd)
    
    def _get_response( self, raw=False ):
        data = self._read().strip()
        if not data:
            self.disconnect()
            raise ConnectionError("Socket closed on remote end")
        c = data[0]

        # Error reply
        if c == '-':
            err = data[1:]
            if data[:5] == '-ERR ':
                err = data[5:]
            raise ResponseError(err)

        # Single line reply
        if c == '+':
            return data[1:]

        # Multi bulk reply
        if c == '*':
            try:
                num = int(data[1:])
            except (TypeError, ValueError):
                raise InvalidResponse("Cannot convert multi-response header '%s' to integer" % data)
            return [self._get_value(raw=raw) for i in range(num)]
        return self._get_value(data=data, raw=raw)
    
    def _get_value(self, data=None, raw=False):
        data = data or self._read().strip()
        if data == '$-1':
            return None

        c, i = data[0], data[1:]

        if c == ':':
            return int(i)
        if c != '$':
            raise InvalidResponse("Unknown response prefix for '%s'" % data)
        i = int(i)  # at this point i is definitely our content length
        buf = []
        while i >= 0:
            data = self._read()
            i -= len(data)
            buf.append(data)

        data = ''.join(buf)[:-2]
        if raw:
            return data

        return self._deserialize( data )
    
    def disconnect(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except socket.error:
                pass
        self._sock = None
        self._fp = None
        if hasattr(connections, self.connection_key):
            delattr(connections, self.connection_key)
    
    def connect(self):
        """
        >>> import socket
        >>> r = Redis(db=9)
        >>> r.connect()
        >>> isinstance(r._sock, socket.socket)
        True
        >>> r.disconnect()
        >>> 
        """
        if isinstance(self._sock, socket.socket):
            return
        try:
            # connection pooling to thread local
            if not hasattr(connections, self.connection_key):
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.host, self.port))
                if self.nodelay is not None:
                    sock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, self.nodelay)
                setattr(connections, self.connection_key, sock)
            sock = getattr(connections, self.connection_key)
        except socket.error, e:
            raise ConnectionError("Error %s connecting to %s:%s. %s." % (e.args[0], self.host, self.port, e.args[1]))
        else:
            # no exceptions
            self._sock = sock
            self._fp = self._sock.makefile('r')
            if self.db:
                self.select(self.db)
    

if __name__ == '__main__':

    # hack to make doctests pass in 2.6
    decimal.Decimal.__repr__ = lambda self: 'Decimal("%s")' % str(self)
    import doctest
    doctest.testmod()
    
