"""出力ウィンドウ（VPT 風）

- 出力ごとに小さなウィンドウを1つ開く。投影したい画面へドラッグして「フルスクリーン」を押すと、
  そのウィンドウがある画面いっぱいに広がる（枠なし・最前面）。
- 映像は見えないメインの OpenGL 環境で出力ごとのフレームバッファに描き、
  各ウィンドウはそれを貼り付けるだけ（テクスチャを共有）。
"""
import time
import ctypes

import glfw
import moderngl
import numpy as np

BLIT_VS = """
#version 330
in vec2 p;
out vec2 uv;
void main(){ uv = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }
"""
BLIT_FS = """
#version 330
uniform sampler2D t;
uniform vec2 span;     // 横長キャンバスのうち、このウィンドウが映す範囲（左端, 右端）0〜1
uniform vec2 blend;    // 左・右の重なり幅（このウィンドウの幅に対する割合）
uniform vec2 gamma;    // 左・右のつなぎ目のガンマ（重なりの明るさをそろえる。つなぎ目ごとに別の値）
in vec2 uv;
out vec4 o;
void main(){
    vec3 c = texture(t, vec2(mix(span.x, span.y, uv.x), uv.y)).rgb;
    float wl = 1.0, wr = 1.0;
    if (blend.x > 0.0) wl = smoothstep(0.0, 1.0, clamp(uv.x / blend.x, 0.0, 1.0));
    if (blend.y > 0.0) wr = smoothstep(0.0, 1.0, clamp((1.0 - uv.x) / blend.y, 0.0, 1.0));
    // 光の量で足して 1 になるように：映像信号は w^(1/gamma) 倍
    o = vec4(c * pow(wl, 1.0 / max(gamma.x, 0.1)) * pow(wr, 1.0 / max(gamma.y, 0.1)), 1.0);
}
"""


def gl_hints(visible=True):
    glfw.default_window_hints()
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
    glfw.window_hint(glfw.VISIBLE, visible)
    glfw.window_hint(glfw.AUTO_ICONIFY, False)  # 操作PCに切り替えても最小化しない


def _same(a, b):
    # GLFW の画面ハンドルは ctypes のポインタなので、== ではなくアドレスで比べる
    try:
        return ctypes.addressof(a.contents) == ctypes.addressof(b.contents)
    except (ValueError, AttributeError):
        return False


def monitors():
    """接続中の画面 [(x, y, w, h), ...]（メインが先頭、あとは左→右）"""
    out = []
    prim = glfw.get_primary_monitor()
    for m in glfw.get_monitors():
        x, y = glfw.get_monitor_pos(m)
        vm = glfw.get_video_mode(m)
        if vm is None:  # 抜いている途中の画面
            continue
        out.append((x, y, vm.size.width, vm.size.height, _same(m, prim)))
    out.sort(key=lambda r: (not r[4], r[0], r[1]))
    return [r[:4] for r in out]


def monitor_info():
    """操作パネルの配置図用：画面ごとの位置・解像度・拡大率・Windows での名前"""
    prim = glfw.get_primary_monitor()
    rows = []
    for m in glfw.get_monitors():
        x, y = glfw.get_monitor_pos(m)
        vm = glfw.get_video_mode(m)
        if vm is None:  # 抜いている途中の画面
            continue
        sx, _ = glfw.get_monitor_content_scale(m)
        name = glfw.get_monitor_name(m)
        if isinstance(name, bytes):
            name = name.decode('utf-8', 'replace')
        try:
            adapter = glfw.get_win32_adapter(m) or ''
            if isinstance(adapter, bytes):
                adapter = adapter.decode('utf-8', 'replace')
        except Exception:
            adapter = ''
        win_no = adapter.replace('\\\\.\\DISPLAY', '') if 'DISPLAY' in adapter else ''
        rows.append({'rect': [x, y, vm.size.width, vm.size.height], 'scale': round(sx * 100),
                     'hz': vm.refresh_rate, 'name': name, 'win': win_no, 'primary': _same(m, prim)})
    rows.sort(key=lambda r: (not r['primary'], r['rect'][0], r['rect'][1]))
    return rows


def monitor_at(x, y):
    """座標 (x, y) を含む画面の番号（なければ一番近い画面）"""
    ms = monitors()
    best, bd = 0, None
    for i, (mx, my, mw, mh) in enumerate(ms):
        if mx <= x < mx + mw and my <= y < my + mh:
            return i
        cx, cy = mx + mw / 2, my + mh / 2
        d = (cx - x) ** 2 + (cy - y) ** 2
        if bd is None or d < bd:
            best, bd = i, d
    return best


class OutputWindow:
    def __init__(self, app, cfg, index):
        self.app = app
        self.cfg = cfg
        self.index = index
        self.fullscreen = False
        self.transition = False
        self.tex = None
        self.fbo = None
        self.ext = None
        self.ext_key = None
        self.last_click = 0.0
        self.mouse = (0.0, 0.0)

        gl_hints(visible=False)
        rect = cfg.get('rect')
        if not rect:
            mx, my, mw, mh = monitors()[0]
            rect = [mx + 80 + 40 * index, my + 80 + 40 * index, 960, 540]
            cfg['rect'] = rect
        self.win = glfw.create_window(int(rect[2]), int(rect[3]), self.title(), None, app.main_win)
        glfw.set_window_pos(self.win, int(rect[0]), int(rect[1]))
        self._ensure_on_screen()

        glfw.make_context_current(self.win)
        self.ctx = moderngl.create_context()
        self.prog = self.ctx.program(vertex_shader=BLIT_VS, fragment_shader=BLIT_FS)
        self.vbo = self.ctx.buffer(np.array([-1, -1, 1, -1, -1, 1, 1, 1], 'f4').tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.vbo, '2f', 'p')])
        glfw.swap_interval(1 if index == 0 else 0)  # 画面のちらつき防止は1つ目の出力だけで十分
        glfw.make_context_current(app.main_win)

        glfw.set_key_callback(self.win, self._on_key)
        glfw.set_mouse_button_callback(self.win, self._on_button)
        glfw.set_cursor_pos_callback(self.win, self._on_cursor)
        glfw.set_window_pos_callback(self.win, self._on_moved)
        glfw.set_window_size_callback(self.win, self._on_moved)
        glfw.set_window_close_callback(self.win, self._on_close)
        glfw.show_window(self.win)

        # --window で起動したときは、その回だけウィンドウのまま（設定は書き換えない）
        if cfg.get('fullscreen') and not app.window_override:
            fs = cfg.get('fs_rect')
            ms = monitors()
            if fs and any(tuple(m) == tuple(fs) for m in ms):
                self.set_fullscreen(True, monitor=tuple(fs))
            else:
                cfg['fullscreen'] = False
                app.warn(f'{self.name()}：前回フルスクリーンにしていた画面が見つからないため、ウィンドウで表示しています')

    # ---- 表示 ----
    def name(self):
        return f'出力{self.index + 1}'

    def title(self):
        return f'VPT39 {self.name()}（投影したい画面へ移動して「フルスクリーン」／F キー／ダブルクリック）'

    def size(self):
        w, h = glfw.get_framebuffer_size(self.win)
        if w < 2 or h < 2:  # 最小化中は 0×0 になる → 最後の大きさのまま描く（横長のつながりや微調整がくずれないように）
            return getattr(self, '_last_size', (max(1, w), max(1, h)))
        self._last_size = (w, h)
        return w, h

    def render_size(self):
        """描く大きさ。「本番の解像度」を決めていればその大きさ（ウィンドウの大きさに関係なく同じ絵を作る。
        小さいウィンドウでは縮小して見せる）、決めていなければウィンドウの大きさ"""
        r = self.cfg.get('res')
        if isinstance(r, (list, tuple)) and len(r) == 2:
            try:
                w, h = int(r[0]), int(r[1])
                if 16 <= w <= 16384 and 16 <= h <= 16384:
                    return w, h
            except (TypeError, ValueError):
                pass
        return self.size()

    def view_rect(self, ww, wh):
        """ウィンドウ（ww×wh）の中で映像を映す範囲 (x, y, 幅, 高さ)。
        本番の解像度を決めていて、ウィンドウと形が違うときは、伸び縮みさせずに黒い帯を付けて収める"""
        if not self.cfg.get('res'):
            return 0, 0, ww, wh
        rw, rh = self.render_size()
        k = min(ww / max(1, rw), wh / max(1, rh))
        vw, vh = max(1, round(rw * k)), max(1, round(rh * k))
        return (ww - vw) // 2, (wh - vh) // 2, vw, vh

    def monitor_index(self):
        x, y = glfw.get_window_pos(self.win)
        w, h = glfw.get_window_size(self.win)
        return monitor_at(x + w / 2, y + h / 2)

    def _ensure_on_screen(self):
        """保存されていた位置が今の画面構成で見えない場所なら、メイン画面に戻す"""
        x, y = glfw.get_window_pos(self.win)
        w, h = glfw.get_window_size(self.win)
        cx, cy = x + w / 2, y + h / 2
        if not any(mx <= cx < mx + mw and my <= cy < my + mh for mx, my, mw, mh in monitors()):
            self.bring_back()

    def bring_back(self):
        """手元（メイン画面）にウィンドウで戻す"""
        if self.fullscreen:
            self.set_fullscreen(False)
        mx, my, mw, mh = monitors()[0]
        w, h = 960, 540
        glfw.set_window_size(self.win, w, h)
        glfw.set_window_pos(self.win, mx + (mw - w) // 2, my + (mh - h) // 2)

    def set_fullscreen(self, on, monitor=None):
        if on == self.fullscreen and monitor is None:
            return
        if on:
            if not self.fullscreen:
                x, y = glfw.get_window_pos(self.win)
                w, h = glfw.get_window_size(self.win)
                self.cfg['rect'] = [x, y, w, h]
            mx, my, mw, mh = monitor or monitors()[self.monitor_index()]
            self.fullscreen = True
            glfw.set_window_attrib(self.win, glfw.DECORATED, False)
            glfw.set_window_attrib(self.win, glfw.FLOATING, True)  # 最前面（タスクバーが重ならない）
            glfw.set_window_pos(self.win, mx, my)
            glfw.set_window_size(self.win, mw, mh)
            self.cfg['fullscreen'] = True
            self.cfg['fs_rect'] = [mx, my, mw, mh]
        else:
            x, y, w, h = self.cfg.get('rect') or [100, 100, 960, 540]  # 切り替え中に上書きされる前に読む
            self.transition = True
            glfw.set_window_attrib(self.win, glfw.FLOATING, False)
            glfw.set_window_attrib(self.win, glfw.DECORATED, True)
            glfw.set_window_size(self.win, int(w), int(h))
            glfw.set_window_pos(self.win, int(x), int(y))
            self.transition = False
            self.fullscreen = False
            self.cfg['rect'] = [int(x), int(y), int(w), int(h)]
            self.cfg['fullscreen'] = False
        self.update_cursor()
        self.app.touch()

    def update_cursor(self):
        hide = self.fullscreen and not self.app.edit
        glfw.set_input_mode(self.win, glfw.CURSOR, glfw.CURSOR_HIDDEN if hide else glfw.CURSOR_NORMAL)

    # ---- 描画 ----
    def ensure_target(self, main_ctx, size=None):
        """メイン側の描画先（出力の解像度。横長につないだときはキャンバス全体）を用意"""
        size = size or self.size()
        if self.tex is None or self.tex.size != size:
            if self.fbo is not None:
                self.fbo.release()
                self.tex.release()
            self.tex = main_ctx.texture(size, 4)
            self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.fbo = main_ctx.framebuffer([self.tex])
            self.ext = None
        return self.fbo, size

    def present(self, src=None, span=(0.0, 1.0), blend=(0.0, 0.0), gamma=(2.2, 2.2)):
        """src: 映すテクスチャ（横長につないだ出力は、先頭の出力のキャンバスを切り出して映す）"""
        src = src or self.tex
        if src is None:
            return
        glfw.make_context_current(self.win)
        if self.ext is None or self.ext_key != (src.glo, src.size):
            self.ext = self.ctx.external_texture(src.glo, src.size, 4, 0, 'f1')
            self.ext.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.ext_key = (src.glo, src.size)
        w, h = self.size()
        self.ctx.screen.use()
        x0, y0, vw, vh = self.view_rect(w, h)
        if (vw, vh) != (w, h):
            self.ctx.viewport = (0, 0, w, h)
            self.ctx.clear(0.0, 0.0, 0.0, 1.0)
        self.ctx.viewport = (x0, h - y0 - vh, vw, vh)
        self.prog['span'].value = tuple(span)
        self.prog['blend'].value = tuple(blend)
        self.prog['gamma'].value = (float(gamma[0]), float(gamma[1]))
        self.ext.use(0)
        self.vao.render(moderngl.TRIANGLE_STRIP)
        glfw.swap_buffers(self.win)

    def drop_target(self):
        """横長の2台目以降になったら、自分用の描画先は不要"""
        if self.fbo is not None:
            self.fbo.release()
            self.tex.release()
        self.fbo = self.tex = None

    def destroy(self):
        self.drop_target()
        try:  # このウィンドウで作った描画用の部品も片付ける（出力を消すたびに残らないように）
            glfw.make_context_current(self.win)
            for o in (self.vao, self.vbo, self.prog):  # ext は他の出力の描画先を借りているだけなので消さない
                if o is not None:
                    o.release()
        except Exception:
            pass
        glfw.make_context_current(self.app.main_win)
        glfw.destroy_window(self.win)

    # ---- 入力 ----
    def _norm(self, x, y):
        """ウィンドウ上の位置 → 映像（キャンバス）上の位置 0〜1"""
        w, h = glfw.get_window_size(self.win)
        x0, y0, vw, vh = self.view_rect(w, h)  # 黒い帯の分をのぞく
        nx, ny = (x - x0) / max(1, vw), (y - y0) / max(1, vh)
        u0, u1 = self.app.span_of(self)
        return u0 + nx * (u1 - u0), ny

    def _on_key(self, win, key, sc, action, mods):
        if action in (glfw.PRESS, glfw.REPEAT):
            self.app.on_key(self, key, mods, action == glfw.REPEAT)

    def _on_button(self, win, button, action, mods):
        if button != glfw.MOUSE_BUTTON_LEFT:
            return
        x, y = self._norm(*glfw.get_cursor_pos(win))
        if action == glfw.PRESS:
            now = time.time()
            dbl = now - self.last_click < 0.35
            self.last_click = now
            if dbl and not self.app.edit:  # ダブルクリックでフルスクリーン切り替え（VPT と同じ）
                self.set_fullscreen(not self.fullscreen)
                return
            self.app.on_mouse_down(self, x, y)
        else:
            self.app.on_mouse_up(self)

    def _on_cursor(self, win, x, y):
        nx, ny = self._norm(x, y)
        self.mouse = (nx, ny)
        self.app.on_mouse_move(self, nx, ny)

    def _on_moved(self, win, a, b):
        if not self.fullscreen and not self.transition:
            x, y = glfw.get_window_pos(win)
            w, h = glfw.get_window_size(win)
            if w > 0 and h > 0:
                self.cfg['rect'] = [x, y, w, h]
                self.app.touch()

    def _on_close(self, win):
        # 誤って閉じないように、×では閉じない（出力を減らすときは操作パネルから）
        glfw.set_window_should_close(win, False)
        self.app.message = '出力ウィンドウは×では閉じません。減らすときは操作パネルの「出力」から削除してください'
