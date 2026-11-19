"""再生ソース: 動画(VLC / OpenCV)、静止画、写真ウォール用フィーダー"""
import os
import time
import ctypes
import threading

import numpy as np
from PIL import Image, ImageOps

VIDEO_EXT = {'.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.wmv', '.mpg', '.mpeg'}
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'}

# ---- VLC (音声付き再生) が使えるか確認 ----------------------------------
# 音声出力は DirectSound を使う。Windows 標準（mmdevice/WASAPI）や waveout では
# 音量がアプリ全体で1つだけになり、最後に設定したレイヤーの音量が全部の動画に効いてしまう。
# DirectSound なら動画ごとに別々の音量にできる（複数レイヤーの同時再生・クロスフェードに必須）。
_vlc_inst = None
AOUT = ''
try:
    import vlc  # python-vlc
    for _aout in ('directsound', None):
        _args = ['--no-video-title-show', '--quiet', '--no-osd']
        if _aout:
            _args.append('--aout=' + _aout)
        try:
            _vlc_inst = vlc.Instance(*_args)
        except Exception:
            _vlc_inst = None
        if _vlc_inst is not None:
            AOUT = _aout or '標準'
            break
except Exception:
    vlc = None
    _vlc_inst = None
HAS_VLC = _vlc_inst is not None

try:
    import cv2
    HAS_CV = True
except Exception:
    cv2 = None
    HAS_CV = False


def backend_name():
    if HAS_VLC:
        return 'VLC（音声あり）' if AOUT == 'directsound' else 'VLC（音声あり・音量は全レイヤー共通）'
    if HAS_CV:
        return 'OpenCV（音声なし）'
    return 'なし'


def is_video(path):
    return os.path.splitext(path)[1].lower() in VIDEO_EXT


def is_image(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXT


class Source:
    """全ソース共通: frame() が (w, h, bytes, swizzle, frame_id) を返す"""
    w = h = 0
    fid = 0
    ended = False
    paused = False
    seekable = False
    ready = True  # 最初の映像が出たか（出るまでは前の素材を流し続ける）

    def update(self):
        pass

    def set_paused(self, p):
        pass

    def seek(self, frac):
        """0.0〜1.0 の位置へ移動（終わっていたら再生し直す）"""
        pass

    def frame(self):
        return None

    def set_volume(self, v):
        pass

    def position(self):
        return 0.0, 0.0  # (現在秒, 長さ秒)

    def release(self):
        pass


# ---- VLC 動画 ---------------------------------------------------------------
class VlcVideoSource(Source):
    def __init__(self, path, w, h, mode='loop', options=None, live=False):
        self.w, self.h, self.mode = w, h, mode
        self.live = live
        self.player = _vlc_inst.media_player_new()
        media = _vlc_inst.media_new(path)
        for o in options or []:
            media.add_option(o)
        if mode == 'loop' and not live:
            media.add_option('input-repeat=65535')
        self.player.set_media(media)
        media.release()  # プレイヤーが持っているので、こちらの分は手放す（切り替えるたびに少しずつ増えないように）
        self.size = w * h * 4
        self.buf = (ctypes.c_ubyte * self.size)()
        self.buf_addr = ctypes.addressof(self.buf)
        self.lock = threading.Lock()
        self._latest = None
        self._fid = 0
        self.fid = 0
        # 裏で行う stop()／play()／release() を1つずつ順番に（同じプレイヤーへの操作が重ならないように）
        self._op_lock = threading.Lock()
        self.closing = False  # release() を呼んだあと：もう操作しない

        # VLC が映像を書き込む場所を教えるだけ（ここではロックしない）
        @vlc.CallbackDecorators.VideoLockCb
        def _lock(opaque, planes):
            planes[0] = self.buf_addr
            return None

        @vlc.CallbackDecorators.VideoUnlockCb
        def _unlock(opaque, picture, planes):
            pass

        # 1フレーム書き終わったら、その時点の映像をコピーして渡す
        @vlc.CallbackDecorators.VideoDisplayCb
        def _display(opaque, picture):
            data = ctypes.string_at(self.buf_addr, self.size)
            with self.lock:
                self._latest = data
                self._fid += 1
            self.last_frame = time.time()

        self._cbs = (_lock, _unlock, _display)  # GC 防止
        self.player.video_set_callbacks(_lock, _unlock, _display, None)
        self.player.video_set_format('RV32', w, h, w * 4)
        self.player.audio_set_volume(0)
        self.player.play()
        self.seekable = not live
        self._pending = None  # 再生が始まってから移動する位置
        self._vol = 0
        self._vol_check = 0.0
        self._born = time.time()
        self.last_frame = 0.0

    @property
    def ready(self):
        # 最初のフレームが届いたら準備完了（読み込めない・音声だけの素材でも3秒で先へ進む）
        # ライブ入力は映像が来るまで待つ（開くのに時間がかかるカメラで、黒い画面に切り替わらないように）
        if self.live:
            return self._fid > 0
        return self._fid > 0 or (self.ended and not self.failed) or time.time() - self._born > 3.0

    @property
    def failed(self):
        # 一度も映像が出ないまま終わった・エラーになった（壊れたファイル、映像の入っていないファイルなど）
        if self._fid > 0 or self._pending is not None:
            return False
        if self.live and time.time() - self._born > 12.0:
            return True  # 12秒たっても映像が来ないカメラ
        st = self.player.get_state()
        if st in (vlc.State.Error, vlc.State.Ended):
            return True
        return st == vlc.State.Playing and time.time() - self._born > 5.0

    def update(self):
        st = self.player.get_state()
        if self._pending is not None and st in (vlc.State.Playing, vlc.State.Paused):
            self.player.set_position(self._pending)
            self._pending = None
            if self.paused and st == vlc.State.Playing:
                self.player.set_pause(1)
        self.ended = st in (vlc.State.Ended, vlc.State.Error) and self._pending is None

    def set_paused(self, p):
        self.paused = bool(p)
        if self.closing:
            return
        st = self.player.get_state()
        if not p and st in (vlc.State.Ended, vlc.State.Stopped) and self._pending is None:
            self.seek(0.0)  # 終わったものを再生 → 頭から
            return
        self.player.set_pause(1 if p else 0)

    def seek(self, frac):
        frac = max(0.0, min(0.999, float(frac)))
        if self.closing:
            return
        st = self.player.get_state()
        if st in (vlc.State.Playing, vlc.State.Paused):
            self.player.set_position(frac)
        else:
            if st in (vlc.State.Ended, vlc.State.Stopped, vlc.State.Error):
                p = self.player

                def work():  # stop() は時間がかかることがあるので、映像を止めないよう裏で
                    with self._op_lock:
                        if self.closing:  # 待っている間に解放が決まった
                            return
                        try:
                            p.stop()
                            p.play()
                        except Exception:
                            pass
                threading.Thread(target=work, daemon=True).start()
            self._pending = frac
        self.ended = False

    def frame(self):
        if self._fid == self.fid or self._latest is None:
            return None
        with self.lock:
            data = self._latest
            self.fid = self._fid
        # RV32 の4バイト目は透明度ではなく空き領域（0のことがある）→ 不透明として扱う
        return self.w, self.h, data, 'BGR1', self.fid

    def set_volume(self, v):
        vol = int(round(max(0, min(1, v)) * 100))
        now = time.time()
        if vol != self._vol:
            if self.player.audio_set_volume(vol) == 0:
                self._vol = vol
        elif now - self._vol_check > 0.5:
            # 音声の準備前に設定した音量が反映されないことがあるので、ときどき確かめて設定し直す
            self._vol_check = now
            cur = self.player.audio_get_volume()
            if cur >= 0 and cur != vol:
                self.player.audio_set_volume(vol)

    def position(self):
        t = self.player.get_time() / 1000.0
        d = max(self.player.get_length() / 1000.0, 0.0)
        if self._pending is not None:  # 移動待ちの間は移動先を表示
            t = self._pending * d
        return max(t, 0.0), d

    def release(self):
        # stop() は少し時間がかかるので、映像が止まらないよう裏で実行
        # 頭出し（stop→play）の途中なら、それが終わってから解放する
        if self.closing:
            return  # 2回目は何もしない
        self.closing = True

        def work():
            with self._op_lock:
                try:
                    self.player.stop()
                    self.player.release()
                except Exception:
                    pass
        threading.Thread(target=work, daemon=True).start()


# ---- OpenCV 動画（VLC が無いときの予備。音声なし）-------------------------
class CvVideoSource(Source):
    def __init__(self, path, w, h, mode='loop'):
        self.mode = mode
        self.cap = cv2.VideoCapture(path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        self.length = n / self.fps if n > 0 else 0.0
        self.w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or w
        self.h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or h
        self._frame = None
        self._fid = 0
        self.fid = 0
        self.t = 0.0
        self.n = n
        self.lock = threading.Lock()
        self.running = True
        self.seekable = n > 0
        self._seek = None
        self._born = time.time()
        self.failed = False  # 先頭から1枚も読めない（壊れたファイル・対応していない形式）
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    @property
    def ready(self):
        return self._fid > 0 or self.ended or time.time() - self._born > 3.0

    def set_paused(self, p):
        if not p and self.ended:
            self.seek(0.0)
        self.paused = bool(p)

    def seek(self, frac):
        self._seek = max(0.0, min(0.999, float(frac)))

    def _run(self):
        start = time.perf_counter()
        idx = 0
        fails = 0  # 頭から読もうとして続けて失敗した回数
        while self.running:
            if self._seek is not None:
                idx = int(self._seek * self.n)
                self._seek = None
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                start = time.perf_counter() - idx / self.fps
                self.ended = False
                show_one = True
            else:
                show_one = False
            if (self.paused or self.ended) and not show_one:
                time.sleep(0.02)
                start = time.perf_counter() - idx / self.fps  # 止めている間は時計も止める
                continue
            ok, img = self.cap.read()
            if not ok:
                if idx == 0:
                    # 頭から1枚も読めない：待たずにくり返すと CPU を使い続けるので、少し待って数回であきらめる
                    fails += 1
                    time.sleep(0.05)
                    if fails >= 3:
                        self.failed = True
                        self.ended = True
                        continue
                if self.mode == 'loop':
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    start = time.perf_counter()
                    idx = 0
                    continue
                self.ended = True  # 最後で待つ（シークされたら再開）
                continue
            fails = 0
            with self.lock:
                self._frame = img
                self._fid += 1
            idx += 1
            self.t = idx / self.fps
            wait = start + idx / self.fps - time.perf_counter()
            if wait > 0 and not self.paused:
                time.sleep(wait)
        self.cap.release()

    def frame(self):
        if self._fid == self.fid:
            return None
        with self.lock:
            img = self._frame
            self.fid = self._fid
        h, w = img.shape[:2]
        self.w, self.h = w, h
        return w, h, np.ascontiguousarray(img).tobytes(), 'BGR1', self.fid

    def position(self):
        return self.t, self.length

    def release(self):
        self.running = False


# ---- 静止画 -----------------------------------------------------------------
def _fit(im, cw, ch, border=0):
    im = im.convert('RGBA')
    if border > 0:
        bw = int(min(im.size) * border)
        im = ImageOps.expand(im, border=bw, fill=(255, 255, 255, 255))
    im.thumbnail((cw, ch), Image.LANCZOS)
    canvas = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
    canvas.paste(im, ((cw - im.width) // 2, (ch - im.height) // 2))
    return canvas.tobytes()


def load_fitted_rgba(path, cw, ch, border=0):
    """画像を cw×ch のキャンバスに縦横比を保って収める（余白は透明）"""
    im = Image.open(path)
    return _fit(ImageOps.exif_transpose(im), cw, ch, border)


ANIM_BUDGET = 256 * 1024 * 1024  # 動く画像1つに使うメモリの上限（超えるときは小さくして持つ）
ANIM_MAX_FRAMES = 1500


def load_fitted(path, cw, ch, border=0):
    """静止画は bytes。動く画像（GIF・APNG・WebP）は (w, h, [各コマ], [各コマの秒数])"""
    im = Image.open(path)
    n = min(getattr(im, 'n_frames', 1) or 1, ANIM_MAX_FRAMES)
    if n <= 1:
        return _fit(ImageOps.exif_transpose(im), cw, ch, border)
    scale = min(1.0, (ANIM_BUDGET / (n * cw * ch * 4)) ** 0.5)
    w, h = max(16, int(cw * scale)), max(16, int(ch * scale))
    frames, durs = [], []
    for i in range(n):
        im.seek(i)  # Pillow は前のコマと重ねた完成画を返す
        frames.append(_fit(im, w, h, border))
        d = im.info.get('duration') or 100  # ミリ秒。0 や無指定は 0.1秒（ブラウザと同じ扱い）
        durs.append(max(0.02, d / 1000.0))
    return w, h, frames, durs


class ImageSource(Source):
    """静止画と動く画像。data を渡さないときは裏で読み込む（大きなGIFでも出力を止めない）"""

    def __init__(self, path, w, h, border=0, data=None):
        self.w, self.h = w, h
        self.path = path
        self.fid = 0
        self._sent = False
        self._idx = -1
        self.frames = None
        self.durs = None
        self.t0 = None
        self._pause_at = None
        self.closed = False
        self.failed = False
        self.fail_msg = f'画像を読み込めません: {os.path.basename(path)}'
        if data is not None:
            self._set(data)
        else:
            def work():
                try:
                    r = load_fitted(path, w, h, border)
                    if not self.closed:  # 読み込み中に消された（別のクリップに切り替えた）ら捨てる
                        self._set(r)
                except Exception as e:
                    print('画像の読み込み失敗:', path, e)
                    self.failed = True
            threading.Thread(target=work, daemon=True).start()

    def _set(self, data):
        if isinstance(data, tuple):
            self.w, self.h, frames, self.durs = data
            self.cum = list(np.cumsum(self.durs))
            self.total = self.cum[-1]
            self.frames = frames
        else:
            self.frames = [data]

    @property
    def ready(self):
        return self.frames is not None

    @property
    def animated(self):
        return self.durs is not None

    def set_paused(self, p):
        if not self.animated:
            return
        now = time.time()
        if p and self._pause_at is None:
            self._pause_at = now
        elif not p and self._pause_at is not None:
            if self.t0 is not None:
                self.t0 += now - self._pause_at
            self._pause_at = None
        self.paused = bool(p)

    def position(self):
        if not self.animated or self.t0 is None:
            return 0.0, 0.0
        now = self._pause_at or time.time()
        return (now - self.t0) % self.total, self.total

    def frame(self):
        if self.frames is None or self.closed:
            return None
        if not self.animated:
            if self._sent:
                return None
            self._sent = True
            self.fid = 1
            return self.w, self.h, self.frames[0], 'RGBA', 1
        now = self._pause_at or time.time()
        if self.t0 is None:
            self.t0 = now
        t = (now - self.t0) % self.total
        import bisect
        i = min(bisect.bisect_right(self.cum, t), len(self.frames) - 1)
        if i == self._idx:
            return None
        self._idx = i
        self.fid += 1
        return self.w, self.h, self.frames[i], 'RGBA', self.fid

    def release(self):
        self.closed = True
        self.frames = None


# ---- 空白（何も映さない・音も出さない）--------------------------------------
class BlankSource(Source):
    silent = True


# ---- レイヤーの映像を素材として使う（同じ映像・同じタイミング）--------------
class LayerSource(Source):
    """別レイヤーの描画結果（GPU 上のテクスチャ）をそのまま使う。音は出さない（元のレイヤーで鳴る）"""
    shared_tex = True
    silent = True
    fail_msg = '元のレイヤーが削除されたため、レイヤー映像を映せません'

    def __init__(self, app, layer_id):
        self.app = app
        self.layer_id = layer_id

    def target(self):
        for L in self.app.layers:
            if L.cfg.get('id') == self.layer_id:
                return L
        return None

    @property
    def failed(self):
        return self.target() is None

    _busy = False

    def position(self):
        # A が B を、B が A を映していると無限にたどってしまうので、2回目はそこで止める
        if self._busy:
            return 0.0, 0.0
        self._busy = True
        try:
            L = self.target()
            d = L.top_deck() if L else None
            return d.src.position() if d else (0.0, 0.0)
        finally:
            self._busy = False


def layer_refs_chain(app, start_id, limit=50):
    """start_id のレイヤーが（レイヤー映像で）映しているレイヤーを、たどれるだけたどった id の集合"""
    seen, todo = set(), [start_id]
    while todo and len(seen) < limit:
        lid = todo.pop()
        if lid in seen:
            continue
        seen.add(lid)
        L = next((x for x in app.layers if x.cfg.get('id') == lid), None)
        if L is None:
            continue
        for d in L.decks:
            if isinstance(d.src, LayerSource) and d.target > 0:
                todo.append(d.src.layer_id)
    return seen


# ---- ライブ入力：音声（WASAPI で低遅延に取り込み、既定のスピーカーへ流す）----
try:
    import sounddevice as sd
except Exception:
    sd = None


_audio_open = [0]  # 開いている音声入力の数


class AudioIn:
    """マイク・キャプチャーボードの音声をそのままスピーカーへ（音量はミキサーに従う）"""

    def __init__(self, name):
        self.vol = 0.0
        self.error = ''
        self.inp = self.out = None
        if sd is None:
            self.error = '音声入力の部品（sounddevice）がありません'
            return
        dev = find_mic(name)
        if dev is None:
            self.error = f'音声入力が見つかりません: {name}'
            return
        import collections
        self.q = collections.deque()
        self.qlen = 0
        info = sd.query_devices(dev)
        rate = int(info['default_samplerate'])
        self.ch = max(1, min(2, int(info['max_input_channels'])))
        self.maxq = int(rate * 0.06)  # 60ms 以上たまったら古い音を捨てて遅れないようにする
        try:
            ws = sd.WasapiSettings(auto_convert=True)
        except Exception:
            ws = None

        lock = threading.Lock()  # 入力と出力は別のスレッドから呼ばれるので、たまった音を同時にさわらない
        prime = int(rate * 0.015)  # 15ms たまってから鳴らし始める（足りなくなってプチプチ鳴るのを防ぐ）
        self.primed = False
        self.last_in = time.time()

        def on_in(indata, frames, t, status):
            with lock:
                self.q.append(indata.copy())
                self.qlen += frames
                while self.qlen > self.maxq and len(self.q) > 1:
                    self.qlen -= len(self.q.popleft())
            self.last_in = time.time()

        def on_out(outdata, frames, t, status):
            got = 0
            with lock:
                if not self.primed:
                    if self.qlen < prime:
                        outdata[:] = 0
                        return
                    self.primed = True
                while got < frames and self.q:
                    b = self.q[0]
                    k = min(frames - got, len(b))
                    blk = b[:k]
                    if blk.shape[1] != outdata.shape[1]:
                        blk = np.repeat(blk[:, :1], outdata.shape[1], axis=1)
                    outdata[got:got + k] = blk * self.vol
                    got += k
                    self.qlen -= k
                    if k == len(b):
                        self.q.popleft()
                    else:
                        self.q[0] = b[k:]
                if got < frames:
                    outdata[got:] = 0
                    self.primed = False  # 足りなくなったら、また少しためてから鳴らす

        try:
            self.inp = sd.InputStream(device=dev, channels=self.ch, samplerate=rate, latency='low',
                                      dtype='float32', callback=on_in, extra_settings=ws)
            self.out = sd.OutputStream(device=_wasapi_default_out(), channels=2, samplerate=rate,
                                       latency='low', dtype='float32', callback=on_out, extra_settings=ws)
            self.inp.start()
            self.out.start()
            _audio_open[0] += 1
            self._counted = True
        except Exception as ex:
            self.error = f'音声入力を開けません: {name}（{ex}）'
            self.release()

    def set_volume(self, v):
        self.vol = max(0.0, min(1.0, float(v)))

    def release(self):
        if getattr(self, '_counted', False):
            self._counted = False
            _audio_open[0] -= 1
        for s in (self.inp, self.out):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self.inp = self.out = None


def _wasapi_index():
    for i, a in enumerate(sd.query_hostapis()):
        if 'WASAPI' in a['name']:
            return i
    return None


def _wasapi_default_out():
    k = _wasapi_index()
    if k is None:
        return None
    d = sd.query_hostapis(k).get('default_output_device', -1)
    return d if d is not None and d >= 0 else None


def list_mics():
    """音声入力（WASAPI）の名前の一覧"""
    if sd is None:
        return []
    if _audio_open[0] == 0:  # あとからつないだマイクも出るように、音声の部品を読み直す（使用中の音声がないときだけ）
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
    k = _wasapi_index()
    out = []
    for d in sd.query_devices():
        if d['max_input_channels'] > 0 and (k is None or d['hostapi'] == k):
            out.append(d['name'])
    return out


def find_mic(name):
    if sd is None or not name:
        return None
    k = _wasapi_index()
    for i, d in enumerate(sd.query_devices()):
        if d['max_input_channels'] > 0 and (k is None or d['hostapi'] == k) and d['name'] == name:
            return i
    return None


def list_cameras():
    """カメラ・キャプチャーボード（DirectShow で開ける名前）。OBS 仮想カメラなども含む。
    同じ機種が2台以上あるときは、VLC と同じく2台目から「名前 #1」「名前 #2」…と番号を付ける"""
    try:
        import comtypes
        from pygrabber.dshow_graph import FilterGraph
        comtypes.CoInitialize()  # 裏のスレッドから呼ぶため
        names = FilterGraph().get_input_devices()  # DirectShow の並び順（VLC が数える順と同じ）
        out = []
        for n in names:
            name, k = n, 0
            while name in out:
                k += 1
                name = f'{n} #{k}'
            out.append(name)
        return out
    except Exception:
        pass
    import subprocess
    import winreg
    names = []
    try:
        ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-CimInstance Win32_PnPEntity -Filter \"PNPClass='Camera' OR PNPClass='Image'\" "
              "| Where-Object {$_.Status -eq 'OK'} | ForEach-Object {$_.Name}")
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, timeout=15,
                           creationflags=0x08000000)
        names += [s.strip() for s in r.stdout.decode('utf-8', 'replace').splitlines() if s.strip()]
    except Exception:
        pass
    try:  # DirectShow に登録された仮想カメラ（OBS Virtual Camera など）
        key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,
                             r'CLSID\{860BB310-5D01-11d0-BD3B-00A0C911CE86}\Instance')
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                sub = winreg.OpenKey(key, winreg.EnumKey(key, i))
                names.append(winreg.QueryValueEx(sub, 'FriendlyName')[0])
            except OSError:
                pass
    except OSError:
        pass
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


class CameraSource(Source):
    """カメラ・キャプチャーボード入力。映像は VLC（遅延を減らす設定）、音声は WASAPI で別々に取り込む
    （VLC で音声も一緒に開くと、開始まで10秒ほどかかり遅延も増えるため）"""

    def __init__(self, vdev, adev, w, h):
        self.fail_msg = f'カメラの映像が来ません: {vdev}（ケーブル・電源、または他のアプリやレイヤーが使用中）'
        self.vdev = vdev
        self.opts = [':dshow-vdev=' + vdev, ':dshow-adev=none', ':live-caching=0', ':dshow-caching=0',
                     ':clock-jitter=0', ':clock-synchro=0', ':no-audio']
        self.video = VlcVideoSource('dshow://', w, h, 'loop', options=self.opts, live=True)
        self.audio = AudioIn(adev) if adev else None
        self.w, self.h = w, h
        self.stall_msg = ''
        self._reopen_at = 0.0
        self._seen = False

    @property
    def ready(self):
        return self.video.ready

    @property
    def failed(self):
        return not self._seen and self.video.failed

    @property
    def ended(self):
        return False

    def update(self):
        self.video.update()
        if self.video._fid > 0:
            self._seen = True
        # 映っていたのに止まった（ケーブルが抜けた・電源が切れた）→ 最後の映像のまま知らせて、5秒ごとに開き直す
        if self._seen:
            now = time.time()
            last = self.video.last_frame or self.video._born
            if now - last > 3.0:
                self.stall_msg = f'カメラの映像が止まっています: {self.vdev}（ケーブル・電源を確認。自動でつなぎ直しています）'
                if now > self._reopen_at:
                    self._reopen_at = now + 5.0
                    old = self.video
                    self.video = VlcVideoSource('dshow://', self.w, self.h, 'loop', options=self.opts, live=True)
                    old.release()
            else:
                self.stall_msg = ''

    def frame(self):
        return self.video.frame()

    def set_volume(self, v):
        if self.audio:
            self.audio.set_volume(v)

    def release(self):
        self.video.release()
        if self.audio:
            self.audio.release()


# ---- Spout（ほかの映像アプリから受け取る。Mac の Syphon にあたる）---------
try:
    import SpoutGL as _spout
except Exception:
    _spout = None


def list_spout():
    """いま Spout で送られている映像の名前"""
    if _spout is None:
        return []
    r = _spout.SpoutReceiver()
    try:
        return [n for n in (r.getSenderList() or []) if n]
    finally:
        _safe(r.releaseReceiver)


class SpoutSource(Source):
    """裏のスレッドで受け取り、いちばん新しい1枚だけを残す（RGBA・透明部分あり）"""
    silent = True

    def __init__(self, name):
        self.name = name
        self.fail_msg = f'Spout の映像が来ません: {name}（送る側のアプリが止まっているなど）'
        self.lock = threading.Lock()
        self._latest = None
        self._fid = 0
        self.fid = 0
        self.closed = False
        self.error = ''
        self._born = time.time()
        self.last_frame = 0.0
        if _spout is None:
            self.error = 'Spout の部品（SpoutGL）がありません'
            self.closed = True
            return
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        r = _spout.SpoutReceiver()
        try:
            if not r.createOpenGL():
                self.error = 'Spout を開けません（OpenGL）'
                return
            r.setReceiverName(self.name)
            buf, w, h = None, 0, 0
            while not self.closed:
                ok = r.receiveImage(buf, 0x1908, False, 0)  # GL_RGBA
                if r.isUpdated():
                    w, h = r.getSenderWidth(), r.getSenderHeight()
                    buf = bytearray(w * h * 4) if w and h else None
                    continue
                if buf is not None and ok and r.isFrameNew():
                    with self.lock:
                        self._latest = (w, h, bytes(buf))
                        self._fid += 1
                    self.last_frame = time.time()
                elif not ok:
                    time.sleep(0.05)  # 送る側がいない
                else:
                    time.sleep(0.002)  # 新しい映像はまだ（フレーム同期の合図を送らないアプリが多いので、短く待って見に行く）
        except Exception as ex:
            self.error = f'Spout の受け取りでエラー: {ex}'
        finally:
            _safe(r.releaseReceiver)
            _safe(r.closeOpenGL)

    @property
    def ready(self):
        return self._fid > 0  # 映像が来るまで前の映像を流し続ける

    @property
    def failed(self):
        return self._fid == 0 and time.time() - self._born > 8.0

    @property
    def stall_msg(self):
        if self._fid > 0 and time.time() - self.last_frame > 5.0:
            return f'Spout の映像が止まっています: {self.name}（送る側のアプリが止まった・閉じたなど。同じ名前で送り直すと続きが映ります）'
        return ''

    def frame(self):
        if self._fid == self.fid or self._latest is None:
            return None
        with self.lock:
            w, h, data = self._latest
            self.fid = self._fid
        self.w, self.h = w, h
        return w, h, data, 'RGBA', self.fid

    def release(self):
        self.closed = True


# ---- ウィンドウ取り込み（Windows Graphics Capture。映像のみ）---------------
try:
    import windows_capture as _wc
except Exception:
    _wc = None


def list_windows():
    """取り込めるウィンドウ（見えていて題名のある、普通のウィンドウ）[(hwnd, 題名), ...]"""
    import ctypes.wintypes as wt
    u = ctypes.windll.user32
    out = []
    me = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, 4):  # 4 = GW_OWNER（ダイアログなど）
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        ex = u.GetWindowLongW(hwnd, -20)
        if ex & 0x80:  # WS_EX_TOOLWINDOW
            return True
        cloaked = ctypes.c_int(0)
        try:
            ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)
        except Exception:
            pass
        if cloaked.value:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        t = buf.value
        if pid.value == me or t in ('Program Manager', 'Windows 入力エクスペリエンス'):
            return True
        out.append((int(hwnd), t))
        return True

    u.EnumWindows(cb, 0)
    return out


def find_window(title):
    if not title or not title.strip():
        return None  # 空の題名はどのウィンドウにも当てはまってしまう
    ws = list_windows()
    for h, t in ws:
        if t == title:
            return h
    low = title.lower()
    for h, t in ws:  # 題名が少し変わるアプリ（再生中の曲名など）にも対応：前方一致・部分一致
        if len(low) >= 4 and (t.lower().startswith(low[:20]) or low in t.lower()):
            return h
    return None


class WindowSource(Source):
    silent = True

    def __init__(self, title):
        self.title = title
        self.fail_msg = f'ウィンドウの映像が来ません: {title}（閉じられた・最小化されたなど）'
        self.lock = threading.Lock()
        self._latest = None
        self._fid = 0
        self.fid = 0
        self.closed = False
        self.error = ''
        self._born = time.time()
        self._last_copy = 0.0
        self.ctl = None
        if _wc is None:
            self.error = 'ウィンドウ取り込みの部品（windows-capture）がありません'
            self.closed = True
            return
        hwnd = find_window(title)
        if hwnd is None:
            self.error = f'ウィンドウが見つかりません: {title}'
            self.closed = True
            return
        cap = None
        for kw in ({'cursor_capture': False, 'draw_border': False}, {'cursor_capture': False}):
            try:
                cap = _wc.WindowsCapture(window_hwnd=hwnd, **kw)
                break
            except Exception:
                cap = None
        if cap is None:
            self.error = f'ウィンドウを取り込めません: {title}'
            self.closed = True
            return

        @cap.event
        def on_frame_arrived(frame, control):
            if self.closed:
                control.stop()
                return
            now = time.time()
            if now - self._last_copy < 1 / 61:  # 高リフレッシュの画面でも 60 回/秒までに（コピーが重いため）
                return
            self._last_copy = now
            buf = frame.frame_buffer
            data = buf.tobytes()  # BGRA
            with self.lock:
                self._latest = (frame.width, frame.height, data)
                self._fid += 1

        @cap.event
        def on_closed():
            self.closed = True

        self.cap = cap
        try:
            self.ctl = cap.start_free_threaded()
        except Exception as ex:
            self.error = f'ウィンドウを取り込めません: {title}（{ex}）'
            self.closed = True

    @property
    def ready(self):
        return self._fid > 0  # 映像が来るまで前の映像を流し続ける

    @property
    def failed(self):
        return self._fid == 0 and (self.closed or time.time() - self._born > 6.0)

    @property
    def stall_msg(self):
        if self._fid > 0 and self.closed:
            return f'ウィンドウが閉じられました: {self.title}（最後の映像のままです）'
        return ''

    def frame(self):
        if self._fid == self.fid or self._latest is None:
            return None
        with self.lock:
            w, h, data = self._latest
            self.fid = self._fid
        self.w, self.h = w, h
        return w, h, data, 'BGR1', self.fid

    def release(self):
        self.closed = True
        ctl = self.ctl
        self.ctl = None
        if ctl is not None:
            threading.Thread(target=lambda: _safe(ctl.stop), daemon=True).start()


def _safe(f):
    try:
        f()
    except Exception:
        pass


def make_source(path, w, h, mode='loop'):
    if is_image(path):
        return ImageSource(path, w, h)
    if HAS_VLC:
        return VlcVideoSource(path, w, h, mode)
    if HAS_CV:
        return CvVideoSource(path, w, h, mode)
    raise RuntimeError('動画再生ライブラリがありません')


# ---- 写真ウォール用フィーダー ----------------------------------------------
class PhotoFeeder:
    """フォルダを監視し、次に表示する写真を裏で読み込んでおく。
    新着写真は優先して次に表示する。"""

    def __init__(self, folder, w, h, border=0.0):
        self.folder = folder
        self.w, self.h, self.border = w, h, border
        self.known = {}          # path -> mtime
        self.order = []          # 表示順（古い順）
        self.new_queue = []      # 新着（まだ表示していない）
        self.pos = -1
        self.ready = None        # (path, data)
        self.ready_new = False
        self.loading = False
        self.last_scan = 0.0
        self.current = None
        self.bad = {}            # 読み込めなかった写真 -> 次に試してよい時刻
        self.scanned = False     # 1回目の確認が終わったか
        self.sizes = {}          # 写真 -> 前回見たファイルの大きさ（コピー中かどうかの判断用）
        self.fixed = None        # 行に並べた画像のリスト（あればフォルダの代わりにこの順で出す）
        self.gen = 0             # 先読みのやり直し用の番号（リストを変えた・番号を選んだとき）
        self.ready_idx = -1
        self.cur_idx = -1        # いま出しているのがリストの何番目か

    # ---- 行に並べた画像のリストで出す ----
    def set_list(self, paths):
        """行に並べた画像（フルパス）の順で出す。None ならフォルダの写真を出す（今までどおり）"""
        paths = None if not paths else list(paths)
        if paths == self.fixed:
            return False
        self.fixed = paths
        self.new_queue = []
        self.ready, self.ready_idx = None, -1
        self.gen += 1
        if paths is None:
            self.known, self.order, self.scanned = {}, [], False
            self.last_scan = 0.0
        else:
            self.order = [p for p in paths if os.path.isfile(p)]
            self.pos = -1  # 新しい並びの最初から
        return True

    def jump(self, i):
        """リストの i 番目を次に出す"""
        if self.fixed:
            self.pos = (i - 1) % len(self.fixed)
            self.ready, self.ready_idx = None, -1
            self.gen += 1

    def scan(self):
        """フォルダを調べるのは裏のスレッドで（写真が多い・Dropbox の中だと時間がかかり、映像が止まるため）。
        結果を反映するのは呼んだ側（描画のスレッド）で、次に呼ばれたとき"""
        self.last_scan = time.time()
        if self.fixed is not None:  # リストのときは、ファイルがあるかだけ確かめる（数が少ないのですぐ終わる）
            self.order = [p for p in self.fixed if os.path.isfile(p)]
            return
        res = getattr(self, '_scan_result', None)
        if res is not None:
            self._scan_result = None
            self._apply(res)
        if getattr(self, '_scanning', False):
            return
        self._scanning = True

        def work():
            try:
                self._scan_result = self._gather()
            except Exception as ex:
                print('写真フォルダを調べられません:', ex)
            finally:
                self._scanning = False
        threading.Thread(target=work, daemon=True).start()

    def _gather(self):
        if not os.path.isdir(self.folder):
            return None
        try:
            names = os.listdir(self.folder)
        except OSError:
            return None
        found = {}
        for n in names:
            p = os.path.join(self.folder, n)
            if is_image(p):
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                # 書き込み途中のファイルを避ける（更新から1秒待つ。エクスプローラーのコピーは
                # 元の日時のままなので、前回見たときと大きさが変わっていないことも確かめる）
                prev = self.sizes.get(p)
                self.sizes[p] = st.st_size
                copying = self.scanned and p not in self.known and prev != st.st_size  # 起動直後の写真は確かめない
                if time.time() - st.st_mtime < 1.0 or copying:
                    continue
                found[p] = st.st_mtime
        return found

    def _apply(self, found):
        if found is None:
            return
        new = [p for p in found if p not in self.known]
        new.sort(key=lambda p: found[p])
        if self.scanned:  # 起動直後の既存写真は「新着」扱いしない（空のフォルダから始めたときも、あとで入った写真は新着）
            self.new_queue.extend(new)
        self.known = found
        self.scanned = True
        self.order = sorted(found, key=lambda p: found[p])
        self.new_queue = [p for p in self.new_queue if p in found]

    def count(self):
        return len(self.order)

    def _pick_next(self, newest_first=True):
        if self.fixed is not None:  # 行のリスト：同じ画像を2回並べてもよいように、番号で進める
            n = len(self.fixed)
            for _ in range(n):
                self.pos = (self.pos + 1) % n
                p = self.fixed[self.pos]
                if os.path.isfile(p):
                    self._pick_idx = self.pos
                    return p, False
            return None, False
        self._pick_idx = -1
        if newest_first and self.new_queue:
            return self.new_queue.pop(0), True
        if not self.order:
            return None, False
        if self.current in self.order:
            self.pos = self.order.index(self.current)
        self.pos = (self.pos + 1) % len(self.order)
        return self.order[self.pos], False

    def prepare(self, newest_first=True):
        """次の写真を別スレッドで読み込む"""
        res = getattr(self, '_scan_result', None)
        if res is not None:  # フォルダを調べ終わっていたら、すぐ反映
            self._scan_result = None
            self._apply(res)
        # 新着が届いたら、先読み済みの通常写真は後回しにして新着を優先
        if (newest_first and self.new_queue and self.ready is not None
                and not self.ready_new and not self.loading):
            self.ready = None
            self.pos -= 1
        if self.loading or self.ready is not None:
            return
        path, is_new = self._pick_next(newest_first)
        if path is None:
            return
        if self.bad.get(path, 0) > time.time():
            return  # 読み込めなかった写真は30秒あけてから試し直す（毎フレーム読み直して重くならないように）
        self.loading = True

        w, h = self.w, self.h
        gen, idx = self.gen, getattr(self, '_pick_idx', -1)

        def work():
            try:
                data = load_fitted(path, w, h, self.border)
                if gen != self.gen:
                    return  # 読み込み中にリストが変わった・別の番号を選んだ → 捨てる
                self.ready_new = is_new
                self.ready_idx = idx
                self.ready = (path, data, w, h)
            except Exception as e:
                print('写真の読み込み失敗:', path, e)
                self.bad[path] = time.time() + 30.0
            finally:
                self.loading = False
        threading.Thread(target=work, daemon=True).start()

    def take(self):
        r = self.ready
        self.ready = None
        if r:
            self.current = r[0]
            self.cur_idx = self.ready_idx
        return r

    def has_new(self):
        return bool(self.new_queue)
