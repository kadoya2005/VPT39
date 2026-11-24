# VPT39 が使っている部品とライセンス

Python の部品は `setup.bat` を実行すると、公式の配布元（PyPI）から入ります。
VLC メディアプレイヤーは公式サイトから入れます。NDI ランタイムの同梱については下記を参照してください。

| 部品 | 使いみち | ライセンス |
|---|---|---|
| VLC メディアプレイヤー（libVLC） | 動画と音の再生 | LGPL 2.1 以降（libVLC）／ GPL 2（VLC アプリ） |
| python-vlc | Python から VLC を呼ぶ | LGPL 2.1 以降 |
| glfw | 出力ウィンドウ | MIT（GLFW 本体は zlib） |
| moderngl / glcontext | 映像の描画（OpenGL） | MIT |
| numpy | 映像・音のデータ処理 | BSD-3-Clause ほか |
| Pillow | 画像の読み込み | MIT-CMU（HPND） |
| opencv-python | VLC がないときの予備の再生・サムネイル | Apache 2.0（中の FFmpeg は LGPL） |
| sounddevice | カメラ・キャプチャーボードの音声 | MIT |
| windows-capture | ウィンドウ取り込み | MIT |
| SpoutGL | Spout の受け取り | BSD-3-Clause |
| NDI 6 Runtime（任意） | NDI 映像送信 | [NDI の配布条件](https://docs.ndi.video/all/developing-with-ndi/sdk/software-distribution) |
| pygrabber / comtypes | カメラの一覧 | MIT |
| qrcode | スマホで開くアドレスの QR コード | BSD |
| imageio-ffmpeg（tools のみ） | テスト用素材を作るときだけ | BSD-2-Clause（中の ffmpeg は GPL。VPT39 本体は使わない） |

## NDI について

`runtime/ndi` に公式の `Processing.NDI.Lib.x64.dll` と `Processing.NDI.Lib.Licenses.txt` がある場合、VPT39 はその DLL を使います。このフォルダは Git の管理対象外です。NDI のファイルには VPT39 本体の MIT ライセンスとは別の条件が適用されます。

第三者向けに DLL を同梱して配布する場合は、NDI SDK のライセンスと配布条件に従い、第三者ライブラリのライセンス文書も添付してください。

[NDI公式サイト](https://ndi.video/) · [SDKライセンス](https://docs.ndi.video/all/developing-with-ndi/sdk/licensing) · [配布条件](https://docs.ndi.video/all/developing-with-ndi/sdk/software-distribution) · [第三者ライブラリ](https://docs.ndi.video/all/developing-with-ndi/sdk/3rd-party-rights)

## VLC について
- VPT39 は、使う人のPCに入っている VLC（libVLC）を、実行するときに呼び出しているだけです。VLC のプログラムを改造したり、VPT39 に組み込んで配ったりはしていません。
- この使い方は LGPL の範囲で認められています。VPT39 を配ったり、有料の作品やイベントで使ったりしても問題ありません。
- 将来、VLC のファイル（libvlc.dll など）を VPT39 と一緒に配る場合は、VLC のライセンス文を同封し、VLC のソースコードの入手先を示す必要があります。

## 動画の形式（コーデック）について
- H.264・AAC などの形式には特許があります。ただし、VPT39 は再生を VLC や Windows に任せています。ふつうの使い方（作品の上映やライブ）で、使う人が手続きをする必要はありません。

## VPT39 本体
- VPT39 本体は **MIT ライセンス** です（全文は LICENSE）。上の部品は、それぞれの部品のライセンスのままです（VPT39 の MIT がそれらを変えることはありません）。
- マニュアルの画面写真は、テスト用の素材だけを使って撮っています。
- 初音ミクなどのキャラクター画像をサンプルに入れる場合は、`photos` のそのフォルダに描いた人と使ってよい範囲を書いた `クレジット.txt` を置いてください（キャラクター：© Crypton Future Media, INC.）。
