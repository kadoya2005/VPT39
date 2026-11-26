"""ブラウザ操作パネル（標準ライブラリだけで動く小さなWebサーバー）"""
import os
import hmac
import json
import secrets
import tempfile
import threading
from urllib.parse import urlparse, parse_qs
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import thumbs
import i18n

_app = None
_upload_lock = threading.Lock()

# ---- スマホ用の受け口（LAN）の合言葉と、スマホから許す操作 ----
# 合言葉は QR コードのアドレス（/m?k=…）に入れて渡し、スマホではクッキーに覚える。
# スマホからは四隅合わせの画面で使う操作だけ許す（素材の削除・アップロード、PC の操作パネルは PC からだけ）。
_lan_key = ''
LAN_GET = {'/m', '/m/', '/api/mstate'}
LAN_CMDS = {'corners', 'edit', 'map_out', 'nudge', 'reset_corners', 'sel_corner', 'select', 'set', 'undo_corners'}
LAN_SET_KEYS = {'test', 'opacity', 'feather'}
LAN_DENY = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            '<body style="font:18px sans-serif;padding:24px;background:#111;color:#eee">'
            '<p>QR コードを読み取り直してください（PC の操作パネルの「スマホ」ボタン）。</p>'
            '<p>Please scan the QR code again (the "Phone" button on the PC control panel).</p>').encode('utf-8')


def new_lan_key():
    """スマホ用の合言葉を作り直す（前の合言葉のスマホは操作できなくなる）"""
    global _lan_key
    abc = 'abcdefghjkmnpqrstuvwxyz23456789'  # 見まちがえやすい文字（0 o 1 l i）は使わない
    _lan_key = ''.join(secrets.choice(abc) for _ in range(8))
    return _lan_key


def lan_key():
    return _lan_key


def lan_allowed(c):
    """スマホから送ってよい操作か"""
    cmd = c.get('cmd')
    return cmd in LAN_CMDS and (cmd != 'set' or c.get('key') in LAN_SET_KEYS)


def _thumb_path(rel):
    """サムネイルを作ってよいファイルか確認して絶対パスを返す
    （media 内、現在の参照フォルダの一覧、登録済みの素材に限る）"""
    if not rel or _app is None:
        return None
    path = os.path.realpath(_app.media_path(rel))
    root = os.path.realpath(_app.resolve(_app.proj['media_dir']))
    inside = os.path.commonpath([path, root]) == root if os.path.splitdrive(path)[0].lower() == \
        os.path.splitdrive(root)[0].lower() else False
    if not inside:
        files = {c.get('file') for L in _app.proj['layers'] for c in L.get('cues', [])}
        if rel not in files and rel not in _app.proj['reference_media'] and rel not in _app.browser_listing['files']:
            return None
    return path if os.path.isfile(path) else None


_pages = {}

# Adobe Spectrum 2 のダークパレットを、PC とスマホの両方で使う。
SPECTRUM_THEME = """
:root {
  color-scheme: dark;
  --bg: #111111;
  --surface: #1b1b1b;
  --panel: #222222;
  --panel2: #2c2c2c;
  --hover: #323232;
  --line: #393939;
  --edge: #6d6d6d;
  --text: #f3f3f3;
  --dim: #afafaf;
  --accent: #345bf8;
  --accent-hover: #2549e5;
  --focus: #8eb9fc;
  --accent-subtle: #242e50;
  --acc: #69d99d;
  --warn: #f9bb64;
  --red: #ff9d93;
  --negative-subtle: #492923;
  --font: "Adobe Clean Spectrum VF", "Adobe Clean", "Segoe UI", "Yu Gothic UI", sans-serif;
  scrollbar-color: #6d6d6d #222222;
}
"""

MAP_DRAG_SCRIPT = r"""
function mapHandle(point,w,h,r){
  return [Math.min(w-r,Math.max(r,point[0]*w)),Math.min(h-r,Math.max(r,point[1]*h))];
}
function insideMap(points,x,y){
  let inside=false;
  for(let i=0,j=points.length-1;i<points.length;j=i++){
    const [a,b]=points[i], [c,d]=points[j];
    if((b>y)!==(d>y)&&x<(c-a)*(y-b)/(d-b)+a)inside=!inside;
  }
  return inside;
}
function bindMapDrag(cv,{target,redraw,post,radius,onStart,onFinish,interval}){
  const control={current:null};
  let sending=Promise.resolve(), pending=null;
  function send(d){
    const command={cmd:'corners',lid:d.lid,output:d.o,value:d.pts};
    if(pending?.drag===d){pending.command=command;return sending;}
    const next={drag:d,command};
    pending=next;
    // 同じジェスチャーの待機中の更新は最新に置き換え、送信順は保つ。
    sending=sending.then(()=>{
      if(pending===next)pending=null;
      return post(next.command);
    }).catch(()=>{});
    return sending;
  }
  function position(e){
    const rect=cv.getBoundingClientRect();
    return [(e.clientX-rect.left-cv.clientLeft)/cv.clientWidth,(e.clientY-rect.top-cv.clientTop)/cv.clientHeight];
  }
  function move(d,e){
    const pos=position(e);
    if(pos.every((v,i)=>v===d.previous[i]))return;
    d.previous=pos;
    const offset=pos.map((v,i)=>v-d.origin[i]);
    if(d.corner<0){
      // 四隅を個別に制限すると形が崩れる。全体に適用する移動量だけを制限する。
      for(let i=0;i<2;i++){
        const values=d.start.map(p=>p[i]);
        const min=-10-Math.min(...values), max=11-Math.max(...values);
        offset[i]=min<=max?Math.max(min,Math.min(max,offset[i])):0;
      }
    }
    d.pts=d.start.map((p,k)=>d.corner<0||d.corner===k?p.map((v,i)=>v+offset[i]):p);
    d.changed=true;
    redraw();
  }
  cv.addEventListener('pointerdown',e=>{
    if(control.current||!e.isPrimary||e.button!==0)return;
    const t=target(); if(!t)return;
    const origin=position(e), [x,y]=origin, r=radius();
    let corner=-1, distance=Infinity;
    t.corners.forEach((p,k)=>{
      const [hx,hy]=mapHandle(p,cv.width,cv.height,r);
      const dx=Math.abs(hx-x*cv.width), dy=Math.abs(hy-y*cv.height);
      const d=dx*dx+dy*dy;
      if(dx<=r&&dy<=r&&d<distance){corner=k;distance=d;}
    });
    if(corner<0&&!insideMap(t.corners,x,y))return;
    const start=t.corners.map(p=>p.slice());
    control.current={lid:t.lid,o:t.o,pointer:e.pointerId,corner,origin,start,pts:start,previous:origin,lastSend:-Infinity,changed:false};
    cv.setPointerCapture(e.pointerId);
    cv.classList.toggle('map-moving',corner<0);
    e.preventDefault();
    onStart(corner<0?4:corner);
    redraw();
  });
  cv.addEventListener('pointermove',e=>{
    const d=control.current;
    if(d?.pointer!==e.pointerId)return;
    move(d,e);
    const now=performance.now();
    if(d.changed&&now-d.lastSend>=interval){d.lastSend=now;send(d);}
  });
  function finish(e){
    const d=control.current;
    if(d?.pointer!==e.pointerId)return;
    if(e.type==='pointerup')move(d,e);
    control.current=null;
    cv.classList.remove('map-moving');
    if(d.changed)onFinish(d,send(d));
    redraw();
  }
  ['pointerup','pointercancel','lostpointercapture'].forEach(name=>cv.addEventListener(name,finish));
  return control;
}
"""


def page(src):
    """ページに共通のテーマと翻訳スクリプトを入れる。"""
    if src not in _pages:
        _pages[src] = (src.replace('/* SPECTRUM_THEME */', SPECTRUM_THEME)
                       .replace('/* MAP_DRAG_SCRIPT */', MAP_DRAG_SCRIPT)
                       .replace('<!--I18N-->', i18n.script()).encode('utf-8'))
    return _pages[src]


def _no_nan(s):
    raise ValueError('NaN / Infinity は受け付けない')  # 1つでも入ると操作パネルの JSON が壊れるため


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, cache='no-store', headers=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _via_lan(self):
        """スマホ用の受け口（LAN）から来たか（このPCの中＝127.0.0.1 の受け口なら False）"""
        return self.server in _lan.values()

    def _lan_auth(self):
        """合言葉（アドレスの k= か、クッキー）が合っているか"""
        if not _lan_key:
            return False
        q = (parse_qs(urlparse(self.path).query).get('k') or [''])[0]
        try:
            c = SimpleCookie(self.headers.get('Cookie') or '')
            ck = c['vptk'].value if 'vptk' in c else ''
        except Exception:
            ck = ''
        key = _lan_key.encode('utf-8')
        return any(hmac.compare_digest(x.encode('utf-8'), key) for x in (q, ck) if x)

    def do_GET(self):
        if self._via_lan():
            p = urlparse(self.path).path
            if p not in LAN_GET or not self._lan_auth():
                return self._send(403, LAN_DENY, 'text/html; charset=utf-8')
            if p in ('/m', '/m/'):  # 合言葉をクッキーに覚えてもらう（ページ内の通信で自動で送られる）
                import mobile
                return self._send(200, page(mobile.PAGE), 'text/html; charset=utf-8',
                                  headers={'Set-Cookie': f'vptk={_lan_key}; Path=/; HttpOnly; SameSite=Strict'})
        if urlparse(self.path).path in ('/', '/index.html'):  # /?view=media など（別ウィンドウの欄）も同じページ
            self._send(200, page(PAGE), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/state'):
            self._send(200, _app.get_state(), 'application/json; charset=utf-8')
        elif self.path.startswith('/api/qr.svg'):  # スマホで開くアドレスの QR コード（このPCの画面に出す）
            urls = _app.lan_urls if _app else []
            q = parse_qs(urlparse(self.path).query)
            k = int((q.get('i') or ['0'])[0] or 0) if (q.get('i') or ['0'])[0].isdigit() else 0
            svg = qr_svg(urls[k]) if 0 <= k < len(urls) else None
            if svg is None:
                return self._send(404, b'no qr', 'text/plain')
            self._send(200, svg, 'image/svg+xml')
        elif self.path.startswith('/api/mstate'):  # スマホ用の軽い状態
            self._send(200, _app.get_state(mobile=True), 'application/json; charset=utf-8')
        elif urlparse(self.path).path in ('/m', '/m/'):  # スマホ用（セッティングモード）
            import mobile
            self._send(200, page(mobile.PAGE), 'text/html; charset=utf-8')
        elif self.path.startswith('/thumb?'):
            self._thumb()
        else:
            self._send(404, b'not found', 'text/plain')

    def _thumb(self):
        q = parse_qs(urlparse(self.path).query)
        rel = (q.get('f') or [''])[0]
        path = _thumb_path(rel)
        data = thumbs.get(path, os.path.join(_app.base, '.thumbs')) if path else None
        if data is None:
            return self._send(404, b'no thumbnail', 'text/plain')
        self._send(200, data, 'image/jpeg', 'max-age=30')

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def do_POST(self):
        # ほかのサイトのページから勝手に操作されないように（ブラウザは別サイトからの送信に Origin を付ける）
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc.lower() != (self.headers.get('Host') or '').lower():
            return self._send(403, b'{"ok":false}', 'application/json')
        p = urlparse(self.path).path
        lan = self._via_lan()
        if lan and (p != '/api/cmd' or not self._lan_auth()):
            return self._send(403, b'{"ok":false}', 'application/json')
        if p == '/api/reference-drop':
            token = self.headers.get('X-VPT39-Desktop') or ''
            if not hmac.compare_digest(token.encode('utf-8'), _app.desktop_token.encode('ascii')):
                return self._json(403, {'ok': False})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 2_000_000:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                target, paths = data['target'], data['paths']
                if not isinstance(target, dict) or not isinstance(paths, list):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                return self._json(400, {'ok': False})
            _app.cmd_q.put({'cmd': '_desktop_reference_files', 'target': target, 'paths': paths})
            return self._json(200, {'ok': True})
        if p == '/api/upload':
            return self._upload()
        if p == '/api/media_delete':
            return self._media_delete()
        if p != '/api/cmd':
            return self._send(404, b'not found', 'text/plain')
        try:
            n = int(self.headers.get('Content-Length', 0) or 0)
            if n > 2_000_000:
                raise ValueError('too large')
            data = json.loads(self.rfile.read(n).decode('utf-8'), parse_constant=_no_nan)
        except Exception:
            return self._send(400, b'{"ok":false}', 'application/json')
        commands = [c for c in (data if isinstance(data, list) else [data])
                    if isinstance(c, dict) and c.get('cmd') != '_desktop_reference_files' and (not lan or lan_allowed(c))]
        # 終了でサーバーが止まる前にブラウザへ応答する。
        quitting = any(c.get('cmd') == 'quit' for c in commands)
        if quitting:
            self._send(200, b'{"ok":true}', 'application/json')
        for c in commands:
            _app.cmd_q.put(c)
        if not quitting:
            self._send(200, b'{"ok":true}', 'application/json')


    # ---- 素材フォルダへの取り込み・削除 ----
    def _upload(self):
        """ブラウザにドロップしたファイルを media フォルダへ保存（大きな動画でも少しずつ書き込む）"""
        q = parse_qs(urlparse(self.path).query)
        name = os.path.basename((q.get('name') or [''])[0].replace('\\', '/')).strip()
        sub = (q.get('dir') or [''])[0].strip().strip('/\\')
        try:
            n = int(self.headers.get('Content-Length', 0) or 0)
        except ValueError:
            n = -1
        from sources import is_video, is_image
        if not name or name.startswith('.') or not (is_video(name) or is_image(name)):
            return self._json(400, {'ok': False, 'msg': f'動画・画像ではないので取り込みません: {name}'})
        if n < 0:
            return self._json(400, {'ok': False, 'msg': '大きさが分かりません'})
        root = os.path.realpath(_app.resolve(_app.proj['media_dir']))
        folder = os.path.realpath(os.path.join(root, sub)) if sub and sub != '.' else root
        if not _inside(folder, root):
            return self._json(400, {'ok': False, 'msg': '保存先が media フォルダの外です'})
        stem, ext = os.path.splitext(name)
        tmp = None
        try:
            os.makedirs(folder, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=folder, suffix='.uploading', delete=False) as f:
                tmp = f.name
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise IOError('途中で切れました')
                    f.write(chunk)
                    left -= len(chunk)
            with _upload_lock:
                dst, k = os.path.join(folder, name), 2
                while True:
                    if not os.path.lexists(dst):
                        try:
                            os.rename(tmp, dst)
                            break
                        except FileExistsError:  # 別のプロセスが同名を作った
                            pass
                    dst = os.path.join(folder, f'{stem} ({k}){ext}')
                    k += 1
        except Exception as ex:
            if tmp is not None:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return self._json(500, {'ok': False, 'msg': f'保存できませんでした: {name}（{ex}）'})
        _app.last_media_scan = 0.0  # すぐ一覧に出す
        return self._json(200, {'ok': True, 'file': os.path.relpath(dst, root).replace('\\', '/')})

    def _media_delete(self):
        """media フォルダの素材をごみ箱へ（元に戻せる）"""
        try:
            n = int(self.headers.get('Content-Length', 0) or 0)
            rel = json.loads(self.rfile.read(min(n, 100000)).decode('utf-8')).get('file', '')
        except Exception:
            return self._json(400, {'ok': False, 'msg': '要求が読めません'})
        from sources import is_video, is_image
        root = os.path.realpath(_app.resolve(_app.proj['media_dir']))
        path = os.path.realpath(os.path.join(root, rel))
        if not rel or not _inside(path, root) or path == root or not (is_video(path) or is_image(path)):
            return self._json(400, {'ok': False, 'msg': 'media フォルダの素材ではありません'})
        if _inside(path, os.path.realpath(os.path.join(root, 'テスト用'))):
            return self._json(403, {'ok': False, 'msg': 'テスト用の素材は削除できません'})
        if not os.path.isfile(path):
            return self._json(404, {'ok': False, 'msg': 'ファイルが見つかりません'})
        if not _to_recycle_bin(path) or os.path.exists(path):
            return self._json(500, {'ok': False, 'msg': f'ごみ箱へ移せませんでした: {rel}（再生中・他のアプリで開いているなど）'})
        _app.last_media_scan = 0.0
        return self._json(200, {'ok': True})


def _inside(path, root):
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(root)]) == os.path.normcase(root)
    except ValueError:  # ドライブが違う
        return False


def _to_recycle_bin(path):
    """Windows のごみ箱へ移す（エクスプローラーで消したときと同じ。ごみ箱から元に戻せる）"""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [('hwnd', wintypes.HWND), ('wFunc', ctypes.c_uint), ('pFrom', wintypes.LPCWSTR),
                    ('pTo', wintypes.LPCWSTR), ('fFlags', ctypes.c_ushort), ('fAnyOperationsAborted', wintypes.BOOL),
                    ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]
    op = SHFILEOPSTRUCTW()
    op.wFunc = 3  # FO_DELETE
    op.pFrom = path + '\0'  # 最後は \0 が2つ必要（もう1つは ctypes が付ける）
    op.fFlags = 0x0040 | 0x0010 | 0x0004 | 0x0400  # ごみ箱へ・確認なし・進行表示なし・エラー表示なし
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0 and not op.fAnyOperationsAborted


_port = None
_lan = {}   # スマホ用の受け口 {アドレス: サーバー}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # Windows では True だと2つ目の VPT39 が同じ番号を取れてしまう（作る前に決める必要がある）


def running():
    return _port is not None


def start(app, host, port):
    """Webサーバーを起動（2回目以降はアプリを差し替えるだけ）。このPCの中だけ（127.0.0.1）で受ける"""
    global _app, _port
    _app = app
    if _port is not None:
        return _port
    for p in range(port, port + 20):  # 使用中なら次の番号を試す
        try:
            srv = Server((host, p), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError('操作パネル用のポートが確保できません')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _port = p
    return p


def lan_start(ips):
    """スマホなど同じ Wi-Fi の機器から開けるようにする（このPCの LAN のアドレスごとに受け口を開く）。
    開けたアドレスの一覧を返す。初めてのときは Windows のファイアウォールの確認が出る"""
    ok = []
    for ip in ips:
        if ip in _lan:
            ok.append(ip)
            continue
        try:
            srv = Server((ip, _port), Handler)
        except OSError as ex:
            print('スマホ用の受け口を開けません:', ip, ex)
            continue
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _lan[ip] = srv
        ok.append(ip)
    for ip in list(_lan):  # もう使っていないアドレス（Wi-Fi を変えたなど）は閉じる
        if ip not in ips:
            lan_stop(ip)
    return ok


def lan_stop(ip=None):
    """スマホ用の受け口を閉じる（ip を省くと全部）"""
    for k in ([ip] if ip else list(_lan)):
        srv = _lan.pop(k, None)
        if srv:
            threading.Thread(target=lambda s=srv: (s.shutdown(), s.server_close()), daemon=True).start()


def qr_svg(text):
    """text の QR コード（SVG）。部品（qrcode）がなければ None"""
    try:
        import qrcode
        import qrcode.image.svg
    except Exception:
        return None
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2, box_size=10)
    return img.to_string()


PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VPT39 操作パネル</title>
<!--I18N-->
<style>
/* SPECTRUM_THEME */
:root{--tw:144px;--lw:222px;--gap:8px;--sidebar:424px}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 var(--font);
display:flex;flex-direction:column;overflow:hidden}
header{flex:none;display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--line)}
h1{margin:0;font-size:21px;font-weight:800;line-height:1.25;letter-spacing:-.02em}
.master-control{display:flex;align-items:center;gap:10px;margin-inline-start:8px;padding-inline:14px;border-inline-start:1px solid var(--line)}
button{min-height:32px;background:var(--panel2);color:var(--text);border:1px solid transparent;border-radius:8px;
padding:5px 10px;font:600 13px/1.35 var(--font);cursor:pointer}
button:hover:not(:disabled){background:var(--hover)}
button:active:not(:disabled){background:var(--line)}
button.on{background:var(--line);border-color:var(--edge)}
button.on:hover:not(:disabled){background:#444444}
button.lock.on,button.held,.lbtns .mut.on,.mbtn.on,#panel-settings.on{background:var(--negative-subtle);border-color:var(--red);color:var(--red)}
:is(button,a,input,select,summary):focus-visible{outline:2px solid var(--focus);outline-offset:2px}
button:disabled{opacity:.45;cursor:default}
button.sm{min-height:26px;padding:2px 8px;font-size:12px}
button.primary{background:var(--accent);color:#fff;border-radius:999px;padding-inline:14px}
button.primary:not(.on):hover:not(:disabled){background:var(--accent-hover)}
#quit,#clearall{background:transparent;color:var(--red)}
#quit:hover,#clearall:hover{background:var(--negative-subtle)}
input,select{min-width:0;background:var(--panel2);color:var(--text);border:1px solid var(--edge);border-radius:7px;
padding:5px 8px;font:inherit}
input::placeholder{color:var(--dim)}
input[type=range]{padding:0;accent-color:var(--focus);width:120px}
input[type=checkbox]{accent-color:var(--accent)}
input[type=number]{width:62px}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap;border:0}
.grow{flex:1}
.meta{color:var(--dim);font-size:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.lbl{color:var(--dim);font-size:12px;min-width:52px}
.hint{color:var(--dim);font-size:12px}
.msg{color:var(--acc);font-size:12px}
.err{color:var(--red);font-size:12px}
.tag{font-size:11px;padding:2px 7px;border-radius:4px;background:var(--panel2);color:var(--dim);white-space:nowrap}
.tag.on{background:var(--acc);color:#04120a}

/* ---- 全体：左＝レイヤー（自分だけスクロール）、右＝素材（固定）＋設定（自分だけスクロール）---- */
main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) var(--sidebar)}
.workspace{display:flex;flex-direction:column;min-width:0;min-height:0}
.deck{flex:1;overflow:auto;min-width:0;position:relative;padding-bottom:24px;background:var(--surface)}
.deck-bar{display:flex;align-items:center;padding:0 10px;border-top:1px solid var(--line);background:var(--panel)}
#show-decks{display:flex;gap:6px;overflow-x:auto;min-width:0;padding:0 2px}
#show-decks button{flex:none;min-height:40px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:none;touch-action:none}
aside{display:flex;flex-direction:column;min-width:0;min-height:0;border-left:1px solid var(--line);background:var(--panel)}
@media(max-width:980px){
  body:not(.child){overflow:auto;display:block}
  main{grid-template-columns:minmax(0,1fr)}
  .deck{max-height:66vh}
  body:not(.child) aside{border-left:0;border-top:1px solid var(--line)}
}
@media(max-width:600px){
  :root{--lw:clamp(132px,38vw,204px);--tw:128px}
  header{padding-inline:10px}
  .master-control{margin:0;padding-inline:8px}
}
.card{padding:2px;display:flex;flex-direction:column;gap:12px}

/* ---- レイヤーの行（VJアプリ風：左に名前と基本操作、右にクリップ）---- */
.grid{display:inline-flex;flex-direction:column;min-width:100%}
.colhead{position:sticky;top:0;z-index:6;display:flex;align-items:center;background:var(--panel);border-bottom:1px solid var(--line)}
.colhead .lh{position:sticky;left:0;z-index:7;width:var(--lw);flex:none;background:var(--panel);min-height:24px;border-right:1px solid var(--line);align-self:stretch;cursor:context-menu}
.colhead .cols{display:flex;gap:var(--gap);padding:6px var(--gap)}
.colhead [data-col]{width:var(--tw);flex:none;min-height:28px;padding:4px 8px;font-size:12px;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
[data-layer-drag],.colhead [data-col]{cursor:grab;user-select:none;touch-action:none}
.grid-moving{background:#3b3020;outline:2px solid var(--warn);outline-offset:-2px;border-radius:5px;cursor:grabbing}
.colhead .grid-target,#show-decks .grid-target{outline:2px solid var(--warn);outline-offset:-2px}
.colhead .grid-target{background:#3b3020}
.lrow.grid-target{outline:2px solid var(--warn);outline-offset:-2px}
.lrow.grid-target>.lhead{outline:2px solid var(--warn);outline-offset:-2px;background:#3b3020}
.colhead .grid-before,#show-decks .grid-before{box-shadow:inset 4px 0 var(--warn)}
.colhead .grid-after,#show-decks .grid-after{box-shadow:inset -4px 0 var(--warn)}
.lrow.grid-before>div{box-shadow:inset 0 4px var(--warn)}
.lrow.grid-after>div{box-shadow:inset 0 -4px var(--warn)}
.lrow{display:flex;border-bottom:1px solid var(--line)}
.lhead{position:sticky;left:0;z-index:3;width:var(--lw);flex:none;background:var(--panel);padding:9px 11px;display:flex;flex-direction:column;gap:5px;border-right:1px solid var(--line);cursor:default}
.lrow.sel .lhead{background:var(--accent-subtle);box-shadow:inset 3px 0 var(--focus)}
.lrow.hidden .lhead .lname,.lrow.hidden .track{opacity:.5}
.lname{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lhead .now{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{height:3px;background:var(--line);border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc);width:0}
.lbtns{display:flex;gap:3px;flex-wrap:wrap}
.lbtns button{min-height:24px;padding:2px 7px;font-size:12px;border-radius:6px}
.lop{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim)}
.lop input{flex:1;width:auto;min-width:0}
.track{display:flex;gap:var(--gap);padding:8px var(--gap);align-items:flex-start;min-height:100%}

/* ---- クリップのタイル ---- */
.clip{width:var(--tw);flex:none;background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden;display:flex;flex-direction:column;position:relative}
.clip.on{border-color:var(--acc);outline:1px solid var(--acc)}
.clip.on.paused{border-color:var(--warn);outline-color:var(--warn)}
.clip.ins::before,.clip.fdrop::before{content:'';position:absolute;inset:0;pointer-events:none;z-index:2}
.clip.ins:not(.empty)::before{border-left:5px solid var(--warn)}
.clip.ins.empty::before,.clip.fdrop::before{border:3px solid var(--warn);border-radius:7px;background:#ffbe381a}
.clip .th,.mtile .th{position:relative;display:block;width:100%;aspect-ratio:16/9;background:#000;overflow:hidden;cursor:pointer}
.th img{width:100%;height:100%;object-fit:cover;display:block}
.missing-thumbnail{display:none;position:absolute;inset:0;align-content:center;justify-items:center;gap:4px;
  padding:5px;text-align:center;color:#ffe2df;background:#39252b;font-size:11px;font-weight:600;line-height:1.2}
.missing-thumbnail-icon{display:grid;place-items:center;width:19px;height:24px;border:2px solid #ffaaa6;
  border-radius:3px;color:#ffaaa6;font-size:15px;line-height:1}
.missing-reference .media-thumbnail{display:none}
.missing-reference .missing-thumbnail{display:grid}
.clip .num{position:absolute;left:4px;top:3px;font-size:11px;font-weight:700;background:#000b;padding:0 5px;border-radius:4px}
.clip.on .num{background:var(--acc);color:#04120a}
.clip.on.paused .num{background:var(--warn);color:#231c12}
.clip .md{position:absolute;right:4px;top:4px;min-height:19px;font-size:12px;background:#000b;color:#fff;border:0;padding:0 5px;border-radius:5px;line-height:1.5}
.clip .md:hover{background:var(--line)}
.clip .cn{display:flex;align-items:center;gap:2px;font-size:11px;padding:3px 3px 4px 6px}
.clip .cn span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clip:not(.empty):not(.on):hover{border-color:var(--edge)}
.clip.empty{border-style:dashed;border-color:var(--edge);background:transparent}
.clip.empty .th{display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--dim);font-size:20px;background:transparent;cursor:default;aspect-ratio:auto;height:118px}
.clip.empty small{font-size:11px;text-align:center;margin-top:4px;line-height:1.35}
.tp{display:flex;gap:2px;align-items:center;padding:3px 4px 0}
.tb{min-height:22px;min-width:23px;padding:0 4px;font-size:11px;line-height:1.5;border-radius:5px}
.sc{height:8px;margin:4px 5px 0;background:var(--line);border-radius:4px;position:relative;cursor:ew-resize;touch-action:none;overflow:hidden}
.sc i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);width:0}
.sc i::after{content:'';position:absolute;right:-1px;top:0;bottom:0;width:3px;background:#fff}
.sc:hover{outline:1px solid var(--edge)}
.clip.paused .sc i{background:var(--warn)}
.tt{font-size:10px;color:var(--dim);white-space:nowrap;margin-left:auto}
.addrow{display:flex;gap:8px;padding:12px 10px}

/* ---- 右：タブ（素材・レイヤー設定・ミキサー・出力）---- */
.panel-bar{flex:none;display:flex;align-items:center;justify-content:space-between;gap:4px;padding:0 9px;border-bottom:1px solid var(--line)}
.panel-tabs{display:flex;align-items:stretch;min-width:0;overflow-x:auto;scrollbar-width:thin}
.panel-tabs button,#show-decks button{background:transparent;color:var(--dim);border:0;border-radius:0;box-shadow:inset 0 -2px transparent}
.panel-tabs button{flex:none;min-height:44px;padding:0 8px;white-space:nowrap}
.panel-tabs button:hover,#show-decks button:hover{background:var(--hover);color:var(--text)}
.panel-tabs [aria-selected=true],#show-decks button.on{color:var(--text);box-shadow:inset 0 -2px var(--text)}
.panel-tabs button:focus-visible,#show-decks button:focus-visible{outline-offset:-3px}
.panel-actions{display:flex;align-items:center;gap:2px}
.panel-link{color:var(--focus);font-size:13px;white-space:nowrap}
.panel-link:hover{color:var(--text)}
.panel-actions :is(button,a){display:grid;place-items:center;min-height:28px;min-width:28px;padding:0;
border:0;border-radius:6px;background:transparent;color:var(--dim);font-size:17px;text-decoration:none}
.panel-actions :is(button,a):hover{background:var(--hover);color:var(--text)}
body.panel-closed main{grid-template-columns:minmax(0,1fr)}
body.panel-closed aside{display:none}
#panel-settings{white-space:nowrap}
.tabbody{flex:1;min-height:0;overflow:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
.pane{display:none}
.pane.act{display:flex}
.media.pane.act{flex:1;min-height:0}
.media{flex-direction:column;gap:8px}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,var(--mt,104px)),1fr));gap:8px;flex:1;min-height:60px;overflow:auto;align-content:start}
.folder-address{display:flex;align-items:center;gap:6px}
.folder-path{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.folder-row{grid-column:1/-1;display:flex;align-items:center;gap:8px;text-align:left;min-width:0}
.folder-row span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.seg{display:flex;gap:2px;padding:2px;background:var(--surface);border-radius:999px}
.seg button{min-height:26px;border:0;border-radius:999px;padding:3px 10px;background:transparent;color:var(--dim);font-size:12px}
.seg button.on{background:var(--line);color:var(--text)}
.mh{grid-column:1/-1;font-size:12px;color:var(--dim);margin-top:6px;display:flex;gap:8px;align-items:center}
.sp{white-space:pre-line;display:flex;align-items:center;justify-content:center;text-align:center;font-size:12px;font-weight:600;padding:4px;line-height:1.3;color:#fff;height:100%;word-break:break-all}
.sp.blank{background:repeating-linear-gradient(45deg,var(--surface) 0 8px,var(--panel) 8px 16px);color:var(--dim)}
.sp.layer{background:linear-gradient(135deg,#123a2a,#0d241b)}
.sp.camera{background:linear-gradient(135deg,#3a2a12,#241a0d)}
.sp.window{background:linear-gradient(135deg,#12283a,#0d1a24)}
.sp.spout{background:linear-gradient(135deg,#2a1838,#170d22)}
.sp.ndi{background:linear-gradient(135deg,#17294b,#102b29)}
.clip select{width:calc(100% - 8px);margin:3px 4px 0;font-size:11px;padding:1px 2px}
.clip.empty .th{gap:6px}

/* ---- 欄を別ウィンドウに切り離す ---- */
.hide{display:none!important}
#context-menu{position:fixed;z-index:40;display:grid;min-width:184px;max-width:calc(100vw - 12px);max-height:calc(100vh - 12px);
overflow:auto;padding:5px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;box-shadow:0 8px 24px #000b}
#context-menu button{border:0;background:transparent;padding:6px 10px;text-align:left;font-weight:400}
#context-menu button:hover,#context-menu button:focus-visible{background:var(--line)}
#context-menu button:focus-visible{outline-offset:-2px}
#context-menu .danger{color:var(--red)}
/* セッティングの窓（画面の上に重ねて出す）*/
.dlg{position:fixed;inset:0;z-index:50;background:#000a;display:flex;align-items:flex-start;justify-content:center;padding:60px 16px 16px}
.dlgbox{width:min(640px,100%);max-height:calc(100vh - 80px);overflow:auto;background:var(--panel2);border:1px solid var(--line);border-radius:16px;padding:20px;display:flex;flex-direction:column;gap:12px;box-shadow:0 12px 40px #000a}
#clipdlg,#photodlg,#resetdlg{padding:0;border:0;border-radius:16px;background:transparent;color:var(--text)}
#clipdlg::backdrop,#photodlg::backdrop,#resetdlg::backdrop{background:#000a}
#clipdlg .dlgbox,#photodlg .dlgbox{width:min(420px,calc(100vw - 32px))}
#resetdlg .dlgbox{width:min(480px,calc(100vw - 32px))}
#resetdlg label{display:flex;align-items:center;gap:8px}
#resetdlg .hint{padding-left:24px}
#clipdlg label,#photodlg label{display:flex;align-items:center;justify-content:space-between;gap:12px}
#clipdlg input,#photodlg input[type=number]{width:84px}
#clipdlg input[type=range]{width:min(160px,35vw)}
#clipdlg input[type=checkbox]{width:auto}
#clipdlg output{min-width:4.5ch;text-align:right;font-variant-numeric:tabular-nums}
#clipdlg fieldset{display:grid;gap:8px;margin:0;padding:8px 10px 10px;border:1px solid var(--line);border-radius:6px}
.setrow{display:flex;gap:6px;align-items:center;padding:6px 8px;border:1px solid var(--line);border-radius:8px;background:#0b0d10}
.setrow.cur{background:var(--accent-subtle);box-shadow:inset 3px 0 var(--focus)}
.setrow .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
.setrow .tm{color:var(--dim);font-size:12px;white-space:nowrap}
.childbar{display:none}
body.child header,body.child .workspace,body.child .panel-bar{display:none}
body.child main{grid-template-columns:minmax(0,1fr)}
body.child aside{border-left:0}
body.child .childbar{display:flex;flex:none;gap:10px;align-items:center;padding:10px 14px;background:var(--surface);border-bottom:1px solid var(--line)}
@media(max-width:640px){.childbar .meta{display:none}}
.chk{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px}
.chk label{display:flex;gap:4px;align-items:center;white-space:nowrap}
.mtabs{display:flex;gap:4px;flex-wrap:wrap}
.mtabs button{font-size:12px;padding:2px 8px}
.wide{font-size:12px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding-left:4px}
.wide input[type=number]{width:64px}
.mtile{background:var(--panel2);border:1px solid var(--line);border-radius:8px;overflow:hidden;cursor:grab;padding-bottom:3px}
.mtile:hover{border-color:var(--edge)}
.mtile{position:relative}
.mtile .cn{font-size:11px;padding:3px 5px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ---- 右：レイヤー設定・マッピング ---- */
.insp #mapsel{color:var(--text);font-size:14px;font-weight:600}
.set{display:grid;grid-template-columns:72px 1fr;gap:8px 10px;align-items:center}
.set .lbl{min-width:0}
.set input[type=range]{width:100%}
#map{display:block;flex:none;width:100%;height:auto;background:#000;border:1px solid var(--line);border-radius:8px;touch-action:none;cursor:crosshair}
#map.map-moving{cursor:grabbing}
body.child .insp{width:min(100%,640px);align-self:center}
body.child .insp #map{align-self:center;width:min(100%,calc(48dvh * var(--map-ratio,1.7778)))}
details summary{cursor:pointer;color:var(--dim);font-size:12px}

/* ---- ミキサー・出力 ---- */
.orow{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:4px 0;border-top:1px solid var(--line)}
.orow .meta{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 0}
.mx{display:flex;flex-direction:column;gap:4px}
.mxr{display:grid;grid-template-columns:minmax(0,1fr) 34px minmax(54px,110px) 38px;gap:6px;align-items:center}
.mxr.o{margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.mxr.l .mn{padding-left:14px}
.mxr .mn{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mxr.o .mn,.mxr.m .mn{font-weight:600;font-size:13px}
.mxr input[type=range]{width:100%;min-width:0}
.mxr .pct{font-size:11px;color:var(--dim);text-align:right}
.mbtn{padding:1px 0;font-size:11px;font-weight:700}
.meter{grid-column:1/-1;height:5px;background:var(--line);border-radius:2px;overflow:hidden;margin:0 0 2px 14px}
.meter i{display:block;height:100%;width:0;background:var(--acc);transition:width .15s}
@media(prefers-reduced-motion:reduce){.meter i{transition:none}}
.scr{position:relative;width:100%;background:var(--surface);border:1px solid var(--line);border-radius:8px}
.scr div{position:absolute;border:2px solid var(--edge);background:var(--panel);border-radius:4px;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;text-align:center;line-height:1.25}
.scr div.has{border-color:var(--acc)}
.scr div.pri{background:var(--panel2)}
.scr b{font-size:22px}
.scr small{font-size:10px;color:var(--dim)}
.scr em{font-size:10px;color:var(--acc);font-style:normal}
.ndi-settings{margin:8px 0;padding:6px 10px;border:1px solid var(--line);border-radius:8px}
.ndi-settings summary{cursor:pointer}
.ndi-output{display:grid;gap:8px;margin-top:8px}
.ndi-output label:has(input[type=text]){display:grid;gap:4px;flex:1 1 145px;min-width:0}
.ndi-output input[type=text]{width:100%}
.ndi-source{overflow-wrap:anywhere}
</style></head><body>
<header>
  <h1>VPT39</h1>
  <div class="master-control">
    <label class="lbl" for="master">全レイヤー明るさ</label><input type="range" id="master" min="0" max="1" step="0.01">
  </div>
  <button id="clearall">全レイヤー再生消去</button>
  <span class="grow"></span>
  <span class="msg" id="msg"></span>
  <button id="lanbtn" title="スマホから四隅を合わせる（オン・オフと、スマホで読み取る QR コード）">スマホ</button>
  <button type="button" class="primary" id="panel-settings" aria-haspopup="menu" aria-controls="context-menu" aria-expanded="false">設定 ▾</button>
  <button id="quit">終了</button>
</header>
<div id="setdlg" class="dlg hide"><div class="dlgbox">
  <div class="row"><b style="font-size:15px">セッティング</b><span class="meta grow">会場・公演ごとの設定ファイル（曲順・クリップ・レイヤー・マッピング・音量・出力の設定）</span><button class="sm" id="setclose">閉じる</button></div>
  <div class="meta" id="setcur"></div>
  <div class="row"><button id="setbrowse">ファイルから開く…</button></div>
  <div id="setlist"></div>
  <div class="hint">settings フォルダのセッティングが一覧に表示されます。開いているセッティングは変更すると自動保存されます。</div>
</div></div>
<div id="landlg" class="dlg hide"><div class="dlgbox" style="width:min(520px,100%)">
  <div class="row"><b style="font-size:15px">スマホから操作</b><span class="meta grow">スクリーンの前で四隅を合わせる（セッティング）</span><button class="sm" id="lanclose">閉じる</button></div>
  <div class="row"><button id="lantoggle" style="min-width:150px"></button><span class="meta grow" id="lanstate"></span></div>
  <div id="lanqr" style="display:flex;flex-direction:column;align-items:center;gap:8px"></div>
  <div class="hint" id="lanhint"></div>
</div></div>
<div class="childbar" id="childbar"></div>
<div id="context-menu" class="hide" role="menu" aria-label="操作メニュー"></div>
<dialog id="resetdlg" aria-labelledby="resettitle">
  <form class="dlgbox" id="resetform">
    <b id="resettitle">設定をリセット</b>
    <div class="meta">リセットする項目を選んでください。</div>
    <label><input name="items" type="checkbox" value="assets"> 素材ファイルを削除</label>
    <div class="hint">media・photos フォルダ内のファイルをごみ箱に移します。同梱のテスト素材と案内ファイルは残ります。</div>
    <label><input name="items" type="checkbox" value="settings"> 設定を削除</label>
    <div class="hint">出力・マッピング・音量などを初期化し、保存済みセッティングをごみ箱に移します。</div>
    <label><input name="items" type="checkbox" value="layers"> コラム・レイヤー・デッキを初期化</label>
    <div class="hint">空の動画レイヤー・コラム・デッキを1つずつ表示します。</div>
    <div class="row"><span class="grow"></span><button type="button" id="resetcancel">キャンセル</button>
      <button type="submit" class="primary" id="resetapply" disabled>選んだ項目をリセット</button></div>
  </form>
</dialog>
<dialog id="clipdlg" aria-labelledby="clipdlgtitle">
  <form class="dlgbox" id="clipform" novalidate>
    <b id="clipdlgtitle">クリップ設定</b>
    <div class="meta" id="clipdlgname"></div>
    <fieldset><legend>映像</legend>
      <label>フェードイン (秒)<input name="fade_in" type="number" min="0" max="30" step="any" required></label>
      <label>フェードアウト (秒)<input name="fade_out" type="number" min="0" max="30" step="any" required></label>
    </fieldset>
    <fieldset><legend>音声</legend>
      <label><span>音量</span><input name="volume" type="range" min="0" max="100" step="1"><output id="clip-volume"></output></label>
      <label>フェードイン (秒)<input name="audio_fade_in" type="number" min="0" max="30" step="any" required></label>
      <label>フェードアウト (秒)<input name="audio_fade_out" type="number" min="0" max="30" step="any" required></label>
    </fieldset>
    <div class="row"><span class="meta grow" id="clip-status" role="status"></span><button class="primary" type="submit">閉じる</button></div>
  </form>
</dialog>
<dialog id="photodlg" aria-labelledby="photodlgtitle">
  <form class="dlgbox" id="photoform">
    <b id="photodlgtitle">写真の再生設定</b>
    <div class="meta" id="photodlgname"></div>
    <label>写真1枚の表示時間（秒）<input name="interval" type="number" min="0" max="600" step="any" required></label>
    <div class="meta">0＝定期切り替えなし</div>
    <label>ループ再生<input name="loop" type="checkbox"></label>
    <div class="row"><span class="meta grow" id="photo-status" role="status"></span><button id="photo-cancel" type="button">キャンセル</button><button class="primary" id="photo-apply" type="submit">適用</button></div>
  </form>
</dialog>
<main>
  <div class="workspace">
  <section class="deck" id="deck">
    <div class="grid">
      <div class="colhead"><div class="lh" id="column-head" title="右クリックでコラムの操作"></div><div class="cols" id="cols"></div></div>
      <div id="layers"></div>
      <div class="addrow" id="layer-footer" title="右クリックでレイヤーを追加"></div>
    </div>
  </section>
    <div class="deck-bar">
      <div id="show-decks"></div>
    </div>
  </div>
  <aside>
    <div class="panel-bar">
      <span class="sr-only" id="panel-tabs-label">パネルの切り替え</span>
      <div class="panel-tabs" id="panel-tabs" role="tablist" aria-labelledby="panel-tabs-label">
        <button type="button" id="tab-media" role="tab" data-panel="media" aria-controls="media-pane">素材</button>
        <button type="button" id="tab-mixer" role="tab" data-panel="mixer" aria-controls="mixer">ミキサー</button>
        <button type="button" id="tab-insp" role="tab" data-panel="insp" aria-controls="insp-pane">レイヤー設定</button>
        <button type="button" id="tab-outs" role="tab" data-panel="outs" aria-controls="outs">出力設定</button>
      </div>
      <div class="panel-actions">
        <a class="panel-link" id="panel-link" href="/?view=media" target="_blank" rel="opener" title="別タブで開く ↗"><span aria-hidden="true">↗</span><span class="sr-only">別タブで開く ↗</span></a>
        <button type="button" id="panel-close" title="右側のパネルを閉じる"><span aria-hidden="true">×</span><span class="sr-only">右側のパネルを閉じる</span></button>
      </div>
    </div>
    <div class="tabbody">
    <div class="media pane" id="media-pane" role="tabpanel" aria-labelledby="tab-media">
      <div class="row"><div class="seg"><button id="mkf" data-media-kind="f" class="on" aria-pressed="true">フォルダ参照</button><button id="mks" data-media-kind="s" aria-pressed="false">空白・レイヤー・入力</button><button id="mkt" data-media-kind="t" aria-pressed="false">テスト用</button></div>
        <span class="meta" id="mcount"></span><span class="grow"></span></div>
      <div class="meta" id="mtarget"></div>
      <div class="msg" id="mup"></div>
      <div class="folder-address" id="folder-address">
        <button type="button" id="folder-up" class="sm" title="上のフォルダへ">↑</button>
        <span id="folder-path" class="meta folder-path"></span>
        <button type="button" id="folder-choose" class="sm">フォルダを選ぶ…</button>
      </div>
      <div class="row mfiles" style="flex-wrap:nowrap">
        <input id="mfilter" class="grow" style="min-width:0" placeholder="ファイル・フォルダ名で絞り込み">
        <input type="range" id="msize" min="80" max="260" step="10" value="104" style="width:70px" title="サムネイルの大きさ">
      </div>
      <div class="mgrid" id="mediagrid"></div>
    </div>
      <div class="card insp pane" id="insp-pane" role="tabpanel" aria-labelledby="tab-insp">
        <div id="mapsel"></div>
        <div id="lset"></div>
        <div class="row"><b style="font-size:13px">マッピング</b><span class="grow"></span>
          <button class="sm lock" id="mapedit">編集</button><button class="sm lock" id="maptest">テスト表示</button><button class="sm" id="mapreset">四隅リセット</button></div>
        <div class="mtabs" id="mtabs"></div>
        <canvas id="map" width="800" height="450"></canvas>
        <div class="hint">角をドラッグして変形、内側をドラッグして全体を移動。</div>
      </div>
      <div class="card pane" id="mixer" role="tabpanel" aria-labelledby="tab-mixer"></div>
      <div class="card pane" id="outs" role="tabpanel" aria-labelledby="tab-outs"></div>
    </div>
  </aside>
</main>
<script>
let S=null, rev=-1, nlayers=-1, mediaKey=null, lsetKey='', gridDeck=null;
const $=id=>document.getElementById(id);
function lsGet(k){try{return localStorage.getItem(k);}catch(e){return null;}}
function lsSet(k,v){try{localStorage.setItem(k,v);}catch(e){}}
// パネルを右側に表示するか、専用 URL をブラウザの別タブで開く。
const PANELS={media:'素材',insp:'レイヤー設定',mixer:'ミキサー',outs:'出力設定'};
const panelName=p=>p==='ndi'?'outs':p;
const requestedView=panelName(new URLSearchParams(location.search).get('view'));
const VIEW=Object.hasOwn(PANELS,requestedView)?requestedView:null;
const panelTabs=[...$('panel-tabs').querySelectorAll('[role=tab]')];
let curTab=VIEW||panelName(lsGet('vpt39-tab'));
if(!Object.hasOwn(PANELS,curTab))curTab='media';
if(VIEW){
  document.body.classList.add('child');
  document.title='VPT39 '+PANELS[VIEW];
  $('childbar').innerHTML=`<b>VPT39</b><span>${PANELS[VIEW]}</span><span class="grow"></span>
    <a class="panel-link" id="panel-return" href="/">メイン画面へ</a>`;
  $('panel-return').addEventListener('click',e=>{
    if(window.vpt39DesktopDrop){
      e.preventDefault();
      window.chrome.webview.postMessage({kind:'vpt39-return-panel'});
      return;
    }
    const main=window.opener;
    try{
      if(!main||main.closed||main.location.origin!==location.origin||
         main.document.body.classList.contains('child')||typeof main.activatePanel!=='function')return;
      main.activatePanel(VIEW);
    }catch(e){return;}
    e.preventDefault();
    main.focus();
    window.close();
  });
}
function activatePanel(p){
  curTab=p;
  lsSet('vpt39-tab',p);
  document.body.classList.remove('panel-closed');
  showPanel();
}
function hidePanel(){
  document.body.classList.add('panel-closed');
  $('panel-settings').focus({preventScroll:true});
}
function showPanel(){
  panelTabs.forEach(tab=>{
    const active=tab.dataset.panel===curTab;
    $(tab.getAttribute('aria-controls')).classList.toggle('act',active);
    tab.setAttribute('aria-selected',active);
    tab.tabIndex=active?0:-1;
  });
  if(VIEW)return;
  $('panel-link').href='/?view='+curTab;
}
panelTabs.forEach((tab,i)=>{
  tab.onclick=()=>activatePanel(tab.dataset.panel);
  tab.onkeydown=e=>{
    const n=panelTabs.length;
    const target={ArrowLeft:(i+n-1)%n,ArrowRight:(i+1)%n,Home:0,End:n-1}[e.key];
    if(target===undefined)return;
    e.preventDefault();
    const next=panelTabs[target];
    activatePanel(next.dataset.panel);
    next.focus();
  };
});
showPanel();
if(window.vpt39DesktopDrop){
  $('panel-link').title='別ウィンドウで開く ↗';
  $('panel-link').querySelector('.sr-only').textContent='別ウィンドウで開く ↗';
}
$('panel-close').onclick=()=>{
  if(curTab==='insp')cmd({cmd:'edit',value:false});
  hidePanel();
};
$('panel-link').addEventListener('click',hidePanel);
$('panel-link').addEventListener('auxclick',e=>{if(e.button===1)hidePanel();});
if(VIEW==='insp'&&!window.vpt39DesktopDrop){
  window.addEventListener('pagehide',()=>{
    const body=JSON.stringify({cmd:'edit',value:false});
    if(!navigator.sendBeacon('/api/cmd',body)){
      fetch('/api/cmd',{method:'POST',body,keepalive:true}).catch(()=>{});
    }
  });
}
function post(c){return fetch('/api/cmd',{method:'POST',body:JSON.stringify({deck:gridDeck??S?.active_deck,...c})});}
function cmd(c){post(c).then(()=>setTimeout(poll,60));}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function fmt(t){t=Math.max(0,t|0);return (t/60|0)+':'+String(t%60).padStart(2,'0');}
function setv(L,key,value){cmd({cmd:'set',layer:L,key,value});}
const MODES=['loop','hold','stop','next'];
const MODE_NAME={loop:'ループ',hold:'最後で停止',stop:'終了で消える',next:'次へ進む'};
const MODE_ICON={loop:'⟳',hold:'⏸',stop:'■',next:'⏭'};
let modeRequests=Promise.resolve(), pendingModes=0;
function cycleCueMode(button,layer,cue){
  const value=MODES[(MODES.indexOf(button.dataset.mode)+1)%MODES.length];
  button.dataset.mode=value;
  button.textContent=MODE_ICON[value];
  button.title=`終了時の動き：${MODE_NAME[value]}（押すと切り替え）`;
  const command={cmd:'cue_mode',deck:gridDeck,lid:layer.id,cue,file:layer.cues[cue].file,value};
  pendingModes++;
  modeRequests=modeRequests.then(async()=>{
    const r=await post(command);
    if(!r.ok)throw new Error();
  }).catch(()=>{
    // 次の状態取得で、実際に適用された設定に戻す。
    rev=-1;
  }).finally(()=>{
    if(--pendingModes===0)setTimeout(poll,60);
  });
}
const BLEND_NAME={normal:'通常',add:'加算',screen:'スクリーン',multiply:'乗算'};
const TRANS_NAME={fade:'フェード（じわっと）',cut:'静止（効果なし・パッと切り替え）',slide_left:'スライド（右から左へ）',slide_right:'スライド（左から右へ）',
  slide_up:'スライド（下から上へ）',slide_down:'スライド（上から下へ）',wipe:'ワイプ（左から順に）',zoom:'ズーム（少し寄りから）'};
function base(f){return String(f).split(/[\\/]/).pop();}
function isImg(f){return /\.(png|jpe?g|bmp|gif|webp)$/i.test(f);}
function mediaThumbnail(file){
  return `<img class="media-thumbnail" src="/thumb?f=${encodeURIComponent(file)}" alt="" loading="lazy" draggable="false" onerror="this.remove()">
    <div class="missing-thumbnail"><span class="missing-thumbnail-icon" aria-hidden="true">!</span><span>ファイルがありません</span></div>`;
}
function editingIn(sel){const a=document.activeElement;return a&&a.closest&&a.closest(sel)&&(a.tagName==='SELECT'||a.tagName==='TEXTAREA'||(a.tagName==='INPUT'&&!/^(range|checkbox|button)$/.test(a.type)));}

// ---- クリップのミニ操作：再生位置バーのドラッグ ----
let scrub=null, lastSeek=0;
function setupScrub(){
  document.querySelectorAll('.sc').forEach(el=>{
    const L=+el.closest('.lrow').dataset.layer, j=+el.closest('.clip').dataset.j;
    const frac=e=>{const r=el.getBoundingClientRect();return Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));};
    const send=(f,force)=>{const t=performance.now(); if(force||t-lastSeek>70){lastSeek=t; cmd({cmd:'transport',layer:L,cue:j,op:'seek',value:f});}};
    el.addEventListener('pointerdown',e=>{e.preventDefault();e.stopPropagation(); el.setPointerCapture(e.pointerId);
      scrub={L,j,f:frac(e)}; $(`sb${L}_${j}`).style.width=scrub.f*100+'%'; send(scrub.f,true);});
    el.addEventListener('pointermove',e=>{ if(!scrub||scrub.L!==L||scrub.j!==j)return; scrub.f=frac(e); $(`sb${L}_${j}`).style.width=scrub.f*100+'%'; send(scrub.f);});
    el.addEventListener('pointerup',e=>{ if(!scrub)return; send(frac(e),true); setTimeout(()=>{scrub=null;},250);});
  });
}

// ---- ドラッグ＆ドロップ：素材→レイヤーの行（どこに落としてもよい）、クリップの並べ替え ----
let dragActive=false, autoScroll=null;
const OURS=t=>t.includes('text/x-sm-cue')||t.includes('text/x-sm-media');
function dropIndex(row,e){
  const tiles=[...row.querySelectorAll('.clip:not(.empty)')];
  for(const t of tiles){const r=t.getBoundingClientRect(); if(e.clientX<r.left+r.width/2) return +t.dataset.j;}
  return tiles.length;
}
function replacementTile(e,moveClip){
  if(!moveClip&&!hasFiles(e)&&!e.dataTransfer.types.includes('text/x-sm-file'))return null;
  const tile=e.target.closest(moveClip?'.clip':'.clip:not(.empty)');
  if(!tile)return null;
  const r=tile.getBoundingClientRect(), edge=r.width/5;
  return e.clientX>=r.left+edge&&e.clientX<=r.right-edge?tile:null;
}
function columnInsert(layer,at){
  return layer.kind==='video'&&at<layer.cues.length
    ? {column:true,anchor:layer.cues[at]} : {};
}
function clearIns(){document.querySelectorAll('.clip.ins,.clip.fdrop').forEach(c=>c.classList.remove('ins','fdrop'));}
function endDrag(){dragActive=false; clearIns(); stopAuto();}
function stopAuto(){if(autoScroll){clearInterval(autoScroll.t);autoScroll=null;}}
function setupDnD(){
  const deck=gridDeck;
  document.querySelectorAll('.clip:not(.empty) .th').forEach(el=>{
    el.addEventListener('dragstart',e=>{const L=+el.closest('.lrow').dataset.layer, j=+el.closest('.clip').dataset.j;
      const layer=S.layers[L], clip=layer.cues[j];
      e.dataTransfer.setData('text/x-sm-cue',JSON.stringify({deck,lid:layer.id,cue:j,clip}));
      if(layer.kind==='video'&&clip.file!=='@gap')e.dataTransfer.setData('text/x-sm-video-cue','1');
      e.dataTransfer.effectAllowed='copyMove'; dragActive=true;});
    el.addEventListener('dragend',endDrag);
  });
  document.querySelectorAll('.lrow').forEach(row=>{
    const layer=S.layers[+row.dataset.layer];
    row.addEventListener('dragover',e=>{
      const files=hasFiles(e);
      if(files&&!e.target.closest('.track')){clearIns();return;}
      if(!files&&!OURS(e.dataTransfer.types))return;
      e.preventDefault(); clearIns();
      if(files)e.dataTransfer.dropEffect='copy';
      const moveClip=layer.kind==='video'&&e.dataTransfer.types.includes('text/x-sm-video-cue');
      const replace=replacementTile(e,moveClip);
      if(moveClip)e.dataTransfer.dropEffect='move';
      if(replace){replace.classList.add('fdrop');return;}
      const at=dropIndex(row,e), tiles=[...row.querySelectorAll('.clip:not(.empty)')];
      if(at<tiles.length) tiles[at].classList.add('ins');
      else{const em=row.querySelector('.clip.empty'); if(em) em.classList.add('ins');}
    });
    row.addEventListener('dragleave',e=>{ if(!row.contains(e.relatedTarget)) clearIns(); });
    row.addEventListener('drop',e=>{
      e.preventDefault();
      const moveClip=layer.kind==='video'&&e.dataTransfer.types.includes('text/x-sm-video-cue');
      const at=dropIndex(row,e), replace=replacementTile(e,moveClip);
      if(hasFiles(e)){
        const files=[...e.dataTransfer.files], track=e.target.closest('.track');
        endDrag();
        if(!track)return;
        const cue=replace?+replace.dataset.j:at, expected=replace?layer.cues[cue]:undefined;
        if(expected&&files.length!==1){alert('差し替えるファイルを1つだけドロップしてください');return;}
        if(layer.kind==='photo'&&files.some(f=>!isImg(f.name))){alert('写真ウォールに入れられるのは画像だけです');return;}
        if(desktopReferenceDrop(files,{deck,lid:layer.id,cue,expected:layer.cues[cue],
            action:expected?'replace':at<layer.cues.length?'insert':'append'}))return;
        const column=columnInsert(layer,at);
        let index=at;
        return uploadFiles(files,async file=>{
          const action=!expected&&at<layer.cues.length
            ? {cmd:'add_cue',at:index,...column}
            : {cmd:'import_cue',cue,expected};
          const response=await post({...action,deck,lid:layer.id,file});
          if(!response.ok) throw new Error();
          index++;
        });
      }
      const m=e.dataTransfer.getData('text/x-sm-media'), q=e.dataTransfer.getData('text/x-sm-cue');
      endDrag();
      const source=q?JSON.parse(q):null;
      if(source&&source.deck!==deck)return;
      if(replace){
        const cue=+replace.dataset.j, expected=layer.cues[cue]??null;
        if(moveClip&&source){
          return cmd({cmd:'move_cue_into',lid:layer.id,cue,expected,
            source_lid:source.lid,source_cue:source.cue,source_expected:source.clip});
        }
        const file=e.dataTransfer.getData('text/x-sm-file');
        if(file&&expected) cmd({cmd:'import_cue',lid:layer.id,cue,expected,file});
        return;
      }
      if(m) return addMedia(layer,m,at,columnInsert(layer,at));
      if(source){const o=source;
        if(layer.kind==='photo'&&(!isImg(o.clip.file||'')||o.clip.file.startsWith('@'))){alert('写真ウォールに入れられるのは画像だけです');return;}
        if(o.lid===layer.id){const to=at>o.cue?at-1:at; if(to!==o.cue) cmd({cmd:'move_cue_to',lid:layer.id,cue:o.cue,to});}
        else if(moveClip){
          cmd({cmd:'move_cue_into',lid:layer.id,cue:at,expected:layer.cues[at]??null,insert:true,
            source_lid:o.lid,source_cue:o.cue,source_expected:o.clip});
        }
        else{const c={...o.clip}; delete c.mode; cmd({cmd:'add_cue',lid:layer.id,at,...c,...columnInsert(layer,at)});}}
    });
  });
}
// ドラッグ中にレイヤー欄の上下（左右）の端へ近づくと自動でスクロール
// （別ウィンドウの素材から持ってきたときも同じ。最後に動いてから少したったら止める）
let lastOver=0;
document.addEventListener('dragover',e=>{
  if(!dragActive&&!OURS(e.dataTransfer.types)&&!hasFiles(e))return;
  lastOver=Date.now();
  const d=$('deck'), r=d.getBoundingClientRect(), edge=70;
  let vy=0,vx=0;
  if(e.clientX>=r.left&&e.clientX<=r.right){
    if(e.clientY<r.top+edge&&e.clientY>r.top-40) vy=-1; else if(e.clientY>r.bottom-edge&&e.clientY<r.bottom+40) vy=1;
  }
  if(e.clientY>=r.top&&e.clientY<=r.bottom){
    if(e.clientX>r.right-edge&&e.clientX<r.right) vx=1; else if(e.clientX<r.left+edge+parseInt(getComputedStyle(document.documentElement).getPropertyValue('--lw'))&&e.clientX>r.left+parseInt(getComputedStyle(document.documentElement).getPropertyValue('--lw'))) vx=-1;
  }
  if(!vx&&!vy){stopAuto();return;}
  if(!autoScroll) autoScroll={t:setInterval(()=>{
    if(Date.now()-lastOver>400){stopAuto();return;}  // ウィンドウの外へ出た・やめた
    d.scrollTop+=autoScroll.vy*16;d.scrollLeft+=autoScroll.vx*16;},30)};
  autoScroll.vy=vy; autoScroll.vx=vx;
});
document.addEventListener('drop',endDrag);
document.addEventListener('dragend',endDrag);

// レイヤー名・コラム見出し・デッキタブを長押しで並べ替える。
const gridDragHandles='[data-layer-drag],#cols [data-col],#show-decks [data-deck]';
let gridDrag=null, suppressGridClick=false;
function markGridDrop(drag,scroll=false){
  const {x,y,layer,tab}=drag;
  const area=$(tab?'show-decks':'deck'), rect=area.getBoundingClientRect();
  drag.marker?.classList.remove('grid-before','grid-after','grid-target');
  drag.marker=null; drag.to=drag.from;
  if(x<rect.left||x>rect.right||y<rect.top||y>rect.bottom)return;
  if(scroll){
    const start=tab?rect.left:layer?rect.top+$('column-head').offsetHeight:rect.left+$('column-head').offsetWidth;
    const end=layer?rect.bottom:rect.right, p=layer?y:x;
    const delta=p>=start&&p<start+36?-14:p>end-36?14:0;
    if(layer)area.scrollTop+=delta;
    else area.scrollLeft+=delta;
  }
  const items=[...area.querySelectorAll(tab?'[data-deck]':layer?'.lrow':'#cols [data-col]')];
  if(!items.length)return;
  let boundary=items.findIndex(item=>{
    const r=item.getBoundingClientRect();
    return layer?y<(r.top+r.bottom)/2:x<(r.left+r.right)/2;
  });
  if(boundary<0)boundary=items.length;
  drag.to=boundary>drag.from?boundary-1:boundary;
  if(drag.to===drag.from)return;
  drag.marker=items[Math.min(boundary,items.length-1)];
  drag.after=boundary===items.length;
  drag.marker.classList.add('grid-target',drag.after?'grid-after':'grid-before');
}
function activateGridDrag(drag){
  if(gridDrag!==drag||drag.active||!drag.handle.isConnected)return;
  clearTimeout(drag.timer);
  drag.active=true;
  drag.handle.classList.add('grid-moving');
  closeContextMenu();
  markGridDrop(drag);
  drag.scroll=setInterval(()=>markGridDrop(drag,true),30);
}
function finishGridDrag(commit=false){
  const drag=gridDrag;
  if(!drag)return;
  gridDrag=null;
  clearTimeout(drag.timer);
  clearInterval(drag.scroll);
  if(drag.handle.hasPointerCapture(drag.pointer))drag.handle.releasePointerCapture(drag.pointer);
  if(!drag.active)return;
  drag.handle.classList.remove('grid-moving');
  drag.marker?.classList.remove('grid-before','grid-after','grid-target');
  suppressGridClick=true;
  if(commit&&drag.to!==drag.from){
    if(drag.tab){
      cmd({cmd:'deck_move',id:drag.handle.dataset.deck,anchor:drag.marker.dataset.deck,after:drag.after});
      return;
    }
    const target=drag.layer?{cmd:'move_layer_row',lid:drag.lid}:{cmd:'move_column',cue:drag.from};
    cmd({...target,to:drag.to,rev:drag.revision});
  }
}
document.addEventListener('pointerdown',e=>{
  if(!gridDrag)suppressGridClick=false;
  if(!e.isPrimary||e.button!==0)return;
  if(!S||gridDrag)return;
  const handle=e.target.closest(gridDragHandles);
  if(!handle)return;
  const layer=handle.hasAttribute('data-layer-drag');
  const tab=handle.hasAttribute('data-deck');
  const from=tab?Array.from(handle.parentElement.children).indexOf(handle):+(layer?handle.closest('.lrow').dataset.layer:handle.dataset.col);
  const drag={handle,layer,tab,from,to:from,lid:handle.dataset.layerDrag,revision:rev,
    pointer:e.pointerId,x:e.clientX,y:e.clientY,active:false};
  gridDrag=drag;
  handle.setPointerCapture(drag.pointer);
  drag.timer=setTimeout(()=>activateGridDrag(drag),250);
});
document.addEventListener('pointermove',e=>{
  const drag=gridDrag;
  if(!drag||drag.pointer!==e.pointerId)return;
  if(!drag.active&&Math.hypot(e.clientX-drag.x,e.clientY-drag.y)>8)activateGridDrag(drag);
  if(!drag.active)return;
  drag.x=e.clientX;drag.y=e.clientY;
  markGridDrop(drag);
});
document.addEventListener('pointerup',e=>{
  if(gridDrag?.pointer!==e.pointerId)return;
  if(gridDrag.active){
    gridDrag.x=e.clientX;gridDrag.y=e.clientY;
    markGridDrop(gridDrag);
  }
  finishGridDrag(true);
});
document.addEventListener('pointercancel',e=>{if(gridDrag?.pointer===e.pointerId)finishGridDrag();});
document.addEventListener('lostpointercapture',e=>{if(gridDrag?.pointer===e.pointerId)finishGridDrag();});
window.addEventListener('blur',()=>finishGridDrag());
document.addEventListener('click',e=>{
  if(suppressGridClick&&e.detail){
    suppressGridClick=false;
    e.preventDefault();e.stopImmediatePropagation();
  }
},true);
document.addEventListener('contextmenu',e=>{
  if((gridDrag?.active||suppressGridClick)&&e.target.closest(gridDragHandles)){
    e.preventDefault();e.stopImmediatePropagation();
  }
},true);

// ---- 素材ブラウザ ----
const isTestMedia=m=>m.startsWith('テスト用/');
// 素材の追加。m はファイル名か、特別な素材（空白・レイヤー映像・カメラ・ウィンドウ）の JSON
function addMedia(layer,m,at,column){
  const c=m.startsWith('{')?JSON.parse(m):{file:m};
  if(layer.kind==='photo'&&(c.file.startsWith('@')||!isImg(c.file))){alert('写真ウォールに入れられるのは画像だけです（動画・カメラなどは動画レイヤーへ）');return;}
  if(c.file==='@layer:'+layer.id){alert('自分自身の映像は追加できません（別のレイヤーに追加してください）');return;}
  cmd({cmd:'add_cue',lid:layer.id,at,...c,...column});
}
let mkind=VIEW==='media'?'f':(lsGet('vpt39-mkind')||'f');
const mediaKindButtons=document.querySelectorAll('[data-media-kind]');
function specialTile(c,label,title,cls){
  return `<div class="mtile" draggable="true" data-f="${esc(JSON.stringify(c))}" title="${esc(title)}"><div class="th"><div class="sp ${cls}">${esc(label)}</div></div><div class="cn">${esc(title)}</div></div>`;
}
function buildMedia(){
  const box=$('mediagrid'); if(!box)return;
  mediaKindButtons.forEach(b=>{
    const active=b.dataset.mediaKind===mkind;
    b.classList.toggle('on',active);
    b.setAttribute('aria-pressed',active);
  });
  const testing=mkind==='t';
  document.querySelector('.mfiles').classList.toggle('hide',mkind==='s');
  $('folder-address').classList.toggle('hide',mkind!=='f');
  if(mkind==='s'){
    const I=S.inputs||{};
    let h=`<div class="mh">何も映さない</div>`+specialTile({file:'@blank'},'空白','空白（何も映さない・音も止める）','blank');
    h+=`<div class="mh">レイヤーの映像</div>`+
      S.layers.map((L,i)=>specialTile({file:'@layer:'+L.id},'L'+(i+1)+'\n'+L.name,`L${i+1} ${L.name} の映像`,'layer')).join('');
    h+=`<div class="mh">カメラ・キャプチャーボード<span class="grow"></span><button class="sm" id="rescan">${I.scanning?'調べています…':'一覧を更新'}</button></div>`+
      ((I.cams||[]).map(n=>specialTile({file:'@camera',vdev:n,adev:''},'📷 '+camName(n),camName(n),'camera')).join('')||'<div class="meta" style="grid-column:1/-1">見つかりません（つないでから「一覧を更新」）</div>');
    h+=`<div class="mh">ウィンドウ（映像のみ。最小化すると止まることがあります）</div>`+
      ((I.windows||[]).map(n=>specialTile({file:'@window',title:n},'🗔 '+n,n,'window')).join('')||'<div class="meta" style="grid-column:1/-1">ありません</div>');
    h+=`<div class="mh">Spout</div>`+
      (I.spout||[]).map(n=>specialTile({file:'@spout',sender:n},'⇄ '+n,n,'spout')).join('');
    h+=`<div class="mh">NDI入力</div>`+
      (I.ndi||[]).map(n=>specialTile({file:'@ndi',sender:n},n,n,'ndi')).join('');
    if(I.ndi_error)h+=`<div class="meta" style="grid-column:1/-1">${esc(T(I.ndi_error))}</div>`;
    $('mcount').textContent='';
    const k=JSON.stringify([S.layers.map(L=>[L.id,L.name]),I]);
    if(box.dataset.k===k)return; box.dataset.k=k;
    box.innerHTML=h;
    $('rescan').onclick=()=>cmd({cmd:'scan_inputs'});
  }else{
    box.dataset.k='';
    const folder=S.browser_folder;
    const filter=$('mfilter');
    const path=$('folder-path');
    if(!testing&&path.title!==folder.path){
      if(path.title)filter.value='';
      path.textContent=folder.path;
      path.title=folder.path;
    }
    const q=filter.value.trim().toLowerCase();
    const media=testing?S.media.filter(isTestMedia):folder.files;
    const files=media.filter(m=>base(m).toLowerCase().includes(q));
    const folders=testing?[]:folder.folders.filter(name=>name.toLowerCase().includes(q));
    if(!testing)$('folder-up').disabled=!folder.can_up;
    const count=files.length+folders.length;
    const total=media.length+(testing?0:folder.folders.length);
    $('mcount').textContent=`${count}${count!==total?' / '+total:''}件`;
    const empty=!testing&&folder.error?'フォルダを開けませんでした':
      q?'一致する素材がありません':testing?'テスト用の素材がありません':'このフォルダに動画・画像がありません';
    const rows=folders.map(name=>`<button type="button" class="folder-row" data-folder="${esc(name)}" title="${esc(name)}"><span aria-hidden="true">📁</span><span>${esc(name)}</span></button>`);
    const tiles=files.map(m=>`<div class="mtile" draggable="true" data-media-file data-f="${esc(m)}" title="${esc(m)}">
      <div class="th">${mediaThumbnail(m)}</div><div class="cn">${esc(base(m))}</div></div>`);
    box.innerHTML=rows.join('')+tiles.join('')||`<div class="meta">${T(empty)}</div>`;
    box.querySelectorAll('[data-folder]').forEach(b=>b.onclick=()=>cmd({
      cmd:'browse_media_folder',direction:'child',folder:folder.path,name:b.dataset.folder,
    }));
  }
  box.querySelectorAll('.mtile').forEach(el=>{
    el.addEventListener('dragstart',e=>{
      e.dataTransfer.setData('text/x-sm-media',el.dataset.f);
      if(el.hasAttribute('data-media-file'))e.dataTransfer.setData('text/x-sm-file',el.dataset.f);
      e.dataTransfer.effectAllowed='copy';dragActive=true;
    });
    el.addEventListener('dragend',endDrag);
    el.addEventListener('click',()=>{ const s=S.layers[S.selected]; if(s&&(s.kind==='video'||isImg(el.dataset.f))) addMedia(s,el.dataset.f);});
  });
}
function hasFiles(e){return e.dataTransfer&&[...e.dataTransfer.types].includes('Files');}
function desktopReferenceDrop(files,target){
  if(!window.vpt39DesktopDrop)return false;
  try{
    window.chrome.webview.postMessageWithAdditionalObjects({kind:'vpt39-reference-drop',target},files);
  }catch(_){
    $('msg').textContent=T('ファイルの参照を登録できませんでした');
  }
  return true;
}
// アプリの外からドロップしたファイルで、ブラウザが操作画面を置き換えないように
document.addEventListener('dragover',e=>{ if(hasFiles(e)) e.preventDefault();});
document.addEventListener('drop',e=>{ if(hasFiles(e)) e.preventDefault();});
let uploading=false;
async function uploadFiles(files,onCopied){
  if(uploading){alert('取り込み中です。終わってからもう一度ドロップしてください');return;}
  const ok=files.filter(f=>isImg(f.name)||/\.(mp4|mov|m4v|avi|mkv|webm|wmv|mpg|mpeg)$/i.test(f.name)), ng=files.length-ok.length;
  if(!ok.length){alert('動画・画像のファイルではありません');return;}
  uploading=true; const st=$('mup'); const errs=[];
  for(let k=0;k<ok.length;k++){const f=ok[k];
    const copied=await new Promise(res=>{const x=new XMLHttpRequest();
      x.open('POST',`/api/upload?name=${encodeURIComponent(f.name)}`);
      x.upload.onprogress=ev=>{ if(ev.lengthComputable) st.textContent=`取り込み中 ${k+1}/${ok.length}：${f.name}（${Math.round(ev.loaded/ev.total*100)}%）`;};
      x.onload=()=>{try{const r=JSON.parse(x.responseText); if(r.ok)return res(r.file); errs.push(r.msg);}catch(_){errs.push(f.name+' を取り込めませんでした');} res();};
      x.onerror=()=>{errs.push(f.name+' を取り込めませんでした'); res();};
      x.send(f);});
    if(copied&&onCopied){
      try{await onCopied(copied);}
      catch(_){errs.push(f.name+' を素材に保存しましたが、レイヤーに反映できませんでした');}
    }
  }
  uploading=false; st.textContent=`取り込みました：${ok.length-errs.length} 件`+(ng?`（動画・画像でない ${ng} 件は飛ばしました）`:'');
  setTimeout(()=>{ if(st.textContent.startsWith('取り込みました')) st.textContent=''; },6000);
  if(errs.length) alert(errs.join('\n'));
  setTimeout(poll,400);
}
function selectMediaKind(kind){
  mkind=kind;
  lsSet('vpt39-mkind',kind);
  if(!S)return;
  if(kind==='s'&&!(S.inputs||{}).time) cmd({cmd:'scan_inputs'});
  buildMedia();
}
mediaKindButtons.forEach(b=>b.onclick=()=>selectMediaKind(b.dataset.mediaKind));

// ---- 列ボタン（クリップの列とそろえて並べる）----
const contextMenu=$('context-menu');
let contextOrigin=null;
function closeContextMenu(restoreFocus=false){
  if(contextMenu.classList.contains('hide'))return;
  contextMenu.classList.add('hide');
  if(contextOrigin?.hasAttribute('aria-expanded'))contextOrigin.setAttribute('aria-expanded','false');
  if(restoreFocus&&contextOrigin?.isConnected)contextOrigin.focus({preventScroll:true});
  contextOrigin=null;
}
function openContextMenu(e,actions,anchor=null){
  e.preventDefault();
  closeContextMenu();
  const menu=contextMenu;
  contextOrigin=anchor||e.target.closest('button,[tabindex]');
  if(anchor)anchor.setAttribute('aria-expanded','true');
  menu.replaceChildren(...actions.map(([label,run,danger])=>{
    const b=document.createElement('button');
    b.type='button'; b.textContent=T(label); b.setAttribute('role','menuitem');
    b.classList.toggle('danger',Boolean(danger));
    b.onclick=()=>{closeContextMenu(true);run();};
    return b;
  }));
  menu.classList.remove('hide');
  const rect=anchor?.getBoundingClientRect();
  const x=Math.max(6,Math.min(rect?.left??e.clientX,innerWidth-menu.offsetWidth-6));
  const y=Math.max(6,Math.min(rect?.bottom??e.clientY,innerHeight-menu.offsetHeight-6));
  menu.style.left=x+'px'; menu.style.top=y+'px';
  menu.firstElementChild.focus({preventScroll:true});
}
document.addEventListener('pointerdown',e=>{
  if(!contextMenu.contains(e.target)&&!e.target.closest('#panel-settings'))closeContextMenu();
});
const settingsButton=$('panel-settings');
settingsButton.onclick=e=>{
  if(contextOrigin===settingsButton&&!contextMenu.classList.contains('hide'))return closeContextMenu(true);
  openContextMenu(e,[
    ['素材',()=>activatePanel('media')],
    ['ミキサー',()=>activatePanel('mixer')],
    ['レイヤー設定',()=>activatePanel('insp')],
    ['出力設定',()=>activatePanel('outs')],
    ['今すぐ保存',()=>cmd({cmd:'save'})],
    ['名前を付けて保存',()=>cmd({cmd:'setting_save_dialog'})],
    ['セッティングを開く',openSettings],
    ['設定をリセット',()=>{
      $('resetform').reset();
      $('resetapply').disabled=true;
      $('resetdlg').showModal();
    }],
    [LANG==='en'?'日本語に切り替え':'English に切り替え',()=>cmd({cmd:'lang',value:LANG==='en'?'ja':'en'})]
  ],settingsButton);
};
function addDeckAction(label,anchor,after=false){
  return [label,()=>{
    const names=new Set(S.decks.map(d=>d.name));
    let n=1;
    while(names.has('デッキ '+n)||names.has('Deck '+n))n++;
    const name=prompt('デッキ名',T('デッキ '+n));
    if(name?.trim())cmd({cmd:'deck_add',name,anchor,after});
  }];
}
function deckActions(deck){
  const actions=[['デッキ名を変更',()=>{
    const name=prompt('デッキ名',deck.name);
    if(name?.trim())cmd({cmd:'deck_rename',id:deck.id,name});
  }],addDeckAction('このデッキの前に追加',deck.id),addDeckAction('このデッキの後に追加',deck.id,true)];
  if(S.decks.length>1)actions.push(['このデッキを削除',()=>{
    if(confirm(`デッキ「${deck.name}」とその中のクリップを削除しますか？`)){
      cmd({cmd:'deck_delete',id:deck.id});
    }
  },true]);
  return actions;
}
function buildDeckTabs(){
  const list=$('show-decks'), key=JSON.stringify([S.active_deck,S.decks]);
  if(list.dataset.key===key)return;
  if(gridDrag?.tab)finishGridDrag();
  const oldActive=list.querySelector('.on')?.dataset.deck;
  const focused=list.contains(document.activeElement)?document.activeElement.dataset.deck:null;
  list.dataset.key=key;
  list.replaceChildren(...S.decks.map(deck=>{
    const b=document.createElement('button');
    const active=deck.id===S.active_deck;
    b.type='button'; b.classList.toggle('on',active);
    b.dataset.deck=deck.id;
    b.translate=false;
    b.setAttribute('aria-pressed',String(active));
    b.textContent=deck.name; b.title=deck.name;
    b.onclick=()=>cmd({cmd:'deck_select',id:deck.id});
    b.oncontextmenu=e=>openContextMenu(e,deckActions(deck));
    return b;
  }));
  const active=list.querySelector('.on');
  if(S.active_deck!==oldActive)active?.scrollIntoView({block:'nearest',inline:'nearest'});
  if(focused)(Array.from(list.children).find(b=>b.dataset.deck===focused)||active)?.focus({preventScroll:true});
}
document.addEventListener('dragstart',()=>closeContextMenu());
$('deck').addEventListener('scroll',()=>closeContextMenu());
window.addEventListener('resize',()=>closeContextMenu());
contextMenu.addEventListener('keydown',e=>{
  e.stopPropagation();
  if(e.key==='Escape'||e.key==='Tab'){
    closeContextMenu(true);
    if(e.key==='Escape')e.preventDefault();
  }else if(e.key==='ArrowDown'||e.key==='ArrowUp'){
    e.preventDefault();
    const items=[...contextMenu.children], i=items.indexOf(document.activeElement);
    items[(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length].focus({preventScroll:true});
  }
});
function deleteColumn(cue,revision){
  if(confirm(`コラム ${cue+1} を削除しますか？ 全動画レイヤーのこの列が消えます。`)){
    cmd({cmd:'remove_column',cue,rev:revision});
  }
}
function columnCount(layers=S.layers.filter(L=>L.kind==='video')){
  return layers.length?Math.max(S.column_names.length,...layers.map(L=>L.cues.length)):0;
}
function columnMenu(cue,revision){
  const actions=[
    ['このコラムを再生',()=>cmd({cmd:'go_column',cue})],
    ['コラム名を変更',()=>{
      const name=prompt('コラム名（空欄で番号のみ）',S.column_names[cue]||'');
      if(name!==null)cmd({cmd:'rename_column',cue,rev:revision,name});
    }],
    ['このコラムの前に追加',()=>cmd({cmd:'add_column',cue,rev:revision})],
    ['このコラムの後に追加',()=>cmd({cmd:'add_column',cue:cue+1,rev:revision})]
  ];
  if(columnCount()>1)actions.push(['このコラムを削除',()=>deleteColumn(cue,revision),true]);
  return actions;
}
function buildColumns(){
  const layers=S.layers.filter(L=>L.kind==='video'), revision=S.rev;
  const names=S.column_names, n=columnCount(layers), cols=$('cols');
  cols.innerHTML=Array.from({length:n},(_,j)=>{
    const name=names[j]||'';
    return `<button data-col="${j}" title="${name?esc(name):`${j+1}番目のクリップを全部の動画レイヤーで一斉に再生`}">▶ ${j+1}${name?' '+esc(name):''}</button>`;
  }).join('');
  cols.querySelectorAll('[data-col]').forEach(b=>b.onclick=()=>cmd({cmd:'go_column',cue:+b.dataset.col}));
  cols.oncontextmenu=e=>{
    const col=e.target.closest('[data-col]');
    if(col)openContextMenu(e,columnMenu(+col.dataset.col,revision));
  };
  $('column-head').oncontextmenu=e=>{
    const actions=layers.length
      ? [['末尾にコラムを追加',()=>cmd({cmd:'add_column',cue:n,rev:revision})]]
      : layerAddMenu();
    openContextMenu(e,actions);
  };
}
function layerAddMenu(){
  return [
    ['動画レイヤーを追加',()=>cmd({cmd:'add_layer',kind:'video'})],
    ['写真ウォールを追加',()=>cmd({cmd:'add_layer',kind:'photo'})]
  ];
}
$('layer-footer').oncontextmenu=e=>openContextMenu(e,layerAddMenu());

function openPhotoSettings(L){
  const dialog=$('photodlg'), form=$('photoform'), fields=form.elements;
  const cancel=$('photo-cancel'), apply=$('photo-apply'), status=$('photo-status');
  $('photodlgname').textContent=L.name;
  fields.interval.value=L.photo_interval;
  fields.loop.checked=L.photo_loop;
  status.textContent='';
  cancel.onclick=()=>dialog.close();
  dialog.oncancel=e=>{if(apply.disabled)e.preventDefault();};
  form.onsubmit=async e=>{
    e.preventDefault();
    cancel.disabled=apply.disabled=true;
    status.textContent='';
    try{
      const response=await post({cmd:'photo_settings',lid:L.id,interval:fields.interval.valueAsNumber,loop:fields.loop.checked});
      if(!response.ok)throw new Error();
      dialog.close();
      setTimeout(poll,60);
    }catch(e){
      status.textContent=T('設定を送れませんでした');
    }finally{
      cancel.disabled=apply.disabled=false;
    }
  };
  dialog.showModal();
}

function photoMenu(L){
  const send=op=>cmd({cmd:'photo_transport',lid:L.id,op});
  const playing=L.photo_state==='playing';
  const actions=[];
  if(!playing||!L.visible)actions.push(['写真を再生',()=>send('play')]);
  if(playing)actions.push(['一時停止',()=>send('pause')]);
  actions.push(['先頭から再生',()=>send('restart')]);
  if(L.photo_state!=='stopped')actions.push(['停止（消す）',()=>send('stop')]);
  actions.push(['写真の再生設定',()=>openPhotoSettings(L)]);
  return actions;
}

// ---- レイヤーの行 ----
// 特別なクリップの表示名
// 同じ機種の2台目からは「名前 #1」…で届く → 「名前（2台目）」と表示
function camName(n){const m=/^(.*) #(\d+)$/.exec(n); return m?`${m[1]}（${+m[2]+1}台目）`:n;}
function cueInfo(c){
  const f=c.file||'';
  if(f==='@gap') return {cls:'blank',label:'空き枠',name:'空き枠'};
  if(f==='@blank') return {cls:'blank',label:'空白',name:'（空白）'};
  if(f.startsWith('@layer:')){const k=S.layers.findIndex(L=>'@layer:'+L.id===f);
    return {cls:'layer',label:k>=0?`L${k+1}\n${S.layers[k].name}`:'（元のレイヤーなし）',name:k>=0?`L${k+1}の映像`:'レイヤー映像'};}
  if(f==='@camera') return {cls:'camera',label:'📷 '+camName(c.vdev||''),name:'カメラ'};
  if(f==='@window') return {cls:'window',label:'🗔 '+(c.title||''),name:'ウィンドウ'};
  if(f==='@spout') return {cls:'spout',label:'⇄ '+(c.sender||''),name:'Spout'};
  if(f==='@ndi') return {cls:'ndi',label:c.sender||'',name:'NDI'};
  return null;
}
// 写真ウォールの行の画像（押すとその画像をすぐ出す・ドラッグで並べ替え）
function photoTile(i,c,j){
  return `<div class="clip" id="go${i}_${j}" data-j="${j}">
    <div class="th" draggable="true" data-cue="${j}" title="${esc(c.file)}（押すとすぐ出す・ドラッグで並べ替え）">${mediaThumbnail(c.file)}<span class="num">${j+1}</span></div>
    <div class="cn"><span title="${esc(c.file)}">${esc(base(c.file))}</span></div>
  </div>`;
}
let clipUpdates=Promise.resolve();
function openClipSettings(layer,j){
  const c=layer.cues[j], form=$('clipform'), dialog=$('clipdlg');
  const deck=gridDeck;
  const fields=form.elements;
  $('clipdlgname').textContent=cueInfo(c)?.name||base(c.file);
  $('clip-status').textContent='';
  fields.volume.value=Math.round((c.volume??1)*100);
  fields.fade_in.value=c.fade_in??0;
  fields.fade_out.value=c.fade_out??0;
  fields.audio_fade_in.value=c.audio_fade_in??c.fade_in??0;
  fields.audio_fade_out.value=c.audio_fade_out??c.fade_out??0;
  const showAudio=()=>{
    $('clip-volume').textContent=fields.volume.value+'%';
  };
  showAudio();
  let pending={}, timer=null, sending=false;
  const flush=(force=false)=>{
    clearTimeout(timer); timer=null;
    if((sending&&!force)||!Object.keys(pending).length)return;
    const values=pending;
    pending={}; sending=true;
    clipUpdates=clipUpdates.then(async()=>{
      const response=await post({cmd:'cue_mix',deck,lid:layer.id,cue:j,file:c.file,...values});
      if(!response.ok)throw new Error();
    }).catch(()=>{
      if(dialog.onclose===closeUpdates)$('clip-status').textContent=T('設定を送れませんでした');
    }).finally(()=>{
      sending=false;
      if(Object.keys(pending).length)flush();
      else if(!dialog.open)setTimeout(poll,60);
    });
  };
  const closeUpdates=()=>flush(true);
  form.oninput=e=>{
    const field=e.target;
    if(!field.name||!field.validity.valid)return;
    showAudio();
    const value=field.type==='checkbox'?field.checked:field.valueAsNumber;
    pending[field.name]=field.name==='volume'?value/100:value;
    if(timer===null&&!sending)timer=setTimeout(flush,30);
  };
  form.onsubmit=e=>{
    e.preventDefault();
    closeUpdates();
    dialog.close();
  };
  dialog.oncancel=closeUpdates;
  dialog.onclose=closeUpdates;
  dialog.showModal();
}
function deleteClip(layer,j){
  const clip=layer.cues[j], sp=cueInfo(clip);
  if(confirm(`クリップ「${sp?sp.name:base(clip.file)}」を削除しますか？`)){
    cmd({cmd:'remove_cue',lid:layer.id,cue:j,file:clip.file});
  }
}
function deleteLayer(layer){
  if(confirm(`レイヤー「${layer.name}」を削除しますか？`))cmd({cmd:'remove_layer',lid:layer.id});
}
function clipTile(i,c,j){
  const sp=cueInfo(c);
  if(sp){
    const mics=(S.inputs||{}).mics||[];
    const adev=c.file==='@camera'?`<select data-adev="${j}" title="一緒に流す音声（キャプチャーボードの音声など）"><option value="">音声なし</option>${
      [...new Set([...mics,c.adev].filter(Boolean))].map(n=>`<option value="${esc(n)}" ${n===c.adev?'selected':''}>${esc(n)}</option>`).join('')}</select>`:'';
    return `<div class="clip" id="go${i}_${j}" data-j="${j}">
      <div class="th" ${c.file==='@gap'?'':`draggable="true" data-cue="${j}" title="クリックで切り替え・ドラッグで並べ替え"`}><div class="sp ${sp.cls}">${esc(sp.label)}</div><span class="num">${j+1}</span></div>
      ${adev}
      <div class="tp">${c.file==='@gap'?'':`<button class="tb" data-tp="stop" title="消す">⏹</button>`}<span class="tt" id="tt${i}_${j}"></span></div>
      <div class="cn"><span title="${esc(sp.name)}">${esc(sp.name)}</span></div>
    </div>`;
  }
  const img=isImg(c.file);
  return `<div class="clip" id="go${i}_${j}" data-j="${j}">
    <div class="th" draggable="true" data-cue="${j}" title="${esc(c.file)}（クリックで再生・ドラッグで並べ替え）">${mediaThumbnail(c.file)}<span class="num">${j+1}</span>
      <button class="md" data-md="${j}" data-mode="${MODE_NAME[c.mode]?c.mode:'loop'}" title="終了時の動き：${MODE_NAME[c.mode]||''}（押すと切り替え）">${MODE_ICON[c.mode]||''}</button></div>
    ${img?'':`<div class="sc" title="ドラッグで再生位置を移動"><i id="sb${i}_${j}"></i></div>
    <div class="tp"><button class="tb" data-tp="restart" title="頭出し">⏮</button><button class="tb" data-tp="toggle" id="tg${i}_${j}" title="再生／一時停止">▶</button><button class="tb" data-tp="stop" title="停止（消す）">⏹</button><span class="tt" id="tt${i}_${j}"></span></div>`}
    <div class="cn"><span title="${esc(c.file)}">${esc(base(c.file))}</span></div>
  </div>`;
}
function build(){
  closeContextMenu();
  const box=$('layers'); box.innerHTML='';
  S.layers.forEach((L,i)=>{
    const d=document.createElement('div'); d.className='lrow'; d.id='row'+i; d.dataset.layer=i; d.dataset.kind=L.kind;
    let track;
    if(L.kind==='video'){
      track=L.cues.map((c,j)=>clipTile(i,c,j)).join('')+
        `<div class="clip empty" data-j="${L.cues.length}"><div class="th"><small>ファイルを<br>ここへドロップ</small></div></div>`;
    }else{
      track=L.cues.map((c,j)=>photoTile(i,c,j)).join('')+
        `<div class="clip empty" data-j="${L.cues.length}"><div class="th"><small>画像を<br>ここへドラッグ</small></div></div>`;
    }
    d.innerHTML=`<div class="lhead">
        <div class="row" data-layer-drag="${esc(L.id)}" style="gap:6px;flex-wrap:nowrap"><span class="lname grow" title="${esc(L.name)}">${esc(L.name)}</span><span class="tag">L${i+1} ${L.kind==='photo'?'写真':'動画'}</span></div>
        <div class="now" id="now${i}">—</div><div class="bar"><i id="bar${i}"></i></div>
        <div class="lbtns"><button data-a="vis" id="vis${i}" title="映す／隠す（音は止まらない）">表示</button>
          ${L.kind==='video'?`<button class="mut" data-a="mute" id="mut${i}" title="このレイヤーの音をミュート">ミュート</button><button data-a="clear" title="このレイヤーの映像と音を消す">消す</button>`:''}</div>
        <div class="lop">不透明度<input type="range" min="0" max="1" step="0.01" id="op${i}" data-k="opacity"></div>
        <div class="err" id="err${i}"></div>
      </div><div class="track">${track}</div>`;
    d.addEventListener('click',e=>{
      const t=e.target.closest('button,[data-cue]'); if(!t)return;
      if(t.dataset.tp) return cmd({cmd:'transport',layer:i,cue:+t.closest('.clip').dataset.j,op:t.dataset.tp});
      if(t.dataset.md!==undefined){e.stopPropagation(); return cycleCueMode(t,L,+t.dataset.md);}
      if(t.dataset.cue!==undefined) return cmd({cmd:'go',layer:i,cue:+t.dataset.cue});
      const a=t.dataset.a;
      if(a==='clear') cmd({cmd:'clear',layer:i});
      // 表示・M は押した瞬間に見た目を切り替える（本体の返事を待つと、一瞬なにも起きないように見えるため）
      else if(a==='vis'){const v=!S.layers[i].visible; S.layers[i].visible=v; t.classList.toggle('on',v); t.classList.toggle('held',!v); t.textContent=v?'表示':'非表示中'; setv(i,'visible',v);}
      else if(a==='mute'){const v=!S.layers[i].audio; S.layers[i].audio=v; t.classList.toggle('on',!v); const m=$('mxlm'+i); if(m) m.classList.toggle('on',!v); setv(i,'audio',v);}
    });
    const revision=S.rev;
    d.addEventListener('contextmenu',e=>{
      const tile=e.target.closest('.clip[data-j]');
      if(tile){
        const j=+tile.dataset.j, clip=L.cues[j];
        if(!clip){
          if(L.kind==='video')openContextMenu(e,[['空白クリップを追加',()=>cmd({cmd:'add_cue',lid:L.id,file:'@blank'})]]);
          return;
        }
        if(clip.file==='@gap')return openContextMenu(e,columnMenu(j,revision));
        const actions=[['クリップを再生',()=>cmd({cmd:'go',lid:L.id,cue:j})]];
        if(L.kind==='video'&&clip.file!=='@blank')actions.push(['クリップ設定',()=>openClipSettings(L,j)]);
        actions.push(['このクリップを削除',()=>deleteClip(L,j),true]);
        return openContextMenu(e,actions);
      }
      if(e.target.closest('.lhead')){
        const layer=S.layers.find(current=>current.id===L.id);
        if(!layer)return;
        const actions=[...(layer.kind==='photo'?photoMenu(layer):[]),['レイヤー名を変更',()=>{
          const name=prompt('レイヤー名',L.name);
          if(name?.trim())cmd({cmd:'set',lid:L.id,key:'name',value:name.trim()});
        }],...layerAddMenu()];
        if(S.layers.length>1)actions.push(['このレイヤーを削除',()=>deleteLayer(L),true]);
        openContextMenu(e,actions);
      }
    });
    const op=d.querySelector('#op'+i); op.addEventListener('input',()=>setv(i,'opacity',parseFloat(op.value)));
    d.querySelectorAll('[data-adev]').forEach(s=>s.onchange=()=>cmd({cmd:'cue_set',layer:i,cue:+s.dataset.adev,key:'adev',value:s.value}));
    d.addEventListener('mousedown',e=>{ if(e.button===0&&S.selected!==i){S.selected=i; cmd({cmd:'select',layer:i});} });
    box.appendChild(d);
  });
  setupDnD(); setupScrub(); buildMedia(); buildColumns(); lsetKey='';
}

// ---- 右：選んでいるレイヤーの設定 ----
function buildLset(){
  const i=S.selected, L=S.layers[i]; if(!L){$('lset').innerHTML='';lsetKey='';return;}
  const key=JSON.stringify([i,rev,S.targets,L.places.map(p=>p.output)]);
  if(key===lsetKey){
    for(const axis of ['flip_x','flip_y'])$('lset').querySelector(`[data-k="${axis}"]`).checked=Boolean(L[axis]);
    return;
  }
  if(editingIn('#lset'))return;
  lsetKey=key;
  let h=`<div class="set">
    <span class="lbl">名前</span><input data-k="name" value="${esc(L.name)}">
    <span class="lbl">重なり順</span><select id="layer-order" aria-label="重なり順">
      ${S.layers.map((_,rank)=>`<option value="${rank}" ${rank===L.z_order?'selected':''}>${rank+1}${rank===0?'（一番奥）':rank===S.layers.length-1?'（一番手前）':''}</option>`).join('')}
    </select>
    <span class="lbl">不透明度</span><input type="range" min="0" max="1" step="0.01" data-k="opacity" id="iop">
    <span class="lbl">反転</span><div class="chk">
      <label><input type="checkbox" data-k="flip_x" ${L.flip_x?'checked':''}>左右反転</label>
      <label><input type="checkbox" data-k="flip_y" ${L.flip_y?'checked':''}>上下反転</label>
    </div>
    <span class="lbl">合成方法</span><select data-k="blend">${S.blends.map(b=>`<option value="${b}" ${b===L.blend?'selected':''}>${BLEND_NAME[b]}</option>`).join('')}</select>
    ${S.targets.length>1?`<span class="lbl">出力先</span><div class="chk">${S.targets.map(t=>{const on=L.places.some(p=>p.output===t.output), only=on&&L.places.length===1;
      return `<label><input type="checkbox" data-place="${t.output}" ${on?'checked':''} ${only?'disabled':''}>${esc(t.name)}</label>`;}).join('')}</div>`:''}
    ${L.kind==='photo'?`
    <span class="lbl">フォルダ</span><input data-k="photo_folder" value="${esc(L.photo_folder)}">
    <span class="lbl">切り替え方</span><select data-k="photo_trans">${(S.transitions||[]).map(x=>`<option value="${x}" ${x===(L.photo_trans||'fade')?'selected':''}>${TRANS_NAME[x]||x}</option>`).join('')}</select>
    <span class="lbl">白フチ</span><input type="range" min="0" max="0.1" step="0.005" data-k="photo_border" value="${L.photo_border}">
    <span class="lbl"></span><label><input type="checkbox" data-k="photo_newest" ${L.photo_newest?'checked':''}> 新着を優先して表示</label>`:''}
    <span class="lbl">エッジぼかし</span><input type="range" min="0" max="0.25" step="0.005" data-k="feather" value="${L.feather}">
  </div>`;
  $('lset').innerHTML=h;
  // 作ったときのレイヤーに送る（入力の途中で別のレイヤーを選んでも、選び直した方を書き換えないように）
  const lid=L.id, idx=()=>{const k=S.layers.findIndex(x=>x.id===lid); return k>=0?k:-1;};
  const order=$('layer-order');
  order.onchange=()=>{cmd({cmd:'set_layer_order',lid,value:+order.value});order.blur();};
  $('lset').querySelectorAll('[data-place]').forEach(c=>c.onchange=()=>{const k=idx(); if(k>=0) cmd({cmd:'place',layer:k,output:+c.dataset.place,value:c.checked});});
  $('lset').querySelectorAll('[data-k]').forEach(el=>{
    const k=el.dataset.k, ev=(el.type==='range')?'input':'change';
    el.addEventListener(ev,()=>{
      const v=el.type==='checkbox'?el.checked:(el.type==='range'||el.type==='number')?parseFloat(el.value):(k==='output'?+el.value:el.value);
      cmd({cmd:'set',lid,key:k,value:v});
    });
  });
}

// ---- 出力ウィンドウ ----
let outsKey='';
// 本番の解像度の選び方（よく使うものと、自由に入れる）
const RES_LIST=[[1920,1080],[1280,720],[3840,2160],[2560,1440],[1920,1200],[1280,800],[1366,768],[1400,1050],[1024,768]];
function resSelect(o,k){
  const cur=o.res?o.res.join('x'):'auto', has=RES_LIST.some(r=>r.join('x')===cur);
  return `<select data-ores="${k}">
    <option value="auto" ${cur==='auto'?'selected':''}>${o.win?`自動（ウィンドウの大きさ・いま ${o.win[0]}×${o.win[1]}）`:'標準（1920×1080）'}</option>
    ${RES_LIST.map(r=>`<option value="${r.join('x')}" ${cur===r.join('x')?'selected':''}>${r[0]}×${r[1]}</option>`).join('')}
    ${!has&&cur!=='auto'?`<option value="${cur}" selected>${o.res[0]}×${o.res[1]}</option>`:''}
    ${o.win?'<option value="win">いまの画面の大きさにする</option>':''}<option value="custom">自分で入れる…</option></select>`;
}
function buildOuts(){
  const box=$('outs'); if(!box)return;
  const key=JSON.stringify([S.outputs.map(o=>[o.name,o.size,o.win,o.res,o.fps_limit,o.fullscreen,o.monitor,o.ndi.name,o.ndi.enabled]),S.screens,S.out_warning,
    S.layers.map(L=>[L.name,L.places.map(p=>p.output)])]);
  if(key===outsKey||editingIn('#outs')) return; outsKey=key;
  const ndiOpen=new Map([...box.querySelectorAll('[data-ndi-settings]')].map(d=>[+d.dataset.ndiSettings,d.open]));
  const sc=S.screens||[];
  let x0=Math.min(...sc.map(m=>m.rect[0])),y0=Math.min(...sc.map(m=>m.rect[1]));
  let x1=Math.max(...sc.map(m=>m.rect[0]+m.rect[2])),y1=Math.max(...sc.map(m=>m.rect[1]+m.rect[3]));
  const sw=Math.max(1,x1-x0), sh=Math.max(1,y1-y0), pad=4;
  const scrHtml=sc.length?`<div class="scr" style="aspect-ratio:${sw}/${sh};width:min(100%,${Math.round(200*sw/sh)}px);margin:0 auto">${sc.map((m,k)=>{
      const [x,y,w,h]=m.rect, L=(x-x0)/sw*100, T=(y-y0)/sh*100, W=w/sw*100, H=h/sh*100;
      return `<div class="${m.outputs.length?'has':''} ${m.primary?'pri':''}" style="left:calc(${L}% + ${pad}px);top:calc(${T}% + ${pad}px);width:calc(${W}% - ${pad*2}px);height:calc(${H}% - ${pad*2}px)" title="${esc(m.name)}${m.win?'（Windows の番号 '+m.win+'）':''}">
        <b>${k+1}</b><small>${w}×${h}・${m.scale}%${m.hz?'・'+m.hz+'Hz':''}${m.primary?'・メイン':''}</small>${m.outputs.map(n=>`<em>${esc(n)}</em>`).join('')}</div>`;}).join('')}</div>`:'';
  // 映すレイヤー：出力ごとにチェック
  const layerChecks=k=>`<div class="chk">${S.layers.map((L,i)=>{const on=L.places.some(p=>p.output===k);
      const only=on&&L.places.length===1;
      return `<label title="${only?'このレイヤーの出力はここだけなので外せません（他の出力にチェックを入れてから外せます）':''}"><input type="checkbox" data-pl="${i}" data-po="${k}" ${on?'checked':''} ${only?'disabled':''}>L${i+1} ${esc(L.name)}</label>`;}).join('')}</div>`;
  box.innerHTML=`<div class="row"><span class="meta grow">画面 ${S.monitors} 台接続中</span>
      <button class="sm" id="oadd">＋出力ウィンドウ</button>
      <button class="sm" id="oaddndi">＋NDI出力</button></div>
    ${S.outputs.map((o,k)=>`<div class="orow">
      <span class="tag ${o.fullscreen?'on':''}">${esc(o.name)}</span>
      <span class="meta grow">${o.size[0]}×${o.size[1]}${o.win?`・画面${o.monitor+1}${o.monitor===0?'（メイン）':''}`:''}</span>
      ${o.win?`<button class="${o.fullscreen?'on':''}" data-ofs="${k}">${o.fullscreen?'フルスクリーン中':'フルスクリーン'}</button>
      <button class="sm" data-oback="${k}" title="ウィンドウに戻してメイン画面の中央へ">手元に戻す</button>`:''}
      ${S.outputs.length>1?`<button class="sm" data-orm="${k}" title="この出力を削除">✕</button>`:''}</div>
      <div class="wide"><label for="output-type-${k}">出力方式</label>
        <select id="output-type-${k}" data-otype="${k}">
          ${!o.win&&!o.ndi.enabled?'<option selected disabled>停止中</option>':''}
          <option value="window" ${o.win?'selected':''}>出力ウィンドウ</option>
          <option value="ndi" ${!o.win&&o.ndi.enabled?'selected':''}>NDIのみ</option>
        </select></div>
      <div class="wide"><span>本番の解像度</span>${resSelect(o,k)}${o.win&&o.res&&(o.win[0]!==o.res[0]||o.win[1]!==o.res[1])?`<span class="meta">（いまのウィンドウ ${o.win[0]}×${o.win[1]} に縮小して表示）</span>`:''}</div>
      <div class="wide"><label for="output-fps-${k}">出力FPSの上限</label>
        <select id="output-fps-${k}" data-ofps="${k}">${[30,60].map(fps=>`<option value="${fps}" ${fps===o.fps_limit?'selected':''}>${fps} FPS</option>`).join('')}</select>
        <span class="meta">実測 <span id="output-rate-${k}"></span></span></div>
      <div class="wide"><span class="meta">映すレイヤー：</span></div>${layerChecks(k)}
      ${o.win?'':ndiSettings(o.ndi,k,ndiOpen.get(k) ?? true)}`).join('')}
    ${S.out_warning?`<div class="err">${esc(S.out_warning)}</div>`:''}
    <div class="row" style="margin-top:4px"><b>画面の配置</b><span class="grow"></span>
      <button class="sm" id="ident" title="各画面の中央に番号を4秒間表示">識別</button>
      <button class="sm" id="dispset" title="Windows の「ディスプレイ」設定を開く">ディスプレイ設定…</button></div>
    ${scrHtml}
    <div class="row"><a class="meta" href="https://ndi.video/" target="_blank" rel="noopener noreferrer">ndi.video</a></div>`;
  box.querySelectorAll('[data-ofs]').forEach(b=>b.onclick=()=>cmd({cmd:'out_fullscreen',output:+b.dataset.ofs}));
  box.querySelectorAll('[data-ores]').forEach(s=>s.onchange=()=>{const k=+s.dataset.ores, v=s.value;
    if(v==='auto') return cmd({cmd:'out_res',output:k,value:null});
    if(v==='win') return cmd({cmd:'out_res',output:k,value:S.outputs[k].win});
    if(v==='custom'){const a=prompt('本番の解像度（幅×高さ）を入れてください。例：1920x1080',S.outputs[k].size.join('x'));
      const m=a&&/^\s*(\d{2,5})\s*[x×*,]\s*(\d{2,5})\s*$/i.exec(a); if(m) return cmd({cmd:'out_res',output:k,value:[+m[1],+m[2]]}); outsKey=''; return buildOuts();}
    const [w,h]=v.split('x').map(Number); cmd({cmd:'out_res',output:k,value:[w,h]});});
  box.querySelectorAll('[data-oback]').forEach(b=>b.onclick=()=>cmd({cmd:'out_bring_back',output:+b.dataset.oback}));
  box.querySelectorAll('[data-otype]').forEach(s=>s.onchange=()=>{
    cmd({cmd:'out_type',output:+s.dataset.otype,type:s.value});
    s.blur();
  });
  box.querySelectorAll('[data-ofps]').forEach(s=>s.onchange=()=>{
    cmd({cmd:'out_fps',output:+s.dataset.ofps,value:+s.value});
    s.blur();
  });
  box.querySelectorAll('[data-orm]').forEach(b=>b.onclick=()=>{ if(confirm(`${S.outputs[+b.dataset.orm].name} を削除しますか？（ここだけに割り当てたレイヤーは出力1に移ります）`)) cmd({cmd:'out_remove',output:+b.dataset.orm}); });
  box.querySelectorAll('[data-pl]').forEach(c=>c.onchange=()=>cmd({cmd:'place',layer:+c.dataset.pl,output:+c.dataset.po,value:c.checked}));
  $('oadd').onclick=()=>cmd({cmd:'out_add'});
  $('oaddndi').onclick=()=>cmd({cmd:'out_add',type:'ndi'});
  $('ident').onclick=()=>cmd({cmd:'identify'});
  $('dispset').onclick=()=>cmd({cmd:'open_display_settings'});
  box.querySelectorAll('[data-ndi-name]').forEach(input=>{
    input.onchange=renameNdi;
    input.onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing)input.blur();};
  });
}

const ndiStatus={off:'停止中',starting:'準備中',missing:'NDI 6 Runtime が見つかりません',error:'NDI送信を開始できませんでした'};
function ndiSettings(ndi,k,open){
  return `<details class="ndi-settings" data-ndi-settings="${k}" ${open?'open':''}>
    <summary><span>NDI送信設定</span> <span class="meta" id="ndi-state-${k}"></span></summary>
    <div class="ndi-output">
      <label class="lbl">NDIソース名<input type="text" data-ndi-name="${k}" maxlength="60" value="${esc(ndi.name)}"></label>
      <span class="meta ndi-source" id="ndi-source-${k}"></span>
      <div class="hide" id="ndi-install-${k}">
        <a class="panel-link" href="https://downloads.ndi.tv/SDK/NDI_SDK/NDI%206%20Runtime.exe" target="_blank" rel="noopener noreferrer">NDI 6 Runtime をダウンロード</a>
        <div class="hint">インストール後に VPT39 を起動し直してください。</div>
      </div>
      <div class="err" role="status" id="ndi-error-${k}"></div></div></details>`;
}
async function renameNdi(e){
  const input=e.currentTarget;
  const output=+input.dataset.ndiName, error=$('ndi-error-'+output);
  const name=input.value.replace(/[\\/:*?"<>|\x00-\x1f\x7f]+/g,' ').trim();
  error.textContent='';
  if(!name){error.textContent=T('NDIソース名を入力してください');return;}
  if(S.outputs.some((o,k)=>k!==output&&o.ndi.enabled&&o.ndi.name.toLowerCase()===name.toLowerCase())){
    error.textContent=T('出力ごとに異なるNDIソース名を指定してください');return;
  }
  input.value=name;
  if(name===S.outputs[output].ndi.name)return;
  try{
    const response=await post({cmd:'out_ndi_name',output,name});
    if(!response.ok)throw new Error();
    setTimeout(poll,60);
  }catch(e){error.textContent=T('設定を送れませんでした');}
}
function updateNdi(){
  S.outputs.forEach(({ndi},k)=>{
    const state=$('ndi-state-'+k), label=ndi.status==='ready'?(ndi.receivers?`送信中・受信 ${ndi.receivers} 台`:''):ndiStatus[ndi.status];
    if(!state)return;
    $('ndi-install-'+k).classList.toggle('hide',ndi.status!=='missing');
    if(state.textContent!==T(label))state.textContent=T(label);
    const source=$('ndi-source-'+k);
    if(source.textContent!==ndi.source)source.textContent=ndi.source;
  });
}

// ---- 音声ミキサー（音声マスター → 出力ごと → レイヤーごと）----
let mixKey='';
function buildMixer(){
  const box=$('mixer'); if(!box)return;
  const key=JSON.stringify([S.outputs.map(o=>o.name),S.layers.map(L=>[L.name,L.kind,L.output])]);
  if(key===mixKey) return; mixKey=key;
  const row=(cls,name,mid,fid,title)=>`<div class="mxr ${cls}"><span class="mn" title="${esc(title||name)}">${esc(name)}</span>
    <button class="sm mbtn" id="${mid}" title="ミュート">M</button><input type="range" min="0" max="1" step="0.01" id="${fid}"><span class="pct" id="${fid}p"></span></div>`;
  let h='<div class="mx">';
  h+=row('m','音声マスター','mxmm','mxm');
  S.outputs.forEach((o,k)=>{
    h+=row('o',o.name,'mxom'+k,'mxo'+k,o.name+' に映しているレイヤー全部の音量');
    S.layers.forEach((L,i)=>{ if(L.kind!=='video'||(L.output||0)!==k)return;
      h+=row('l',`L${i+1} ${L.name}`,'mxlm'+i,'mxl'+i)+`<div class="meter"><i id="mxg${i}"></i></div>`;});
  });
  box.innerHTML=h+'</div>';
  $('mxmm').style.visibility='hidden';
  $('mxm').addEventListener('input',e=>cmd({cmd:'audio_master',value:parseFloat(e.target.value)}));
  S.outputs.forEach((o,k)=>{
    $('mxo'+k).addEventListener('input',e=>cmd({cmd:'out_audio',output:k,volume:parseFloat(e.target.value)}));
    $('mxom'+k).onclick=e=>{const v=!S.outputs[k].mute; S.outputs[k].mute=v; e.currentTarget.classList.toggle('on',v); cmd({cmd:'out_audio',output:k,mute:v});};
  });
  S.layers.forEach((L,i)=>{ const f=$('mxl'+i); if(!f)return;
    f.addEventListener('input',e=>setv(i,'volume',parseFloat(e.target.value)));
    $('mxlm'+i).onclick=e=>{const v=!S.layers[i].audio; S.layers[i].audio=v; e.currentTarget.classList.toggle('on',!v); const m=$('mut'+i); if(m) m.classList.toggle('on',!v); setv(i,'audio',v);};
  });
}
function updateMixer(){
  const fv=(id,v)=>{const el=$(id); if(!el)return; if(document.activeElement!==el) el.value=v; $(id+'p').textContent=Math.round(v*100)+'%';};
  fv('mxm',S.audio_master);
  S.outputs.forEach((o,k)=>{fv('mxo'+k,o.volume); const m=$('mxom'+k); if(m) m.classList.toggle('on',o.mute);});
  S.layers.forEach((L,i)=>{ if(!$('mxl'+i))return; fv('mxl'+i,L.volume);
    $('mxlm'+i).classList.toggle('on',!L.audio); $('mxg'+i).style.width=Math.round((L.gain||0)*100)+'%';});
}

function update(){
  updateMixer();
  S.outputs.forEach((o,k)=>{
    const el=$('output-rate-'+k), rate=o.fps?`${o.fps} FPS`:'—';
    if(el&&el.textContent!==rate)el.textContent=rate;
  });
  buildLan();
  if(document.activeElement!==$('master')) $('master').value=S.master;
  $('panel-settings').classList.toggle('on',S.edit);
  $('mapedit').classList.toggle('on',S.edit);
  const mapLabel=T(S.edit?'編集を終了':'編集');
  if($('mapedit').textContent!==mapLabel)$('mapedit').textContent=mapLabel;
  $('msg').textContent=S.message||(S.dirty?'未保存':'');
  const missingReferences=new Set(S.missing_references||[]);
  S.layers.forEach((L,i)=>{
    const row=$('row'+i); if(!row)return;
    row.classList.toggle('sel',i===S.selected);
    row.classList.toggle('hidden',!L.visible);
    {const b=$('vis'+i); b.classList.toggle('on',L.visible); b.classList.toggle('held',!L.visible); b.textContent=L.visible?'表示':'非表示中';}
    const mu=$('mut'+i); if(mu) mu.classList.toggle('on',!L.audio);
    const photoStatus=L.photo_state==='paused'?'一時停止':L.photo_state==='finished'?'再生終了':'';
    $('now'+i).textContent=photoStatus?`${T(photoStatus)}${L.label?' · '+L.label:''}`:
      L.label?(`▶ ${L.label}`+(L.dur>0?`  ${fmt(L.pos)} / ${fmt(L.dur)}`:'')):'— 停止中';
    $('bar'+i).style.width=(L.dur>0?Math.min(100,L.pos/L.dur*100):0)+'%';
    $('err'+i).textContent=L.error||(L.kind==='photo'&&!L.cues.length&&!L.photo_dir_ok?'フォルダが見つかりません':'');
    const op=$('op'+i); if(document.activeElement!==op) op.value=L.opacity;
    if(L.kind==='video') L.cues.forEach((c,j)=>{
      const b=$(`go${i}_${j}`); if(!b)return;
      b.classList.toggle('missing-reference',missingReferences.has(c.file));
      const on=L.playing===j; b.classList.toggle('on',on); b.classList.toggle('paused',on&&L.paused);
      const bar=$(`sb${i}_${j}`); if(!bar)return;
      if(!(scrub&&scrub.L===i&&scrub.j===j)) bar.style.width=(on&&L.dur>0?Math.min(100,L.pos/L.dur*100):0)+'%';
      $(`tt${i}_${j}`).textContent=on&&L.dur>0?`${fmt(L.pos)}/${fmt(L.dur)}`:'';
      $(`tg${i}_${j}`).textContent=on&&!L.paused?'⏸':'▶';
    });
    if(L.kind==='photo') L.cues.forEach((c,j)=>{
      const b=$(`go${i}_${j}`); if(!b)return;
      b.classList.toggle('missing-reference',missingReferences.has(c.file));
      const on=L.playing===j; b.classList.toggle('on',on); b.classList.toggle('paused',on&&L.photo_state==='paused');
    });
  });
  const sel=S.layers[S.selected];
  for(const id of ['mapedit','maptest','mapreset'])$(id).disabled=!sel;
  $('mtarget').textContent=sel?(sel.kind==='video'?`クリックで「L${S.selected+1} ${sel.name}」に追加`:`画像をクリックで写真ウォール「L${S.selected+1} ${sel.name}」に追加`):'レイヤーを選ぶと、クリックで追加できます';
  const tname=(S.targets.find(t=>t.output===S.map_out)||{}).name||'';
  $('mapsel').textContent=sel?`L${S.selected+1} ${sel.name}`+(S.outputs.length>1?`（${tname}・${S.size[0]}×${S.size[1]}）`:''):'';
  // マッピング：複数の出力に映しているレイヤーは、どの出力の四隅を直すか選ぶ
  const mk=JSON.stringify([S.selected,S.map_out,sel?sel.places.map(p=>p.output):[],S.targets.map(t=>t.name)]);
  if($('mtabs').dataset.k!==mk){$('mtabs').dataset.k=mk;
    $('mtabs').innerHTML=sel&&sel.places.length>1?'<span class="meta" style="align-self:center">位置合わせする出力：</span>'+sel.places.map(p=>`<button class="${p.output===S.map_out?'on':''}" data-mo="${p.output}">${esc((S.targets.find(t=>t.output===p.output)||{}).name||'')}</button>`).join(''):'';
    $('mtabs').querySelectorAll('[data-mo]').forEach(b=>b.onclick=()=>cmd({cmd:'map_out',output:+b.dataset.mo}));}
  const iop=$('iop'); if(iop&&sel&&document.activeElement!==iop) iop.value=sel.opacity;
  $('maptest').classList.toggle('on',!!(sel&&sel.test));
  if(!mapDrag.current) drawMap();
}

/* MAP_DRAG_SCRIPT */
const COLORS=['#ff5a5a','#59ccff','#73ff73','#ffd94d','#d980ff','#4dffe6'];
function drawMap(){
  const cv=$('map'), g=cv.getContext('2d');
  const ar=S.size[1]/S.size[0], height=Math.max(1,Math.round(cv.width*ar));
  if(cv.height!==height)cv.height=height;
  if(VIEW==='insp') cv.style.setProperty('--map-ratio',1/ar);
  const W=cv.width,H=cv.height;
  g.fillStyle='#000';g.fillRect(0,0,W,H);
  g.strokeStyle='#222';g.lineWidth=1;
  for(let k=1;k<8;k++){g.beginPath();g.moveTo(W*k/8,0);g.lineTo(W*k/8,H);g.moveTo(0,H*k/8);g.lineTo(W,H*k/8);g.stroke();}
  const so=S.map_out;
  const dragging=mapDrag.current;
  let nth=0;
  S.layers.forEach((L,i)=>{
    const pl=L.places.find(p=>p.output===so); if(!pl) return;  // 編集中の出力に映しているものだけ
    const c=(dragging&&L.id===dragging.lid&&dragging.o===so)?dragging.pts:pl.corners, col=COLORS[i%COLORS.length], sel=i===S.selected;
    g.globalAlpha=sel?1:(L.visible?.55:.25);
    g.fillStyle=col+(sel?'30':'14'); g.strokeStyle=col; g.lineWidth=sel?3:1.2;
    g.beginPath(); c.forEach((p,k)=>k?g.lineTo(p[0]*W,p[1]*H):g.moveTo(p[0]*W,p[1]*H)); g.closePath(); g.fill(); g.stroke();
    // 名前は重ならないように少しずつ下へずらす
    g.fillStyle=col; g.font=(sel?'bold ':'')+'22px sans-serif'; g.fillText('L'+(i+1)+' '+L.name,Math.max(4,c[0][0]*W+10),Math.max(0,c[0][1]*H)+28+26*nth++);
    if(sel){c.forEach(p=>{
      const [x,y]=mapHandle(p,W,H,9);
      g.fillStyle='#fff';g.fillRect(x-9,y-9,18,18);g.strokeStyle=col;g.lineWidth=3;g.strokeRect(x-9,y-9,18,18);});}
    g.globalAlpha=1;
  });
}
const mapDrag=bindMapDrag($('map'),{
  target:()=>{
    const L=S?.layers[S.selected], place=L?.places.find(p=>p.output===S.map_out);
    return place&&{lid:L.id,o:S.map_out,corners:place.corners};
  },
  redraw:drawMap,post,radius:()=>9,interval:40,
  onStart:corner=>cmd({cmd:'sel_corner',value:corner}),
  onFinish:(d,sent)=>{
    const place=S.layers.find(L=>L.id===d.lid)?.places.find(p=>p.output===d.o);
    if(place)place.corners=d.pts;
    sent.then(()=>setTimeout(poll,60));
  }
});

$('master').addEventListener('input',e=>cmd({cmd:'master',value:parseFloat(e.target.value)}));
$('clearall').onclick=()=>cmd({cmd:'clear_all'});
$('mapedit').onclick=()=>{if(S)cmd({cmd:'edit',value:!S.edit});};

// ---- スマホから操作（オン・オフと QR コード）----
let lanKey='';
function buildLan(){
  const on=!!S.allow_lan, urls=S.lan_urls||[];
  $('lanbtn').classList.toggle('on',on&&urls.length>0);
  $('lanbtn').textContent=on?(urls.length?'スマホ ON':'スマホ（つながらない）'):'スマホ';
  if($('landlg').classList.contains('hide')) return;
  const k=JSON.stringify([on,urls]); if(k===lanKey) return; lanKey=k;
  $('lantoggle').textContent=on?'スマホからの操作をやめる':'スマホから操作する';
  $('lantoggle').classList.toggle('on',!on);
  $('lanstate').textContent=on?(urls.length?'オン：同じ Wi-Fi のスマホから開けます':'オンですが、このPCのアドレスが見つかりません（Wi-Fi を確認）'):'オフ：このPCの中からだけ操作できます';
  $('lanqr').innerHTML=on&&urls.length?urls.map((u,i)=>`<img src="/api/qr.svg?i=${i}&t=${Date.now()}" alt="QR コード" style="width:220px;height:220px;background:#fff;border-radius:8px;padding:6px">
      <div style="font-size:15px;font-weight:700;user-select:all">${esc(u)}</div>`).slice(0,2).join('')+
      `<button class="sm" id="lanrefresh" title="Wi-Fi を変えたときなど">アドレスを調べ直す</button>`:'';
  if($('lanrefresh')) $('lanrefresh').onclick=()=>{lanKey=''; cmd({cmd:'lan_refresh'});};
  $('lanhint').innerHTML=on?
    T('① PC とスマホを<b>同じ Wi-Fi</b> につなぐ　② スマホのカメラで QR コードを読み取る（または上のアドレスを入れる）　③ 開いたページで四隅を合わせる。<br>')+
    'QR コードのアドレスには合言葉が入っていて、それを読み取ったスマホだけが操作できます（スマホの画面の操作だけ。素材の削除などは PC からだけ）。合言葉は、オンにし直したときと VPT39 を起動し直したときに変わります（スマホで QR を読み直す）。自分のルーターかスマホのテザリングで使い、終わったら「やめる」を押してください。会場・学校・ホテルの Wi-Fi ではつながらないことがあります。'
    :'押すと、同じ Wi-Fi のスマホから四隅の位置合わせができるようになります（起動し直す必要はありません）。初めてのときは Windows の「セキュリティの重要な警告」が出るので、「プライベート ネットワーク」にチェックして「アクセスを許可する」を押してください。';
}
$('lanbtn').onclick=()=>{ $('landlg').classList.remove('hide'); lanKey=''; buildLan(); };
$('lanclose').onclick=()=>$('landlg').classList.add('hide');
$('landlg').addEventListener('click',e=>{ if(e.target.id==='landlg') $('landlg').classList.add('hide'); });
$('lantoggle').onclick=()=>{ lanKey=''; cmd({cmd:'lan',value:!S.allow_lan}); };

// ---- セッティング（会場・公演ごとの設定ファイル）----
function fmtTime(t){const d=new Date(t*1000), p=n=>String(n).padStart(2,'0'); return `${d.getFullYear()}/${p(d.getMonth()+1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;}
let setKey='';
function buildSettings(){
  if($('setdlg').classList.contains('hide')) return;
  const nm=S.setting_name||'';
  const managed=S.setting_external?'':nm;
  const k=JSON.stringify([nm,managed,S.settings]); if(k===setKey) return; setKey=k;
  $('setcur').innerHTML=nm?T(`いま開いている：<b style="color:var(--acc)">${esc(nm)}</b>（変えると自動で上書き保存）`):
    'いまの状態には名前が付いていません。残しておくなら、名前を付けて保存してください。';
  const L=S.settings||[];
  $('setlist').innerHTML=L.length?L.map(r=>`<div class="setrow ${r.name===managed?'cur':''}"><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span>
      <span class="tm">${fmtTime(r.time)}</span>
      ${r.name===managed?'<span class="tag on">開いている</span>':`<button class="sm" data-sopen="${esc(r.name)}">開く</button>`}
      ${r.name===managed?'<button class="sm" data-sdup title="このセッティングを元に、新しい名前で作る">複製</button>':''}
      <button class="sm" data-sdel="${esc(r.name)}" title="ごみ箱へ移す">✕</button></div>`).join('')
    :'<div class="meta">settings フォルダに保存したセッティングはありません。</div>';
  $('setlist').querySelectorAll('[data-sopen]').forEach(b=>b.onclick=()=>{const n=b.dataset.sopen;
    const note=nm?`いまの「${nm}」は保存されています。`:'いまの状態には名前がないので、開くと置き換わります（残すなら先に名前を付けて保存）。';
    if(confirm(`セッティング「${n}」を開きますか？\n\n${note}\nレイヤー・クリップ・マッピング・出力ウィンドウが切り替わります。`)){cmd({cmd:'setting_open',name:n}); closeSettings();}});
  $('setlist').querySelectorAll('[data-sdup]').forEach(b=>b.onclick=()=>cmd({cmd:'setting_save_dialog',copy:true}));
  $('setlist').querySelectorAll('[data-sdel]').forEach(b=>b.onclick=()=>{const n=b.dataset.sdel;
    if(confirm(`セッティング「${n}」をごみ箱へ移しますか？（ごみ箱から戻せます）`+(n===managed?'\n\nいま開いているセッティングです。いまの状態は残りますが、名前なしになります。':''))) cmd({cmd:'setting_delete',name:n});});
}
function openSettings(){
  if(!S)return;
  $('setdlg').classList.remove('hide');
  setKey='';
  buildSettings();
  $('setclose').focus({preventScroll:true});
}
function closeSettings(){
  $('setdlg').classList.add('hide');
  settingsButton.focus({preventScroll:true});
}
$('setclose').onclick=closeSettings;
$('setdlg').addEventListener('click',e=>{ if(e.target.id==='setdlg') closeSettings(); });
$('setbrowse').onclick=()=>{
  closeSettings();
  cmd({cmd:'setting_open_dialog'});
};
const resetItems=()=>new FormData($('resetform')).getAll('items');
$('resetform').onchange=()=>{$('resetapply').disabled=resetItems().length===0;};
$('resetcancel').onclick=()=>$('resetdlg').close();
$('resetform').onsubmit=e=>{
  e.preventDefault();
  const items=resetItems();
  if(!items.length)return;
  $('resetdlg').close();
  cmd({cmd:'setting_reset',confirmed:true,items});
};
let panelEnded=false;
function closeControlPanel(){
  if(panelEnded)return;
  panelEnded=true;
  window.close();
  if(window.closed)return;
  const message=document.createElement('p');
  message.className='card';
  message.textContent=T('VPT39 は終了しました。このタブを閉じてください。');
  document.body.replaceChildren(message);
}
window.addEventListener('storage',e=>{
  if(S&&e.key==='vpt39-closed-boot'&&e.newValue===String(S.boot))closeControlPanel();
});
$('quit').onclick=async e=>{
  if(!confirm('VPT39 を終了しますか？（設定は保存されます）'))return;
  const button=e.currentTarget;
  button.disabled=true;
  try{
    const response=await post({cmd:'quit'});
    if(!response.ok)throw new Error();
    if(S)lsSet('vpt39-closed-boot',String(S.boot));
    closeControlPanel();
  }catch(e){
    button.disabled=false;
    $('msg').textContent=T('終了できませんでした');
  }
};
$('mfilter').addEventListener('input',()=>{ if(S) buildMedia(); });
$('folder-up').onclick=()=>cmd({cmd:'browse_media_folder',direction:'up'});
$('folder-choose').onclick=()=>{
  cmd({cmd:'browse_media_folder',direction:'choose'});
};
function setThumbSize(v){document.documentElement.style.setProperty('--mt',v+'px');}
$('msize').addEventListener('input',e=>{setThumbSize(e.target.value); lsSet('vpt39-msize-'+(VIEW||'main'),e.target.value);});
{const v=lsGet('vpt39-msize-'+(VIEW||'main')); if(v){$('msize').value=v; setThumbSize(v);} else if(VIEW==='media'){$('msize').value=170; setThumbSize(170);}}
$('maptest').onclick=()=>{const layer=S?.layers[S.selected];if(layer)setv(S.selected,'test',!layer.test);};
$('mapreset').onclick=()=>{ if(confirm('このレイヤーの四隅を、画面いっぱいの四角に戻しますか？')) cmd({cmd:'reset_corners',layer:S.selected,output:S.map_out}); };
document.addEventListener('keydown',e=>{
  if($('clipdlg').open||$('resetdlg').open)return;
  if(!e.repeat&&!e.ctrlKey&&!e.metaKey&&!e.altKey&&e.key.toLowerCase()==='e'
      &&S?.edit&&$('setdlg').classList.contains('hide')&&$('landlg').classList.contains('hide')
      &&!e.target.closest('input,textarea,select,[contenteditable]')){
    cmd({cmd:'edit',value:false});
    return;
  }
  if(e.key!=='Escape')return;
  if(gridDrag)finishGridDrag();
  else if(!$('setdlg').classList.contains('hide'))closeSettings();
  else if(!$('landlg').classList.contains('hide'))$('landlg').classList.add('hide');
});

async function poll(){
  if(panelEnded)return;
  try{
    const r=await fetch('/api/state'), state=await r.json();
    if(panelEnded)return;
    if(S&&state.boot===S.boot&&state.rev<S.rev)return;
    S=state; setLang(S.lang);
    const deckChanged=gridDeck!==null&&gridDeck!==S.active_deck;
    if(deckChanged){
      closeContextMenu();
      if($('clipdlg').open)$('clipdlg').close();
      if(gridDrag)finishGridDrag();
      endDrag();
      scrub=null;
      $('deck').scrollLeft=0;
    }
    buildDeckTabs();
    const mkey=JSON.stringify([S.media,S.browser_folder,S.boot,(S.inputs||{}).mics,S.missing_references]);
    buildOuts(); updateNdi(); buildMixer(); buildSettings();
    if(deckChanged||S.rev!==rev||S.layers.length!==nlayers||mkey!==mediaKey){
      // ドラッグ中（別ウィンドウからのドラッグも）と、文字を入力している間は作り直さない
      const dragNow=gridDrag||dragActive||Date.now()-lastOver<800;
      if(deckChanged||(!pendingModes&&!$('clipdlg').open&&!dragNow&&(!editingIn('.deck')||S.layers.length!==nlayers))){
        rev=S.rev; nlayers=S.layers.length; mediaKey=mkey; gridDeck=S.active_deck; build();
      }
    }
    if(mkind==='s') buildMedia();
    buildLset();
    update();
  }catch(e){ if(panelEnded)return; $('msg').textContent='（本体と接続できません）'; if(VIEW) $('childbar').firstChild.textContent='VPT39（本体と接続できません）'; }
}
setInterval(poll,300); poll();
</script></body></html>
"""
