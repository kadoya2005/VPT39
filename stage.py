"""VPT39 — ライブ用 映像再生＋プロジェクションマッピング

起動:  python stage.py              （project.json を使用）
       python stage.py --window     （この回だけ、全部の出力をウィンドウで開く）

出力ウィンドウは VPT と同じ使い方:
  小さなウィンドウで開く → 投影したい画面（プロジェクター）へドラッグ →
  「フルスクリーン」ボタン / F キー / ダブルクリック で、その画面いっぱいに表示
"""
import os
import sys
import json
import math
import time
import queue
import argparse
import threading
import subprocess
import webbrowser

import glfw
import moderngl
from PIL import Image, ImageDraw, ImageFont

from engine import Layer, Renderer, layer_defaults, DEFAULT_CORNERS, BLENDS, CUE_MODES, LAYER, TRANSITIONS
from sources import backend_name, is_video, is_image, list_cameras, list_mics, list_windows, list_spout
from outputs import OutputWindow, gl_hints, monitors, monitor_info
import webui

HERE = os.path.dirname(os.path.abspath(__file__))


def new_id():
    import uuid
    return uuid.uuid4().hex[:8]


def default_project():
    bg = layer_defaults('video', '背景動画')
    ov = layer_defaults('video', 'オーバーレイ')
    ov['blend'] = 'screen'
    ov['audio'] = False
    ph = layer_defaults('photo', '写真ウォール')
    ph['corners'] = [[0.62, 0.08], [0.95, 0.08], [0.95, 0.52], [0.62, 0.52]]
    ph['visible'] = False
    return {
        # 出力ウィンドウ。rect=ウィンドウ時の位置と大きさ、fullscreen=フルスクリーンか、fs_rect=その画面
        'outputs': [{}],
        'web_port': 8080,
        'allow_lan': False,
        'media_dir': 'media',
        'master': 1.0,        # 映像の明るさ（ブラックアウトはこちらだけに効く）
        'audio_master': 1.0,  # 音声ミキサーのマスター音量
        'layers': [bg, ov, ph],
    }


def normalize_project(p):
    """足りない項目を補う（古い設定ファイル・手で書いたファイルでも開けるように）"""
    if not isinstance(p, dict) or not isinstance(p.get('layers', []), list):
        raise ValueError('VPT39 の設定ファイルではありません')
    dp = default_project()
    for k, v in dp.items():
        if k != 'outputs':
            p.setdefault(k, v)
    for L in p['layers']:
        for k, v in layer_defaults(L.get('kind', 'video')).items():
            L.setdefault(k, v)
        L['test'] = False  # 開いたときはテストパターンを必ずオフ
        if not L.get('id'):
            L['id'] = new_id()
    return p


SETTING_BAD = set('\\/:*?"<>|')


def clean_setting_name(name):
    """セッティング名（ファイル名になる）を確かめる。使えない名前なら None"""
    n = str(name or '').strip()
    if not n or len(n) > 60 or n.startswith('.') or any(ch in SETTING_BAD or ord(ch) < 32 for ch in n):
        return None
    if n.upper() in ('CON', 'PRN', 'AUX', 'NUL') or n.lower().endswith('.json'):
        return None
    return n


def _font(size):
    for name in ('arial.ttf', 'segoeui.ttf', 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


class App:
    def __init__(self, project_path, args):
        self.project_path = os.path.abspath(project_path)
        self.base = os.path.dirname(self.project_path)
        if os.path.exists(self.project_path):
            with open(self.project_path, encoding='utf-8-sig') as f:  # メモ帳で保存したBOM付きにも対応
                self.proj = json.load(f)
        else:
            self.proj = default_project()
        normalize_project(self.proj)
        self.settings_dir = os.path.join(self.base, 'settings')
        self._settings_cache = (0.0, [])
        self.window_override = bool(args.window)
        os.makedirs(self.resolve(self.proj['media_dir']), exist_ok=True)

        self.layers = [Layer(c, self) for c in self.proj['layers']]
        self.master_eff = self.proj['master']
        self.blackout = False
        self.edit = False
        self.selected = 0
        self.sel_corner = 0
        self.drag = None
        self.rev = 1
        self.dirty = False
        self.last_change = 0.0
        self.cmd_q = queue.Queue()
        self.state_lock = threading.Lock()
        self.state_json = b'{}'
        self.media = []
        self.last_media_scan = 0.0
        self.fps = 0.0
        self.running = True
        self.message = ''
        self.warnings = []
        self.snap_path = None
        self.boot = time.time()
        self.map_out = 0           # マッピング編集の対象（選んでいるレイヤーの、どの出力の四隅か）
        self.layer_fbos = {}       # レイヤー映像（別レイヤーの素材）用の描画先 {id: (tex, fbo)}
        # 裏のスレッドが書き換えるので、キーは最初に全部用意しておく（途中でキーが増えると読み出しがエラーになる）
        self.inputs = {'cams': [], 'mics': [], 'windows': [], 'spout': [], 'scanning': False, 'time': 0.0}
        self._err_t = {}
        self.corner_hist = []      # 四隅の「1つ戻す」用 [(レイヤーid, 出力), 前の四隅, 時刻]
        self.mstate_json = b'{}'
        self.lan_urls = []
        self.web_port = 8080
        self._grp = None

        self._init_gl()
        self.outs = []
        self._migrate_outputs()
        for i, oc in enumerate(self.proj['outputs']):
            self.outs.append(OutputWindow(self, oc, i))
        glfw.make_context_current(self.main_win)
        self.font = _font(20)
        self.hud_tex = None
        self.hud_text = None

    # ---- OpenGL（見えないメインウィンドウ。映像はここで描いて各出力に配る）----
    def _init_gl(self):
        if not glfw.init():
            raise RuntimeError('OpenGL（GLFW）を初期化できません')
        gl_hints(visible=False)
        self.main_win = glfw.create_window(64, 64, 'VPT39', None, None)
        if not self.main_win:
            raise RuntimeError('OpenGL 3.3 のウィンドウを作れません（グラボのドライバを確認してください）')
        glfw.make_context_current(self.main_win)
        self.ctx = moderngl.create_context()
        self.renderer = Renderer(self.ctx)
        self.max_tex = self.ctx.info.get('GL_MAX_TEXTURE_SIZE', 16384)

    def _migrate_outputs(self):
        """旧形式（output.displays / output.display）を出力ウィンドウ形式に変換"""
        if 'outputs' in self.proj and self.proj['outputs']:
            self.proj.pop('output', None)
            return
        old = self.proj.pop('output', None) or {}
        ds = old.get('displays') or ([old['display']] if 'display' in old else [])
        ms = monitors()
        outs = []
        for d in ds:
            oc = {}
            if old.get('mode', 'fullscreen') != 'window':
                if 0 <= int(d) < len(ms):
                    oc = {'fullscreen': True, 'fs_rect': list(ms[int(d)])}
                else:
                    self.warn(f'画面{int(d) + 1}が見つからないため、出力{len(outs) + 1}はウィンドウで開きました')
            outs.append(oc)
        self.proj['outputs'] = outs or [{}]

    def warn(self, msg):
        print(msg)
        self.warnings.append(msg)

    # ---- パス ----
    def resolve(self, p):
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def media_path(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.resolve(self.proj['media_dir']), rel)

    # ---- 出力 ----
    # 横長（ビッグモニター）：出力の設定 join=True で「左どなりの出力の右につなぐ」。
    # つないだ出力のまとまりは、先頭の出力が横長のキャンバス全体を描き、各ウィンドウはその一部を映す。
    def groups(self):
        """[{host, members, xs, ws, W, H, gamma}]（毎フレーム計算。ウィンドウの大きさで変わる）"""
        if self._grp is not None:
            return self._grp
        gs = []
        for k, o in enumerate(self.outs):
            w, h = o.render_size()
            if k > 0 and o.cfg.get('join') and gs:
                g = gs[-1]
                ov = max(0, min(int(o.cfg.get('overlap', 0)), min(w, g['ws'][-1]) - 1))
                x = g['xs'][-1] + g['ws'][-1] - ov
                g['members'].append(k)
                g['xs'].append(x)
                g['ws'].append(w)
                g['ovs'].append(ov)
                # このつなぎ目（左どなりとの重なり）のガンマ。まだ決めていなければ先頭の出力の値（以前の設定）
                g['gms'].append(self.seam_gamma(k))
                g['W'] = x + w
            else:
                gs.append({'host': k, 'members': [k], 'xs': [0], 'ws': [w], 'ovs': [0], 'W': w, 'H': h,
                           'gms': [2.2]})
        self._grp = gs
        return gs

    def seam_gamma(self, k):
        """出力 k とその左どなりのつなぎ目のガンマ"""
        g = self.outs[k].cfg.get('gamma')
        if g is None:
            for j in range(k - 1, -1, -1):  # 以前はまとまりの先頭の出力に1つだけ持っていた
                if not self.outs[j].cfg.get('join') or j == 0:
                    g = self.outs[j].cfg.get('gamma', 2.2)
                    break
        return float(g if g is not None else 2.2)

    def group_of(self, k):
        for g in self.groups():
            if k in g['members']:
                return g
        return self.groups()[0]

    def host_of(self, k):
        return self.group_of(k)['host']

    def span_of(self, O):
        g = self.group_of(O.index)
        i = g['members'].index(O.index)
        return g['xs'][i] / g['W'], (g['xs'][i] + g['ws'][i]) / g['W']

    def target_size(self, k):
        g = self.group_of(k)
        return max(1, min(g['W'], self.max_tex)), max(1, g['H'])

    def target_name(self, k):
        g = self.group_of(k)
        if len(g['members']) > 1:
            return '＋'.join(f'出力{m + 1}' for m in g['members']) + '（横長）'
        return f'出力{k + 1}'

    def norm_out(self, v):
        try:
            i = int(v)
        except (TypeError, ValueError):
            i = 0
        return self.host_of(max(0, min(len(self.outs) - 1, i)))

    def out_index(self, L):
        return self.norm_out(L.cfg.get('output', 0))

    def out_size(self, L):
        return self.target_size(self.out_index(L))

    def layer_places(self, L):
        """[(出力のまとまりの先頭番号, 四隅)]（同じまとまりへの重複は1つ目だけ）"""
        seen, out = set(), []
        for k, c in L.places():
            h = self.norm_out(k)
            if h not in seen:
                seen.add(h)
                out.append((h, c))
        return out

    def layer_by_id(self, lid):
        for L in self.layers:
            if L.cfg.get('id') == lid:
                return L
        return None

    def sel_corners(self):
        """選んでいるレイヤーの、マッピング編集中の出力の四隅"""
        L = self.layer(self.selected)
        if L is None:
            return None, None
        for k, c in self.layer_places(L):
            if k == self.map_out:
                return L, c
        k, c = self.layer_places(L)[0]
        self.map_out = k
        return L, c

    def set_place(self, L, k, on):
        """レイヤーを出力 k にも映す／映さない（1つ目の出力を外すと、次の出力が1つ目になる）"""
        k = self.norm_out(k)
        cfg = L.cfg
        more = cfg.setdefault('more', [])
        hosts = [h for h, _ in self.layer_places(L)]
        if on and k not in hosts:
            more.append({'output': k, 'corners': [list(p) for p in DEFAULT_CORNERS]})
        elif not on and k in hosts and len(hosts) > 1:
            if self.norm_out(cfg.get('output', 0)) == k:
                m = more.pop(0)
                cfg['output'], cfg['corners'] = m['output'], m['corners']
            else:
                cfg['more'] = [m for m in more if self.norm_out(m['output']) != k]
        self.touch(True)

    def _layer_refs(self):
        """いま再生中のクリップが映しているレイヤーの id"""
        used = set()
        for L in self.layers:
            for d in L.decks:
                lid = getattr(d.src, 'layer_id', None)
                if lid:
                    used.add(lid)
        return used

    def _render_layer_sources(self, used):
        """別レイヤーの素材になっているレイヤーの中身を、テクスチャに描いて渡す"""
        for lid in list(self.layer_fbos):
            if lid not in used or self.layer_by_id(lid) is None:
                tex, fbo = self.layer_fbos.pop(lid)
                fbo.release()
                tex.release()
        for lid in used:
            L = self.layer_by_id(lid)
            if L is None:
                continue
            size = tuple(int(v) for v in L.cfg['res'])
            ent = self.layer_fbos.get(lid)
            if ent is None or ent[0].size != size:
                if ent:
                    ent[1].release()
                    ent[0].release()
                tex = self.ctx.texture(size, 4)
                tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
                ent = (tex, self.ctx.framebuffer([tex]))
                self.layer_fbos[lid] = ent
            self.renderer.render_layer_content(L, ent[1], size)
        for L in self.layers:
            for d in L.decks:
                lid = getattr(d.src, 'layer_id', None)
                if lid:
                    ent = self.layer_fbos.get(lid)
                    d.tex = ent[0] if ent else None

    def _fix_places(self):
        """出力をつないだ・消したあと、レイヤーの出力番号をまとまりの先頭にそろえ、重複を消す"""
        self._grp = None
        for x in self.proj['layers']:
            x['output'] = self.norm_out(x.get('output', 0))
            seen, more = {x['output']}, []
            for m in x.get('more', []):
                h = self.norm_out(m['output'])
                if h not in seen:
                    seen.add(h)
                    more.append({'output': h, 'corners': m['corners']})
            x['more'] = more

    def scan_inputs(self):
        """カメラ・音声入力・ウィンドウの一覧を裏で調べる"""
        if self.inputs.get('scanning'):
            return
        self.inputs['scanning'] = True

        def work():
            try:
                wins = [t for _, t in list_windows()]
                self.inputs['windows'] = sorted(set(wins), key=wins.index)
                self.inputs['mics'] = list_mics()
                self.inputs['cams'] = list_cameras()
                self.inputs['spout'] = list_spout()
            except Exception as ex:
                print('入力の一覧を取得できません:', ex)
            finally:
                self.inputs['scanning'] = False
                self.inputs['time'] = time.time()
        threading.Thread(target=work, daemon=True).start()

    def audio_gain(self, L):
        """レイヤーの音量 = レイヤーのフェーダー × 出力のフェーダー × 音声マスター（ミュートなら0）"""
        c = L.cfg
        if c.get('kind') != 'video' or not c.get('audio', True):
            return 0.0
        oc = self.outs[self.out_index(L)].cfg if self.outs else {}
        if oc.get('mute'):
            return 0.0
        return float(c.get('volume', 1.0)) * float(oc.get('volume', 1.0)) * \
            float(self.proj.get('audio_master', 1.0))

    # ---- コマンド処理（ブラウザ・キーボード共通）----
    def touch(self, structural=False):
        self.dirty = True
        self.last_change = time.time()
        if structural:
            self.rev += 1

    def layer(self, i):
        if isinstance(i, int) and 0 <= i < len(self.layers):
            return self.layers[i]
        return None

    def out(self, i):
        if isinstance(i, int) and 0 <= i < len(self.outs):
            return self.outs[i]
        return None

    def set_edit(self, v):
        self.edit = bool(v)
        for o in self.outs:
            o.update_cursor()

    def handle(self, c):
        cmd = c.get('cmd')
        L = self.layer(c.get('layer'))
        if c.get('lid'):  # 確認の間に並びが変わっても、確かめたレイヤーに対して行う
            L = self.layer_by_id(c['lid'])
            if L is None:
                return
            c['layer'] = self.layers.index(L)
        if cmd == 'remove_cue' and L and 'file' in c:
            i = int(c.get('cue', -1))
            if not (0 <= i < len(L.cfg['cues'])) or L.cfg['cues'][i].get('file') != c['file']:
                return  # 消そうとしたクリップと違う（並びが変わった）
        O = self.out(c.get('output'))
        if cmd == 'go' and L:
            L.go(int(c['cue']))
        elif cmd == 'transport' and L:
            # クリップのミニ操作：toggle（再生/一時停止）, restart（頭出し）, seek, stop
            L.transport(int(c['cue']), c.get('op'), c.get('value'))
        elif cmd == 'next' and L:
            L.next()
        elif cmd == 'prev' and L:
            L.prev()
        elif cmd == 'clear' and L:
            L.clear()
        elif cmd == 'clear_all':
            for x in self.layers:
                x.clear()
        elif cmd == 'skip_photo' and L:
            L.skip_photo()
        elif cmd == 'master':
            self.proj['master'] = max(0.0, min(1.0, float(c['value'])))
            self.touch()
        elif cmd == 'audio_master':
            self.proj['audio_master'] = max(0.0, min(1.0, float(c['value'])))
            self.touch()
        elif cmd == 'out_audio' and O:
            if 'volume' in c:
                O.cfg['volume'] = max(0.0, min(1.0, float(c['volume'])))
            if 'mute' in c:
                O.cfg['mute'] = bool(c['mute'])
            self.touch()
        elif cmd == 'identify':
            self.identify()
        elif cmd == 'open_display_settings':
            try:
                os.startfile('ms-settings:display')
            except OSError as ex:
                self.message = f'ディスプレイ設定を開けませんでした（{ex}）'
        elif cmd == 'blackout':
            self.blackout = bool(c.get('value', not self.blackout))
        elif cmd == 'edit':
            self.set_edit(c.get('value', not self.edit))
        elif cmd == 'sel_corner':
            self.sel_corner = max(0, min(4, int(c.get('value', 4))))
        elif cmd == 'undo_corners':
            self.undo_corners()
        elif cmd == 'nudge':
            st = int(c.get('step') or (10 if c.get('shift') else 1))
            self.nudge(int(c.get('dx', 0)), int(c.get('dy', 0)), max(1, min(100, st)))
        elif cmd == 'select' and L:
            self.selected = c['layer']
            if 'output' in c:
                self.map_out = self.norm_out(c['output'])
            self.sel_corners()
        elif cmd == 'map_out':
            self.map_out = self.norm_out(c.get('output', 0))
            self.sel_corners()
        elif cmd == 'set' and L:
            self.set_layer_value(L, c['key'], c['value'])
        elif cmd == 'place' and L:
            self.set_place(L, c.get('output', 0), bool(c.get('value')))
        elif cmd == 'corners' and L:
            pts = c['value']
            if len(pts) == 4:
                k = self.norm_out(c.get('output', self.map_out))
                pts = [[max(-10.0, min(11.0, float(x))), max(-10.0, min(11.0, float(y)))] for x, y in pts]
                if not all(math.isfinite(v) for p in pts for v in p):
                    return
                self.remember_corners(L, k)
                for h, cc in self.layer_places(L):
                    if h == k:
                        cc[:] = pts
                self.touch()
        elif cmd == 'reset_corners' and L:
            k = self.norm_out(c.get('output', self.map_out))
            self.remember_corners(L, k, alone=True)  # リセットは続けて動かしたものとまとめない
            for h, cc in self.layer_places(L):
                if h == k:
                    cc[:] = [list(p) for p in DEFAULT_CORNERS]
            self.touch()
        elif cmd == 'scan_inputs':
            self.scan_inputs()
        elif cmd == 'cue_set' and L:
            # カメラの音声入力・ウィンドウの題名など、特別なクリップの設定
            i = int(c['cue'])
            if 0 <= i < len(L.cfg['cues']) and c.get('key') in ('vdev', 'adev', 'title', 'sender'):
                L.cfg['cues'][i][c['key']] = str(c.get('value', ''))[:300]
                if c['key'] == 'adev':  # 再生中なら音声だけ入れ替える（映像は止めない）
                    from sources import AudioIn
                    for d in L.decks:
                        if d.cue == i and hasattr(d.src, 'audio'):
                            new = AudioIn(str(c['value'])) if c['value'] else None  # 先に新しい方を作ってから入れ替える
                            old, d.src.audio = d.src.audio, new
                            if old:
                                old.release()
                            L.error = new.error if new else ''
                self.touch(True)
        elif cmd == 'add_cue' and L:
            f = c.get('file', '')
            if f:
                cues = L.cfg['cues']
                at = c.get('at')
                at = len(cues) if at is None else max(0, min(len(cues), int(at)))
                cue = {'file': f, 'mode': 'next' if c.get('playlist') else 'loop'}
                for k in ('vdev', 'adev', 'title', 'sender'):
                    if k in c:
                        cue[k] = str(c[k])[:300]
                if f == '@camera' and 'adev' not in cue:
                    cue['adev'] = ''
                cues.insert(at, cue)
                for d in L.decks:
                    if d.cue >= at:
                        d.cue += 1
                self.touch(True)
        elif cmd == 'move_cue_to' and L:
            # ドラッグで並べ替え（i 番目を j 番目へ）
            cues = L.cfg['cues']
            i, j = int(c['cue']), int(c['to'])
            if 0 <= i < len(cues) and 0 <= j < len(cues) and i != j:
                order = list(range(len(cues)))
                order.insert(j, order.pop(i))
                L.cfg['cues'] = [cues[k] for k in order]
                new = {old: k for k, old in enumerate(order)}
                for d in L.decks:
                    if d.cue in new:
                        d.cue = new[d.cue]
                self.touch(True)
        elif cmd == 'go_column':
            # 列ごと再生（同じ列番号のキューを全レイヤーで一斉に）
            j = int(c['cue'])
            for x in self.layers:
                if x.cfg['kind'] == 'video' and 0 <= j < len(x.cfg['cues']):
                    x.go(j)
        elif cmd == 'remove_cue' and L:
            i = int(c['cue'])
            if 0 <= i < len(L.cfg['cues']):
                L.cfg['cues'].pop(i)
                for d in L.decks:
                    if d.cue == i:
                        d.cue = -1
                    elif d.cue > i:
                        d.cue -= 1
                self.touch(True)
        elif cmd == 'move_cue' and L:
            i, j = int(c['cue']), int(c['cue']) + int(c['dir'])
            cues = L.cfg['cues']
            if 0 <= i < len(cues) and 0 <= j < len(cues):
                cues[i], cues[j] = cues[j], cues[i]
                for d in L.decks:
                    if d.cue == i:
                        d.cue = j
                    elif d.cue == j:
                        d.cue = i
                self.touch(True)
        elif cmd == 'cue_mode' and L:
            i = int(c['cue'])
            if 0 <= i < len(L.cfg['cues']) and c['value'] in CUE_MODES:
                L.cfg['cues'][i]['mode'] = c['value']
                self.touch(True)
        elif cmd == 'add_layer':
            kind = 'photo' if c.get('kind') == 'photo' else 'video'
            cfg = layer_defaults(kind)
            cfg['id'] = new_id()
            cfg['output'] = self.out_index(self.layers[self.selected]) if self.layer(self.selected) else 0
            self.proj['layers'].append(cfg)
            self.layers.append(Layer(cfg, self))
            self.touch(True)
        elif cmd == 'remove_layer' and L and len(self.layers) > 1:
            i = self.layers.index(L)
            L.release()
            self.layers.pop(i)
            self.proj['layers'].pop(i)
            if self.selected > i:  # 上のレイヤーを消したら、選んでいたレイヤーを選んだままにする
                self.selected -= 1
            self.selected = max(0, min(self.selected, len(self.layers) - 1))
            self.touch(True)
        elif cmd == 'move_layer' and L:
            i = self.layers.index(L)
            j = i + int(c['dir'])
            if 0 <= j < len(self.layers):
                self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
                P = self.proj['layers']
                P[i], P[j] = P[j], P[i]
                if self.selected == i:  # 選択はレイヤーについていく
                    self.selected = j
                elif self.selected == j:
                    self.selected = i
                self.touch(True)
        # ---- 出力ウィンドウ ----
        elif cmd == 'out_fullscreen' and O:
            O.set_fullscreen(bool(c.get('value', not O.fullscreen)))
            self.touch(True)
        elif cmd == 'out_bring_back' and O:
            O.bring_back()
            self.touch(True)
        elif cmd == 'out_add':
            oc = {}
            self.proj['outputs'].append(oc)
            self.outs.append(OutputWindow(self, oc, len(self.outs)))
            glfw.make_context_current(self.main_win)
            self._grp = None
            self.touch(True)
        elif cmd == 'out_join' and O and self.outs.index(O) > 0:
            # 横長：左どなりの出力の右につなぐ／切り離す
            O.cfg['join'] = bool(c.get('value'))
            O.cfg.setdefault('overlap', 0)
            self._grp = None
            self._fix_places()
            self.touch(True)
        elif cmd == 'lan':
            self.set_lan(bool(c.get('value')))
        elif cmd == 'lan_refresh' and self.proj.get('allow_lan'):
            self.set_lan(True)  # Wi-Fi を変えたときなど、アドレスを調べ直す
        elif cmd == 'setting_save_as':
            self.setting_save_as(c.get('name'), bool(c.get('overwrite')))
        elif cmd == 'setting_open':
            self.setting_open(c.get('name'))
        elif cmd == 'setting_delete':
            self.setting_delete(c.get('name'))
        elif cmd == 'out_res' and O:
            # 本番の解像度。null で「自動（ウィンドウの大きさ）」
            v = c.get('value')
            if v is None:
                O.cfg.pop('res', None)
            else:
                w, h = int(v[0]), int(v[1])
                if not (16 <= w <= 16384 and 16 <= h <= 16384):
                    return
                O.cfg['res'] = [w, h]
            self._grp = None
            self.touch(True)
        elif cmd == 'out_blend' and O:
            if 'overlap' in c:
                O.cfg['overlap'] = max(0, min(2000, int(c['overlap'])))
            if 'gamma' in c:
                O.cfg['gamma'] = max(1.0, min(3.0, float(c['gamma'])))
            self._grp = None
            self.touch()
        elif cmd == 'out_remove' and O and len(self.outs) > 1:
            i = self.outs.index(O)
            O.destroy()
            self.outs.pop(i)
            self.proj['outputs'].pop(i)
            for k, o in enumerate(self.outs):
                o.index = k
                glfw.set_window_title(o.win, o.title())
            if self.outs and self.outs[0].cfg.get('join'):
                self.outs[0].cfg['join'] = False
            fix = lambda n: 0 if n == i else (n - 1 if n > i else n)
            for x in self.proj['layers']:
                x['output'] = fix(int(x.get('output', 0)))
                for m in x.get('more', []):
                    m['output'] = fix(int(m['output']))
            # 新しい出力1で垂直同期を効かせる（出力1を消すと、どのウィンドウにも同期がなくなっていた）
            glfw.make_context_current(self.outs[0].win)
            glfw.swap_interval(1)
            glfw.make_context_current(self.main_win)
            self.map_out = fix(self.map_out)
            self._grp = None
            self._fix_places()
            self.touch(True)
        elif cmd == 'save':
            self.save()
            self.message = '保存しました'
        elif cmd == 'snapshot':
            self.snap_path = os.path.join(self.base, 'snapshot.png')
        elif cmd == 'quit':
            self.running = False

    def screens(self):
        """画面ごとの情報（配置図・識別用）。番号は操作パネルの「画面1, 2…」と同じ"""
        info = monitor_info()
        for m in info:
            m['outputs'] = []
        for o in self.outs:
            k = o.monitor_index()
            if 0 <= k < len(info):
                info[k]['outputs'].append(o.name() + ('（フルスクリーン）' if o.fullscreen else ''))
        return info

    def identify(self):
        """各画面の中央に番号を4秒間表示（別プロセスなので映像は止まらない）"""
        items = []
        for k, m in enumerate(self.screens()):
            sub = f'{m["rect"][2]}×{m["rect"][3]}・拡大率 {m["scale"]}%'
            if m['win']:
                sub += f'・Windows の番号 {m["win"]}'
            if m['primary']:
                sub += '（メイン）'
            if m['outputs']:
                sub += '\n' + '、'.join(m['outputs'])
            items.append({'rect': m['rect'], 'label': str(k + 1), 'sub': sub})
        exe = sys.executable
        if exe.lower().endswith('python.exe'):
            w = exe[:-len('python.exe')] + 'pythonw.exe'
            exe = w if os.path.exists(w) else exe
        try:
            subprocess.Popen([exe, os.path.join(HERE, 'identify.py'),
                              json.dumps(items, ensure_ascii=False), '4'])
        except OSError as ex:
            self.message = f'識別を表示できませんでした（{ex}）'

    def set_layer_value(self, L, key, v):
        num = {'opacity': (0, 1), 'volume': (0, 1), 'fade': (0, 30), 'feather': (0, 0.5),
               'photo_interval': (0, 600), 'photo_border': (0, 0.2)}
        boo = {'visible', 'audio', 'test', 'photo_newest'}
        if key in num:
            lo, hi = num[key]
            L.cfg[key] = max(lo, min(hi, float(v)))
        elif key in boo:
            L.cfg[key] = bool(v)
        elif key == 'blend' and v in BLENDS:
            L.cfg[key] = v
        elif key == 'photo_trans' and v in TRANSITIONS:
            L.cfg[key] = v
        elif key in ('name', 'photo_folder'):
            L.cfg[key] = str(v)[:200]
        elif key == 'output':
            L.cfg[key] = self.norm_out(v)
            L.cfg['more'] = [m for m in L.cfg.get('more', []) if self.norm_out(m['output']) != L.cfg[key]]
            self.touch(True)
            return
        else:
            return
        self.touch(key in ('name', 'photo_folder'))

    # ---- 保存 ----
    def save(self):
        """project.json（いまの状態）に保存。セッティングを開いているときは、そのファイルにも保存（上書き保存）"""
        self._write_json(self.project_path, self.proj)
        name = self.proj.get('setting_name')
        if name and clean_setting_name(name):
            self._write_json(self.setting_path(name), self.proj)
        self.dirty = False

    @staticmethod
    def _write_json(path, data):
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(tmp, path)

    # ---- セッティング（会場・公演ごとの設定ファイル。settings フォルダに1つずつ）----
    def setting_path(self, name):
        return os.path.join(self.settings_dir, name + '.json')

    def list_settings(self):
        """[{name, time}]（新しい順）。Dropbox の中なので、調べるのは2秒に1回まで"""
        t, cache = self._settings_cache
        if time.time() - t < 2.0:
            return cache
        out = []
        try:
            for fn in os.listdir(self.settings_dir):
                if fn.lower().endswith('.json') and not fn.startswith('.'):
                    p = os.path.join(self.settings_dir, fn)
                    out.append({'name': fn[:-5], 'time': os.path.getmtime(p)})
        except OSError:
            pass
        out.sort(key=lambda r: -r['time'])
        self._settings_cache = (time.time(), out)
        return out

    def setting_save_as(self, name, overwrite=False):
        n = clean_setting_name(name)
        if not n:
            self.message = 'その名前は使えません（\\ / : * ? " < > | は使えない・60文字まで）'
            return
        os.makedirs(self.settings_dir, exist_ok=True)
        p = self.setting_path(n)
        if os.path.exists(p) and not overwrite and n != self.proj.get('setting_name'):
            self.message = f'「{n}」はもうあります（上書きするときは確認のあと保存）'
            return
        self.proj['setting_name'] = n
        self.save()
        self._settings_cache = (0.0, [])
        self.message = f'セッティング「{n}」に保存しました'
        self.touch(True)
        self.dirty = False

    def setting_open(self, name):
        n = clean_setting_name(name)
        p = self.setting_path(n) if n else None
        if not p or not os.path.isfile(p):
            self.message = f'セッティングが見つかりません: {name}'
            return
        try:
            with open(p, encoding='utf-8-sig') as f:
                newp = normalize_project(json.load(f))
        except Exception as ex:
            self.message = f'開けませんでした: {n}（{ex}）'
            return
        if self.dirty:
            self._safe_call('保存', self.save)  # 今の状態を先に保存してから切り替える
        # このPCの設定（操作パネルの番号・スマホからの操作）は、開いたファイルではなく今のまま
        for k in ('web_port', 'allow_lan'):
            if k in self.proj:
                newp[k] = self.proj[k]
        newp['setting_name'] = n
        self.apply_project(newp)
        self.message = f'セッティング「{n}」を開きました' + ('（' + ' ／ '.join(self.warnings) + '）' if self.warnings else '')

    def setting_delete(self, name):
        n = clean_setting_name(name)
        p = self.setting_path(n) if n else None
        if not p or not os.path.isfile(p):
            return
        if not webui._to_recycle_bin(p) or os.path.exists(p):
            self.message = f'ごみ箱へ移せませんでした: {n}'
            return
        if self.proj.get('setting_name') == n:
            self.proj.pop('setting_name', None)
            self.touch(True)
        self._settings_cache = (0.0, [])
        self.message = f'セッティング「{n}」をごみ箱へ移しました'

    def apply_project(self, p):
        """別のセッティングに切り替える：レイヤーと出力ウィンドウを作り直す"""
        for L in self.layers:
            L.release()
        for tex, fbo in self.layer_fbos.values():
            fbo.release()
            tex.release()
        self.layer_fbos = {}
        for o in self.outs:
            o.destroy()
        self.proj = p
        self.warnings = []
        self._migrate_outputs()
        self.layers = [Layer(c, self) for c in self.proj['layers']]
        self.outs = []
        for i, oc in enumerate(self.proj['outputs']):
            self.outs.append(OutputWindow(self, oc, i))
        glfw.make_context_current(self.main_win)
        self.master_eff = 0.0 if self.blackout else self.proj.get('master', 1.0)
        self.selected, self.map_out, self.sel_corner = 0, 0, 0
        self.corner_hist = []
        self.drag = None
        self._grp = None
        self.hud_text = None
        try:
            os.makedirs(self.resolve(self.proj['media_dir']), exist_ok=True)
        except OSError:
            pass
        self.last_media_scan = 0.0
        self.touch(True)
        self.save()

    # ---- 素材一覧 ----
    def scan_media(self, wait=False):
        """media フォルダの一覧。フォルダを調べるのは裏のスレッドで（Dropbox の中だと時間がかかり、映像が止まるため）"""
        self.last_media_scan = time.time()
        if getattr(self, '_media_scanning', False):
            return
        root = self.resolve(self.proj['media_dir'])
        self._media_scanning = True

        def work():
            try:
                out = []
                for dp, dns, fns in os.walk(root):
                    dns.sort()
                    for fn in sorted(fns):
                        p = os.path.join(dp, fn)
                        if is_video(p) or is_image(p):
                            out.append(os.path.relpath(p, root).replace('\\', '/'))
                self.media = out
            except Exception as ex:
                print('素材の一覧を作れません:', ex)
            finally:
                self._media_scanning = False
        if wait:
            work()
        else:
            threading.Thread(target=work, daemon=True).start()

    # ---- 状態（ブラウザへ）----
    def build_state(self):
        ls = []
        for L in self.layers:
            c = L.cfg
            top = L.top_deck() or (L.decks[-1] if L.decks else None)
            pos, dur = top.src.position() if top else (0.0, 0.0)
            item = dict(c)
            item.update({
                'output': self.out_index(L),
                'places': [{'output': k, 'corners': cc} for k, cc in self.layer_places(L)],
                'playing': L.current_cue,
                'label': top.label if top and top.target > 0 else '',
                'pos': round(pos, 2), 'dur': round(dur, 2),
                'paused': bool(top and top.src.paused),
                'seekable': bool(top and top.src.seekable),
                'gain': round(L.gain, 3),                'error': L.error,
            })
            if c['kind'] == 'photo':
                item['photo_count'] = L.feeder.count() if L.feeder else 0
                item['photo_dir_ok'] = os.path.isdir(self.resolve(c.get('photo_folder', 'photos')))
            ls.append(item)
        sel, _ = self.sel_corners()
        outs = []
        for k, o in enumerate(self.outs):
            outs.append({'name': o.name(), 'size': list(o.render_size()), 'win': list(o.size()),
                         'res': o.cfg.get('res'), 'fullscreen': o.fullscreen,
                         'monitor': o.monitor_index(), 'host': self.host_of(k),
                         'target': self.target_name(k), 'join': bool(o.cfg.get('join')) and k > 0,
                         'overlap': int(o.cfg.get('overlap', 0)), 'gamma': self.seam_gamma(k),
                         'volume': float(o.cfg.get('volume', 1.0)), 'mute': bool(o.cfg.get('mute'))})
        screens = self.screens()
        targets = [{'output': g['host'], 'name': self.target_name(g['host']), 'size': [g['W'], g['H']]}
                   for g in self.groups()]
        st = {
            'rev': self.rev, 'boot': self.boot, 'backend': backend_name(), 'fps': round(self.fps),
            'master': self.proj['master'], 'blackout': self.blackout, 'edit': self.edit,
            'selected': self.selected, 'map_out': self.map_out, 'targets': targets,
            'inputs': dict(self.inputs),
            'size': list(self.target_size(self.map_out)) if sel else [1920, 1080],
            'outputs': outs, 'monitors': len(screens), 'screens': screens,
            'audio_master': self.proj.get('audio_master', 1.0),
            'out_warning': ' ／ '.join(self.warnings),
            'media': self.media, 'layers': ls, 'dirty': self.dirty,
            'message': self.message, 'blends': BLENDS, 'modes': CUE_MODES, 'transitions': TRANSITIONS,
            'sel_corner': self.sel_corner,
        }
        st['lan_urls'] = self.lan_urls
        st['allow_lan'] = bool(self.proj.get('allow_lan'))
        st['setting_name'] = self.proj.get('setting_name') or ''
        st['settings'] = self.list_settings()
        data = json.dumps(st, ensure_ascii=False).encode('utf-8')
        # スマホ用：位置合わせに要るものだけ（Wi-Fi でも軽く・速く）
        mst = {
            'rev': self.rev, 'boot': self.boot, 'edit': self.edit, 'selected': self.selected,
            'map_out': self.map_out, 'sel_corner': self.sel_corner, 'targets': targets,
            'size': st['size'], 'blackout': self.blackout, 'undo': len(self.corner_hist),
            'message': self.message,
            'layers': [{k: x[k] for k in ('id', 'name', 'kind', 'visible', 'test', 'opacity', 'feather', 'places')}
                       for x in ls],
        }
        mdata = json.dumps(mst, ensure_ascii=False).encode('utf-8')
        with self.state_lock:
            self.state_json = data
            self.mstate_json = mdata

    def get_state(self, mobile=False):
        with self.state_lock:
            return self.mstate_json if mobile else self.state_json

    # ---- スマホから操作（同じ Wi-Fi の機器に受け口を開く）----
    def set_lan(self, on, save=True):
        """起動したまま、スマホから開けるようにする／やめる。設定（allow_lan）にも残す"""
        if on:
            ips = webui.lan_start(lan_ips())
            self.lan_urls = [f'http://{ip}:{self.web_port}/m' for ip in ips]
            for u in self.lan_urls:
                print('スマホ（セッティング）:', u)
            if not ips:
                self.message = 'スマホ用の受け口を開けませんでした（Wi-Fi・有線LAN につながっているか確認）'
        else:
            webui.lan_stop()
            self.lan_urls = []
        if save:
            self.proj['allow_lan'] = bool(on)
            self.touch()

    # ---- 四隅の「1つ戻す」----
    def remember_corners(self, L, k, alone=False):
        """四隅を変える前の形を覚える。同じ所を続けて動かしている間（1.5秒以内）は1回分にまとめる"""
        key = (L.cfg.get('id'), k)
        now = time.time()
        if not alone and self.corner_hist and self.corner_hist[-1][0] == key and now - self.corner_hist[-1][2] < 1.5:
            self.corner_hist[-1][2] = now
            return
        cc = next((c for h, c in self.layer_places(L) if h == k), None)
        if cc is None:
            return
        self.corner_hist.append([key, [list(p) for p in cc], now])
        del self.corner_hist[:-100]

    def undo_corners(self):
        while self.corner_hist:
            (lid, k), pts, _ = self.corner_hist.pop()
            L = self.layer_by_id(lid)
            cc = next((c for h, c in self.layer_places(L) if h == k), None) if L else None
            if cc is not None:
                cc[:] = pts
                self.selected, self.map_out = self.layers.index(L), k
                self.message = f'四隅を1つ前に戻しました（{L.cfg["name"]}）'
                self.touch()
                return

    # ---- キーボード・マウス（出力ウィンドウ）----
    def on_key(self, O, k, mods, repeat):
        ctrl = mods & glfw.MOD_CONTROL
        shift = mods & glfw.MOD_SHIFT
        if ctrl and k == glfw.KEY_Q:
            self.running = False
        elif ctrl and k == glfw.KEY_S:
            self.save()
        elif k in (glfw.KEY_F, glfw.KEY_F11) and not repeat:
            O.set_fullscreen(not O.fullscreen)
            self.touch(True)
        elif k == glfw.KEY_SPACE and not repeat:
            self.layers[0].next()
        elif k == glfw.KEY_B and not repeat:
            self.blackout = not self.blackout
        elif k == glfw.KEY_E and not repeat:
            self.set_edit(not self.edit)
        elif k == glfw.KEY_ESCAPE:
            self.set_edit(False)
        elif glfw.KEY_1 <= k <= glfw.KEY_9:
            i = k - glfw.KEY_1
            if i < len(self.layers):
                self.selected = i
        elif self.edit:
            L, C = self.sel_corners()
            if L is None:
                return
            if k == glfw.KEY_TAB:  # 角1→2→3→4→全体→角1…
                self.sel_corner = (self.sel_corner + (4 if shift else 1)) % 5
            elif k == glfw.KEY_A and not repeat:
                self.sel_corner = 4
            elif k == glfw.KEY_T and not repeat:
                L.cfg['test'] = not L.cfg['test']
                self.touch()
            elif k == glfw.KEY_R and not repeat:
                C[:] = [list(p) for p in DEFAULT_CORNERS]
                self.touch()
            elif k in (glfw.KEY_LEFT, glfw.KEY_RIGHT, glfw.KEY_UP, glfw.KEY_DOWN):
                self.nudge({glfw.KEY_LEFT: -1, glfw.KEY_RIGHT: 1}.get(k, 0),
                           {glfw.KEY_UP: -1, glfw.KEY_DOWN: 1}.get(k, 0), 10 if shift else 1)

    def nudge(self, dx, dy, step=1):
        """矢印キー：選んでいる角（sel_corner=4 なら全体）を出力の画素単位で動かす"""
        L, C = self.sel_corners()
        if L is None:
            return
        self.remember_corners(L, self.map_out)
        w, h = self.target_size(self.map_out)
        dx, dy = dx * step / w, dy * step / h
        for p in (C if self.sel_corner == 4 else [C[self.sel_corner]]):
            p[0] += dx
            p[1] += dy
        self.touch()

    def on_mouse_down(self, O, x, y):
        if not self.edit:
            return
        oi = self.host_of(O.index)
        w, h = self.target_size(oi)
        cs = {i: c for i, L in enumerate(self.layers) for k, c in self.layer_places(L) if k == oi}
        mine = list(cs)
        order = ([self.selected] if self.selected in mine else []) + [i for i in mine if i != self.selected]
        best = None
        for li in order:
            for ci, (cx, cy) in enumerate(cs[li]):
                d = (((cx - x) * w) ** 2 + ((cy - y) * h) ** 2) ** 0.5
                if d < 22 and (best is None or d < best[0]):
                    best = (d, li, ci)
            if best:
                break
        if best:
            _, li, ci = best
            self.selected, self.sel_corner, self.map_out = li, ci, oi
            self.remember_corners(self.layers[li], oi)
            self.drag = ('corner', li, ci, None, cs[li])
        else:
            for li in order:
                if self._inside(cs[li], x, y):
                    self.selected, self.map_out, self.sel_corner = li, oi, 4
                    self.remember_corners(self.layers[li], oi)
                    self.drag = ('move', li, 0, (x, y), cs[li])
                    break

    def on_mouse_up(self, O):
        self.drag = None

    def on_mouse_move(self, O, x, y):
        if not (self.edit and self.drag):
            return
        kind, li, ci, last, C = self.drag
        if li >= len(self.layers):
            self.drag = None
            return
        if kind == 'corner':
            C[ci] = [x, y]
        else:
            dx, dy = x - last[0], y - last[1]
            for p in C:
                p[0] += dx
                p[1] += dy
            self.drag = (kind, li, ci, (x, y), C)
        self.touch()

    @staticmethod
    def _inside(poly, x, y):
        inside = False
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                inside = not inside
        return inside

    def hud(self):
        L = self.layer(self.selected)
        name = f'L{self.selected + 1}'
        if L is not None and len(self.outs) > 1:
            name += f' (OUT {self.map_out + 1})'
        cn = 'ALL' if self.sel_corner == 4 else f'corner {self.sel_corner + 1}/4'
        text = (f'EDIT {name}  {cn}   drag / arrows(+Shift)   '
                f'Tab:corner/ALL  A:ALL  1-9:layer  T:test  R:reset  E:exit')
        if text != self.hud_text:
            tw, th = self.font.getbbox(text)[2:]
            im = Image.new('RGBA', (tw + 16, th + 12), (0, 0, 0, 190))
            ImageDraw.Draw(im).text((8, 4), text, font=self.font, fill=(255, 230, 60, 255))
            if self.hud_tex:
                self.hud_tex.release()
            self.hud_tex = self.ctx.texture(im.size, 4, im.tobytes())
            self.hud_text = text
        return self.hud_tex, self.hud_tex.size

    def snapshot(self):
        """出力ごとの画像を保存（出力1 = snapshot.png、出力2 = snapshot_2.png …）"""
        for i, o in enumerate(self.outs):
            if o.fbo is None:
                continue
            w, h = o.tex.size
            img = Image.frombytes('RGB', (w, h), o.fbo.read(components=3))
            p = self.snap_path if i == 0 else self.snap_path.replace('.png', f'_{i + 1}.png')
            img.transpose(Image.FLIP_TOP_BOTTOM).save(p)
        self.message = 'snapshot.png を保存しました'
        self.snap_path = None

    # ---- メインループ ----
    def run(self):
        port = int(self.proj.get('web_port', 8080))
        port = webui.start(self, '127.0.0.1', port)  # このPCの中からはいつでも
        self.web_port = port
        if self.proj.get('allow_lan'):
            self.set_lan(True, save=False)
        url = f'http://localhost:{port}/'
        print(f'操作パネル: {url}   （出力映像の再生: {backend_name()}、出力 {len(self.outs)}）')
        if not os.environ.get('STAGEMAPPER_NO_BROWSER'):
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()

        self.scan_inputs()
        last = time.perf_counter()
        last_state = 0.0
        frames, fps_t = 0, last
        while self.running:
            glfw.poll_events()
            while True:
                try:
                    c = self.cmd_q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle(c)
                except Exception as ex:
                    print('コマンドエラー:', c, ex)

            now = time.perf_counter()
            dt = min(now - last, 0.1)
            last = now
            target = 0.0 if self.blackout else self.proj['master']
            sp = dt / 0.5
            if self.master_eff < target:
                self.master_eff = min(target, self.master_eff + sp)
            else:
                self.master_eff = max(target, self.master_eff - sp)

            wall = time.time()
            for L in self.layers:
                try:
                    L.update(dt, wall)
                except Exception as ex:  # 1つのレイヤーの不具合で本番全体を止めない
                    L.error = f'内部エラー: {ex}'
                    self._report('レイヤー更新', ex)

            try:
                self._draw_frame()
            except Exception as ex:
                self._report('描画', ex)
                try:
                    glfw.make_context_current(self.main_win)
                except Exception:
                    pass

            frames += 1
            if now - fps_t >= 1.0:
                self.fps = frames / (now - fps_t)
                frames, fps_t = 0, now
            if wall - self.last_media_scan > 3.0:
                self._safe_call('素材の一覧', self.scan_media)
                self.last_media_scan = wall
            if self.dirty and wall - self.last_change > 2.0:
                if not self._safe_call('保存', self.save):
                    self.last_change = wall + 3.0  # 保存できなかった（Dropbox や他のアプリが使用中など）→ 5秒後にもう一度
                    self.message = '設定を保存できませんでした。少しあとでもう一度保存します'
            if wall - last_state > 0.15:
                self._safe_call('状態', self.build_state)
                last_state = wall
                if self.message and int(wall) % 4 == 0:
                    self.message = ''
            # 垂直同期が効いていないときに空回りしない（最大 約240fps）
            spent = time.perf_counter() - now
            if spent < 1 / 240:
                time.sleep(1 / 240 - spent)

        if self.dirty:
            self._safe_call('保存', self.save)
        for L in self.layers:
            L.release()
        for o in self.outs:
            o.destroy()
        glfw.terminate()

    def _report(self, where, ex):
        """エラーを記録（同じ場所のエラーは5秒に1回だけ表示）"""
        now = time.time()
        if now - self._err_t.get(where, 0) > 5.0:
            self._err_t[where] = now
            import traceback
            print(f'[{where}] エラー:', ex)
            traceback.print_exc()

    def _safe_call(self, where, fn):
        try:
            fn()
            return True
        except Exception as ex:
            self._report(where, ex)
            return False

    def _draw_frame(self):
        # メイン側で出力ごとに描く → 各ウィンドウに貼る
        glfw.make_context_current(self.main_win)
        self._grp = None
        used = self._layer_refs()
        self.renderer.upload_all(self.layers, used)
        self._render_layer_sources(used)
        hud = self.hud() if self.edit else None
        groups = self.groups()
        for g in groups:
            host = self.outs[g['host']]
            fbo, size = host.ensure_target(self.ctx, self.target_size(g['host']))
            mine = [(li, L, cc) for li, L in enumerate(self.layers)
                    for k, cc in self.layer_places(L) if k == g['host']]
            self.renderer.render_output(fbo, size, mine, self.master_eff, self.edit,
                                        self.selected, self.sel_corner,
                                        hud if self.map_out == g['host'] else None)
            for m in g['members'][1:]:
                self.outs[m].drop_target()
        if self.snap_path:
            self.snapshot()
        self.ctx.finish()
        for g in groups:
            src = self.outs[g['host']].tex
            n = len(g['members'])
            for i, m in enumerate(g['members']):
                w = g['ws'][i]
                bl = g['ovs'][i] / w if i > 0 else 0.0
                br = g['ovs'][i + 1] / w if i + 1 < n else 0.0
                gl = g['gms'][i] if i > 0 else 2.2          # 左のつなぎ目
                gr = g['gms'][i + 1] if i + 1 < n else 2.2  # 右のつなぎ目
                self.outs[m].present(src, (g['xs'][i] / g['W'], (g['xs'][i] + w) / g['W']),
                                     (bl, br), (gl, gr))
        glfw.make_context_current(self.main_win)


def lan_ips():
    """同じ Wi-Fi の機器からつなぐときの、このPCのアドレス（192.168.… など）"""
    import socket
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))  # 実際には送らない。外へ出るときに使うアドレスを知るだけ
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith('127.') and not ip.startswith('169.254.'):
                ips.append(ip)
    except OSError:
        pass
    return ips


def main():
    ap = argparse.ArgumentParser(description='VPT39')
    ap.add_argument('project', nargs='?', default=os.path.join(HERE, 'project.json'))
    ap.add_argument('--window', action='store_true', help='この回だけ全部の出力をウィンドウで開く（リハ・テスト用）')
    ap.add_argument('--display', type=int, default=None, help='（旧オプション・無視されます）')
    args = ap.parse_args()
    App(args.project, args).run()


if __name__ == '__main__':
    main()
