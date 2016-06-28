from hiredis._cffi._hiredis import ffi, lib


class HiredisError(Exception):
    pass


class ProtocolError(HiredisError):
    pass


class ReplyError(HiredisError):
    pass


class _GlobalHandles(object):
    def __init__(self):
        self._handles = set()

    def new(self, obj):
        obj_id = ffi.new_handle(obj)
        self._handles.add(obj_id)
        return obj_id

    def get(self, obj_id):
        return ffi.from_handle(obj_id)

    def free(self, obj_id):
        obj = ffi.from_handle(obj_id)
        self._handles.discard(obj_id)
        return obj


_global_handles = _GlobalHandles()


def _parentize(task, obj):
    if task and task.parent:
        parent = _global_handles.get(task.parent.obj)
        assert isinstance(parent, list)
        parent[task.idx] = (obj)


class Reader(object):
    "Hiredis protocol reader"

    def __init__(self, protocolError=None, replyError=None, encoding=None):
        self._protocol_error = ProtocolError
        self._reply_error = ReplyError
        self._encoding = encoding or None

        if protocolError:
            if not callable(protocolError):
                raise TypeError("Expected a callable")
            self._protocol_error = protocolError

        if replyError:
            if not callable(replyError):
                raise TypeError("Expected a callable")
            self._reply_error = replyError

        self._self_handle = ffi.new_handle(self)
        self._exception = None

        self._reader = lib.redisReaderCreate()
        self._reader.privdata = self._self_handle
        self._reader.fn.createString = self._create_string
        self._reader.fn.createArray = self._create_array
        self._reader.fn.createInteger = self._create_integer
        self._reader.fn.createNil = self._create_nil
        self._reader.fn.freeObject = self._free_object

    @ffi.callback("void * (const redisReadTask*, char*, size_t)")
    def _create_string(self, s, length):
        self = ffi.cast("redisReadTask*", self)
        self = ffi.from_handle(self.privdata)
        data = ffi.string(s, length)
        if self.type == lib.REDIS_REPLY_ERROR:
            data = self._reply_error(data)
        elif self._encoding is not None:
            try:
                data = data.decode(self._encoding)
            except ValueError:
                # for compatibility with hiredis
                pass
            except Exception as err:
                self._exception = err
                data = None
        _parentize(self, data)
        return _global_handles.new(data)

    @ffi.callback("void *(const redisReadTask*, int)")
    def _create_array(self, i):
        self = ffi.cast("redisReadTask*", self)
        data = [None] * i
        _parentize(self, data)
        return _global_handles.new(data)

    @ffi.callback("void *(const redisReadTask*, long long)")
    def _create_integer(self, n):
        self = ffi.cast("redisReadTask*", self)
        data = n
        _parentize(self, data)
        return _global_handles.new(data)

    @ffi.callback("void *(const redisReadTask*)")
    def _create_nil(self):
        self = ffi.cast("redisReadTask*", self)
        data = None
        _parentize(self, data)
        return _global_handles.new(data)

    @ffi.callback("void (void*)")
    def _free_object(self):
        _global_handles.free(self)

    def feed(self, buf, offset=None, length=None):
        if offset is None:
            offset = 0
        if length is None:
            length = len(buf) - offset

        if offset < 0 or length < 0:
            raise ValueError("negative input")

        if offset + length > len(buf):
            raise ValueError("input is larger than buffer size")

        if isinstance(buf, bytearray):
            c_buf = ffi.new("char[]", length)
            for i in range(length):
                c_buf[i] = chr(buf[offset + i])
        else:
            c_buf = ffi.new("char[]", buf[offset:offset + length])
        lib.redisReaderFeed(self._reader, c_buf, length)

    def gets(self):
        reply = ffi.new("void **")
        result = lib.redisReaderGetReply(self._reader, reply)

        if result != lib.REDIS_OK:
            errstr = ffi.string(self._reader.errstr)
            raise self._protocol_error(errstr)

        if reply[0] == ffi.NULL:
            return False

        if self._exception:
            err, self._exception = self._exception, None
            raise err

        return _global_handles.free(reply[0])
