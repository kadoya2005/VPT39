"""NDI discovery and audio/video reception using the shared NDI runtime."""

import ctypes as ct
from contextlib import ExitStack
from functools import partial
import threading
import time

import numpy as np

from ndi_output import PROGRESSIVE, RGBX, _Source, _VideoFrame, _get_runtime
from sources import Source, live_audio_output


RGBA = int.from_bytes(b'RGBA', 'little')
COLOR_RGBX_RGBA = 2
BANDWIDTH_HIGHEST = 100
MISSING = 'NDI 6 Runtime が見つかりません'
PCM_PER_NDI_UNIT = 0.1  # SMPTE reference: NDI +/-1 is -20 dBFS.


class _FindOptions(ct.Structure):
    _fields_ = [('show_local_sources', ct.c_bool), ('groups', ct.c_char_p), ('extra_ips', ct.c_char_p)]


class _RecvOptions(ct.Structure):
    _fields_ = [('source', _Source), ('color_format', ct.c_int), ('bandwidth', ct.c_int),
                ('allow_video_fields', ct.c_bool), ('name', ct.c_char_p)]


class _AudioFrame(ct.Structure):
    _fields_ = [('rate', ct.c_int), ('channels', ct.c_int), ('samples', ct.c_int),
                ('timecode', ct.c_int64), ('data', ct.c_void_p), ('stride', ct.c_int),
                ('metadata', ct.c_char_p), ('timestamp', ct.c_int64)]


class NdiFinder:
    def __init__(self):
        self.state = ((), '')
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='NDI discovery', daemon=True)
        self.thread.start()

    def close(self):
        self._stop.set()

    def _run(self):
        handle = None
        try:
            sdk = _get_runtime()
            lib = sdk.library
            create = sdk._bind(lib, 'find_create_v2', ct.c_void_p, ct.POINTER(_FindOptions))
            destroy = sdk._bind(lib, 'find_destroy', None, ct.c_void_p)
            wait = sdk._bind(lib, 'find_wait_for_sources', ct.c_bool, ct.c_void_p, ct.c_uint32)
            current = sdk._bind(lib, 'find_get_current_sources', ct.POINTER(_Source),
                                ct.c_void_p, ct.POINTER(ct.c_uint32))
            handle = create(ct.byref(_FindOptions(True, None, None)))
            if not handle:
                raise RuntimeError('NDIlib_find_create_v2 failed')
            while not self._stop.is_set():
                count = ct.c_uint32()
                items = current(handle, ct.byref(count))
                names = {item.name.decode('utf-8', 'replace')
                         for item in items[:count.value] if item.name} if items else set()
                self.state = (tuple(sorted(names, key=str.casefold)), '')
                wait(handle, 500)
        except FileNotFoundError:
            self.state = ((), MISSING)
        except Exception as ex:
            self.state = ((), 'NDI入力の一覧を取得できませんでした')
            print(f'[NDI] discovery: {ex}', flush=True)
        finally:
            if handle:
                destroy(handle)


def _copy_frame(frame):
    w, h, stride = frame.width, frame.height, frame.stride
    row_bytes = w * 4
    if not (frame.data and 0 < w <= 8192 and 0 < h <= 8192
            and frame.fourcc in (RGBX, RGBA) and row_bytes <= stride <= 65536):
        return None
    if stride == row_bytes:
        pixels = ct.string_at(frame.data, row_bytes * h)
    else:
        # NDI owns the buffer and rows may have padding. Copy before freeing it.
        size = (h - 1) * stride + row_bytes
        buffer = (ct.c_ubyte * size).from_address(frame.data)
        pixels = np.ndarray((h, row_bytes), np.uint8, buffer=buffer, strides=(stride, 1)).tobytes()
    return w, h, pixels, 'RGBA' if frame.fourcc == RGBA else 'RGB1'


class NdiSource(Source):
    def __init__(self, name):
        self.name = name
        self._latest = None
        self._born = time.monotonic()
        self._error = ''
        self._audio_error = ''
        self._volume = self._output_gain = 0.0
        self.silent = True
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name='NDI receiver', daemon=True)
        self._thread.start()

    def _run(self):
        handle = sync = None
        try:
            sdk = _get_runtime()
            lib = sdk.library
            video_ptr = ct.POINTER(_VideoFrame)
            create = sdk._bind(lib, 'recv_create_v3', ct.c_void_p, ct.POINTER(_RecvOptions))
            destroy = sdk._bind(lib, 'recv_destroy', None, ct.c_void_p)
            create_sync = sdk._bind(lib, 'framesync_create', ct.c_void_p, ct.c_void_p)
            destroy_sync = sdk._bind(lib, 'framesync_destroy', None, ct.c_void_p)
            capture = sdk._bind(lib, 'framesync_capture_video', None, ct.c_void_p, video_ptr, ct.c_int)
            free = sdk._bind(lib, 'framesync_free_video', None, ct.c_void_p, video_ptr)
            audio_ptr = ct.POINTER(_AudioFrame)
            capture_audio = sdk._bind(lib, 'framesync_capture_audio', None, ct.c_void_p, audio_ptr,
                                      ct.c_int, ct.c_int, ct.c_int)
            free_audio = sdk._bind(lib, 'framesync_free_audio', None, ct.c_void_p, audio_ptr)
            # Resolve the saved source name at playback, including after an address change.
            options = _RecvOptions(_Source(self.name.encode('utf-8'), None),
                                   COLOR_RGBX_RGBA, BANDWIDTH_HIGHEST, False, None)
            handle = create(ct.byref(options))
            if not handle:
                raise RuntimeError('NDIlib_recv_create_v3 failed')
            sync = create_sync(handle)
            if not sync:
                raise RuntimeError('NDIlib_framesync_create failed')
            # Keep the sound callback on the hardware clock. Close it before freeing the NDI handle.
            callback = partial(self._play_audio, capture_audio, free_audio, sync)
            with ExitStack() as streams:
                try:
                    streams.enter_context(live_audio_output(callback))
                except Exception as ex:
                    self._audio_error = f'NDI音声を出力できませんでした: {self.name}'
                    print(f'[NDI] {self.name} audio: {ex}', flush=True)
                frame = _VideoFrame()
                last_frame = None
                sequence = 0
                while not self._stop.is_set():
                    deadline = time.monotonic() + 1 / 120
                    capture(sync, ct.byref(frame), PROGRESSIVE)
                    try:
                        key = (frame.timestamp, frame.timecode, frame.width, frame.height)
                        if frame.data and key != last_frame:
                            pixels = _copy_frame(frame)
                            if pixels is not None:
                                last_frame = key
                                sequence += 1
                                self._latest = (time.monotonic(), (*pixels, sequence))
                    finally:
                        free(sync, ct.byref(frame))
                    self._stop.wait(max(0, deadline - time.monotonic()))
        except FileNotFoundError:
            self._error = MISSING
        except Exception as ex:
            self._error = f'NDI映像を受信できませんでした: {self.name}'
            print(f'[NDI] {self.name}: {ex}', flush=True)
        finally:
            if sync:
                destroy_sync(sync)
            if handle:
                destroy(handle)

    def _play_audio(self, capture, free, sync, output, frames, rate):
        frame = _AudioFrame()
        channels = output.shape[1]
        output.fill(0)
        # Keep pulling while muted/paused so playback resumes at the live position.
        capture(sync, ct.byref(frame), rate, channels, frames)
        try:
            gain = 0.0 if self.paused or not self.ready or self._stop.is_set() else self._volume
            previous_gain, self._output_gain = self._output_gain, gain
            if (not (gain or previous_gain) or not frame.data or frame.channels != channels
                    or frame.samples != frames or frame.stride < frames * 4):
                self.silent = True
                return
            size = (channels - 1) * frame.stride + frames * 4
            buffer = (ct.c_ubyte * size).from_address(frame.data)
            pcm = np.ndarray((frames, channels), np.float32, buffer=buffer, strides=(4, frame.stride))
            if gain != previous_gain:
                gain = np.linspace(previous_gain, gain, frames + 1, dtype=np.float32)[1:, None]
            np.multiply(pcm, gain * PCM_PER_NDI_UNIT, out=output)
            np.nan_to_num(output, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.clip(output, -1, 1, out=output)
            self.silent = not output.any()
        finally:
            free(sync, ct.byref(frame))

    @property
    def ready(self):
        return self._latest is not None

    @property
    def failed(self):
        return not self.ready and (bool(self._error) or time.monotonic() - self._born > 8)

    @property
    def fail_msg(self):
        return self._error or f'NDIの映像が届きません: {self.name}'

    @property
    def stall_msg(self):
        latest = self._latest
        if latest and time.monotonic() - latest[0] > 5:
            return self._error or f'NDIの映像が止まっています: {self.name}'
        return self._audio_error

    def frame(self):
        if self.paused:
            return None
        latest = self._latest
        if latest is not None:
            frame = latest[1]
            if frame[4] != self.fid:
                self.w, self.h = frame[:2]
                self.fid = frame[4]
                return frame
        return None

    def set_volume(self, value):
        self._volume = max(0.0, min(1.0, float(value)))

    def set_paused(self, paused):
        self.paused = paused

    def release(self):
        self._stop.set()
