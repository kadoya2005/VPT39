"""ブラウザ操作パネル（標準ライブラリだけで動く小さなWebサーバー）"""
import os
import json
import threading
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import thumbs

_app = None


def _thumb_path(rel):
    """サムネイルを作ってよいファイルか確認して絶対パスを返す
    （media フォルダの中か、キューに登録済みのファイルだけ）"""
    if not rel or _app is None:
        return None
    path = os.path.realpath(_app.media_path(rel))
    root = os.path.realpath(_app.resolve(_app.proj['media_dir']))
    inside = os.path.commonpath([path, root]) == root if os.path.splitdrive(path)[0].lower() == \
        os.path.splitdrive(root)[0].lower() else False
    if not inside:
        files = {c.get('file') for L in _app.proj['layers'] for c in L.get('cues', [])}
        if rel not in files:
            return None
    return path if os.path.isfile(path) else None


def _no_nan(s):
    raise ValueError('NaN / Infinity は受け付けない')  # 1つでも入ると操作パネルの JSON が壊れるため


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype, cache='no-store'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path in ('/', '/index.html'):  # /?view=media など（別ウィンドウの欄）も同じページ
            self._send(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
        elif self.path.startswith('/api/state'):
            self._send(200, _app.get_state(), 'application/json; charset=utf-8')
        elif self.path.startswith('/api/mstate'):  # スマホ用の軽い状態
            self._send(200, _app.get_state(mobile=True), 'application/json; charset=utf-8')
        elif urlparse(self.path).path in ('/m', '/m/'):  # スマホ用（セッティングモード）
            import mobile
            self._send(200, mobile.PAGE.encode('utf-8'), 'text/html; charset=utf-8')
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
        for c in (data if isinstance(data, list) else [data]):
            if isinstance(c, dict):
                _app.cmd_q.put(c)
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
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(name)
        dst, k = os.path.join(folder, name), 2
        while os.path.exists(dst):  # 同じ名前があれば「名前 (2).mp4」に
            dst = os.path.join(folder, f'{stem} ({k}){ext}')
            k += 1
        tmp = dst + '.uploading'
        try:
            with open(tmp, 'wb') as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        raise IOError('途中で切れました')
                    f.write(chunk)
                    left -= len(chunk)
            os.replace(tmp, dst)
        except Exception as ex:
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


def running():
    return _port is not None


def start(app, host, port):
    """Webサーバーを起動（2回目以降はアプリを差し替えるだけ）"""
    global _app, _port
    _app = app
    if _port is not None:
        return _port
    for p in range(port, port + 20):  # 使用中なら次の番号を試す
        try:
            srv = ThreadingHTTPServer((host, p), Handler)
            break
        except OSError:
            continue
    else:
        raise RuntimeError('操作パネル用のポートが確保できません')
    srv.daemon_threads = True
    srv.allow_reuse_address = False  # Windows では True だと2つ目の VPT39 が同じ番号を取れてしまう
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _port = p
    return p


PAGE = r"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VPT39 操作パネル</title>
<style>
:root{--bg:#0e1013;--panel:#171a1f;--panel2:#1f232a;--line:#2b3038;--text:#e6e8eb;--dim:#8a919c;
--acc:#3dd68c;--warn:#ffb020;--red:#ff4d4f;--blue:#4da3ff;
/* 色のきまり：緑＝ふつうに動いている・選んでいる／薄い緑＝マウスを乗せた所／赤＝止めている・固定している状態（ミュート・編集中など）とエラー／黄色（オレンジ）＝BLACKOUT */
--hov:#a9ecc9;--hovtx:#06291a;--mag:var(--red);--magtx:#fff;--tw:136px;--lw:232px;--gap:8px}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 "Segoe UI","Yu Gothic UI","Hiragino Sans",sans-serif;
display:flex;flex-direction:column;overflow:hidden}
header{flex:none;display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:10px 16px;background:#0a0b0d;border-bottom:1px solid var(--line)}
h1{font-size:24px;margin:0;letter-spacing:.06em;color:var(--acc);font-weight:800;line-height:1}
h1 small{color:var(--dim);font-weight:400;margin-left:8px;font-size:12px}
button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font:inherit;cursor:pointer}
button.on{background:var(--acc);color:#04120a;border-color:var(--acc);font-weight:600}
button.bo{background:#3a2a08;border-color:var(--warn);color:#ffd27a;font-weight:700;padding:8px 16px}
button.bo.on{background:var(--warn);border-color:var(--warn);color:#241600}
/* 止めている・固定している状態 */
button.lock.on,button.held,.lbtns .mut.on,.mbtn.on{background:var(--mag);border-color:var(--mag);color:var(--magtx);font-weight:700}
/* マウスを乗せたとき：ふつうのボタンは薄い緑。
   オン・固定中（緑・赤・黄）のボタンは色を変えずに薄い緑の縁だけ付ける（押した直後でも、切り替わったことが見えるように）*/
button:hover:not(:disabled):not(.on):not(.held){background:var(--hov);border-color:var(--hov);color:var(--hovtx)}
button.on:hover,button.held:hover{box-shadow:0 0 0 2px var(--hov);filter:brightness(1.08)}
button:disabled{opacity:.45;cursor:default}
button.sm{padding:2px 7px;font-size:12px}
input,select{background:#0c0e11;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:4px 6px;font:inherit}
input[type=range]{padding:0;accent-color:var(--acc);width:120px}
input[type=number]{width:62px}
.grow{flex:1}
.meta{color:var(--dim);font-size:12px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.lbl{color:var(--dim);font-size:12px;min-width:52px}
.hint{color:var(--dim);font-size:12px}
.msg{color:var(--acc);font-size:12px}
.err{color:var(--red);font-size:12px}
.tag{font-size:11px;padding:1px 6px;border-radius:4px;background:#2a2f37;color:var(--dim);white-space:nowrap}
.tag.on{background:var(--acc);color:#04120a}

/* ---- 全体：左＝レイヤー（自分だけスクロール）、右＝素材（固定）＋設定（自分だけスクロール）---- */
main{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 410px}
.deck{overflow:auto;min-width:0;position:relative;padding-bottom:30px}
aside{display:flex;flex-direction:column;min-height:0;border-left:1px solid var(--line);background:#121418}
.side-scroll{flex:1;min-height:0;overflow:auto;padding:10px;display:flex;flex-direction:column;gap:10px}
@media(max-width:1000px){body{overflow:auto;display:block}main{display:block}.deck{overflow:visible}aside{border-left:0}.side-scroll{overflow:visible}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:10px}

/* ---- レイヤーの行（VJアプリ風：左に名前と基本操作、右にクリップ）---- */
.grid{display:inline-flex;flex-direction:column;min-width:100%}
.colhead{position:sticky;top:0;z-index:6;display:flex;align-items:center;background:var(--bg);border-bottom:1px solid var(--line)}
.colhead .lh{position:sticky;left:0;z-index:7;width:var(--lw);flex:none;background:var(--bg);padding:8px 10px;font-size:12px;color:var(--dim);border-right:1px solid var(--line);align-self:stretch;display:flex;align-items:center}
.colhead .cols{display:flex;gap:var(--gap);padding:6px var(--gap)}
.colhead .cols button{width:var(--tw);flex:none;padding:4px 0;font-size:12px}
.lrow{display:flex;border-bottom:1px solid var(--line)}
.lhead{position:sticky;left:0;z-index:3;width:var(--lw);flex:none;background:var(--panel);padding:8px 10px;display:flex;flex-direction:column;gap:5px;border-right:1px solid var(--line);cursor:default}
.lrow.sel .lhead{background:#221d12;box-shadow:inset 4px 0 0 var(--warn)}
.lrow.hidden .lhead .lname,.lrow.hidden .track{opacity:.5}
.lname{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lhead .now{font-size:11px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar{height:3px;background:#22262d;border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--acc);width:0}
.lbtns{display:flex;gap:3px;flex-wrap:wrap}
.lbtns button{padding:2px 6px;font-size:12px}
.lop{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--dim)}
.lop input{flex:1;width:auto;min-width:0}
.track{display:flex;gap:var(--gap);padding:8px var(--gap);align-items:flex-start;min-height:100%}
.lrow.over .track{background:#0f1a14;outline:1px dashed var(--acc);outline-offset:-3px}

/* ---- クリップのタイル ---- */
.clip{width:var(--tw);flex:none;background:#0b0d10;border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column;position:relative}
.clip.on{border-color:var(--acc);box-shadow:0 0 0 2px var(--acc)}
.clip.ins::before{content:'';position:absolute;left:-6px;top:0;bottom:0;width:4px;background:var(--warn);border-radius:2px;z-index:2}
.clip.ins.end::before{left:auto;right:-6px}
.clip .th,.mtile .th{position:relative;display:block;width:100%;aspect-ratio:16/9;background:#000;overflow:hidden;cursor:pointer}
.th img{width:100%;height:100%;object-fit:cover;display:block}
.clip .num{position:absolute;left:4px;top:3px;font-size:11px;font-weight:700;background:#000b;padding:0 5px;border-radius:4px}
.clip.on .num{background:var(--acc);color:#04120a}
.clip .md{position:absolute;right:3px;top:3px;font-size:12px;background:#000b;color:#fff;border:0;padding:0 5px;border-radius:4px;line-height:1.5}
.clip .md:hover{background:var(--hov);color:var(--hovtx)}
.clip .cn{display:flex;align-items:center;gap:2px;font-size:11px;padding:3px 3px 4px 6px}
.clip .cn span{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clip .x{padding:0 5px;font-size:11px;line-height:1.4;border:0;background:transparent;color:var(--dim)}
.clip .x:hover{color:var(--hovtx);background:var(--hov)}
.clip:not(.empty):not(.on):hover{border-color:var(--hov)}
.clip.empty{border-style:dashed;background:transparent}
.clip.empty .th{display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--dim);font-size:20px;background:transparent;cursor:default;aspect-ratio:auto;height:118px}
.clip.empty small{font-size:11px;text-align:center;margin-top:4px;line-height:1.35}
.tp{display:flex;gap:2px;align-items:center;padding:3px 4px 0}
.tb{padding:0 5px;font-size:11px;line-height:1.5}
.sc{height:11px;margin:4px 5px 0;background:#22262d;border-radius:3px;position:relative;cursor:ew-resize;touch-action:none;overflow:hidden}
.sc i{position:absolute;left:0;top:0;bottom:0;background:var(--acc);width:0}
.sc i::after{content:'';position:absolute;right:-1px;top:0;bottom:0;width:3px;background:#fff}
.sc:hover{outline:1px solid var(--hov)}
.clip.paused .sc i{background:var(--warn)}
.tt{font-size:10px;color:var(--dim);white-space:nowrap;margin-left:auto}
.photo{width:360px;flex:none;background:#0b0d10;border:1px solid var(--line);border-radius:8px;padding:10px;display:flex;flex-direction:column;gap:6px;font-size:12px}
.addrow{display:flex;gap:8px;padding:12px 10px}

/* ---- 右：タブ（素材・レイヤー設定・ミキサー・出力）---- */
.tabs{flex:none;display:flex;gap:2px;padding:6px 6px 0;background:#0a0b0d;border-bottom:1px solid var(--line)}
.tab{display:flex;align-items:center;gap:4px;padding:6px 8px 6px 10px;border:1px solid var(--line);border-bottom:0;border-radius:7px 7px 0 0;background:#14171c;color:var(--dim);font-size:13px;cursor:pointer;user-select:none;white-space:nowrap}
.tab:hover{border-color:var(--hov);color:var(--hov)}
.tab.act{background:var(--panel);color:var(--text);font-weight:600;position:relative;top:1px}
.tab.det{opacity:.55;font-style:italic}
.tab .pop{border:0;background:transparent;color:var(--dim)}
.tab .pop:hover{color:var(--hovtx);background:var(--hov)}
.tabbody{flex:1;min-height:0;overflow:auto;padding:10px;display:flex;flex-direction:column;gap:10px}
.pane{display:none}
.pane.act{display:flex}
.media.pane.act{flex:1;min-height:0}
.media{flex-direction:column;gap:6px}
.mgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(var(--mt,104px),1fr));gap:6px;flex:1;min-height:60px;overflow:auto;align-content:start}
#mfolder{max-width:130px;font-size:12px}
.seg{display:flex}
.seg button{border-radius:0;padding:3px 9px;font-size:12px}
.seg button:first-child{border-radius:6px 0 0 6px}
.seg button:last-child{border-radius:0 6px 6px 0}
.mh{grid-column:1/-1;font-size:12px;color:var(--dim);margin-top:6px;display:flex;gap:8px;align-items:center}
.sp{white-space:pre-line;display:flex;align-items:center;justify-content:center;text-align:center;font-size:12px;font-weight:600;padding:4px;line-height:1.3;color:#fff;height:100%;word-break:break-all}
.sp.blank{background:repeating-linear-gradient(45deg,#15181d 0 8px,#1b1f25 8px 16px);color:var(--dim)}
.sp.layer{background:linear-gradient(135deg,#123a2a,#0d241b)}
.sp.camera{background:linear-gradient(135deg,#3a2a12,#241a0d)}
.sp.window{background:linear-gradient(135deg,#12283a,#0d1a24)}
.sp.spout{background:linear-gradient(135deg,#2a1838,#170d22)}
.clip select{width:calc(100% - 8px);margin:3px 4px 0;font-size:11px;padding:1px 2px}
.clip.empty .th{gap:6px}
.addblank{font-size:12px;padding:3px 8px}

/* ---- 欄を別ウィンドウに切り離す ---- */
.pop{padding:0 7px;font-size:13px;line-height:1.5}
.hide{display:none!important}
.childbar{display:none}
body.child header,body.child .deck,body.child .tabs,body.child .pop{display:none}
body.child main{grid-template-columns:minmax(0,1fr)}
body.child aside{border-left:0}
body.child .childbar{display:flex;flex:none;gap:10px;align-items:center;padding:8px 14px;background:#0a0b0d;border-bottom:1px solid var(--line)}
@media(max-width:640px){.childbar .meta{display:none}}
body.noaside main{grid-template-columns:minmax(0,1fr)}
body.noaside aside{display:none}
.chk{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px}
.chk label{display:flex;gap:4px;align-items:center;white-space:nowrap}
.mtabs{display:flex;gap:4px;flex-wrap:wrap}
.mtabs button{font-size:12px;padding:2px 8px}
.wide{font-size:12px;display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding-left:4px}
.wide input[type=number]{width:64px}
.mtile{background:#0b0d10;border:1px solid var(--line);border-radius:6px;overflow:hidden;cursor:grab;padding-bottom:3px}
.mtile:hover{border-color:var(--hov)}
.mtile{position:relative}
.mtile .del{position:absolute;right:3px;top:3px;display:none;padding:0 6px;font-size:11px;line-height:1.5;border:0;background:#000c;color:#fff;border-radius:4px}
.mtile:hover .del{display:block}
@media (hover:none){.mtile .del{display:block}}
.media.fdrop .mgrid{outline:2px dashed var(--acc);outline-offset:-2px;background:#0f1a14}
.mtile .cn{font-size:11px;padding:3px 5px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* ---- 右：レイヤー設定・マッピング ---- */
.insp .ttl{display:flex;align-items:center;gap:8px}
.insp .ttl b{font-size:15px}
.set{display:grid;grid-template-columns:72px 1fr;gap:8px 10px;align-items:center}
.set .lbl{min-width:0}
.set input[type=range]{width:100%}
#map{width:100%;background:#000;border:1px solid var(--line);border-radius:8px;touch-action:none;cursor:crosshair}
details summary{cursor:pointer;color:var(--dim);font-size:12px}

/* ---- ミキサー・出力 ---- */
.orow{display:flex;gap:6px;align-items:center;flex-wrap:wrap;padding:4px 0;border-top:1px solid var(--line)}
.orow .meta{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0;flex:1 1 0}
.mx{display:flex;flex-direction:column;gap:4px}
.mxr{display:grid;grid-template-columns:minmax(0,1fr) 34px 110px 38px;gap:6px;align-items:center}
.mxr.o{margin-top:6px;padding-top:6px;border-top:1px solid var(--line)}
.mxr.l .mn{padding-left:14px}
.mxr .mn{font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mxr.o .mn,.mxr.m .mn{font-weight:600;font-size:13px}
.mxr input[type=range]{width:100%;min-width:0}
.mxr .pct{font-size:11px;color:var(--dim);text-align:right}
.mbtn{padding:1px 0;font-size:11px;font-weight:700}
.meter{grid-column:1/-1;height:5px;background:#22262d;border-radius:2px;overflow:hidden;margin:0 0 2px 14px}
.meter i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc),#c8f060);transition:width .15s}
.scr{position:relative;width:100%;background:#0b0d10;border:1px solid var(--line);border-radius:8px}
.scr div{position:absolute;border:2px solid #4a515c;background:#1a1e24;border-radius:4px;display:flex;flex-direction:column;align-items:center;justify-content:center;overflow:hidden;text-align:center;line-height:1.25}
.scr div.has{border-color:var(--acc)}
.scr div.pri{background:#20262e}
.scr b{font-size:22px}
.scr small{font-size:10px;color:var(--dim)}
.scr em{font-size:10px;color:var(--acc);font-style:normal}
</style></head><body>
<header>
  <h1>VPT39<small id="meta"></small></h1>
  <span class="lbl" title="映像全体の明るさ（音量は右の「音声ミキサー」）">明るさ</span><input type="range" id="master" min="0" max="1" step="0.01">
  <button id="bo" class="bo">BLACKOUT (B)</button>
  <button id="clearall">全レイヤー消去</button>
  <button id="edit" class="lock">マッピング編集 (E)</button>
  <span class="grow"></span>
  <span class="msg" id="msg"></span>
  <button id="save">保存</button>
  <button id="quit">終了</button>
</header>
<div class="childbar" id="childbar"></div>
<main>
  <section class="deck" id="deck">
    <div class="grid">
      <div class="colhead"><div class="lh">列で再生 →</div><div class="cols" id="cols"></div></div>
      <div id="layers"></div>
      <div class="addrow"><button id="addv">＋動画レイヤー</button><button id="addp">＋写真ウォール</button>
        <span class="hint" style="align-self:center">Space＝L1の次へ ／ B＝ブラックアウト ／ E＝マッピング編集</span></div>
    </div>
  </section>
  <aside>
    <div class="tabs" id="tabs"></div>
    <div class="tabbody">
    <div class="media pane">
      <div class="row"><div class="seg"><button id="mkf" class="on">ファイル</button><button id="mks">空白・レイヤー・入力</button></div>
        <span class="meta" id="mcount"></span><span class="grow"></span></div>
      <div class="meta" id="mtarget"></div>
      <div class="msg" id="mup"></div>
      <div class="row mfiles" style="flex-wrap:nowrap">
        <select id="mfolder" title="フォルダで絞り込み"><option value="">すべてのフォルダ</option></select>
        <input id="mfilter" class="grow" style="min-width:0" placeholder="ファイル名で絞り込み">
        <input type="range" id="msize" min="80" max="260" step="10" value="104" style="width:70px" title="サムネイルの大きさ">
      </div>
      <div class="mgrid" id="mediagrid"></div>
      <div class="hint">レイヤーの行へドラッグすると、好きな位置に追加。クリックで選んでいるレイヤーの最後に追加。パソコンの動画・画像をここへドロップすると media フォルダに取り込みます。✕ でごみ箱へ。</div>
    </div>
      <div class="card insp pane">
        <div class="ttl"><b>レイヤー設定</b><span class="meta grow" id="mapsel"></span></div>
        <div id="lset"></div>
        <div class="row"><b style="font-size:13px">マッピング</b><span class="grow"></span>
          <button class="sm lock" id="mapedit">編集 (E)</button><button class="sm lock" id="maptest">テスト表示</button><button class="sm" id="mapreset">四隅リセット</button></div>
        <div class="mtabs" id="mtabs"></div>
        <canvas id="map" width="800" height="450"></canvas>
        <div class="row" id="cornsel"><span class="meta">矢印キーで動かす所：</span><button class="sm" data-c="0">左上</button><button class="sm" data-c="1">右上</button><button class="sm" data-c="2">右下</button><button class="sm" data-c="3">左下</button><button class="sm" data-c="4">全体</button></div>
        <div class="hint">角をドラッグで変形、内側をドラッグで移動。「編集 (E)」中は矢印キーで1pxずつ（Shiftで10px）動かせます（この画面でも出力ウィンドウ上でも）。</div>
      </div>
      <div class="card pane" id="mixer"></div>
      <div class="card pane" id="outs"></div>
    </div>
  </aside>
</main>
<script>
let S=null, rev=-1, nlayers=-1, mediaKey=null, dragging=null, lsetKey='';
const $=id=>document.getElementById(id);
function lsGet(k){try{return localStorage.getItem(k);}catch(e){return null;}}
function lsSet(k,v){try{localStorage.setItem(k,v);}catch(e){}}
function lsDel(k){try{localStorage.removeItem(k);}catch(e){}}

// ---- 欄を別ウィンドウに切り離す（?view=media / insp / mixer / outs）----
const VIEW=new URLSearchParams(location.search).get('view');
const PANELS={media:'素材',insp:'レイヤー設定',mixer:'音声ミキサー',outs:'出力ウィンドウ'};
const TABNAME={media:'素材',insp:'レイヤー設定',mixer:'ミキサー',outs:'出力'};
const POPSIZE={media:[1100,800],insp:[480,920],mixer:[480,560],outs:[520,820]};
function panelEl(p){return p==='media'?document.querySelector('.media'):p==='insp'?document.querySelector('.insp'):$(p);}
let curTab=lsGet('vpt39-tab')||'media'; if(!PANELS[curTab]) curTab='media';
if(VIEW&&PANELS[VIEW]){
  document.body.classList.add('child');
  document.title='VPT39 '+PANELS[VIEW];
  curTab=VIEW;
  $('childbar').innerHTML=`<b>VPT39</b><span>${PANELS[VIEW]}（別ウィンドウ）</span><span class="grow"></span>
    <span class="meta">このウィンドウを閉じると、メイン画面のタブに戻ります</span><button class="sm" id="dock">メイン画面に戻す</button>`;
  $('dock').onclick=()=>{lsDel('vpt39-pop-'+VIEW); window.close();};
  const beat=()=>lsSet('vpt39-pop-'+VIEW,Date.now());
  beat(); setInterval(beat,1000);
  window.addEventListener('beforeunload',()=>lsDel('vpt39-pop-'+VIEW));
}
// 別ウィンドウが最小化されていると、ブラウザは合図を1分に1回まで間引くので、75秒は待つ（閉じたときは beforeunload ですぐ消える）
const isDet=p=>!VIEW&&Date.now()-(+(lsGet('vpt39-pop-'+p)||0))<75000;
// タブ：押すと切り替え。⧉ か、タブをウィンドウの外へドラッグして離すと別ウィンドウになる
function buildTabs(){
  const dets=Object.keys(PANELS).filter(isDet);
  const key=curTab+'|'+dets.join(',');
  if($('tabs').dataset.k===key)return; $('tabs').dataset.k=key;
  $('tabs').innerHTML=Object.keys(PANELS).map(p=>`<div class="tab ${p===curTab?'act':''} ${isDet(p)?'det':''}" data-tab="${p}" draggable="true"
     title="${isDet(p)?'別ウィンドウで開いています（押すとそのウィンドウを前に出します）':'押すと切り替え／ウィンドウの外へドラッグすると別ウィンドウ'}">${TABNAME[p]}${isDet(p)?' ↗':`<button class="sm pop" data-pop="${p}" title="別ウィンドウで開く">⧉</button>`}</div>`).join('');
  $('tabs').querySelectorAll('.tab').forEach(t=>{
    t.addEventListener('click',e=>{ if(e.target.closest('[data-pop]'))return; const p=t.dataset.tab;
      if(isDet(p)){const w=window.open('','vpt39-'+p); if(w) w.focus(); return;}
      curTab=p; lsSet('vpt39-tab',p); applyDetached();});
    t.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/x-sm-tab',t.dataset.tab); e.dataTransfer.effectAllowed='move';});
    t.addEventListener('dragend',e=>{
      // ブラウザのウィンドウの外で離したら、その場所に別ウィンドウで開く
      const out=e.screenX<window.screenX||e.screenX>window.screenX+window.outerWidth||e.screenY<window.screenY||e.screenY>window.screenY+window.outerHeight;
      const offPage=e.clientX<=0&&e.clientY<=0&&e.screenX===0&&e.screenY===0;  // 位置が取れないとき
      if(out&&!offPage&&!isDet(t.dataset.tab)) popOut(t.dataset.tab,e.screenX-60,e.screenY-20);
    });
  });
}
function popOut(p,x,y){
  const [w,h]=POPSIZE[p];
  lsSet('vpt39-pop-'+p,Date.now());
  const pos=(x!==undefined)?`,left=${Math.round(x)},top=${Math.round(y)}`:'';
  const win=window.open('/?view='+p,'vpt39-'+p,`width=${w},height=${h}${pos}`);
  if(!win){lsDel('vpt39-pop-'+p); alert('別ウィンドウを開けませんでした（ブラウザのポップアップブロックを許可してください）');}
  applyDetached();
}
// メイン画面：別ウィンドウで開いている欄はタブから外す（別ウィンドウが閉じたら自動で戻る）
function applyDetached(){
  if(VIEW){Object.keys(PANELS).forEach(p=>panelEl(p).classList.toggle('act',p===VIEW)); return;}
  const live=Object.keys(PANELS).filter(p=>!isDet(p));
  if(!live.includes(curTab)&&live.length) curTab=live[0];
  Object.keys(PANELS).forEach(p=>panelEl(p).classList.toggle('act',p===curTab&&live.includes(p)));
  document.body.classList.toggle('noaside',!live.length);
  buildTabs();
}
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-pop]'); if(!b)return;
  e.stopPropagation(); popOut(b.dataset.pop);
});
window.addEventListener('storage',applyDetached);
function post(c){return fetch('/api/cmd',{method:'POST',body:JSON.stringify(c)});}
function cmd(c){post(c).then(()=>setTimeout(poll,60));}
function esc(s){return String(s).replace(/[&<>"]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[m]));}
function fmt(t){t=Math.max(0,t|0);return (t/60|0)+':'+String(t%60).padStart(2,'0');}
function setv(L,key,value){cmd({cmd:'set',layer:L,key,value});}
const MODES=['loop','hold','stop','next'];
const MODE_NAME={loop:'ループ',hold:'最後で停止',stop:'終了で消える',next:'次へ進む'};
const MODE_ICON={loop:'⟳',hold:'⏸',stop:'■',next:'⏭'};
const BLEND_NAME={normal:'通常',add:'加算',screen:'スクリーン',multiply:'乗算'};
const TRANS_NAME={fade:'フェード（じわっと）',cut:'静止（効果なし・パッと切り替え）',slide_left:'スライド（右から左へ）',slide_right:'スライド（左から右へ）',
  slide_up:'スライド（下から上へ）',slide_down:'スライド（上から下へ）',wipe:'ワイプ（左から順に）',zoom:'ズーム（少し寄りから）'};
function base(f){return String(f).split(/[\\/]/).pop();}
function isImg(f){return /\.(png|jpe?g|bmp|gif|webp)$/i.test(f);}
function thumbImg(f){return `<img src="/thumb?f=${encodeURIComponent(f)}" loading="lazy" draggable="false" onerror="this.remove()">`;}
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
function clearIns(){document.querySelectorAll('.lrow.over').forEach(r=>r.classList.remove('over'));document.querySelectorAll('.clip.ins').forEach(c=>c.classList.remove('ins','end'));}
function endDrag(){dragActive=false; clearIns(); stopAuto();}
function stopAuto(){if(autoScroll){clearInterval(autoScroll.t);autoScroll=null;}}
function setupDnD(){
  document.querySelectorAll('.clip:not(.empty) .th').forEach(el=>{
    el.addEventListener('dragstart',e=>{const L=+el.closest('.lrow').dataset.layer, j=+el.closest('.clip').dataset.j;
      e.dataTransfer.setData('text/x-sm-cue',JSON.stringify({layer:L,cue:j,...S.layers[L].cues[j]}));
      e.dataTransfer.effectAllowed='copyMove'; dragActive=true;});
    el.addEventListener('dragend',endDrag);
  });
  document.querySelectorAll('.lrow[data-kind=video]').forEach(row=>{
    row.addEventListener('dragover',e=>{
      if(!OURS(e.dataTransfer.types))return;
      e.preventDefault(); clearIns(); row.classList.add('over');
      const at=dropIndex(row,e), tiles=[...row.querySelectorAll('.clip:not(.empty)')];
      if(at<tiles.length) tiles[at].classList.add('ins');
      else{const em=row.querySelector('.clip.empty'); if(em) em.classList.add('ins');}
    });
    row.addEventListener('dragleave',e=>{ if(!row.contains(e.relatedTarget)) row.classList.remove('over'); });
    row.addEventListener('drop',e=>{
      e.preventDefault();
      const L=+row.dataset.layer, at=dropIndex(row,e);
      const m=e.dataTransfer.getData('text/x-sm-media'), q=e.dataTransfer.getData('text/x-sm-cue');
      endDrag();
      if(m) return addMedia(L,m,at);
      if(q){const o=JSON.parse(q);
        if(o.layer===L){const to=at>o.cue?at-1:at; if(to!==o.cue) cmd({cmd:'move_cue_to',layer:L,cue:o.cue,to});}
        else{const c={...o}; delete c.layer; delete c.cue; delete c.mode; cmd({cmd:'add_cue',layer:L,at,...c});}}  // 別レイヤーへはコピー
    });
  });
}
// ドラッグ中にレイヤー欄の上下（左右）の端へ近づくと自動でスクロール
// （別ウィンドウの素材から持ってきたときも同じ。最後に動いてから少したったら止める）
let lastOver=0;
document.addEventListener('dragover',e=>{
  if(!dragActive&&!OURS(e.dataTransfer.types))return;
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

// ---- 素材ブラウザ ----
function buildFolders(){
  // media フォルダの中のサブフォルダ（1段目）で絞り込めるようにする
  const sel=$('mfolder'), cur=sel.value;
  const dirs=[...new Set(S.media.filter(m=>m.includes('/')).map(m=>m.split('/')[0]))].sort();
  if(sel.dataset.k===dirs.join('|'))return; sel.dataset.k=dirs.join('|');
  sel.innerHTML='<option value="">すべてのフォルダ</option>'+(dirs.length?'<option value=".">（media の直下）</option>':'')+
    dirs.map(d=>`<option value="${esc(d)}">${esc(d)}</option>`).join('');
  sel.value=[...sel.options].some(o=>o.value===cur)?cur:'';
  sel.style.display=dirs.length?'':'none';
}
// 素材の追加。m はファイル名か、特別な素材（空白・レイヤー映像・カメラ・ウィンドウ）の JSON
function addMedia(L,m,at){
  const c=m.startsWith('{')?JSON.parse(m):{file:m};
  if(c.file==='@layer:'+S.layers[L].id){alert('自分自身の映像は追加できません（別のレイヤーに追加してください）');return;}
  cmd({cmd:'add_cue',layer:L,at,...c});
}
let mkind=VIEW==='media'?'f':(lsGet('vpt39-mkind')||'f');
function specialTile(c,label,title,cls){
  return `<div class="mtile" draggable="true" data-f="${esc(JSON.stringify(c))}" title="${esc(title)}"><div class="th"><div class="sp ${cls}">${esc(label)}</div></div><div class="cn">${esc(title)}</div></div>`;
}
function buildMedia(){
  const box=$('mediagrid'); if(!box)return;
  $('mkf').classList.toggle('on',mkind==='f'); $('mks').classList.toggle('on',mkind==='s');
  document.querySelector('.mfiles').classList.toggle('hide',mkind!=='f');
  if(mkind==='s'){
    const I=S.inputs||{};
    let h=`<div class="mh">何も映さない</div>`+specialTile({file:'@blank'},'空白','空白（何も映さない・音も止める）','blank');
    h+=`<div class="mh">レイヤーの映像（同じ映像を同じタイミングで、別の場所・別の出力に）</div>`+
      S.layers.map((L,i)=>specialTile({file:'@layer:'+L.id},'L'+(i+1)+'\n'+L.name,`L${i+1} ${L.name} の映像`,'layer')).join('');
    h+=`<div class="mh">カメラ・キャプチャーボード<span class="grow"></span><button class="sm" id="rescan">${I.scanning?'調べています…':'一覧を更新'}</button></div>`+
      ((I.cams||[]).map(n=>specialTile({file:'@camera',vdev:n,adev:''},'📷 '+camName(n),camName(n),'camera')).join('')||'<div class="meta" style="grid-column:1/-1">見つかりません（つないでから「一覧を更新」）</div>');
    h+=`<div class="mh">ウィンドウ（映像のみ。最小化すると止まることがあります）</div>`+
      ((I.windows||[]).map(n=>specialTile({file:'@window',title:n},'🗔 '+n,n,'window')).join('')||'<div class="meta" style="grid-column:1/-1">ありません</div>');
    h+=`<div class="mh">Spout（TouchDesigner・OBS・VJアプリなどから。遅れなし・透明あり）</div>`+
      ((I.spout||[]).map(n=>specialTile({file:'@spout',sender:n},'⇄ '+n,n,'spout')).join('')||'<div class="meta" style="grid-column:1/-1">送られていません（送る側で Spout 出力をオンにしてから「一覧を更新」）</div>');
    $('mcount').textContent='';
    const k=JSON.stringify([S.layers.map(L=>[L.id,L.name]),I]);
    if(box.dataset.k===k)return; box.dataset.k=k;
    box.innerHTML=h;
    $('rescan').onclick=()=>cmd({cmd:'scan_inputs'});
  }else{
    box.dataset.k='';
    const q=($('mfilter').value||'').toLowerCase(), fd=$('mfolder').value;
    const list=S.media.filter(m=>(!q||m.toLowerCase().includes(q))&&
      (!fd||(fd==='.'?!m.includes('/'):m.startsWith(fd+'/'))));
    $('mcount').textContent=`${list.length}${list.length!==S.media.length?' / '+S.media.length:''}件`;
    box.innerHTML=list.map(m=>`<div class="mtile" draggable="true" data-f="${esc(m)}" title="${esc(m)}">
      <div class="th">${thumbImg(m)}<button class="del" data-del="${esc(m)}" title="この素材をごみ箱へ移す（ごみ箱から元に戻せます）">✕</button></div><div class="cn">${esc(base(m))}</div></div>`).join('')
      ||'<div class="meta">media フォルダに素材がありません。動画や画像を入れると数秒で表示されます。</div>';
  }
  box.querySelectorAll('.mtile').forEach(el=>{
    el.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/x-sm-media',el.dataset.f);e.dataTransfer.effectAllowed='copy';dragActive=true;});
    el.addEventListener('dragend',endDrag);
    el.addEventListener('click',e=>{ if(e.target.closest('.del')) return; const s=S.layers[S.selected]; if(s&&s.kind==='video') addMedia(S.selected,el.dataset.f);});
  });
  box.querySelectorAll('[data-del]').forEach(b=>b.onclick=e=>{e.stopPropagation(); deleteMedia(b.dataset.del);});
}
// 素材をごみ箱へ（クリップで使っていれば、そのことも知らせる）
async function deleteMedia(m){
  const used=S.layers.reduce((n,L)=>n+L.cues.filter(c=>c.file===m).length,0);
  if(!confirm(`「${base(m)}」をごみ箱へ移しますか？${used?`\n\nこの素材は ${used} 個のクリップで使っています。クリップは残りますが、再生すると「ファイルが見つかりません」になります。`:''}\n\n（ごみ箱から元に戻せます）`))return;
  try{const r=await fetch('/api/media_delete',{method:'POST',body:JSON.stringify({file:m})}).then(r=>r.json());
    if(!r.ok) alert(r.msg||'削除できませんでした');}catch(e){alert('削除できませんでした');}
  setTimeout(poll,300);
}
// パソコンのファイルを素材の欄へドロップ → media フォルダへ取り込む（いま絞り込んでいるフォルダがあればそこへ）
function hasFiles(e){return e.dataTransfer&&[...e.dataTransfer.types].includes('Files');}
{const pane=document.querySelector('.media');
  pane.addEventListener('dragover',e=>{ if(!hasFiles(e))return; e.preventDefault(); e.dataTransfer.dropEffect='copy'; pane.classList.add('fdrop');});
  pane.addEventListener('dragleave',e=>{ if(!pane.contains(e.relatedTarget)) pane.classList.remove('fdrop');});
  pane.addEventListener('drop',e=>{ if(!hasFiles(e))return; e.preventDefault(); pane.classList.remove('fdrop'); uploadFiles([...e.dataTransfer.files]);});
}
// 素材の欄の外に落としても、ブラウザがそのファイルを開いて操作画面が消えないように
document.addEventListener('dragover',e=>{ if(hasFiles(e)) e.preventDefault();});
document.addEventListener('drop',e=>{ if(hasFiles(e)) e.preventDefault();});
let uploading=false;
async function uploadFiles(files){
  if(uploading){alert('取り込み中です。終わってからもう一度ドロップしてください');return;}
  const ok=files.filter(f=>isImg(f.name)||/\.(mp4|mov|m4v|avi|mkv|webm|wmv|mpg|mpeg)$/i.test(f.name)), ng=files.length-ok.length;
  if(!ok.length){alert('動画・画像のファイルではありません');return;}
  const fd=$('mfolder').value, dir=fd&&fd!=='.'?fd:'';
  uploading=true; const st=$('mup'); const errs=[];
  for(let k=0;k<ok.length;k++){const f=ok[k];
    await new Promise(res=>{const x=new XMLHttpRequest();
      x.open('POST',`/api/upload?name=${encodeURIComponent(f.name)}&dir=${encodeURIComponent(dir)}`);
      x.upload.onprogress=ev=>{ if(ev.lengthComputable) st.textContent=`取り込み中 ${k+1}/${ok.length}：${f.name}（${Math.round(ev.loaded/ev.total*100)}%）`;};
      x.onload=()=>{try{const r=JSON.parse(x.responseText); if(!r.ok) errs.push(r.msg);}catch(_){errs.push(f.name+' を取り込めませんでした');} res();};
      x.onerror=()=>{errs.push(f.name+' を取り込めませんでした'); res();};
      x.send(f);});
  }
  uploading=false; st.textContent=`取り込みました：${ok.length-errs.length} 件`+(ng?`（動画・画像でない ${ng} 件は飛ばしました）`:'')+(dir?`→ ${dir}`:'');
  setTimeout(()=>{ if(st.textContent.startsWith('取り込みました')) st.textContent=''; },6000);
  if(errs.length) alert(errs.join('\n'));
  setTimeout(poll,400);
}
$('mkf').onclick=()=>{mkind='f'; lsSet('vpt39-mkind','f'); if(S) buildMedia();};
$('mks').onclick=()=>{mkind='s'; lsSet('vpt39-mkind','s'); if(S){ if(!(S.inputs||{}).time) cmd({cmd:'scan_inputs'}); buildMedia();}};

// ---- 列ボタン（クリップの列とそろえて並べる）----
function buildColumns(){
  const n=Math.max(0,...S.layers.filter(L=>L.kind==='video').map(L=>L.cues.length));
  $('cols').innerHTML=Array.from({length:n},(_,j)=>`<button data-col="${j}" title="${j+1}番目のクリップを全部の動画レイヤーで一斉に再生">▶ ${j+1}</button>`).join('');
  $('cols').querySelectorAll('[data-col]').forEach(b=>b.onclick=()=>cmd({cmd:'go_column',cue:+b.dataset.col}));
}

// ---- レイヤーの行 ----
// 特別なクリップの表示名
// 同じ機種の2台目からは「名前 #1」…で届く → 「名前（2台目）」と表示
function camName(n){const m=/^(.*) #(\d+)$/.exec(n); return m?`${m[1]}（${+m[2]+1}台目）`:n;}
function cueInfo(c){
  const f=c.file||'';
  if(f==='@blank') return {cls:'blank',label:'空白',name:'（空白）'};
  if(f.startsWith('@layer:')){const k=S.layers.findIndex(L=>'@layer:'+L.id===f);
    return {cls:'layer',label:k>=0?`L${k+1}\n${S.layers[k].name}`:'（元のレイヤーなし）',name:k>=0?`L${k+1}の映像`:'レイヤー映像'};}
  if(f==='@camera') return {cls:'camera',label:'📷 '+camName(c.vdev||''),name:'カメラ'};
  if(f==='@window') return {cls:'window',label:'🗔 '+(c.title||''),name:'ウィンドウ'};
  if(f==='@spout') return {cls:'spout',label:'⇄ '+(c.sender||''),name:'Spout'};
  return null;
}
function clipTile(i,c,j){
  const sp=cueInfo(c);
  if(sp){
    const mics=(S.inputs||{}).mics||[];
    const adev=c.file==='@camera'?`<select data-adev="${j}" title="一緒に流す音声（キャプチャーボードの音声など）"><option value="">音声なし</option>${
      [...new Set([...mics,c.adev].filter(Boolean))].map(n=>`<option value="${esc(n)}" ${n===c.adev?'selected':''}>${esc(n)}</option>`).join('')}</select>`:'';
    return `<div class="clip" id="go${i}_${j}" data-j="${j}">
      <div class="th" draggable="true" data-cue="${j}" title="クリックで切り替え・ドラッグで並べ替え"><div class="sp ${sp.cls}">${esc(sp.label)}</div><span class="num">${j+1}</span></div>
      ${adev}
      <div class="tp"><button class="tb" data-tp="stop" title="消す（フェードアウト）">⏹</button><span class="tt" id="tt${i}_${j}"></span></div>
      <div class="cn"><span title="${esc(sp.name)}">${esc(sp.name)}</span><button class="x" data-rm="${j}" title="このクリップを削除">✕</button></div>
    </div>`;
  }
  const img=isImg(c.file);
  return `<div class="clip" id="go${i}_${j}" data-j="${j}">
    <div class="th" draggable="true" data-cue="${j}" title="${esc(c.file)}（クリックで再生・ドラッグで並べ替え）">${thumbImg(c.file)}<span class="num">${j+1}</span>
      <button class="md" data-md="${j}" title="終了時の動き：${MODE_NAME[c.mode]||''}（押すと切り替え）">${MODE_ICON[c.mode]||''}</button></div>
    ${img?'':`<div class="sc" title="ドラッグで再生位置を移動"><i id="sb${i}_${j}"></i></div>
    <div class="tp"><button class="tb" data-tp="restart" title="頭出し">⏮</button><button class="tb" data-tp="toggle" id="tg${i}_${j}" title="再生／一時停止">▶</button><button class="tb" data-tp="stop" title="停止（消す）">⏹</button><span class="tt" id="tt${i}_${j}"></span></div>`}
    <div class="cn"><span title="${esc(c.file)}">${esc(base(c.file))}</span><button class="x" data-rm="${j}" title="このクリップを削除">✕</button></div>
  </div>`;
}
function build(){
  const box=$('layers'); box.innerHTML='';
  S.layers.forEach((L,i)=>{
    const d=document.createElement('div'); d.className='lrow'; d.id='row'+i; d.dataset.layer=i; d.dataset.kind=L.kind;
    let track;
    if(L.kind==='video'){
      track=L.cues.map((c,j)=>clipTile(i,c,j)).join('')+
        `<div class="clip empty" data-j="${L.cues.length}"><div class="th"><button class="addblank" data-a="blank" title="何も映さないクリップを最後に追加（他のレイヤーが映している間、このレイヤーは休ませる）">＋ 空白を追加</button><small>素材は<br>ここへドラッグ</small></div></div>`;
    }else{
      track=`<div class="photo"><div class="row"><b>写真ウォール</b><span class="meta" id="pc${i}"></span><span class="grow"></span><button class="sm" data-a="skip">次の写真 ▶</button></div>
        <div class="meta">フォルダ：${esc(L.photo_folder)}　間隔：${L.photo_interval>0?L.photo_interval+'秒':'自動で切り替えない'}${L.photo_newest?'　新着優先':''}</div>
        <div class="meta">設定は右の「レイヤー設定」で変えられます</div></div>`;
    }
    d.innerHTML=`<div class="lhead">
        <div class="row" style="gap:6px;flex-wrap:nowrap"><span class="lname grow" title="${esc(L.name)}">${esc(L.name)}</span><span class="tag">L${i+1} ${L.kind==='photo'?'写真':'動画'}</span></div>
        <div class="now" id="now${i}">—</div><div class="bar"><i id="bar${i}"></i></div>
        ${L.kind==='video'?`<div class="lbtns"><button data-a="prev" title="前のクリップ">◀</button><button data-a="next" title="次のクリップ">次へ ▶</button><button data-a="clear" title="このレイヤーをフェードアウトして消す">消す</button></div>`:''}
        <div class="lbtns"><button data-a="vis" id="vis${i}" title="映す／隠す（音は止まらない）">表示</button>
          ${L.kind==='video'?`<button class="mut" data-a="mute" id="mut${i}" title="このレイヤーの音をミュート">M</button>`:''}
          <button data-ml="-1" title="奥へ（上の行へ）">▲</button><button data-ml="1" title="手前へ（下の行へ）">▼</button>
          <button data-a="del" title="このレイヤーを削除">🗑</button></div>
        <div class="lop">不透明度<input type="range" min="0" max="1" step="0.01" id="op${i}" data-k="opacity"></div>
        <div class="err" id="err${i}"></div>
      </div><div class="track">${track}</div>`;
    d.addEventListener('click',e=>{
      const t=e.target.closest('button,[data-cue]'); if(!t)return;
      if(t.dataset.tp) return cmd({cmd:'transport',layer:i,cue:+t.closest('.clip').dataset.j,op:t.dataset.tp});
      if(t.dataset.md!==undefined){e.stopPropagation(); const j=+t.dataset.md, m=S.layers[i].cues[j].mode; return cmd({cmd:'cue_mode',layer:i,cue:j,value:MODES[(MODES.indexOf(m)+1)%MODES.length]});}
      if(t.dataset.rm!==undefined){const c=S.layers[i].cues[+t.dataset.rm], sp=cueInfo(c);
        const lid=S.layers[i].id;
        if(confirm(`クリップ「${sp?sp.name:base(c.file)}」を削除しますか？`)) cmd({cmd:'remove_cue',layer:i,lid,cue:+t.dataset.rm,file:c.file}); return;}
      if(t.dataset.cue!==undefined) return cmd({cmd:'go',layer:i,cue:+t.dataset.cue});
      if(t.dataset.ml!==undefined) return cmd({cmd:'move_layer',layer:i,dir:+t.dataset.ml});
      const a=t.dataset.a;
      if(a==='next'||a==='prev'||a==='clear') cmd({cmd:a,layer:i});
      else if(a==='skip') cmd({cmd:'skip_photo',layer:i});
      else if(a==='blank') cmd({cmd:'add_cue',layer:i,file:'@blank'});
      // 表示・M は押した瞬間に見た目を切り替える（本体の返事を待つと、一瞬なにも起きないように見えるため）
      else if(a==='vis'){const v=!S.layers[i].visible; S.layers[i].visible=v; t.classList.toggle('on',v); t.classList.toggle('held',!v); t.textContent=v?'表示':'隠し中'; setv(i,'visible',v);}
      else if(a==='mute'){const v=!S.layers[i].audio; S.layers[i].audio=v; t.classList.toggle('on',!v); const m=$('mxlm'+i); if(m) m.classList.toggle('on',!v); setv(i,'audio',v);}
      else if(a==='del'){ const lid=S.layers[i].id; if(confirm(`レイヤー「${S.layers[i].name}」を削除しますか？`)) cmd({cmd:'remove_layer',layer:i,lid});}
    });
    const op=d.querySelector('#op'+i); op.addEventListener('input',()=>setv(i,'opacity',parseFloat(op.value)));
    d.querySelectorAll('[data-adev]').forEach(s=>s.onchange=()=>cmd({cmd:'cue_set',layer:i,cue:+s.dataset.adev,key:'adev',value:s.value}));
    d.addEventListener('mousedown',()=>{ if(S.selected!==i){S.selected=i; cmd({cmd:'select',layer:i});} });
    box.appendChild(d);
  });
  setupDnD(); setupScrub(); buildFolders(); buildMedia(); buildColumns(); lsetKey='';
}

// ---- 右：選んでいるレイヤーの設定 ----
function buildLset(){
  const i=S.selected, L=S.layers[i]; if(!L){$('lset').innerHTML='';return;}
  const key=JSON.stringify([i,rev,S.targets,L.places.map(p=>p.output)]);
  if(key===lsetKey||editingIn('#lset')) return; lsetKey=key;
  let h=`<div class="set">
    <span class="lbl">名前</span><input data-k="name" value="${esc(L.name)}">
    <span class="lbl">不透明度</span><input type="range" min="0" max="1" step="0.01" data-k="opacity" id="iop">
    <span class="lbl">合成方法</span><select data-k="blend">${S.blends.map(b=>`<option value="${b}" ${b===L.blend?'selected':''}>${BLEND_NAME[b]}</option>`).join('')}</select>
    ${S.targets.length>1?`<span class="lbl">映す出力</span><div class="chk">${S.targets.map(t=>{const on=L.places.some(p=>p.output===t.output), only=on&&L.places.length===1;
      return `<label><input type="checkbox" data-place="${t.output}" ${on?'checked':''} ${only?'disabled':''}>${esc(t.name)}</label>`;}).join('')}
      <span class="meta" style="flex-basis:100%">複数にチェックすると、同じ映像を同時に映します（位置合わせは出力ごと）</span></div>`:''}
    <span class="lbl">フェード</span><span class="row"><input type="number" min="0" max="30" step="0.1" data-k="fade" value="${L.fade}"><span class="meta">秒（切り替えにかける時間・音も同じ）</span></span>
    ${L.kind==='photo'?`
    <span class="lbl">フォルダ</span><input data-k="photo_folder" value="${esc(L.photo_folder)}">
    <span class="lbl">間隔(秒)</span><span class="row"><input type="number" step="0.5" min="0" data-k="photo_interval" value="${L.photo_interval}"><span class="meta">0＝自動で切り替えない</span></span>
    <span class="lbl">切り替え方</span><select data-k="photo_trans">${(S.transitions||[]).map(x=>`<option value="${x}" ${x===(L.photo_trans||'fade')?'selected':''}>${TRANS_NAME[x]||x}</option>`).join('')}</select>
    <span class="lbl">白フチ</span><input type="range" min="0" max="0.1" step="0.005" data-k="photo_border" value="${L.photo_border}">
    <span class="lbl"></span><label><input type="checkbox" data-k="photo_newest" ${L.photo_newest?'checked':''}> 新着を優先して表示</label>`:''}
    <span class="lbl">エッジぼかし</span><input type="range" min="0" max="0.25" step="0.005" data-k="feather" value="${L.feather}">
  </div>`;
  $('lset').innerHTML=h;
  // 作ったときのレイヤーに送る（入力の途中で別のレイヤーを選んでも、選び直した方を書き換えないように）
  const lid=L.id, idx=()=>{const k=S.layers.findIndex(x=>x.id===lid); return k>=0?k:-1;};
  $('lset').querySelectorAll('[data-place]').forEach(c=>c.onchange=()=>{const k=idx(); if(k>=0) cmd({cmd:'place',layer:k,output:+c.dataset.place,value:c.checked});});
  $('lset').querySelectorAll('[data-k]').forEach(el=>{
    const k=el.dataset.k, ev=(el.type==='range')?'input':'change';
    el.addEventListener(ev,()=>{
      const v=el.type==='checkbox'?el.checked:(el.type==='range'||el.type==='number')?parseFloat(el.value):(k==='output'?+el.value:el.value);
      const j=idx(); if(j>=0) setv(j,k,v);
    });
  });
}

// ---- 出力ウィンドウ ----
let outsKey='';
function buildOuts(){
  const box=$('outs'); if(!box)return;
  const key=JSON.stringify([S.outputs.map(o=>[o.name,o.size,o.fullscreen,o.monitor,o.join,o.overlap,o.gamma,o.host]),S.screens,S.out_warning,
    S.layers.map(L=>[L.name,L.places.map(p=>p.output)])]);
  if(key===outsKey||editingIn('#outs')) return; outsKey=key;
  const sc=S.screens||[];
  let x0=Math.min(...sc.map(m=>m.rect[0])),y0=Math.min(...sc.map(m=>m.rect[1]));
  let x1=Math.max(...sc.map(m=>m.rect[0]+m.rect[2])),y1=Math.max(...sc.map(m=>m.rect[1]+m.rect[3]));
  const sw=Math.max(1,x1-x0), sh=Math.max(1,y1-y0), pad=4;
  const scrHtml=sc.length?`<div class="scr" style="aspect-ratio:${sw}/${sh};width:min(100%,${Math.round(200*sw/sh)}px);margin:0 auto">${sc.map((m,k)=>{
      const [x,y,w,h]=m.rect, L=(x-x0)/sw*100, T=(y-y0)/sh*100, W=w/sw*100, H=h/sh*100;
      return `<div class="${m.outputs.length?'has':''} ${m.primary?'pri':''}" style="left:calc(${L}% + ${pad}px);top:calc(${T}% + ${pad}px);width:calc(${W}% - ${pad*2}px);height:calc(${H}% - ${pad*2}px)" title="${esc(m.name)}${m.win?'（Windows の番号 '+m.win+'）':''}">
        <b>${k+1}</b><small>${w}×${h}・${m.scale}%${m.hz?'・'+m.hz+'Hz':''}${m.primary?'・メイン':''}</small>${m.outputs.map(n=>`<em>${esc(n)}</em>`).join('')}</div>`;}).join('')}</div>`:'';
  // 映すレイヤー：出力（横長につないだものは1まとまり）ごとにチェック
  const layerChecks=t=>`<div class="chk">${S.layers.map((L,i)=>{const on=L.places.some(p=>p.output===t.output);
      const only=on&&L.places.length===1;
      return `<label title="${only?'このレイヤーの出力はここだけなので外せません（他の出力にチェックを入れてから外せます）':''}"><input type="checkbox" data-pl="${i}" data-po="${t.output}" ${on?'checked':''} ${only?'disabled':''}>L${i+1} ${esc(L.name)}</label>`;}).join('')}</div>`;
  box.innerHTML=`<div class="row"><b>出力ウィンドウ</b><span class="meta grow">画面 ${S.monitors} 台接続中</span>
      <button class="sm" id="oadd">＋出力を追加</button></div>
    ${S.outputs.map((o,k)=>{const head=o.host===k, t=S.targets.find(t=>t.output===o.host);
      const grp=S.outputs.filter(x=>x.host===o.host).length>1;
      return `<div class="orow">
      <span class="tag ${o.fullscreen?'on':''}">${esc(o.name)}</span>
      <span class="meta grow">${o.size[0]}×${o.size[1]}・画面${o.monitor+1}${o.monitor===0?'（メイン）':''}</span>
      <button class="${o.fullscreen?'on':''}" data-ofs="${k}">${o.fullscreen?'フルスクリーン中':'フルスクリーン'}</button>
      <button class="sm" data-oback="${k}" title="ウィンドウに戻してメイン画面の中央へ">手元に戻す</button>
      ${S.outputs.length>1?`<button class="sm" data-orm="${k}" title="この出力を削除">✕</button>`:''}</div>
      ${k>0?`<div class="wide"><label title="2台のプロジェクターで1枚の横長の映像にする"><input type="checkbox" data-join="${k}" ${o.join?'checked':''}> 出力${k}の右につなげて横長にする</label>
        ${o.join?`<span>重なり</span><input type="number" min="0" max="2000" step="10" data-ov="${k}" value="${o.overlap}"><span class="meta">px（ぼかしてつなぐ幅）</span>
          <span>ガンマ</span><input type="number" min="1" max="3" step="0.1" data-gm="${k}" value="${o.gamma}" title="出力${k}と出力${k+1}のつなぎ目の明るさがそろわないときに調整（つなぎ目が明るすぎる→大きく、暗すぎる→小さく）"><span class="meta">（このつなぎ目だけ）</span>`:''}</div>`:''}
      ${head&&grp?`<div class="wide"><b>${esc(t.name)}</b><span class="meta">${t.size[0]}×${t.size[1]}</span></div>`:''}
      ${head?`<div class="wide"><span class="meta">映すレイヤー：</span></div>${layerChecks(t)}`:''}`;}).join('')}
    ${S.out_warning?`<div class="err">${esc(S.out_warning)}</div>`:''}
    <div class="row" style="margin-top:4px"><b>画面の配置</b><span class="grow"></span>
      <button class="sm" id="ident" title="各画面の中央に番号を4秒間表示">識別</button>
      <button class="sm" id="dispset" title="Windows の「ディスプレイ」設定を開く">ディスプレイ設定…</button></div>
    ${scrHtml}
    <div class="hint">出力ウィンドウを投影したい画面へドラッグしてから「フルスクリーン」（出力ウィンドウ上の F キー・ダブルクリックでも）。緑の枠＝出力ウィンドウがある画面。プロジェクターは拡大率 100% が確実。</div>`;
  box.querySelectorAll('[data-ofs]').forEach(b=>b.onclick=()=>cmd({cmd:'out_fullscreen',output:+b.dataset.ofs}));
  box.querySelectorAll('[data-oback]').forEach(b=>b.onclick=()=>cmd({cmd:'out_bring_back',output:+b.dataset.oback}));
  box.querySelectorAll('[data-orm]').forEach(b=>b.onclick=()=>{ if(confirm(`${S.outputs[+b.dataset.orm].name} を削除しますか？（その出力のレイヤーは出力1に移ります）`)) cmd({cmd:'out_remove',output:+b.dataset.orm}); });
  box.querySelectorAll('[data-pl]').forEach(c=>c.onchange=()=>cmd({cmd:'place',layer:+c.dataset.pl,output:+c.dataset.po,value:c.checked}));
  box.querySelectorAll('[data-join]').forEach(c=>c.onchange=()=>cmd({cmd:'out_join',output:+c.dataset.join,value:c.checked}));
  box.querySelectorAll('[data-ov]').forEach(c=>c.onchange=()=>cmd({cmd:'out_blend',output:+c.dataset.ov,overlap:+c.value}));
  box.querySelectorAll('[data-gm]').forEach(c=>c.onchange=()=>cmd({cmd:'out_blend',output:+c.dataset.gm,gamma:+c.value}));
  $('oadd').onclick=()=>cmd({cmd:'out_add'});
  $('ident').onclick=()=>cmd({cmd:'identify'});
  $('dispset').onclick=()=>cmd({cmd:'open_display_settings'});
}

// ---- 音声ミキサー（音声マスター → 出力ごと → レイヤーごと）----
let mixKey='';
function buildMixer(){
  const box=$('mixer'); if(!box)return;
  const key=JSON.stringify([S.outputs.map(o=>o.name),S.layers.map(L=>[L.name,L.kind,L.output])]);
  if(key===mixKey) return; mixKey=key;
  const row=(cls,name,mid,fid,title)=>`<div class="mxr ${cls}"><span class="mn" title="${esc(title||name)}">${esc(name)}</span>
    <button class="sm mbtn" id="${mid}" title="ミュート">M</button><input type="range" min="0" max="1" step="0.01" id="${fid}"><span class="pct" id="${fid}p"></span></div>`;
  let h=`<div class="row"><b>音声ミキサー</b><span class="meta grow">映像の表示切替やブラックアウトでは音は止まりません</span></div><div class="mx">`;
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
  $('meta').textContent=`${S.backend} ・ 出力 ${S.outputs.length>1?S.outputs.length+'画面':S.size[0]+'×'+S.size[1]} ・ ${S.fps}fps${S.dirty?' ・ 未保存':''}`+
    ((S.lan_urls||[]).length?` ・ スマホ: ${S.lan_urls[0]}`:'');
  $('meta').title=(S.lan_urls||[]).length?'同じ Wi-Fi のスマホでこのアドレスを開くと、四隅の位置合わせができます\n'+S.lan_urls.join('\n'):'';
  if(document.activeElement!==$('master')) $('master').value=S.master;
  $('bo').classList.toggle('on',S.blackout);
  $('edit').classList.toggle('on',S.edit); $('mapedit').classList.toggle('on',S.edit); markCorner();
  $('msg').textContent=S.message||'';
  S.layers.forEach((L,i)=>{
    const row=$('row'+i); if(!row)return;
    row.classList.toggle('sel',i===S.selected);
    row.classList.toggle('hidden',!L.visible);
    {const b=$('vis'+i); b.classList.toggle('on',L.visible); b.classList.toggle('held',!L.visible); b.textContent=L.visible?'表示':'隠し中';}
    const mu=$('mut'+i); if(mu) mu.classList.toggle('on',!L.audio);
    $('now'+i).textContent=L.label?(`▶ ${L.label}`+(L.dur>0?`  ${fmt(L.pos)} / ${fmt(L.dur)}`:'')):'— 停止中';
    $('bar'+i).style.width=(L.dur>0?Math.min(100,L.pos/L.dur*100):0)+'%';
    $('err'+i).textContent=L.error||(L.kind==='photo'&&!L.photo_dir_ok?'フォルダが見つかりません':'');
    const op=$('op'+i); if(document.activeElement!==op) op.value=L.opacity;
    if(L.kind==='video') L.cues.forEach((c,j)=>{
      const b=$(`go${i}_${j}`); if(!b)return;
      const on=L.playing===j; b.classList.toggle('on',on); b.classList.toggle('paused',on&&L.paused);
      const bar=$(`sb${i}_${j}`); if(!bar)return;
      if(!(scrub&&scrub.L===i&&scrub.j===j)) bar.style.width=(on&&L.dur>0?Math.min(100,L.pos/L.dur*100):0)+'%';
      $(`tt${i}_${j}`).textContent=on&&L.dur>0?`${fmt(L.pos)}/${fmt(L.dur)}`:'';
      $(`tg${i}_${j}`).textContent=on&&!L.paused?'⏸':'▶';
    });
    if(L.kind==='photo'){const p=$('pc'+i); if(p) p.textContent=`${L.photo_count}枚`;}
  });
  const sel=S.layers[S.selected];
  $('mtarget').textContent=sel&&sel.kind==='video'?`クリックで「L${S.selected+1} ${sel.name}」に追加`:'動画レイヤーを選ぶと、クリックで追加できます';
  const tname=(S.targets.find(t=>t.output===S.map_out)||{}).name||'';
  $('mapsel').textContent=sel?`L${S.selected+1} ${sel.name}`+(S.outputs.length>1?`（${tname}・${S.size[0]}×${S.size[1]}）`:''):'';
  // マッピング：複数の出力に映しているレイヤーは、どの出力の四隅を直すか選ぶ
  const mk=JSON.stringify([S.selected,S.map_out,sel?sel.places.map(p=>p.output):[],S.targets.map(t=>t.name)]);
  if($('mtabs').dataset.k!==mk){$('mtabs').dataset.k=mk;
    $('mtabs').innerHTML=sel&&sel.places.length>1?'<span class="meta" style="align-self:center">位置合わせする出力：</span>'+sel.places.map(p=>`<button class="${p.output===S.map_out?'on':''}" data-mo="${p.output}">${esc((S.targets.find(t=>t.output===p.output)||{}).name||'')}</button>`).join(''):'';
    $('mtabs').querySelectorAll('[data-mo]').forEach(b=>b.onclick=()=>cmd({cmd:'map_out',output:+b.dataset.mo}));}
  const iop=$('iop'); if(iop&&sel&&document.activeElement!==iop) iop.value=sel.opacity;
  $('maptest').classList.toggle('on',!!(sel&&sel.test));
  if(!dragging) drawMap();
}

const COLORS=['#ff5a5a','#59ccff','#73ff73','#ffd94d','#d980ff','#4dffe6'];
function drawMap(){
  const cv=$('map'), g=cv.getContext('2d');
  const ar=S.size[1]/S.size[0]; cv.height=Math.round(cv.width*ar);
  const W=cv.width,H=cv.height;
  g.fillStyle='#000';g.fillRect(0,0,W,H);
  g.strokeStyle='#222';g.lineWidth=1;
  for(let k=1;k<8;k++){g.beginPath();g.moveTo(W*k/8,0);g.lineTo(W*k/8,H);g.moveTo(0,H*k/8);g.lineTo(W,H*k/8);g.stroke();}
  const so=S.map_out;
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
      const x=Math.min(W-9,Math.max(9,p[0]*W)), y=Math.min(H-9,Math.max(9,p[1]*H));  // 画面の端の角も見えるように
      g.fillStyle='#fff';g.fillRect(x-9,y-9,18,18);g.strokeStyle=col;g.lineWidth=3;g.strokeRect(x-9,y-9,18,18);});
      // 矢印キーで動かす所（赤）
      g.strokeStyle='#ff3b30'; g.lineWidth=4;
      if(S.sel_corner===4){g.beginPath(); c.forEach((p,k)=>k?g.lineTo(p[0]*W,p[1]*H):g.moveTo(p[0]*W,p[1]*H)); g.closePath(); g.stroke();}
      else if(c[S.sel_corner]){const p=c[S.sel_corner], x=Math.min(W-9,Math.max(9,p[0]*W)), y=Math.min(H-9,Math.max(9,p[1]*H)); g.fillStyle='#ff3b30'; g.fillRect(x-9,y-9,18,18);}}
    g.globalAlpha=1;
  });
}
function mapPos(e){const r=$('map').getBoundingClientRect();return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height];}
function inside(poly,x,y){let o=false;for(let i=0,j=3;i<4;j=i++){const[a,b]=poly[i],[c,d]=poly[j];if((b>y)!==(d>y)&&x<(c-a)*(y-b)/(d-b+1e-12)+a)o=!o;}return o;}
let lastSend=0;
$('map').addEventListener('pointerdown',e=>{
  if(!S)return; const [x,y]=mapPos(e); const cv=$('map');
  const L=S.layers[S.selected]; if(!L)return;
  const pl=L.places.find(p=>p.output===S.map_out); if(!pl)return;
  let best=-1,bd=1e9; pl.corners.forEach((p,k)=>{const d=Math.hypot((p[0]-x)*cv.clientWidth,(p[1]-y)*cv.clientHeight); if(d<bd){bd=d;best=k;}});
  const pts=pl.corners.map(p=>p.slice());
  // ドラッグ中に選択が変わっても、つかんだレイヤーを動かす（id で覚える）
  const base={pts,o:S.map_out,lid:L.id};
  if(bd<18) dragging={...base,k:best}; else if(inside(pts,x,y)) dragging={...base,k:-1,last:[x,y]}; else return;
  S.sel_corner=bd<18?best:4; post({cmd:'sel_corner',value:S.sel_corner}); markCorner();
  cv.setPointerCapture(e.pointerId);
});
$('map').addEventListener('pointermove',e=>{
  if(!dragging)return; const [x,y]=mapPos(e);
  if(dragging.k>=0) dragging.pts[dragging.k]=[x,y];
  else{const dx=x-dragging.last[0],dy=y-dragging.last[1]; dragging.pts.forEach(p=>{p[0]+=dx;p[1]+=dy;}); dragging.last=[x,y];}
  drawMap();
  const t=performance.now(), k=dragIdx(); if(k>=0&&t-lastSend>40){lastSend=t; post({cmd:'corners',layer:k,output:dragging.o,value:dragging.pts});}
});
function dragIdx(){return dragging?S.layers.findIndex(x=>x.id===dragging.lid):-1;}
function endDragMap(send){
  const d=dragging; dragging=null; if(!d) return;
  try{ const k=S.layers.findIndex(x=>x.id===d.lid); if(k<0) return;
    if(send) cmd({cmd:'corners',layer:k,output:d.o,value:d.pts});
    const pl=S.layers[k].places.find(p=>p.output===d.o); if(pl&&send) pl.corners=d.pts;
  }catch(_){}
  drawMap();
}
$('map').addEventListener('pointerup',()=>endDragMap(true));
$('map').addEventListener('pointercancel',()=>endDragMap(true));
$('map').addEventListener('lostpointercapture',()=>{ if(dragging) endDragMap(true); });

$('master').addEventListener('input',e=>cmd({cmd:'master',value:parseFloat(e.target.value)}));
$('bo').onclick=()=>cmd({cmd:'blackout',value:!S.blackout});
$('clearall').onclick=()=>cmd({cmd:'clear_all'});
$('edit').onclick=()=>cmd({cmd:'edit',value:!S.edit});
$('mapedit').onclick=()=>cmd({cmd:'edit',value:!S.edit});
$('save').onclick=()=>cmd({cmd:'save'});
$('quit').onclick=()=>{ if(confirm('VPT39 を終了しますか？（設定は保存されます）')) cmd({cmd:'quit'}); };
$('mfilter').addEventListener('input',()=>{ if(S) buildMedia(); });
$('mfolder').addEventListener('change',()=>{ if(S) buildMedia(); });
function setThumbSize(v){document.documentElement.style.setProperty('--mt',v+'px');}
$('msize').addEventListener('input',e=>{setThumbSize(e.target.value); lsSet('vpt39-msize-'+(VIEW||'main'),e.target.value);});
{const v=lsGet('vpt39-msize-'+(VIEW||'main')); if(v){$('msize').value=v; setThumbSize(v);} else if(VIEW==='media'){$('msize').value=170; setThumbSize(170);}}
$('addv').onclick=()=>cmd({cmd:'add_layer',kind:'video'});
$('addp').onclick=()=>cmd({cmd:'add_layer',kind:'photo'});
$('maptest').onclick=()=>setv(S.selected,'test',!S.layers[S.selected].test);
$('mapreset').onclick=()=>{ if(confirm('このレイヤーの四隅を、画面いっぱいの四角に戻しますか？')) cmd({cmd:'reset_corners',layer:S.selected,output:S.map_out}); };
document.addEventListener('keydown',e=>{
  if(!S)return;
  // 文字を打つ欄と選択欄だけはキーを奪わない。スライダーやボタンを触った直後でも Space / B / E が効く
  const a=document.activeElement, tag=a?a.tagName:'';
  if(tag==='TEXTAREA'||tag==='SELECT'||(tag==='INPUT'&&!/^(range|checkbox|button)$/.test(a.type)))return;
  if(e.ctrlKey||e.metaKey||e.altKey) return;  // Ctrl+B などはブラウザの操作に任せる
  const arrow=/^Arrow/.test(e.key);
  if(arrow&&!S.edit) return;  // 編集中でなければ矢印はスライダーなどに任せる
  if(!arrow&&e.repeat){ if(e.code==='Space') e.preventDefault(); return; }  // 押しっぱなしで何度も切り替えない（矢印は押しっぱなしで動かせる）
  if(tag==='INPUT'||tag==='BUTTON') a.blur();
  if(e.code==='Space'){e.preventDefault();cmd({cmd:'next',layer:0});}
  else if(e.key==='b'||e.key==='B') cmd({cmd:'blackout',value:!S.blackout});
  else if(e.key==='e'||e.key==='E') cmd({cmd:'edit',value:!S.edit});
  else if(S.edit&&/^Arrow/.test(e.key)){e.preventDefault();
    cmd({cmd:'nudge',dx:{ArrowLeft:-1,ArrowRight:1}[e.key]||0,dy:{ArrowUp:-1,ArrowDown:1}[e.key]||0,shift:e.shiftKey});}
});
function markCorner(){document.querySelectorAll('#cornsel [data-c]').forEach(b=>b.classList.toggle('on',+b.dataset.c===S.sel_corner));}
document.querySelectorAll('#cornsel [data-c]').forEach(b=>b.onclick=()=>{S.sel_corner=+b.dataset.c; cmd({cmd:'sel_corner',value:S.sel_corner}); markCorner(); drawMap(); b.blur();});

async function poll(){
  try{
    const r=await fetch('/api/state'); S=await r.json();
    const mkey=S.media.join('\n')+'|'+S.boot+'|'+((S.inputs||{}).mics||[]).join('|');
    applyDetached(); buildOuts(); buildMixer();
    if(S.rev!==rev||S.layers.length!==nlayers||mkey!==mediaKey){
      // ドラッグ中（別ウィンドウからのドラッグも）と、文字を入力している間は作り直さない
      const dragNow=dragActive||Date.now()-lastOver<800;
      if(!dragNow&&(!editingIn('.deck')||S.layers.length!==nlayers)){
        rev=S.rev; nlayers=S.layers.length; mediaKey=mkey; build();
      }
    }
    if(mkind==='s') buildMedia();
    buildLset();
    update();
  }catch(e){ $('meta').textContent='（本体と接続できません）'; if(VIEW) $('childbar').firstChild.textContent='VPT39（本体と接続できません）'; }
}
setInterval(poll,300); poll();
</script></body></html>
"""
