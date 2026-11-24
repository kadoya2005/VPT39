"""VPT39 — ライブ用 映像再生＋プロジェクションマッピング

起動:  python stage.py              （project.json を使用）
       python stage.py --window     （この回だけ、全部の出力をウィンドウで開く）

出力ウィンドウは VPT と同じ使い方:
  小さなウィンドウで開く → 投影したい画面（プロジェクター）へドラッグ →
  「フルスクリーン」ボタン、F／F11、ダブルクリックで、その画面いっぱいに表示
"""
import os
import sys
import json
import hashlib
import math
import time
import queue
import shutil
import stat
import argparse
import threading
import tempfile
import subprocess
import webbrowser
from pathlib import Path
from collections import Counter

import glfw
import moderngl
from PIL import Image, ImageDraw, ImageFont

from engine import (Layer, Renderer, layer_defaults, clip_mix, DEFAULT_CORNERS, BLENDS,
                    BLANK, CLIP_MIX, CUE_MODES, GAP, LAYER, LAYER_FLIPS, TRANSITIONS)
from sources import backend_name, is_video, is_image, list_cameras, list_mics, list_windows, list_spout
from outputs import OutputWindow, gl_hints, monitors, monitor_info
import webui
import i18n
from ndi_output import NdiSender, source_name
from i18n import tr

HERE = os.path.dirname(os.path.abspath(__file__))


def new_id():
    import uuid
    return uuid.uuid4().hex[:8]


def new_deck(number):
    return {'id': new_id(), 'name': tr(f'デッキ {number}')}


def column_names(names):
    return [n.strip()[:80] if isinstance(n, str) else '' for n in names] if isinstance(names, list) else []


def default_project():
    bg = layer_defaults('video', tr('背景動画'))
    ov = layer_defaults('video', tr('オーバーレイ'))
    ov['blend'] = 'screen'
    ov['audio'] = False
    ph = layer_defaults('photo', tr('写真ウォール'))
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
        'column_names': [],
    }


def normalize_project(p):
    """足りない項目を補う（古い設定ファイル・手で書いたファイルでも開けるように）"""
    if not isinstance(p, dict) or not isinstance(p.get('layers', []), list):
        raise ValueError('VPT39 の設定ファイルではありません')
    dp = default_project()
    for k, v in dp.items():
        if k != 'outputs':
            p.setdefault(k, v)
    p['column_names'] = column_names(p['column_names'])
    for L in p['layers']:
        for k, v in layer_defaults(L.get('kind', 'video')).items():
            if k not in LAYER_FLIPS:
                L.setdefault(k, v)
        L['test'] = False  # 開いたときはテストパターンを必ずオフ
        if not L.get('id'):
            L['id'] = new_id()
    ranked = sorted(enumerate(p['layers']),
                    key=lambda pair: pair[1]['z_order'] if type(pair[1].get('z_order')) is int else pair[0])
    for rank, (_, layer) in enumerate(ranked):
        layer['z_order'] = rank
    # 表示中のデッキは従来どおり layers と column_names に保存する。
    # ほかのデッキには動画レイヤーのクリップ一覧とコラム名を保存する。
    decks = []
    seen = set()
    video_ids = {L['id'] for L in p['layers'] if L['kind'] == 'video'}
    if isinstance(p.get('decks'), list):
        for entry in p['decks']:
            if not isinstance(entry, dict):
                continue
            deck_id = entry.get('id')
            if not isinstance(deck_id, str) or not deck_id or deck_id in seen:
                continue
            seen.add(deck_id)
            name = entry.get('name')
            name = name.strip()[:80] if isinstance(name, str) else ''
            deck = {'id': deck_id, 'name': name or tr(f'デッキ {len(decks) + 1}')}
            if deck_id != p.get('active_deck'):
                rows = entry.get('cues')
                deck['cues'] = {lid: row for lid, row in rows.items()
                                if lid in video_ids and isinstance(row, list)} if isinstance(rows, dict) else {}
                deck['column_names'] = column_names(entry.get('column_names'))
            decks.append(deck)
    if not isinstance(p.get('active_deck'), str) or p['active_deck'] not in seen:
        active = new_deck(len(decks) + 1)
        p['active_deck'] = active['id']
        decks.append(active)
    p['decks'] = decks
    for layer in p['layers']:
        missing = [key for key in LAYER_FLIPS if key not in layer]
        if not missing:
            continue
        rows = [layer['cues'], *(d.get('cues', {}).get(layer['id'], []) for d in decks)]
        clips = [c for row in rows for c in row if c.get('file') not in (None, '', BLANK, GAP)]
        # 旧形式のクリップで反転が統一されていた軸だけ、レイヤーに引き継ぐ。
        for key in missing:
            layer[key] = bool(clips) and all(c.get(key) is True for c in clips)
    return p


SETTING_BAD = set('\\/:*?"<>|')
DECK_SCOPED_CMDS = {
    'go', 'transport', 'next', 'prev', 'go_column', 'cue_mix', 'cue_set', 'cue_mode',
    'add_cue', 'import_cue', 'remove_cue', 'move_cue', 'move_cue_into', 'move_cue_to',
    'add_column', 'remove_column', 'rename_column', 'move_column',
}


def clean_setting_name(name):
    """セッティング名（ファイル名になる）を確かめる。使えない名前なら None"""
    n = str(name or '').strip()
    if not n or len(n) > 60 or n.startswith('.') or any(ch in SETTING_BAD or ord(ch) < 32 for ch in n):
        return None
    if n.upper() in ('CON', 'PRN', 'AUX', 'NUL') or n.lower().endswith('.json'):
        return None
    return n


def choose_setup_file(action, folder, suggested):
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        options = {'parent': root, 'initialdir': folder,
                   'filetypes': [('VPT39 JSON', '*.json')], 'defaultextension': '.json'}
        if action == 'save':
            return filedialog.asksaveasfilename(title=tr('名前を付けて保存'), initialfile=suggested,
                                                 confirmoverwrite=True, **options)
        return filedialog.askopenfilename(title=tr('セッティングを開く'), **options)
    finally:
        root.destroy()


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
        self._setup_file_result = queue.SimpleQueue()
        self._setup_dialog_open = False
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
        self._media_use_cache = (None, {})
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
        self.ndi_workers = {}

        self._init_gl()
        self.outs = []
        self._migrate_outputs()
        for i, oc in enumerate(self.proj['outputs']):
            self.outs.append(OutputWindow(self, oc, i))
        self.restore_ndi()
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
        """旧形式の出力設定を、独立した出力ウィンドウ形式に変換する。"""
        self.proj.pop('video_wall', None)
        old = self.proj.pop('output', None) or {}
        if not self.proj.get('outputs'):
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
        was_joined = any(o.get('join') for o in self.proj['outputs'][1:])
        for o in self.proj['outputs']:
            for key in ('join', 'overlap', 'gamma'):
                o.pop(key, None)
            if not o.get('ndi_only') and o.get('ndi_enabled') is True:
                o['ndi_enabled'] = False
        if was_joined:
            self.warn('以前の横長設定を解除しました。レイヤーの出力先を確認してください')

    def warn(self, msg):
        print(msg)
        self.warnings.append(msg)

    # ---- パス ----
    def resolve(self, p):
        return p if os.path.isabs(p) else os.path.join(self.base, p)

    def media_path(self, rel):
        return rel if os.path.isabs(rel) else os.path.join(self.resolve(self.proj['media_dir']), rel)

    # ---- 出力 ----
    @staticmethod
    def ndi_name(out):
        return source_name(out.cfg.get('ndi_name')) or f'VPT39 Output {out.index + 1}'

    def enable_ndi_output(self, out):
        if out.cfg.get('ndi_enabled') is True:
            return
        taken = {self.ndi_name(o).casefold() for o in self.outs
                 if o is not out and o.cfg.get('ndi_enabled') is True}
        name = self.ndi_name(out)
        number = out.index + 1
        while name.casefold() in taken:
            name = f'VPT39 NDI {number}'
            number += 1
        self.set_ndi(out, {'enabled': True, 'name': name})

    def start_ndi(self, out, name):
        if out.ndi_sender is not None:
            out.ndi_sender.close()
        self.ndi_workers = {key: worker for key, worker in self.ndi_workers.items() if worker.is_alive()}
        key = name.casefold()
        out.ndi_sender = NdiSender(name, self.ndi_workers.get(key))
        self.ndi_workers[key] = out.ndi_sender

    def restore_ndi(self):
        names = set()
        for out in self.outs:
            if out.cfg.get('ndi_enabled') is not True:
                continue
            name = self.ndi_name(out)
            if name.casefold() in names:
                out.cfg['ndi_enabled'] = False
                self.warn('NDIソース名が重複した出力を停止しました')
                continue
            out.cfg['ndi_name'] = name
            names.add(name.casefold())
            self.start_ndi(out, name)

    def set_ndi(self, out, command):
        enabled = command.get('enabled') is True
        name = source_name(command.get('name'))
        if enabled and not name:
            self.message = 'NDIソース名を入力してください'
            return
        if enabled and any(o is not out and o.cfg.get('ndi_enabled') is True and
                           self.ndi_name(o).casefold() == name.casefold() for o in self.outs):
            self.message = '出力ごとに異なるNDIソース名を指定してください'
            return
        out.cfg.update(ndi_enabled=enabled, ndi_name=name or self.ndi_name(out))
        if enabled:
            self.start_ndi(out, name)
        elif out.ndi_sender is not None:
            out.ndi_sender.close()
            out.ndi_sender = None
        self.touch()

    def target_size(self, k):
        w, h = self.outs[k].render_size()
        return min(w, self.max_tex), h

    def norm_out(self, v):
        try:
            i = int(v)
        except (TypeError, ValueError):
            i = 0
        return max(0, min(len(self.outs) - 1, i))

    def out_index(self, L):
        return self.norm_out(L.cfg.get('output', 0))

    def out_size(self, L):
        return self.target_size(self.out_index(L))

    def layer_places(self, L):
        """[(出力番号, 四隅)]（同じ出力への重複は1つ目だけ）"""
        seen, out = set(), []
        for k, c in L.places():
            k = self.norm_out(k)
            if k not in seen:
                seen.add(k)
                out.append((k, c))
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
        outputs = [i for i, _ in self.layer_places(L)]
        if on and k not in outputs:
            more.append({'output': k, 'corners': [list(p) for p in DEFAULT_CORNERS]})
        elif not on and k in outputs and len(outputs) > 1:
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
        if not self.edit:
            self.drag = None
            for layer in self.layers:
                if layer.cfg['test']:
                    layer.cfg['test'] = False
                    self.touch()
        for o in self.outs:
            o.update_cursor()

    @staticmethod
    def insert_layer_cue(layer, at, cue):
        row = layer.cfg['cues']
        row.extend({'file': GAP, 'mode': 'loop'} for _ in range(at - len(row)))
        row.insert(at, cue)
        for d in layer.current_row_decks():
            if d.cue >= at:
                d.cue += 1

    def layers_in_output_order(self):
        return sorted(enumerate(self.layers), key=lambda pair: pair[1].cfg['z_order'])

    def insert_column_name(self, at):
        names = self.proj['column_names']
        if at < len(names):
            names.insert(at, '')

    def switch_deck(self, deck):
        active_id = self.proj['active_deck']
        if deck['id'] == active_id:
            return
        active = next(d for d in self.proj['decks'] if d['id'] == active_id)
        active['cues'] = {}
        rows = deck.pop('cues', {})
        for layer in self.layers:
            cfg = layer.cfg
            if cfg['kind'] != 'video':
                continue
            lid = cfg['id']
            active['cues'][lid] = cfg['cues']
            cfg['cues'] = rows.get(lid, [])
        active['column_names'] = self.proj['column_names']
        self.proj['column_names'] = deck.pop('column_names', [])
        self.proj['active_deck'] = deck['id']
        self.touch(True)

    def handle_deck(self, c):
        decks = self.proj['decks']
        cmd = c['cmd']
        anchor = c.get('anchor')
        target = next((d for d in decks if d['id'] == anchor), None) if anchor is not None else None
        if anchor is not None and target is None:
            self.message = 'デッキ一覧が変わりました。もう一度操作してください'
            return
        if cmd == 'deck_add':
            name = c.get('name')
            if isinstance(name, str) and name.strip():
                deck = {'id': new_id(), 'name': name.strip()[:80]}
                at = len(decks) if target is None else decks.index(target) + (c.get('after') is True)
                decks.insert(at, deck)
                self.switch_deck(deck)
            return
        deck = next((d for d in decks if d['id'] == c.get('id')), None)
        if deck is None:
            return
        if cmd == 'deck_select':
            self.switch_deck(deck)
        elif cmd == 'deck_move' and target is not None and target is not deck:
            old = decks.index(deck)
            at = decks.index(target) + (c.get('after') is True)
            if old < at:
                at -= 1
            if at != old:
                decks.insert(at, decks.pop(old))
                self.touch(True)
        elif cmd == 'deck_rename':
            name = c.get('name')
            if isinstance(name, str) and name.strip():
                deck['name'] = name.strip()[:80]
                self.touch(True)
        elif cmd == 'deck_delete' and len(decks) > 1:
            index = decks.index(deck)
            if deck['id'] == self.proj['active_deck']:
                self.switch_deck(decks[index - 1 if index else 1])
            for row in deck['cues'].values():
                row.clear()  # 削除したデッキで再生中の素材は、新しい素材に切り替えるまで残す。
            decks.remove(deck)
            self.touch(True)

    def handle(self, c):
        cmd = c.get('cmd')
        if cmd in ('deck_add', 'deck_select', 'deck_move', 'deck_rename', 'deck_delete'):
            self.handle_deck(c)
            return
        if cmd in DECK_SCOPED_CMDS and c.get('deck', self.proj['active_deck']) != self.proj['active_deck']:
            self.message = ('素材に保存しましたが、操作先のデッキが切り替わりました' if cmd == 'import_cue'
                            else 'デッキが切り替わりました。もう一度操作してください')
            return
        L = self.layer(c.get('layer'))
        if c.get('lid'):  # 確認の間に並びが変わっても、確かめたレイヤーに対して行う
            L = self.layer_by_id(c['lid'])
            if L is None:
                if cmd == 'import_cue':
                    self.message = '素材に保存しましたが、追加先のレイヤーが見つかりません'
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
        elif cmd == 'skip_photo' and L and L.cfg['kind'] == 'photo':
            L.skip_photo()
        elif cmd == 'photo_transport' and L and L.cfg['kind'] == 'photo':
            op = c.get('op')
            if op in ('play', 'restart') and not L.cfg['visible']:
                self.set_layer_value(L, 'visible', True)
            L.photo_transport(op)
        elif cmd == 'photo_settings' and L and L.cfg['kind'] == 'photo':
            interval, loop = c.get('interval'), c.get('loop')
            if type(interval) in (int, float) and 0 <= interval <= 600 and type(loop) is bool:
                L.cfg.update(photo_interval=interval, photo_loop=loop)
                self.touch()
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
        elif cmd == 'cue_mix' and L and L.cfg['kind'] == 'video':
            i = c.get('cue')
            cues = L.cfg['cues']
            if not isinstance(i, int) or not 0 <= i < len(cues):
                return
            cue = cues[i]
            if cue['file'] != c.get('file') or cue['file'] in (BLANK, GAP):
                return
            values = clip_mix({**clip_mix(cue), **c})
            cue.update(values)
            for d in L.decks:
                if d.settings is cue:
                    d.mix = values
            self.touch(True)
        elif cmd == 'cue_set' and L:
            # カメラの音声入力・ウィンドウの題名など、特別なクリップの設定
            i = int(c['cue'])
            if 0 <= i < len(L.cfg['cues']) and c.get('key') in ('vdev', 'adev', 'title', 'sender'):
                L.cfg['cues'][i][c['key']] = str(c.get('value', ''))[:300]
                if c['key'] == 'adev':  # 再生中なら音声だけ入れ替える（映像は止めない）
                    from sources import AudioIn
                    for d in L.current_row_decks():
                        if d.cue == i and hasattr(d.src, 'audio'):
                            new = AudioIn(str(c['value'])) if c['value'] else None  # 先に新しい方を作ってから入れ替える
                            old, d.src.audio = d.src.audio, new
                            if old:
                                old.release()
                            L.error = new.error if new else ''
                self.touch(True)
        elif cmd == 'import_cue' and L:
            f = c.get('file')
            if not isinstance(f, str) or not (is_image(f) or is_video(f)) or not os.path.isfile(self.media_path(f)):
                self.message = '取り込んだ素材が見つかりません'
                return
            if L.cfg['kind'] == 'photo' and not is_image(f):
                self.message = '写真ウォールに入れられるのは画像だけです'
                return
            cues = L.cfg['cues']
            expected = c.get('expected')
            if expected is None:
                cues.append({'file': f, 'mode': 'loop'})
                self.message = 'クリップを追加しました'
            else:
                i = c.get('cue')
                if not isinstance(i, int) or not (0 <= i < len(cues)) or cues[i] != expected:
                    self.message = '素材に保存しましたが、クリップが変わっていたため差し替えませんでした'
                    return
                old = cues[i]
                if f == old.get('file'):
                    return
                cues[i] = {'file': f, 'mode': old.get('mode', 'loop')}
                if L.cfg['kind'] == 'video':
                    cues[i].update(clip_mix(old))
                    for d in L.current_row_decks():
                        if d.cue == i:
                            d.cue = -1
                            if d.mode == 'next':
                                d.mode = 'hold'
                self.message = 'クリップを差し替えました'
            self.touch(True)
        elif cmd == 'add_cue' and L:
            f = c.get('file', '')
            if f and L.cfg['kind'] == 'photo' and (f.startswith('@') or not is_image(f)):
                self.message = '写真ウォールに入れられるのは画像だけです（動画・カメラなどは動画レイヤーへ）'
                f = ''
            if f:
                cues = L.cfg['cues']
                at = c.get('at')
                at = len(cues) if at is None else max(0, min(len(cues), int(at)))
                cue = {'file': f, 'mode': 'next' if c.get('playlist') else 'loop'}
                if L.cfg['kind'] == 'video' and CLIP_MIX.keys() & c.keys():
                    cue.update(clip_mix(c))
                for k in ('vdev', 'adev', 'title', 'sender'):
                    if k in c:
                        cue[k] = str(c[k])[:300]
                if f == '@camera' and 'adev' not in cue:
                    cue['adev'] = ''
                insert_column = c.get('column') and L.cfg['kind'] == 'video'
                if insert_column and (at >= len(cues) or cues[at] != c.get('anchor')):
                    self.message = '挿入先が変わったため、コラムを追加できませんでした'
                    return
                if insert_column:
                    self.insert_column_name(at)
                layers = [x for x in self.layers if x.cfg['kind'] == 'video'] if insert_column else [L]
                for layer in layers:
                    self.insert_layer_cue(layer, at, cue if layer is L else {'file': GAP, 'mode': 'loop'})
                self.touch(True)
        elif cmd == 'move_cue_into' and L and L.cfg['kind'] == 'video':
            source = self.layer_by_id(c.get('source_lid'))
            if source is None or source.cfg['kind'] != 'video':
                return
            i, j = c.get('source_cue'), c.get('cue')
            src, dst = source.cfg['cues'], L.cfg['cues']
            if not isinstance(i, int) or not isinstance(j, int) or not (0 <= i < len(src) and 0 <= j <= len(dst)):
                return
            incoming = src[i]
            outgoing = dst[j] if j < len(dst) else None
            if incoming != c.get('source_expected') or outgoing != c.get('expected'):
                self.message = 'ドラッグ中にクリップが変わったため、移動できませんでした'
                return
            if incoming['file'] == GAP or (source is L and i == j):
                return
            if incoming['file'] == LAYER + L.cfg['id']:
                self.message = '自分自身の映像は追加できません（別のレイヤーに追加してください）'
                return
            insert = c.get('insert') is True and source is not L and j < len(dst)
            src[i] = {'file': GAP, 'mode': 'loop'}
            for layer in (L,) if source is L else (source, L):
                for d in layer.current_row_decks():
                    if source is L and d.cue == i:
                        d.cue = j
                    elif layer is source and layer is not L and d.cue == i:
                        d.cue = -1
                        d.target = 0.0
                    elif not insert and layer is L and d.cue == j:
                        d.cue = -1
                        if d.mode == 'next':
                            d.mode = 'hold'
            if insert:
                self.insert_column_name(j)
                for layer in self.layers:
                    if layer.cfg['kind'] == 'video':
                        self.insert_layer_cue(layer, j, incoming if layer is L else {'file': GAP, 'mode': 'loop'})
            else:
                dst[j:j + 1] = [incoming]
            self.touch(True)
        elif cmd == 'move_cue_to' and L:
            # ドラッグで並べ替え（i 番目を j 番目へ）
            cues = L.cfg['cues']
            i, j = int(c['cue']), int(c['to'])
            if 0 <= i < len(cues) and 0 <= j < len(cues) and i != j:
                order = list(range(len(cues)))
                order.insert(j, order.pop(i))
                cues[:] = [cues[k] for k in order]
                new = {old: k for k, old in enumerate(order)}
                for d in L.current_row_decks():
                    if d.cue in new:
                        d.cue = new[d.cue]
                self.touch(True)
        elif cmd in ('add_column', 'remove_column', 'rename_column', 'move_column'):
            j = c.get('cue')
            layers = [x for x in self.layers if x.cfg['kind'] == 'video']
            names = self.proj['column_names']
            count = max(len(names), max((len(x.cfg['cues']) for x in layers), default=0))
            if not layers or not isinstance(j, int) or not 0 <= j <= count:
                return
            if c.get('rev') != self.rev:
                self.message = 'コラムが変わりました。もう一度操作してください'
                return
            if cmd == 'add_column':
                self.insert_column_name(j)
                for layer in layers:
                    self.insert_layer_cue(layer, j, {'file': GAP, 'mode': 'loop'})
            elif cmd == 'move_column':
                to = c.get('to')
                if type(to) is not int or not (0 <= j < count and 0 <= to < count) or j == to:
                    return
                order = list(range(count))
                order.insert(to, order.pop(j))
                new_positions = {old: new for new, old in enumerate(order)}
                names[:] = [names[k] if k < len(names) else '' for k in order]
                while names and not names[-1]:
                    names.pop()
                for layer in layers:
                    row = layer.cfg['cues']
                    row[:] = [row[k] if k < len(row) else {'file': GAP, 'mode': 'loop'} for k in order]
                    for d in layer.current_row_decks():
                        d.cue = new_positions.get(d.cue, d.cue)
            elif cmd == 'rename_column' and j < count:
                name = c.get('name')
                if not isinstance(name, str):
                    return
                names.extend('' for _ in range(j + 1 - len(names)))
                names[j] = name.strip()[:80]
                while names and not names[-1]:
                    names.pop()
            elif cmd == 'remove_column' and count > 1 and j < count:
                if j < len(names):
                    names.pop(j)
                for layer in layers:
                    row = layer.cfg['cues']
                    if j >= len(row):
                        continue
                    row.pop(j)
                    for d in layer.current_row_decks():
                        if d.cue == j:
                            d.cue = -1
                            if d.mode == 'next':
                                d.mode = 'hold'
                        elif d.cue > j:
                            d.cue -= 1
            else:
                return
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
                compact = L.cfg['kind'] != 'video'
                if compact:
                    L.cfg['cues'].pop(i)
                else:
                    L.cfg['cues'][i] = {'file': GAP, 'mode': 'loop'}
                for d in L.current_row_decks():
                    if d.cue == i:
                        d.cue = -1
                        if d.mode == 'next':
                            d.mode = 'hold'
                    elif compact and d.cue > i:
                        d.cue -= 1
                self.touch(True)
        elif cmd == 'move_cue' and L:
            i, j = int(c['cue']), int(c['cue']) + int(c['dir'])
            cues = L.cfg['cues']
            if 0 <= i < len(cues) and 0 <= j < len(cues):
                cues[i], cues[j] = cues[j], cues[i]
                for d in L.current_row_decks():
                    if d.cue == i:
                        d.cue = j
                    elif d.cue == j:
                        d.cue = i
                self.touch(True)
        elif cmd == 'cue_mode' and L:
            i = int(c['cue'])
            mode = c['value']
            if 0 <= i < len(L.cfg['cues']) and mode in CUE_MODES:
                cue = L.cfg['cues'][i]
                if 'file' in c and cue['file'] != c['file']:
                    return
                cue['mode'] = mode
                for d in L.current_row_decks():
                    if d.cue == i and d.label == cue['file']:
                        d.mode = mode
                        if hasattr(d.src, 'mode'):
                            d.src.mode = mode
                self.touch(True)
        elif cmd == 'add_layer':
            kind = 'photo' if c.get('kind') == 'photo' else 'video'
            cfg = layer_defaults(kind)
            cfg['id'] = new_id()
            cfg['z_order'] = len(self.layers)
            cfg['output'] = self.out_index(self.layers[self.selected]) if self.layer(self.selected) else 0
            self.proj['layers'].append(cfg)
            self.layers.append(Layer(cfg, self))
            self.touch(True)
        elif cmd == 'remove_layer' and L and len(self.layers) > 1:
            i = self.layers.index(L)
            L.release()
            self.layers.pop(i)
            self.proj['layers'].pop(i)
            for deck in self.proj['decks']:
                deck.get('cues', {}).pop(L.cfg['id'], None)
            for rank, (_, layer) in enumerate(self.layers_in_output_order()):
                layer.cfg['z_order'] = rank
            if self.selected > i:  # 上のレイヤーを消したら、選んでいたレイヤーを選んだままにする
                self.selected -= 1
            self.selected = max(0, min(self.selected, len(self.layers) - 1))
            self.touch(True)
        elif cmd == 'move_layer_row' and L:
            to = c.get('to')
            if c.get('rev') != self.rev:
                self.message = 'レイヤー一覧が変わりました。もう一度操作してください'
                return
            if type(to) is not int or not 0 <= to < len(self.layers):
                return
            i = self.layers.index(L)
            if i == to:
                return
            selected = self.layers[self.selected]
            self.layers.insert(to, self.layers.pop(i))
            rows = self.proj['layers']
            rows.insert(to, rows.pop(i))
            self.selected = self.layers.index(selected)
            self.drag = None
            self.touch(True)
        elif cmd == 'set_layer_order' and L:
            rank = c.get('value')
            if type(rank) is int and 0 <= rank < len(self.layers) and rank != L.cfg['z_order']:
                ordered = [layer for _, layer in self.layers_in_output_order() if layer is not L]
                ordered.insert(rank, L)
                for i, layer in enumerate(ordered):
                    layer.cfg['z_order'] = i
                self.touch(True)
        # ---- 出力ウィンドウ ----
        elif cmd == 'out_fullscreen' and O:
            O.set_fullscreen(bool(c.get('value', not O.fullscreen)))
            self.touch(True)
        elif cmd == 'out_bring_back' and O:
            O.bring_back()
            self.touch(True)
        elif cmd == 'out_add':
            ndi_only = c.get('type') == 'ndi'
            oc = {'ndi_only': True} if ndi_only else {}
            out = OutputWindow(self, oc, len(self.outs))
            self.proj['outputs'].append(oc)
            self.outs.append(out)
            if ndi_only:
                self.enable_ndi_output(out)
            self.touch(True)
        elif cmd == 'out_type' and O and c.get('type') in ('window', 'ndi'):
            kind = c['type']
            if kind == 'ndi' and O.win:
                O.close_window()
                O.cfg['ndi_only'] = True
            elif kind == 'window' and not O.win:
                O.open_window()
                O.cfg.pop('ndi_only', None)
            if kind == 'window' and O.cfg.get('ndi_enabled') is True:
                self.set_ndi(O, {'enabled': False, 'name': self.ndi_name(O)})
            elif kind == 'ndi':
                self.enable_ndi_output(O)
            self.touch(True)
        elif cmd == 'out_ndi_name' and O:
            name = source_name(c.get('name'))
            if name != self.ndi_name(O):
                self.set_ndi(O, {'enabled': O.cfg.get('ndi_enabled') is True, 'name': name})
        elif cmd == 'lan':
            self.set_lan(bool(c.get('value')))
        elif cmd == 'lan_refresh' and self.proj.get('allow_lan'):
            self.set_lan(True)  # Wi-Fi を変えたときなど、アドレスを調べ直す
        elif cmd == 'setting_save_as':
            self.setting_save_as(c.get('name'), bool(c.get('overwrite')))
        elif cmd in ('setting_save_dialog', 'setting_open_dialog'):
            self.show_setup_dialog('save' if cmd == 'setting_save_dialog' else 'open', c.get('copy') is True)
        elif cmd == 'setting_open':
            self.setting_open(c.get('name'))
        elif cmd == 'setting_reset' and c.get('confirmed') is True:
            self.reset_project(c.get('items'))
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
            self.touch(True)
        elif cmd == 'out_fps' and O:
            limit = c.get('value')
            if type(limit) is int and limit in (30, 60):
                O.cfg['fps_limit'] = limit
                O.next_frame = 0.0
                self.touch()
        elif cmd == 'out_remove' and O and len(self.outs) > 1:
            i = self.outs.index(O)
            self.drag = None
            O.destroy()
            def reindex(k):
                return k - 1 if k > i else k

            for layer in self.layers:
                places = [(reindex(k), corners) for k, corners in self.layer_places(layer) if k != i]
                if not places:
                    places = [(0, layer.cfg['corners'])]
                layer.cfg['output'], layer.cfg['corners'] = places[0]
                layer.cfg['more'] = [{'output': k, 'corners': corners} for k, corners in places[1:]]
            self.corner_hist = [[(lid, reindex(k)), pts, stamp]
                                for (lid, k), pts, stamp in self.corner_hist if k != i]
            self.outs.pop(i)
            self.proj['outputs'].pop(i)
            for k, o in enumerate(self.outs):
                o.index = k
                o.update_title()
            glfw.make_context_current(self.main_win)
            self.map_out = 0 if self.map_out == i else reindex(self.map_out)
            self.sel_corners()
            self.touch(True)
        elif cmd == 'lang':
            i18n.set_lang(c.get('value'))
            for o in self.outs:
                o.update_title()
            self.rev += 1
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
            items.append({'rect': m['rect'], 'label': str(k + 1), 'sub': '\n'.join(tr(x) for x in sub.split('\n'))})
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
        num = {'opacity': (0, 1), 'volume': (0, 1), 'feather': (0, 0.5),
               'photo_interval': (0, 600), 'photo_border': (0, 0.2)}
        boo = {'visible', 'audio', 'test', 'photo_newest', *LAYER_FLIPS}
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
        path = self.active_setting_path()
        if path:
            self.backup_setting(path)
            self._write_json(path, {k: v for k, v in self.proj.items() if k != 'setting_file'})
        self.dirty = False

    BACKUP_EVERY = 600   # 同じセッティングのバックアップは10分に1回まで
    BACKUP_KEEP = 10     # セッティングごとに残す数（古いものから消す）

    def backup_setting(self, p):
        """上書きする前のセッティングを settings\\_backup に残す（自動保存で本番用の設定を変えてしまっても戻せるように）"""
        if not os.path.isfile(p):
            return
        name = os.path.splitext(os.path.basename(p))[0]
        if self.managed_setting_name(p) is None:
            name = 'external-' + hashlib.sha256(os.path.normcase(p).encode('utf-8')).hexdigest()[:16]
        last = getattr(self, '_backup_at', {})
        self._backup_at = last
        now = time.time()
        if now - last.get(name, 0.0) < self.BACKUP_EVERY:
            return
        last[name] = now
        try:
            d = os.path.join(self.settings_dir, '_backup')
            os.makedirs(d, exist_ok=True)
            stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(os.path.getmtime(p)))
            shutil.copy2(p, os.path.join(d, f'{name}_{stamp}.json'))
            mine = sorted(fn for fn in os.listdir(d)
                          if fn.startswith(name + '_') and fn.endswith('.json')
                          and len(fn) == len(name) + 21)  # 名前_YYYYMMDD-HHMMSS.json
            for fn in mine[:-self.BACKUP_KEEP]:
                os.remove(os.path.join(d, fn))
        except OSError as ex:
            print('セッティングのバックアップを作れません:', ex)

    @staticmethod
    def _write_json(path, data):
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', suffix='.tmp',
                                             dir=os.path.dirname(os.path.abspath(path)), delete=False) as f:
                tmp = f.name
                json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)
            os.replace(tmp, path)
            tmp = None
        finally:
            if tmp is not None:
                os.remove(tmp)

    # ---- セッティング（会場・公演ごとの設定ファイル）----
    def setting_path(self, name):
        return os.path.join(self.settings_dir, name + '.json')

    def managed_setting_name(self, path):
        name = clean_setting_name(os.path.splitext(os.path.basename(path))[0])
        if name and os.path.normcase(path) == os.path.normcase(self.setting_path(name)):
            return name
        return None

    def valid_setting_path(self, path):
        return (isinstance(path, str) and os.path.isabs(path) and path.lower().endswith('.json')
                and os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.realpath(self.project_path)))

    def active_setting_path(self):
        path = self.proj.get('setting_file')
        if path and self.valid_setting_path(path):
            return path
        name = clean_setting_name(self.proj.get('setting_name'))
        path = self.setting_path(name) if name else None
        return path if self.valid_setting_path(path) else None

    def set_setting_path(self, project, path):
        project['setting_name'] = os.path.splitext(os.path.basename(path))[0]
        project.pop('setting_file', None)
        if self.managed_setting_name(path) is None:
            project['setting_file'] = path

    def show_setup_dialog(self, action, duplicate=False):
        if self._setup_dialog_open:
            return
        os.makedirs(self.settings_dir, exist_ok=True)
        current = self.active_setting_path()
        folder = os.path.dirname(current) if current else self.settings_dir
        suggested = os.path.basename(current) if current else ''
        if duplicate and suggested:
            suggested = tr(f'{os.path.splitext(suggested)[0]} のコピー') + '.json'
        self._setup_dialog_open = True

        def choose():
            try:
                path = choose_setup_file(action, folder, suggested)
                self._setup_file_result.put((action, path, None))
            except Exception as ex:
                self._setup_file_result.put((action, None, str(ex)))

        threading.Thread(target=choose, daemon=True).start()

    def collect_setup_file(self):
        if not self._setup_dialog_open:
            return
        try:
            action, path, error = self._setup_file_result.get_nowait()
        except queue.Empty:
            return
        self._setup_dialog_open = False
        if error is not None:
            self.message = f'ファイル選択画面を開けませんでした（{error}）'
        elif path:
            if action == 'open':
                self.open_setting_file(os.path.abspath(path))
            else:
                self.save_setting_file(os.path.abspath(path))

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
        if os.path.exists(p) and not overwrite and self.managed_setting_name(self.active_setting_path() or '') != n:
            self.message = f'「{n}」はもうあります（上書きするときは確認のあと保存）'
            return
        self.save_setting_file(p)

    def save_setting_file(self, path):
        name = os.path.splitext(os.path.basename(path))[0]
        if not self.valid_setting_path(path):
            self.message = 'セッティングには project.json とは別の JSON ファイルを選んでください'
            return
        project = self.proj.copy()
        self.set_setting_path(project, path)
        try:
            self.backup_setting(path)
            self._write_json(path, {k: v for k, v in project.items() if k != 'setting_file'})
            self._write_json(self.project_path, project)
        except (OSError, ValueError) as ex:
            self.message = f'保存できませんでした: {name}（{ex}）'
            return
        self.proj = project
        self._settings_cache = (0.0, [])
        self.message = f'セッティング「{name}」に保存しました'
        self.touch(True)
        self.dirty = False

    def setting_open(self, name):
        n = clean_setting_name(name)
        p = self.setting_path(n) if n else None
        if not p or not os.path.isfile(p):
            self.message = f'セッティングが見つかりません: {name}'
            return
        self.open_setting_file(p)

    def reset_project(self, items):
        if not isinstance(items, list):
            return
        assets, settings, layers = (item in items for item in ('assets', 'settings', 'layers'))
        if not (assets or settings or layers):
            return
        saved = list(self.reset_files('settings', settings_only=True)) if settings else []
        name = 'Reset backup ' + time.strftime('%Y%m%d-%H%M%S') + '-' + new_id()
        backup = {k: v for k, v in self.proj.items() if k not in ('setting_name', 'setting_file')}
        try:
            os.makedirs(self.settings_dir, exist_ok=True)
            self._write_json(self.setting_path(name), backup)
        except (OSError, ValueError) as ex:
            self.message = f'バックアップを作成できなかったため、リセットを中止しました: {ex}'
            return
        self._settings_cache = (0.0, [])
        if settings or layers:
            project = backup.copy()
            if settings:
                project.update(outputs=[{}], master=1.0, audio_master=1.0)
                self.blackout = self.edit = False
                if not layers:
                    project['layers'] = [
                        dict(layer_defaults(c['kind'], c['name']), id=c['id'], cues=c['cues'],
                             z_order=c['z_order']) for c in project['layers']
                    ]
            if layers:
                layer = layer_defaults('video')
                layer['cues'] = [{'file': GAP, 'mode': 'loop'}]
                project.update(layers=[layer], column_names=[''], decks=[])
                self.edit = False
            self.apply_project(normalize_project(project), replace_outputs=settings)
        elif assets:
            for layer in self.layers:
                layer.clear()
                layer.release()
        targets = saved
        if assets:
            targets.extend(self.reset_files('media', keep='ここに素材を入れる.txt'))
            targets.extend(self.reset_files('photos', keep='ここに写真を入れる.txt'))
        failed = sum(not webui._to_recycle_bin(str(path)) or path.exists() for path in targets if path.exists())
        self.last_media_scan = 0.0
        self.message = (f'{failed}件をごみ箱へ移せませんでした。バックアップ: {name}'
                        if failed else f'選択した項目をリセットしました。バックアップ: {name}')

    def reset_files(self, folder, keep=None, settings_only=False):
        root = Path(self.base, folder)

        def reparse(path):
            try:
                info = path.lstat()
            except OSError:
                return True
            return stat.S_ISLNK(info.st_mode) or bool(
                getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

        if not root.is_dir() or reparse(root):
            return
        for directory, children, files in os.walk(root):
            at = Path(directory)
            children[:] = [] if settings_only else [
                child for child in children
                if (at != root or child != 'テスト用') and not reparse(at / child)
            ]
            for file in files:
                path = at / file
                if ((at == root and file == keep) or (settings_only and path.suffix.lower() != '.json')
                        or reparse(path)):
                    continue
                yield path

    def open_setting_file(self, p):
        n = os.path.splitext(os.path.basename(p))[0]
        if not self.valid_setting_path(p):
            self.message = 'セッティングには project.json とは別の JSON ファイルを選んでください'
            return
        try:
            with open(p, encoding='utf-8-sig') as f:
                newp = json.load(f)
            if not isinstance(newp, dict) or 'layers' not in newp:
                raise ValueError(tr('VPT39 の設定ファイルではありません'))
            normalize_project(newp)
        except Exception as ex:
            self.message = f'開けませんでした: {n}（{ex}）'
            return
        if self.dirty:
            self._safe_call('保存', self.save)  # 今の状態を先に保存してから切り替える
        # このPCの設定（操作パネルの番号・スマホからの操作）は、開いたファイルではなく今のまま
        for k in ('web_port', 'allow_lan'):
            if k in self.proj:
                newp[k] = self.proj[k]
        self.set_setting_path(newp, p)
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
        active = self.active_setting_path()
        if active and os.path.normcase(active) == os.path.normcase(p):
            self.proj.pop('setting_name', None)
            self.proj.pop('setting_file', None)
            self.touch(True)
        self._settings_cache = (0.0, [])
        self.message = f'セッティング「{n}」をごみ箱へ移しました'

    def apply_project(self, p, *, replace_outputs=True):
        """別のセッティングに切り替える：レイヤーと出力ウィンドウを作り直す"""
        for L in self.layers:
            L.release()
        for tex, fbo in self.layer_fbos.values():
            fbo.release()
            tex.release()
        self.layer_fbos = {}
        if replace_outputs:
            for o in self.outs:
                o.destroy()
        self.proj = p
        self.layers = [Layer(c, self) for c in self.proj['layers']]
        if replace_outputs:
            self.warnings = []
            self._migrate_outputs()
            self.outs = []
            for i, oc in enumerate(self.proj['outputs']):
                self.outs.append(OutputWindow(self, oc, i))
            self.restore_ndi()
            self.master_eff = 0.0 if self.blackout else self.proj.get('master', 1.0)
        glfw.make_context_current(self.main_win)
        self.selected, self.map_out, self.sel_corner = 0, 0, 0
        self.corner_hist = []
        self.drag = None
        self.hud_text = None
        try:
            os.makedirs(self.resolve(self.proj['media_dir']), exist_ok=True)
        except OSError:
            pass
        self.last_media_scan = 0.0
        self.touch(True)
        self._write_json(self.project_path, self.proj)
        self.dirty = False

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
        if self._media_use_cache[0] != self.rev:
            counts = Counter(cue['file'] for deck in self.proj['decks'] for row in deck.get('cues', {}).values()
                             for cue in row if not cue['file'].startswith('@'))
            self._media_use_cache = (self.rev, counts)
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
                item['photo_state'] = L.photo_state
            ls.append(item)
        sel, _ = self.sel_corners()
        outs = []
        for o in self.outs:
            ndi = o.ndi_sender.state() if o.ndi_sender else {'status': 'off', 'receivers': 0, 'source': ''}
            name = o.name() if o.win else f'NDI · {self.ndi_name(o)}'
            outs.append({'name': name, 'size': list(o.render_size()), 'win': list(o.size()) if o.win else None,
                         'res': o.cfg.get('res'), 'fullscreen': o.fullscreen,
                         'fps_limit': o.fps_limit, 'fps': round(o.fps),
                         'monitor': o.monitor_index(),
                         'volume': float(o.cfg.get('volume', 1.0)), 'mute': bool(o.cfg.get('mute')),
                         'ndi': {'enabled': o.cfg.get('ndi_enabled') is True, 'name': self.ndi_name(o), **ndi}})
        screens = self.screens()
        targets = [{'output': k, 'name': o['name'], 'size': o['size']} for k, o in enumerate(outs)]
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
            'column_names': self.proj['column_names'],
            'active_deck': self.proj['active_deck'],
            'decks': [{'id': d['id'], 'name': d['name']} for d in self.proj['decks']],
            'other_deck_media_uses': self._media_use_cache[1],
            'message': self.message, 'blends': BLENDS, 'modes': CUE_MODES, 'transitions': TRANSITIONS,
            'sel_corner': self.sel_corner,
        }
        st['lan_urls'] = self.lan_urls
        st['allow_lan'] = bool(self.proj.get('allow_lan'))
        st['setting_name'] = self.proj.get('setting_name') or ''
        st['setting_external'] = bool(self.proj.get('setting_file'))
        st['settings'] = self.list_settings()
        st['lang'] = i18n.get_lang()
        data = json.dumps(st, ensure_ascii=False).encode('utf-8')
        # スマホ用：位置合わせに要るものだけ（Wi-Fi でも軽く・速く）
        mst = {
            'rev': self.rev, 'boot': self.boot, 'edit': self.edit, 'selected': self.selected,
            'map_out': self.map_out, 'sel_corner': self.sel_corner, 'targets': targets,
            'size': st['size'], 'blackout': self.blackout, 'undo': len(self.corner_hist),
            'message': self.message, 'lang': st['lang'],
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
            if not self.lan_urls or not webui.lan_key():
                webui.new_lan_key()  # オンにするたびに新しい合言葉（「アドレスを調べ直す」では変えない）
            ips = webui.lan_start(lan_ips())
            self.lan_urls = [f'http://{ip}:{self.web_port}/m?k={webui.lan_key()}' for ip in ips]
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

    def nudge(self, dx, dy, step=1):
        """スマホから、選んだ角または全体を出力の画素単位で動かす。"""
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

    # ---- マウス（出力ウィンドウ）----
    def on_mouse_down(self, O, x, y):
        if not self.edit:
            return
        oi = O.index
        w, h = self.target_size(oi)
        cs = {i: c for i, L in enumerate(self.layers) for k, c in self.layer_places(L) if k == oi}
        mine = list(cs)
        order = ([self.selected] if self.selected in mine else []) + [i for i in mine if i != self.selected]
        self.drag = None
        for li in order:
            corners = cs[li]
            distance, corner = min((max(abs(cx - x) * w, abs(cy - y) * h), ci)
                                   for ci, (cx, cy) in enumerate(corners))
            if distance > 6:
                if not self._inside(corners, x, y):
                    continue
                corner = 4
            self.selected, self.map_out, self.sel_corner = li, oi, corner
            self.remember_corners(self.layers[li], oi)
            self.drag = (O, corners, corner, (x, y), [tuple(p) for p in corners])
            break

    def on_mouse_up(self, O):
        if self.drag and self.drag[0] is O:
            self.drag = None

    def on_mouse_move(self, O, x, y):
        if not (self.edit and self.drag and self.drag[0] is O):
            return
        _, corners, corner, (start_x, start_y), original = self.drag
        dx, dy = x - start_x, y - start_y
        for i in range(4) if corner == 4 else (corner,):
            px, py = original[i]
            corners[i] = [px + dx, py + dy]
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
        print(tr(f'操作パネル: {url}   （出力映像の再生: {backend_name()}、出力 {len(self.outs)}）'))
        if not os.environ.get('STAGEMAPPER_NO_BROWSER'):
            threading.Timer(0.8, lambda: webbrowser.open(url)).start()

        self.scan_inputs()
        last = time.perf_counter()
        last_state = 0.0
        fps_t = last
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
            if self._setup_dialog_open:
                self._safe_call('セッティング', self.collect_setup_file)

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

            if now - fps_t >= 1.0:
                fps_sample_at = time.perf_counter()
                for out in self.outs:
                    out.fps = out.frame_count / (fps_sample_at - fps_t)
                    out.frame_count = 0
                self.fps = max((out.fps for out in self.outs), default=0.0)
                fps_t = fps_sample_at
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
            # 操作・再生の更新間隔より先に描画の時刻が来るなら、その時刻に起きる。
            wake_at = min([now + 1 / 240, *(o.next_frame for o in self.outs)])
            delay = wake_at - time.perf_counter()
            if delay > 0:
                time.sleep(delay)

        if self.dirty:
            self._safe_call('保存', self.save)
        for L in self.layers:
            L.release()
        for o in self.outs:
            o.destroy()
        glfw.terminate()
        deadline = time.monotonic() + 1.0
        for worker in self.ndi_workers.values():
            worker.join(max(0, deadline - time.monotonic()))

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
        # メイン側で描き、出力ウィンドウや NDI に渡す。
        now = time.perf_counter()
        due = [o for o in self.outs if now >= o.next_frame]
        if not due and not self.snap_path:
            return
        for o in due:
            o.advance_frame(now)
        ndi_due = {o for o in due if o.ndi_sender is not None and o.ndi_sender.wants_frame()}
        windows = [o for o in due if o.win]
        if not windows and not ndi_due and not self.snap_path:
            return
        glfw.make_context_current(self.main_win)
        used = self._layer_refs()
        self.renderer.upload_all(self.layers, used)
        self._render_layer_sources(used)
        hud = self.hud() if self.edit else None
        ordered_layers = self.layers_in_output_order()
        for o in self.outs if self.snap_path else due:
            if not self.snap_path and not o.win and o not in ndi_due:
                continue
            fbo, size = o.ensure_target(self.ctx, self.target_size(o.index))
            mine = [(li, L, cc) for li, L in ordered_layers
                    for k, cc in self.layer_places(L) if k == o.index]
            self.renderer.render_output(fbo, size, mine, self.master_eff, self.edit,
                                        self.selected, self.sel_corner,
                                        hud if self.map_out == o.index else None)
            if o in ndi_due:
                # 4:2:2 に変換できるように偶数サイズで読み出す。
                width, height = size[0] & ~1, size[1] & ~1
                if width and height:
                    try:
                        pixels = fbo.read(viewport=(0, size[1] - height, width, height), components=4, alignment=1)
                        o.ndi_sender.put(width, height, o.fps_limit, pixels)
                        if not o.win:
                            o.frame_count += 1
                    except Exception as ex:
                        o.ndi_sender.fail_readback(ex)
        if self.snap_path:
            self.snapshot()
        if windows:
            self.ctx.finish()
        for o in windows:
            o.present()
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
