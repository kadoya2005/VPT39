"""レイヤー・合成・四隅変形（ホモグラフィ）描画"""
import os
import time
import copy

import numpy as np
import moderngl

from sources import make_source, ImageSource, PhotoFeeder, is_image

DEFAULT_CORNERS = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]  # 左上,右上,右下,左下
BLENDS = ['normal', 'add', 'screen', 'multiply']
CUE_MODES = ['loop', 'hold', 'stop', 'next']
# 写真ウォールの切り替え方
TRANSITIONS = ['fade', 'cut', 'slide_left', 'slide_right', 'slide_up', 'slide_down', 'wipe', 'zoom']
XF0, WIPE0 = (0.0, 0.0, 1.0), (0.0, 0.0)


def layer_defaults(kind='video', name=None):
    d = {
        'name': name or ('写真ウォール' if kind == 'photo' else '動画レイヤー'),
        'kind': kind,
        'visible': True,
        'opacity': 1.0,
        'blend': 'normal',
        'fade': 1.0,
        'audio': kind == 'video',
        'volume': 1.0,
        'corners': copy.deepcopy(DEFAULT_CORNERS),
        'feather': 0.0,
        'test': False,
        'res': [1920, 1080],
        'cues': [],
        'output': 0,  # 出力番号（0=出力1）
    }
    if kind == 'photo':
        d.update({'photo_folder': 'photos', 'photo_interval': 6.0,
                  'photo_border': 0.03, 'photo_newest': True, 'fade': 0.8,
                  'photo_trans': 'fade'})
    return d


def homography(dst):
    """単位正方形 (0,0)-(1,1) を 四隅 dst に写す 3x3 行列"""
    src = [(0, 0), (1, 0), (1, 1), (0, 1)]
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def inv_homography_bytes(corners):
    try:
        H = homography(corners)
        Hi = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return None
    return Hi.T.astype('f4').tobytes()  # GLSL は列優先


# ---------------------------------------------------------------------------
class Deck:
    """1つのレイヤー内で再生中の1素材（クロスフェード用に複数重なる）"""

    def __init__(self, src, label, cue=-1, mode='loop'):
        self.src = src
        self.label = label
        self.cue = cue
        self.mode = mode
        self.tex = None
        self.alpha = 0.0
        self.target = 1.0

    def release(self):
        self.src.release()
        if self.tex is not None and not getattr(self.src, 'shared_tex', False):
            self.tex.release()
        self.tex = None


# 特別なクリップ（ファイルではないもの）
BLANK = '@blank'      # 空白：何も映さない・音も出さない
LAYER = '@layer:'     # @layer:<id> 別レイヤーの映像
CAMERA = '@camera'    # カメラ・キャプチャーボード（vdev, adev）
WINDOW = '@window'    # ウィンドウ取り込み（title）
SPOUT = '@spout'      # Spout で受け取る（sender）


def is_special(f):
    return isinstance(f, str) and f.startswith('@')


def cue_label(cue, app=None):
    f = cue.get('file', '')
    if f == BLANK:
        return '（空白）'
    if f.startswith(LAYER):
        L = app.layer_by_id(f[len(LAYER):]) if app else None
        return f'レイヤー映像: {L.cfg["name"]}' if L else 'レイヤー映像（元のレイヤーがありません）'
    if f == CAMERA:
        return 'カメラ: ' + cue.get('vdev', '')
    if f == WINDOW:
        return 'ウィンドウ: ' + cue.get('title', '')
    if f == SPOUT:
        return 'Spout: ' + cue.get('sender', '')
    return f


class Layer:
    def __init__(self, cfg, app):
        self.cfg = cfg
        self.app = app
        self.decks = []
        self.feeder = None
        self.feeder_key = None
        self.next_photo_at = 0.0
        self.last_switch = 0.0
        self.error = ''
        self.gain = 0.0

    # ---- 操作 ----
    @property
    def current_cue(self):
        for d in reversed(self.decks):
            if d.target > 0:
                return d.cue
        return -1

    def go(self, i):
        cues = self.cfg['cues']
        if not (0 <= i < len(cues)):
            return
        if self.cfg['kind'] == 'photo':
            # 写真ウォール：行の i 番目の画像を、すぐ次に出す（そこから順番に続く）
            if self.feeder is not None:
                self.feeder.jump(i)
            self.skip_photo()
            return
        cue = cues[i]
        f = cue.get('file', '')
        w, h = self.cfg['res']
        if is_special(f):
            try:
                src = self._special_source(cue, w, h)
            except Exception as e:
                src, self.error = None, f'開けません: {cue_label(cue, self.app)} ({e})'
            if src is None:
                print(self.error)
                return
            self.error = getattr(getattr(src, 'audio', None), 'error', '') or ''
            if f == BLANK:
                for d in self.decks:  # 空白：今の映像と音をフェードアウト
                    d.target = 0.0
            self.decks.append(Deck(src, cue_label(cue, self.app), i, 'hold'))
            return
        path = self.app.media_path(f)
        if not os.path.isfile(path):
            # ファイルが無いときは切り替えない（今の映像を流したまま、赤字で知らせる）
            self.error = f'ファイルが見つかりません: {f}'
            print(self.error)
            return
        try:
            src = make_source(path, w, h, cue.get('mode', 'loop'))
        except Exception as e:
            self.error = f'読み込み失敗: {f} ({e})'
            print(self.error)
            return
        self.error = ''
        # 新しい素材は最初の映像が出てからフェードイン（それまで前の素材の映像と音を流し続ける）
        d = Deck(src, f, i, cue.get('mode', 'loop'))
        self.decks.append(d)

    def _special_source(self, cue, w, h):
        from sources import BlankSource, LayerSource, CameraSource, WindowSource, SpoutSource
        f = cue['file']
        if f == BLANK:
            return BlankSource()
        if f.startswith(LAYER):
            lid = f[len(LAYER):]
            if lid == self.cfg.get('id'):
                self.error = '自分自身の映像は使えません'
                return None
            if self.app.layer_by_id(lid) is None:
                self.error = '元のレイヤーが見つかりません（削除された可能性があります）'
                return None
            from sources import layer_refs_chain
            if self.cfg.get('id') in layer_refs_chain(self.app, lid):
                self.error = 'おたがいの映像を映し合う形になるので使えません（元のレイヤーが、このレイヤーの映像を映しています）'
                return None
            return LayerSource(self.app, lid)
        if f == CAMERA:
            return CameraSource(cue.get('vdev', ''), cue.get('adev', ''), w, h)
        if f == WINDOW:
            s = WindowSource(cue.get('title', ''))
            if s.error:
                self.error = s.error
                return None
            return s
        if f == SPOUT:
            s = SpoutSource(cue.get('sender', ''))
            if s.error:
                self.error = s.error
                return None
            return s
        self.error = f'不明なクリップ: {f}'
        return None

    # ---- 映す場所（出力ごとの四隅）----
    def places(self):
        """[(出力番号, 四隅), ...] 1つ目が基本の出力。四隅のリストは直接書き換えてよい"""
        out = [(int(self.cfg.get('output', 0)), self.cfg['corners'])]
        for m in self.cfg.get('more', []):
            out.append((int(m['output']), m['corners']))
        return out

    def corners_for(self, k):
        for o, c in self.places():
            if o == k:
                return c
        return None

    def set_corners(self, k, pts):
        if int(self.cfg.get('output', 0)) == k:
            self.cfg['corners'] = pts
            return True
        for m in self.cfg.get('more', []):
            if int(m['output']) == k:
                m['corners'] = pts
                return True
        return False

    def next(self):
        n = len(self.cfg['cues'])
        if n:
            self.go((self.current_cue + 1) % n)

    def prev(self):
        n = len(self.cfg['cues'])
        if n:
            self.go((self.current_cue - 1) % n)

    def clear(self):
        # 写真ウォールは「次の写真 ▶」を押すまで新しい写真を出さない（消したのに次の写真が出てこないように）
        self.photo_stopped = self.cfg['kind'] == 'photo'
        for d in self.decks:
            d.target = 0.0
        if self.cfg['fade'] <= 0:
            for d in self.decks:
                d.alpha = 0.0

    # ---- クリップのミニ操作（一時停止・頭出し・シーク）----
    def top_deck(self):
        for d in reversed(self.decks):
            if d.target > 0:
                return d
        return None

    def transport(self, cue, op, value=None):
        """op: 'toggle'（再生/一時停止）, 'restart'（頭出し）, 'seek'（value=0〜1）, 'stop'（消す）"""
        d = self.top_deck()
        current = d is not None and d.cue == cue
        if op == 'stop':
            if current:
                self.clear()
            return
        if not current:
            # まだ再生していないクリップ → 再生を始めてから操作
            self.go(cue)
            d = self.decks[-1] if self.decks and self.decks[-1].cue == cue else None
            if d is None:
                return
            if op == 'seek':
                d.src.seek(value or 0.0)
            return
        if op == 'toggle':
            d.src.set_paused(not d.src.paused)
        elif op == 'restart':
            d.src.seek(0.0)
            if d.src.paused:
                d.src.set_paused(False)
        elif op == 'seek':
            d.src.seek(value or 0.0)

    def deck_draws(self):
        """描く素材の一覧 [(deck, 不透明度, ずらし・拡大, ワイプ)]。写真ウォールは切り替え方に合わせて動かす"""
        ds = [d for d in self.decks if d.tex is not None and d.alpha > 0]
        tr = self.cfg.get('photo_trans', 'fade') if self.cfg['kind'] == 'photo' else 'fade'
        if tr in ('fade', 'cut') or not ds or ds[-1].target <= 0:  # 消すときはふつうにフェードアウト
            return [(d, d.alpha, XF0, WIPE0) for d in ds]
        top, p = ds[-1], ds[-1].alpha
        e = p * p * (3 - 2 * p)  # ゆっくり始まり、ゆっくり止まる
        sl = {'slide_left': (1, 0), 'slide_right': (-1, 0), 'slide_up': (0, 1), 'slide_down': (0, -1)}
        out = []
        for d in ds[:-1]:
            if tr in sl:  # 前の写真は押し出される
                vx, vy = sl[tr]
                out.append((d, 1.0, (-vx * e, -vy * e, 1.0), WIPE0))
            else:
                out.append((d, 1.0 if tr == 'wipe' else d.alpha, XF0, WIPE0))
        if tr in sl:
            vx, vy = sl[tr]
            out.append((top, 1.0, (vx * (1 - e), vy * (1 - e), 1.0), WIPE0))
        elif tr == 'wipe':
            s = 0.08
            out.append((top, 1.0, XF0, (e * (1 + s), s)))
        else:  # zoom：少し大きい所から、フェードしながら元の大きさへ
            out.append((top, p, (0.0, 0.0, 1.0 + 0.18 * (1 - e)), WIPE0))
        return out

    def skip_photo(self):
        self.photo_stopped = False
        self.next_photo_at = 0.0
        self.last_switch = 0.0

    # ---- 毎フレーム ----
    def update(self, dt, now):
        if self.cfg['kind'] == 'photo':
            self._update_photo(now)

        fade = self.cfg['fade']
        step = 1.0 if fade <= 0 else dt / fade
        for d in self.decks:
            d.src.update()
            if getattr(d.src, 'failed', False) and d.target > 0:
                # 壊れていて再生できない動画：黒くしたままにせず、知らせて消す
                self.error = getattr(d.src, 'fail_msg', '') or \
                    f'再生できません（ファイルが壊れているか、対応していない形式）: {d.label}'
                d.target = 0.0
                continue
            if d.src.ended and d.target > 0:
                if d.mode == 'stop':
                    d.target = 0.0
                elif d.mode == 'next' and d is self.decks[-1]:
                    d.mode = 'hold'
                    self.next()
            if d.alpha < d.target:
                if d.src.ready:
                    d.alpha = min(d.target, d.alpha + step)
            elif d.alpha > d.target:
                d.alpha = max(d.target, d.alpha - step)

        # ライブ入力が途中で止まった（ケーブルが抜けた・送る側が止まった）ときは、最後の映像のまま赤字で知らせる
        cur = self.top_deck()
        stall = getattr(cur.src, 'stall_msg', '') if cur else ''
        if stall:
            self.error = self._stall = stall
        elif getattr(self, '_stall', '') and self.error == self._stall:
            self.error = self._stall = ''  # 戻ったら消す

        # 一番上が完全に表示されたら下の素材を解放
        top = None
        for d in reversed(self.decks):
            if d.target > 0:
                top = d
                break
        keep = []
        for d in self.decks:
            dead = (d.target <= 0 and d.alpha <= 0) or \
                   (top is not None and d is not top and top.alpha >= 1.0 and
                    self.decks.index(d) < self.decks.index(top))
            if dead:
                d.release()
            else:
                keep.append(d)
        self.decks = keep

        # 音量（クロスフェードに合わせて上下）。映像の表示/非表示やブラックアウトとは関係なく鳴らし続ける
        base = self.app.audio_gain(self)
        total = 0.0
        for i, d in enumerate(self.decks):
            v = d.alpha
            for nd in self.decks[i + 1:]:
                v *= (1.0 - nd.alpha)
            d.src.set_volume(v * base)
            if not d.src.paused and not d.src.ended and not getattr(d.src, 'silent', False):
                total += v * base
        self.gain = min(1.0, total)  # ミキサーの表示用

    def _update_photo(self, now):
        c = self.cfg
        folder = self.app.resolve(c.get('photo_folder', 'photos'))
        key = (folder, tuple(c['res']), c.get('photo_border', 0))
        if self.feeder is None or key != self.feeder_key:
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
            self.feeder = PhotoFeeder(folder, c['res'][0], c['res'][1], c.get('photo_border', 0))
            self.feeder_key = key
        f = self.feeder
        # 行に画像を並べていれば、フォルダの代わりにその順で出す（なければフォルダの写真）
        listed = [self.app.media_path(q.get('file', '')) for q in c.get('cues', [])
                  if not is_special(q.get('file', '')) and is_image(q.get('file', ''))]
        if f.set_list(listed or None):
            self.next_photo_at = 0.0  # 行の画像を変えたら、すぐ新しい並びで出し直す
            self.photo_stopped = False
        # 投影する四角形の縦横比に合わせて写真キャンバスを作る（写真が歪まないように）
        ow, oh = self.app.out_size(self)
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = c['corners']
        qw = (((x1 - x0) * ow) ** 2 + ((y1 - y0) * oh) ** 2) ** 0.5 + \
             (((x2 - x3) * ow) ** 2 + ((y2 - y3) * oh) ** 2) ** 0.5
        qh = (((x3 - x0) * ow) ** 2 + ((y3 - y0) * oh) ** 2) ** 0.5 + \
             (((x2 - x1) * ow) ** 2 + ((y2 - y1) * oh) ** 2) ** 0.5
        if qw > 1 and qh > 1:
            aspect = qw / qh
            base = max(c['res'])
            if aspect >= 1:
                f.w, f.h = base, max(64, int(base / aspect))
            else:
                f.w, f.h = max(64, int(base * aspect)), base
        if now - f.last_scan > 2.0:
            f.scan()
        newest = c.get('photo_newest', True)
        f.prepare(newest)
        if f.ready is None or getattr(self, 'photo_stopped', False):
            return
        # 切り替えの途中は次の写真を重ねない（間隔より切り替え時間が長いと、写真が何枚も積み重なってメモリを食うため）
        if any(d.target > 0 and d.alpha < d.target for d in self.decks):
            return
        interval = c.get('photo_interval', 6.0)
        due = (interval > 0 and now >= self.next_photo_at) or self.next_photo_at == 0.0
        urgent = newest and getattr(f, 'ready_new', False) and now - self.last_switch >= 3.0
        # 行の画像が1枚だけで、もうそれを出しているなら、出し続ける（同じ写真へ切り替え直さない）
        top = self.top_deck()
        if f.fixed is not None and len(f.order) == 1 and top is not None and getattr(top, 'path', None) == f.order[0]:
            if due:
                self.next_photo_at = now + interval if interval > 0 else float('inf')
            return
        if due or urgent:
            path, data, w, h = f.take()
            d = Deck(ImageSource(path, w, h, data=data), os.path.basename(path),
                     cue=f.cur_idx if f.fixed is not None else -1)
            d.path = path
            if c['fade'] <= 0 or c.get('photo_trans') == 'cut':
                d.alpha = 1.0
            self.decks.append(d)
            self.last_switch = now
            self.next_photo_at = now + interval if interval > 0 else float('inf')

    def release(self):
        for d in self.decks:
            d.release()
        self.decks = []


# ---------------------------------------------------------------------------
VS = """
#version 330
in vec2 in_pos;
void main(){ gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

FS = """
#version 330
uniform sampler2D tex;
uniform mat3 hinv;
uniform vec2 res;
uniform vec2 origin;   // 出力の左下（ウィンドウ内の画素位置）
uniform float opacity;
uniform float feather;
uniform int mode;      // 0=素材, 1=テストパターン
uniform int premul;
uniform vec3 tint;
uniform vec3 xf;       // 写真の切り替え用：中身のずらし (x, y) と拡大率
uniform vec2 wipe;     // 写真の切り替え用：ワイプの位置とぼかし幅（幅0＝使わない）
out vec4 fragColor;
void main(){
    vec2 p = (gl_FragCoord.xy - origin) / res;
    p.y = 1.0 - p.y;
    vec3 q = hinv * vec3(p, 1.0);
    if (q.z <= 0.0) discard;
    vec2 uv = q.xy / q.z;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
    vec4 c;
    if (mode == 1) {
        vec2 g = uv * 8.0;
        vec2 fw = fwidth(g);
        vec2 d = abs(fract(g + 0.5) - 0.5) / fw;
        float line = 1.0 - clamp(min(d.x, d.y) - 0.5, 0.0, 1.0);
        vec2 e = min(uv, 1.0 - uv) / fwidth(uv);
        float border = 1.0 - clamp(min(e.x, e.y) - 4.0, 0.0, 1.0);
        float diag = 1.0 - clamp(min(abs(uv.x - uv.y), abs(uv.x + uv.y - 1.0)) / fwidth(uv.x) - 0.5, 0.0, 1.0);
        float r = length((uv - 0.5) * vec2(res.x / max(res.y, 1.0), 1.0));  // 出力の縦横の比に合わせて丸く
        float circ = 1.0 - clamp(abs(r - 0.3) / fwidth(r) - 0.5, 0.0, 1.0);
        vec3 col = tint * 0.25;
        col = mix(col, vec3(0.85), max(line, max(diag * 0.6, circ)));
        col = mix(col, tint, border);
        c = vec4(col, 1.0);
    } else {
        // レイヤー映像（GPU で描いたテクスチャ）は行が下から上なので上下を戻す
        vec2 s = (uv - 0.5) / xf.z + 0.5 - xf.xy;
        if (s.x < 0.0 || s.x > 1.0 || s.y < 0.0 || s.y > 1.0) discard;
        c = texture(tex, premul == 1 ? vec2(s.x, 1.0 - s.y) : s);
    }
    float k = opacity;
    if (wipe.y > 0.0) k *= 1.0 - smoothstep(wipe.x - wipe.y, wipe.x, uv.x);
    if (feather > 0.0) {
        vec2 m = min(uv, 1.0 - uv);
        k *= smoothstep(0.0, feather, min(m.x, m.y));
    }
    // premul=1：レイヤー映像（すでに透明度を掛けた色）
    fragColor = premul == 1 ? c * k : vec4(c.rgb * c.a * k, c.a * k);
}
"""

SOLID_VS = """
#version 330
in vec2 in_pos;
void main(){ gl_Position = vec4(in_pos, 0.0, 1.0); }
"""
SOLID_FS = """
#version 330
uniform vec4 color;
out vec4 fragColor;
void main(){ fragColor = vec4(color.rgb * color.a, color.a); }
"""

TINTS = [(1.0, 0.35, 0.35), (0.35, 0.8, 1.0), (0.45, 1.0, 0.45),
         (1.0, 0.85, 0.3), (0.85, 0.5, 1.0), (0.3, 1.0, 0.9)]


class Renderer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.prog = ctx.program(vertex_shader=VS, fragment_shader=FS)
        self.solid = ctx.program(vertex_shader=SOLID_VS, fragment_shader=SOLID_FS)
        quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype='f4')
        self.vbo = ctx.buffer(quad.tobytes())
        self.vao = ctx.vertex_array(self.prog, [(self.vbo, '2f', 'in_pos')])
        self.dyn = ctx.buffer(reserve=4096)
        self.svao = ctx.vertex_array(self.solid, [(self.dyn, '2f', 'in_pos')])
        self.dummy = ctx.texture((1, 1), 4, b'\x00\x00\x00\x00')

    def set_blend(self, mode):
        c = self.ctx
        c.enable(moderngl.BLEND)
        if mode == 'add':
            c.blend_func = (moderngl.ONE, moderngl.ONE)
        elif mode == 'screen':
            c.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_COLOR)
        elif mode == 'multiply':
            c.blend_func = (moderngl.DST_COLOR, moderngl.ONE_MINUS_SRC_ALPHA)
        else:
            c.blend_func = (moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA)

    def upload(self, deck):
        if getattr(deck.src, 'shared_tex', False):
            return  # レイヤー映像：テクスチャは本体が割り当てる
        fr = deck.src.frame()
        if fr is None:
            return
        w, h, data, swz, _ = fr
        comps = len(data) // (w * h)  # 1画素あたりのバイト数（3 or 4）
        t = deck.tex
        if t is None or t.size != (w, h) or t.components != comps:
            if t is not None:
                t.release()
            t = self.ctx.texture((w, h), comps, data, alignment=1)
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            deck.tex = t
        else:
            t.write(data)
        t.swizzle = swz

    def draw_quad(self, tex, corners, size, opacity, feather=0.0, mode=0, tint=(1, 1, 1), origin=(0, 0),
                  premul=False, xf=XF0, wipe=WIPE0):
        hb = inv_homography_bytes(corners)
        if hb is None:
            return
        p = self.prog
        p['premul'].value = 1 if premul else 0
        p['hinv'].write(hb)
        p['res'].value = size
        p['origin'].value = origin
        p['opacity'].value = float(opacity)
        p['feather'].value = float(feather)
        p['mode'].value = mode
        p['tint'].value = tint
        p['xf'].value = xf
        p['wipe'].value = wipe
        (tex or self.dummy).use(0)
        p['tex'].value = 0
        self.vao.render(moderngl.TRIANGLE_STRIP)

    def draw_solid(self, pts_ndc, color, prim):
        data = np.array(pts_ndc, dtype='f4').tobytes()
        self.dyn.orphan(max(len(data), 16))
        self.dyn.write(data)
        self.solid['color'].value = color
        self.svao.render(prim, vertices=len(pts_ndc))

    def upload_all(self, layers, used=()):
        """動画のフレームを1フレームに1回だけ転送（出力がいくつあっても）。
        used: 別レイヤーの素材として使われているレイヤーの id（非表示でも転送する）"""
        for L in layers:
            if (L.cfg['visible'] and not L.cfg['test']) or L.cfg.get('id') in used:
                for d in L.decks:
                    self.upload(d)

    def render_layer_content(self, L, fbo, size):
        """レイヤーの中身（四隅変形や不透明度をかける前）を、別レイヤーの素材用に描く"""
        ctx = self.ctx
        fbo.use()
        ctx.viewport = (0, 0, size[0], size[1])
        ctx.clear(0.0, 0.0, 0.0, 0.0)
        self.set_blend('normal')
        for d, a, xf, wp in L.deck_draws():
            self.draw_quad(d.tex, DEFAULT_CORNERS, size, a, premul=getattr(d.src, 'shared_tex', False),
                           xf=xf, wipe=wp)

    def render_output(self, fbo, size, mine, master, edit=False, selected=0, sel_corner=0, hud=None):
        """1つの出力をフレームバッファに描く。mine: その出力に映す [(レイヤー番号, Layer, 四隅), ...]"""
        ctx = self.ctx
        fbo.use()
        ctx.viewport = (0, 0, size[0], size[1])
        ctx.clear(0.0, 0.0, 0.0, 1.0)
        org = (0, 0)
        if True:
            for li, L, corners in mine:
                c = L.cfg
                if not c['visible']:
                    continue
                self.set_blend(c['blend'])
                tint = TINTS[li % len(TINTS)]
                if c['test']:
                    self.draw_quad(None, corners, size, c['opacity'] * master, c['feather'], 1, tint, org)
                    continue
                for d, a, xf, wp in L.deck_draws():
                    self.draw_quad(d.tex, corners, size, c['opacity'] * a * master,
                                   c['feather'], origin=org, premul=getattr(d.src, 'shared_tex', False),
                                   xf=xf, wipe=wp)

            if edit:
                self.set_blend('normal')
                for li, L, corners in mine:
                    pts = [(2 * x - 1, 1 - 2 * y) for x, y in corners]
                    sel = li == selected
                    col = ((1.0, 0.2, 0.2, 1.0) if sel_corner == 4 else (1.0, 0.9, 0.2, 1.0)) if sel \
                        else (1.0, 1.0, 1.0, 0.45)  # 赤い枠＝全体を動かす
                    self.draw_solid(pts, col, moderngl.LINE_LOOP)
                    if sel:
                        for ci, (x, y) in enumerate(pts):
                            hx, hy = 9.0 / size[0], 9.0 / size[1]
                            sq = [(x - hx, y - hy), (x + hx, y - hy), (x - hx, y + hy),
                                  (x + hx, y - hy), (x + hx, y + hy), (x - hx, y + hy)]
                            hc = (1.0, 0.2, 0.2, 1.0) if ci == sel_corner else (1.0, 0.9, 0.2, 1.0)
                            self.draw_solid(sq, hc, moderngl.TRIANGLES)
            if hud is not None:
                self.set_blend('normal')
                tex, (tw, th) = hud
                x0, y0 = 12 / size[0], 1.0 - (th + 12) / size[1]
                x1, y1 = x0 + tw / size[0], y0 + th / size[1]
                self.draw_quad(tex, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], size, 1.0, origin=org)
