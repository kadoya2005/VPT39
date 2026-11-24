"""素材のサムネイル（操作パネルのクリップ一覧用）

- 動画は全体の 10% あたり（最大 3 秒目）の1コマ、画像はそのまま縮小
- 16:9 の JPEG にして `.thumbs` フォルダにキャッシュ（素材を差し替えると作り直す）
- 本番中に重くならないよう、同時に作るのは2つまで
"""
import os
import hashlib
import io
import threading
import time

from PIL import Image, ImageOps

from sources import HAS_VLC, VlcVideoSource, is_video, is_image, opencv, vlc

TW, TH = 320, 180
_sem = threading.Semaphore(2)
_lock = threading.Lock()
_mem = {}  # key -> jpeg bytes（最近使ったもの）


def _vlc_video_frame(path):
    source = VlcVideoSource(path, TW, TH, 'hold', options=[':no-audio'])
    try:
        deadline = time.monotonic() + 3.0
        image = None
        size = (TW, TH)
        while time.monotonic() < deadline:
            frame = source.frame()
            if frame is not None:
                if image is not None:
                    return Image.frombytes('RGB', (TW, TH), frame[2], 'raw', 'BGRX'), size
                image = Image.frombytes('RGB', (TW, TH), frame[2], 'raw', 'BGRX')
                try:
                    w, h = source.player.video_get_size(0)
                    if w and h:
                        size = (w, h)
                except vlc.VLCException:
                    pass
                _, duration = source.position()
                if not duration or not source.player.is_seekable():
                    break
                source.seek(min(0.1, 3.0 / duration))
                deadline = min(deadline, time.monotonic() + 0.5)
            elif source.failed:
                break
            time.sleep(0.02)
        return image, size
    finally:
        source.release()


def _video_frame(path):
    if HAS_VLC:
        return _vlc_video_frame(path)
    cv2 = opencv()
    if cv2 is None:
        return None, None
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None, None
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if n > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(min(n * 0.1, fps * 3)))
        ok, img = cap.read()
        if not ok:  # 位置指定に失敗したら先頭
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = cap.read()
        if not ok:
            return None, None
        image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        return image, image.size
    finally:
        cap.release()


def make(path):
    """サムネイルの JPEG バイト列（作れなければ None）"""
    if is_video(path):
        image, size = _video_frame(path)
        if image is None:
            return None
    elif is_image(path):
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert('RGB')
        size = image.size
    else:
        return None
    w, h = size
    scale = min(TW / w, TH / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    out = Image.new('RGB', (TW, TH))
    out.paste(image.resize((nw, nh), Image.Resampling.LANCZOS), ((TW - nw) // 2, (TH - nh) // 2))
    data = io.BytesIO()
    out.save(data, 'JPEG', quality=82)
    return data.getvalue()


def get(path, cache_dir):
    """キャッシュがあればそれを、なければ作って返す"""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = hashlib.sha1(f'{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}'.encode('utf-8')).hexdigest()
    with _lock:
        if key in _mem:
            return _mem[key]
    fp = os.path.join(cache_dir, key + '.jpg')
    data = None
    if os.path.exists(fp):
        try:
            with open(fp, 'rb') as f:
                data = f.read()
        except OSError:
            data = None
    if data is None:
        with _sem:
            try:
                data = make(path)
            except Exception as e:
                print('サムネイル作成失敗:', path, e)
                data = None
        if data is None:
            return None
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = fp + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, fp)
        except OSError:
            pass
    with _lock:
        if len(_mem) > 300:
            _mem.clear()
        _mem[key] = data
    return data
