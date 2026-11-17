"""VPT39 のテスト用素材を作る（動画・写真・動くGIF）。

使い方（VPT39 フォルダで）:
    .venv\\Scripts\\python.exe tools\\make_test_media.py
media\\テスト用\\ に動画、photos\\テスト用\\ に写真ができます。
映像は PIL で1コマずつ描き、ffmpeg で H.264（音は AAC）の mp4 にします。
ffmpeg は imageio-ffmpeg に入っているものを使います（無ければ  .venv\\Scripts\\python.exe -m pip install imageio-ffmpeg ）。
"""
import os
import sys
import math
import time
import wave
import shutil
import tempfile

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, 'media', 'テスト用')
PHOTO = os.path.join(HERE, 'photos', 'テスト用')
FONT = next((p for p in (r'C:\Windows\Fonts\BIZ-UDGothicB.ttc', r'C:\Windows\Fonts\YuGothB.ttc',
                         r'C:\Windows\Fonts\meiryob.ttc') if os.path.exists(p)), None)
SR = 48000


def font(size):
    return ImageFont.truetype(FONT, size) if FONT else ImageFont.load_default()


def text(d, xy, s, size, fill=(255, 255, 255), anchor='mm', stroke=0):
    d.text(xy, s, font=font(size), fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=(0, 0, 0))


# ---- 音 ----
def tone(sec, freq, vol=0.3):
    t = np.arange(int(SR * sec)) / SR
    env = np.minimum(1, np.minimum(t, sec - t) / 0.005)  # プチッと鳴らないように端をなめらかに
    return np.sin(2 * np.pi * freq * t) * vol * env


def write_wav(path, left, right=None):
    right = left if right is None else right
    st = np.stack([left, right], 1)
    st = (np.clip(st, -1, 1) * 32767).astype('<i2')
    with wave.open(path, 'wb') as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(st.tobytes())


def place(buf, start, snd):
    a = int(start * SR)
    b = min(len(buf), a + len(snd))
    buf[a:b] += snd[:b - a]


# ---- 映像をまとめる ----
def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        p = shutil.which('ffmpeg')
        if p:
            return p
        raise SystemExit('ffmpeg が見つかりません。 .venv\\Scripts\\python.exe -m pip install imageio-ffmpeg を実行してください')


def encode(name, size, fps, frames, sec, wav=None, crf=20):
    """frames(i, t) が PIL 画像を返す。ffmpeg に1コマずつ渡して H.264（yuv420p）＋AAC の mp4 にする。
    （どの再生ソフトでも映るように、色の形式はいちばん一般的なものにそろえる）"""
    import subprocess
    out = os.path.join(OUT, name)
    tmp_out = out + '.making.mp4'
    W, H = size
    cmd = [ffmpeg_exe(), '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
           '-r', str(fps), '-i', '-']
    if wav:
        cmd += ['-i', wav]
    cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf), '-pix_fmt', 'yuv420p',
            '-profile:v', 'high', '-color_range', 'tv', '-movflags', '+faststart']
    cmd += (['-c:a', 'aac', '-b:a', '192k', '-shortest'] if wav else ['-an'])
    cmd += [tmp_out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    n = int(round(sec * fps))
    try:
        for i in range(n):
            p.stdin.write(frames(i, i / fps).convert('RGB').tobytes())
            if i % fps == 0:
                print(f'\r  {name}: {i * 100 // n}%', end='', flush=True)
    finally:
        p.stdin.close()
        p.wait()
    if p.returncode != 0 or not os.path.exists(tmp_out):
        raise RuntimeError(f'mp4 を作れませんでした: {name}')
    os.replace(tmp_out, out)
    print(f'\r  {name}: できました（{os.path.getsize(out) / 1e6:.1f}MB）')

# ---- 1. カウント（切り替え・音のずれの確認）----
def count_video():
    W, H, fps, sec = 1920, 1080, 30, 12
    cols = [(200, 60, 60), (60, 140, 220), (60, 170, 90), (220, 160, 40), (150, 80, 200), (40, 170, 170)]
    snd = np.zeros(int(SR * sec))
    for s in range(sec):
        place(snd, s, tone(0.12, 1760 if s == 0 else 880))

    def fr(i, t):
        s = int(t)
        im = Image.new('RGB', (W, H), cols[s % len(cols)])
        d = ImageDraw.Draw(im)
        text(d, (W / 2, H / 2 - 40), str(s + 1), 520, stroke=8)
        frac = t - s
        d.rectangle((0, H - 40, int(W * frac), H), fill=(255, 255, 255))  # 1秒ごとに左から伸びるバー
        text(d, (40, 50), 'テスト 01 カウント（音あり：1秒ごとにピッ、最初は高い音）', 40, anchor='lm', stroke=3)
        text(d, (W - 40, H - 80), f'{t:05.2f} 秒', 48, anchor='rm', stroke=3)
        return im
    wav = os.path.join(tempfile.gettempdir(), 'vpt39_count.wav')
    write_wav(wav, snd)
    encode('01_カウント_音あり.mp4', (W, H), fps, fr, sec, wav)


# ---- 2. グリッド（マッピング・なめらかさの確認）----
def grid_base(W, H, label):
    im = Image.new('RGB', (W, H), (18, 18, 22))
    d = ImageDraw.Draw(im)
    for x in range(0, W + 1, 120):
        d.line((x, 0, x, H), fill=(70, 70, 80), width=2)
    for y in range(0, H + 1, 120):
        d.line((0, y, W, y), fill=(70, 70, 80), width=2)
    d.line((W / 2, 0, W / 2, H), fill=(0, 200, 255), width=3)
    d.line((0, H / 2, W, H / 2), fill=(0, 200, 255), width=3)
    r = min(W, H) * 0.42
    d.ellipse((W / 2 - r, H / 2 - r, W / 2 + r, H / 2 + r), outline=(255, 255, 255), width=4)
    d.line((0, 0, W, H), fill=(90, 90, 100), width=2)
    d.line((W, 0, 0, H), fill=(90, 90, 100), width=2)
    d.rectangle((2, 2, W - 3, H - 3), outline=(255, 60, 60), width=6)
    for (x, y, a, s) in ((30, 30, 'la', '左上'), (W - 30, 30, 'ra', '右上'), (30, H - 30, 'ld', '左下'), (W - 30, H - 30, 'rd', '右下')):
        text(d, (x, y), s, 56, fill=(255, 220, 0), anchor=a, stroke=3)
    text(d, (W / 2, 70), label, 44, anchor='mm', stroke=3)
    return im


def grid_video():
    W, H, fps, sec = 1920, 1080, 60, 20
    base = grid_base(W, H, 'テスト 02 グリッド（四隅合わせ・60fps のなめらかさ）')

    def fr(i, t):
        im = base.copy()
        d = ImageDraw.Draw(im)
        x = int((t * 480) % (W + 80)) - 40  # 左右に流れる白い帯（カクつき確認）
        d.rectangle((x, H / 2 + 140, x + 40, H / 2 + 300), fill=(255, 255, 255))
        a = t * 2 * math.pi / 4  # 4秒で1回転する針
        cx, cy, r = W / 2, H / 2, min(W, H) * 0.42
        d.line((cx, cy, cx + r * math.sin(a), cy - r * math.cos(a)), fill=(255, 80, 80), width=6)
        text(d, (W / 2, H / 2 - 160), f'{int(t // 60):02d}:{t % 60:05.2f}', 120, stroke=5)
        text(d, (W / 2, H / 2 + 60), f'フレーム {i}', 52, stroke=3)
        return im
    encode('02_グリッド_60fps.mp4', (W, H), fps, fr, sec)


# ---- 3. カラーバー（色・明るさの確認）----
def bars_video():
    W, H, fps, sec = 1920, 1080, 30, 10
    im0 = Image.new('RGB', (W, H), (0, 0, 0))
    d = ImageDraw.Draw(im0)
    cols = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0), (0, 0, 192)]
    bw = W / 7
    for k, c in enumerate(cols):
        d.rectangle((k * bw, 0, (k + 1) * bw, H * 0.62), fill=c)
    for k in range(16):  # 明るさの段階（黒から白まで16段）
        v = int(255 * k / 15)
        d.rectangle((k * W / 16, H * 0.62, (k + 1) * W / 16, H * 0.82), fill=(v, v, v))
        text(d, ((k + .5) * W / 16, H * 0.72), str(k), 30, fill=(255 - v, 255 - v, 255 - v))
    text(d, (W / 2, H * 0.9), 'テスト 03 カラーバー（色・明るさ・黒のつぶれの確認／小さな 1kHz の音）', 40, stroke=3)
    snd = tone(sec, 1000, 0.08)

    def fr(i, t):
        im = im0.copy()
        dd = ImageDraw.Draw(im)
        x = int((t / sec) * W)
        dd.rectangle((x - 3, 0, x + 3, H * 0.62), fill=(255, 255, 255))
        return im
    wav = os.path.join(tempfile.gettempdir(), 'vpt39_bars.wav')
    write_wav(wav, snd)
    encode('03_カラーバー.mp4', (W, H), fps, fr, sec, wav)


# ---- 4. 横長（2台のプロジェクターをつないだときの確認）----
def wide_video():
    W, H, fps, sec = 3840, 1080, 30, 16
    base = grid_base(W, H, 'テスト 04 横長（3840×1080：プロジェクター2台用）')
    d = ImageDraw.Draw(base)
    text(d, (W * 0.25, H * 0.3), '左のプロジェクター', 90, fill=(120, 200, 255), stroke=4)
    text(d, (W * 0.75, H * 0.3), '右のプロジェクター', 90, fill=(255, 170, 120), stroke=4)
    for k in range(64):  # 下に左右へ続くグラデーション（つなぎ目の明るさ確認）
        v = int(255 * (0.5 + 0.5 * math.sin(k / 63 * math.pi)))
        d.rectangle((k * W / 64, H * 0.82, (k + 1) * W / 64, H * 0.95), fill=(v, v, v))

    def fr(i, t):
        im = base.copy()
        dd = ImageDraw.Draw(im)
        x = int((t / sec) * W)  # つなぎ目を横切る線
        dd.rectangle((x - 4, 0, x + 4, H), fill=(255, 255, 0))
        r = 70
        dd.ellipse((x - r, H / 2 - r, x + r, H / 2 + r), outline=(255, 255, 0), width=8)
        text(dd, (W / 2, H * 0.62), f'{t:05.2f} 秒', 80, stroke=4)
        return im
    encode('04_横長_3840x1080.mp4', (W, H), fps, fr, sec, crf=22)


# ---- 5. 左右の音 ----
def lr_video():
    W, H, fps, sec = 1280, 720, 30, 12
    L = np.zeros(int(SR * sec)); R = np.zeros(int(SR * sec))
    for s in range(sec):
        (L if s % 2 == 0 else R)[int(s * SR):int(s * SR) + int(0.6 * SR)] += tone(0.6, 660 if s % 2 == 0 else 990)

    def fr(i, t):
        s = int(t)
        left = s % 2 == 0
        on = t - s < 0.6
        im = Image.new('RGB', (W, H), (20, 20, 26))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, W / 2, H) if left else (W / 2, 0, W, H), fill=(60, 140, 220) if on else (40, 60, 90))
        text(d, (W / 4, H / 2), '左', 260, stroke=6, fill=(255, 255, 255) if left else (90, 90, 100))
        text(d, (W * 3 / 4, H / 2), '右', 260, stroke=6, fill=(255, 255, 255) if not left else (90, 90, 100))
        text(d, (W / 2, 50), 'テスト 05 左右の音（光っている側のスピーカーから鳴れば正しい）', 30, stroke=2)
        return im
    wav = os.path.join(tempfile.gettempdir(), 'vpt39_lr.wav')
    write_wav(wav, L, R)
    encode('05_左右の音.mp4', (W, H), fps, fr, sec, wav)


# ---- 写真・動くGIF ----
def photos():
    os.makedirs(PHOTO, exist_ok=True)
    spec = [('写真_01_横長.jpg', (1920, 1080), (200, 90, 60)), ('写真_02_4対3.jpg', (1600, 1200), (60, 130, 200)),
            ('写真_03_縦長.jpg', (1080, 1620), (70, 160, 90)), ('写真_04_正方形.jpg', (1200, 1200), (170, 90, 190))]
    for k, (n, (w, h), c) in enumerate(spec):
        im = Image.new('RGB', (w, h), c)
        d = ImageDraw.Draw(im)
        for x in range(0, w, 100):
            d.line((x, 0, x, h), fill=tuple(min(255, v + 30) for v in c), width=2)
        text(d, (w / 2, h / 2), str(k + 1), min(w, h) // 2, stroke=8)
        text(d, (w / 2, h - 80), f'{n}（{w}×{h}）', 44, stroke=3)
        im.save(os.path.join(PHOTO, n), quality=90)
    print('  写真: 4枚 →', PHOTO)


def anim_gif():
    fr = []
    for k in range(24):
        im = Image.new('RGBA', (480, 480), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        a = k / 24 * 2 * math.pi
        for j in range(8):
            b = a + j / 8 * 2 * math.pi
            x, y = 240 + 170 * math.cos(b), 240 + 170 * math.sin(b)
            r = 22 + 14 * (j / 7)
            d.ellipse((x - r, y - r, x + r, y + r), fill=(80 + 20 * j, 220 - 15 * j, 140, 255))
        text(d, (240, 240), 'GIF', 90, stroke=4)
        fr.append(im)
    p = os.path.join(OUT, '06_動くGIF_透明あり.gif')
    fr[0].save(p, save_all=True, append_images=fr[1:], duration=50, loop=0, disposal=2, transparency=0)
    png = os.path.join(OUT, '07_透明PNG_丸.png')
    im = Image.new('RGBA', (1200, 1200), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((100, 100, 1100, 1100), fill=(255, 255, 255, 255))
    d.ellipse((300, 300, 900, 900), fill=(0, 0, 0, 0))
    text(d, (600, 600), '透明', 160, fill=(255, 200, 0, 255), stroke=6)
    im.save(png)
    print('  動くGIF・透明PNG →', OUT)


def main():
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[1:]
    jobs = [('count', count_video), ('grid', grid_video), ('bars', bars_video), ('wide', wide_video),
            ('lr', lr_video), ('photos', photos), ('gif', anim_gif)]
    for k, fn in jobs:
        if not only or k in only:
            fn()
    print('完了:', OUT)


if __name__ == '__main__':
    main()
