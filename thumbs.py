"""素材のサムネイル（操作パネルのクリップ一覧用）

- 動画は全体の 10% あたり（最大 3 秒目）の1コマ、画像はそのまま縮小
- 16:9 の JPEG にして `.thumbs` フォルダにキャッシュ（素材を差し替えると作り直す）
- 本番中に重くならないよう、同時に作るのは2つまで
"""
import os
import hashlib
import threading

import numpy as np

from sources import is_video, is_image

try:
    import cv2
except Exception:
    cv2 = None

TW, TH = 320, 180
_sem = threading.Semaphore(2)
_lock = threading.Lock()
_mem = {}  # key -> jpeg bytes（最近使ったもの）


def _fit(img):
    """BGR 画像を TW×TH の黒背景に縦横比を保って収める"""
    h, w = img.shape[:2]
    s = min(TW / w, TH / h)
    nw, nh = max(1, int(w * s)), max(1, int(h * s))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((TH, TW, 3), np.uint8)
    x, y = (TW - nw) // 2, (TH - nh) // 2
    out[y:y + nh, x:x + nw] = small
    return out


def _video_frame(path):
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        if n > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(min(n * 0.1, fps * 3)))
        ok, img = cap.read()
        if not ok:  # 位置指定に失敗したら先頭
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = cap.read()
        return img if ok else None
    finally:
        cap.release()


def _image_frame(path):
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(Image.open(path)).convert('RGB')
    im.thumbnail((TW * 2, TH * 2))
    return np.asarray(im)[:, :, ::-1].copy()  # RGB→BGR


def make(path):
    """サムネイルの JPEG バイト列（作れなければ None）"""
    if cv2 is None:
        return None
    img = _video_frame(path) if is_video(path) else _image_frame(path) if is_image(path) else None
    if img is None:
        return None
    ok, buf = cv2.imencode('.jpg', _fit(img), [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes() if ok else None


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
