"""Optional NDI 6 video output through a local or installed Windows runtime."""

import ctypes as ct
import os
from pathlib import Path
import re
import sys
import threading

import numpy as np


DLL = 'Processing.NDI.Lib.x64.dll'
RGBX = int.from_bytes(b'RGBX', 'little')
PROGRESSIVE = 1
SYNTHESIZE_TIMECODE = (1 << 63) - 1


def source_name(value):
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', ' ', value).strip()[:60].rstrip()


class _SendOptions(ct.Structure):
    _fields_ = [('name', ct.c_char_p), ('groups', ct.c_char_p),
                ('clock_video', ct.c_bool), ('clock_audio', ct.c_bool)]


class _Source(ct.Structure):
    _fields_ = [('name', ct.c_char_p), ('address', ct.c_char_p)]


class _VideoFrame(ct.Structure):
    _fields_ = [('width', ct.c_int), ('height', ct.c_int), ('fourcc', ct.c_int),
                ('fps_n', ct.c_int), ('fps_d', ct.c_int), ('aspect', ct.c_float),
                ('format', ct.c_int), ('timecode', ct.c_int64), ('data', ct.c_void_p),
                ('stride', ct.c_int), ('metadata', ct.c_char_p), ('timestamp', ct.c_int64)]


def _runtime_paths():
    module_dir = Path(__file__).resolve().parent
    app_dir = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else module_dir
    for directory in dict.fromkeys((app_dir, module_dir)):
        yield directory / 'runtime' / 'ndi' / DLL
    runtime = os.environ.get('NDI_RUNTIME_DIR_V6')
    if runtime and Path(runtime).is_absolute():
        yield Path(runtime) / DLL
    root = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'NDI'
    yield root / 'NDI 6 Runtime' / 'v6' / DLL
    yield root / 'NDI 6 Runtime' / DLL
    yield root / 'NDI 6 Tools' / 'Runtime' / DLL
    yield root / 'NDI 6 Tools' / 'Studio Monitor' / DLL


class _Runtime:
    def __init__(self):
        path = next((p for p in _runtime_paths() if p.is_file()), None)
        if path is None:
            raise FileNotFoundError(DLL)
        self.library = lib = ct.CDLL(str(path))
        initialize = self._bind(lib, 'initialize', ct.c_bool)
        self.create = self._bind(lib, 'send_create', ct.c_void_p, ct.POINTER(_SendOptions))
        self.destroy = self._bind(lib, 'send_destroy', None, ct.c_void_p)
        self.send = self._bind(lib, 'send_send_video_v2', None, ct.c_void_p, ct.POINTER(_VideoFrame))
        self.connections = self._bind(lib, 'send_get_no_connections', ct.c_int, ct.c_void_p, ct.c_uint32)
        self.registered_name = self._bind(lib, 'send_get_source_name', ct.POINTER(_Source), ct.c_void_p)
        if not initialize():
            raise RuntimeError('NDIlib_initialize failed')

    @staticmethod
    def _bind(lib, name, result, *args):
        fn = getattr(lib, 'NDIlib_' + name)
        fn.restype, fn.argtypes = result, args
        return fn


_runtime = None
_runtime_lock = threading.Lock()


def _get_runtime():
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = _Runtime()
        return _runtime


class NdiSender:
    """Keep at most the newest unsent frame. The render thread never waits on NDI."""

    def __init__(self, name, previous=None):
        self.name = name
        self._condition = threading.Condition()
        self._stopped = False
        self._pending = None
        self._status = 'starting'
        self._receivers = 0
        self._registered = ''
        self._thread = threading.Thread(target=self._run, args=(previous,), daemon=True)
        self._thread.start()

    def state(self):
        with self._condition:
            return {'status': self._status, 'receivers': self._receivers, 'source': self._registered}

    def wants_frame(self):
        with self._condition:
            return not self._stopped and self._receivers > 0 and self._pending is None

    def put(self, width, height, fps, pixels):
        with self._condition:
            if not self._stopped:
                self._pending = (width, height, fps, pixels)
                self._condition.notify()

    def close(self):
        with self._condition:
            self._stopped = True
            self._pending = None
            self._condition.notify()

    def fail_readback(self, error):
        with self._condition:
            self._status = 'error'
        self.close()
        print(f'[NDI] {self.name}: framebuffer read failed: {error}', flush=True)

    def join(self, timeout=None):
        self._thread.join(timeout)

    def is_alive(self):
        return self._thread.is_alive()

    def _run(self, previous):
        handle = None
        try:
            if previous is not None:
                while previous.is_alive():
                    with self._condition:
                        if self._stopped:
                            return
                    previous.join(.25)
            with self._condition:
                if self._stopped:
                    return
            sdk = _get_runtime()
            handle = sdk.create(ct.byref(_SendOptions(self.name.encode('utf-8'), None, False, False)))
            if not handle:
                raise RuntimeError('NDIlib_send_create failed')
            registered = sdk.registered_name(handle)
            with self._condition:
                self._status = 'ready'
                if registered and registered.contents.name:
                    self._registered = registered.contents.name.decode('utf-8', 'replace')
            while True:
                receivers = max(0, sdk.connections(handle, 0))
                with self._condition:
                    self._receivers = receivers
                    if self._stopped:
                        break
                    frame, self._pending = self._pending, None
                    if not receivers or frame is None:
                        self._condition.wait(.25)
                        continue
                width, height, fps, pixels = frame
                # OpenGL の下から上の走査順を NDI の上から下に直す。
                rows = np.frombuffer(pixels, np.uint8).reshape(height, width * 4)[::-1].copy()
                video = _VideoFrame(width, height, RGBX, fps, 1, 0.0, PROGRESSIVE,
                                    SYNTHESIZE_TIMECODE, rows.ctypes.data, width * 4, None, 0)
                sdk.send(handle, ct.byref(video))
        except Exception as ex:
            with self._condition:
                self._status = 'missing' if isinstance(ex, FileNotFoundError) else 'error'
            print(f'[NDI] {self.name}: {ex}', flush=True)
        finally:
            if handle:
                sdk.destroy(handle)
            with self._condition:
                self._receivers = 0
                self._pending = None
