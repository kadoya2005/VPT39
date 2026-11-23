"""画面の言語（日本語 / English）

コードの中の文は日本語のまま書き、英語のときだけ下の辞書 EN で置き換える。
- 辞書のキーは、画面に出る日本語そのまま。{0} {1} … は「ここに何かが入る」の印
  （例：'ファイルが見つかりません: {0}' → 'File not found: {0}'）
- 操作パネル・スマホの画面は、表示するときにブラウザ側で置き換える（JS は SCRIPT）
- 出力ウィンドウの題名・識別の表示など Python で作る文は tr() で置き換える
- 選んだ言語は language.txt に保存（ない場合は Windows の表示言語で決める）
"""
import os
import re
import json

HERE = os.path.dirname(os.path.abspath(__file__))
LANG_FILE = os.path.join(HERE, 'language.txt')
LANGS = ('ja', 'en')

EN = {
    # ---- 上のバー ----
    'VPT39 操作パネル': 'VPT39 Control Panel',
    '全レイヤー明るさ': 'All-layer brightness',
    '全レイヤー再生消去': 'Stop playback on all layers',
    'マッピング編集': 'Edit mapping',
    'スマホから四隅を合わせる（オン・オフと、スマホで読み取る QR コード）':
        'Adjust corners from a phone (on/off and the QR code to scan)',
    'スマホ': 'Phone',
    'スマホ ON': 'Phone ON',
    'スマホ（つながらない）': 'Phone (no network)',
    '今すぐ保存': 'Save now',
    '終了': 'Quit',
    '（本体と接続できません）': '(cannot connect to VPT39)',
    'VPT39（本体と接続できません）': 'VPT39 (cannot connect to VPT39)',
    '{#0}画面': '{0} outputs',
    '未保存': 'unsaved',
    '日本語に切り替え': 'Switch to Japanese (日本語)',

    # ---- セッティング ----
    'セッティング': 'Setup',
    '会場・公演ごとの設定ファイル（曲順・クリップ・レイヤー・マッピング・音量・出力の設定）':
        'A setup file for each venue or show (running order, clips, layers, mapping, volume and outputs)',
    '閉じる': 'Close',
    '新しい名前（例：2026-12 ライブ本番）': 'New name (e.g. 2026-12 live show)',
    '名前を付けて保存': 'Save as',
    'セッティングを開く': 'Open setup',
    'ファイルから開く…': 'Open from file…',
    'settings フォルダのセッティングが一覧に表示されます。開いているセッティングは変更すると自動保存されます。':
        'The list shows setups in the settings folder. Changes to the open setup are saved automatically.',
    'settings フォルダに保存したセッティングはありません。': 'There are no setups in the settings folder.',
    'ファイル選択画面を開けませんでした（{0}）': 'Could not open the file dialog ({0})',
    'セッティングには project.json とは別の JSON ファイルを選んでください':
        'Choose a JSON file other than project.json for the setup.',
    '開いているセッティングには、変えると2秒後に自動で上書き保存されます。ファイルは VPT39\\settings フォルダにあり、Dropbox で同期すれば別のPCでも同じ一覧が使えます。別のPCで準備するときは、「出力」タブの「本番の解像度」を本番のプロジェクターに合わせておきます。':
        'Changes to the open setup are saved automatically 2 seconds later. The files are in the VPT39\\settings folder; '
        'sync it with Dropbox to use the same list on another PC. When you prepare on another PC, set "Show resolution" '
        'in the "Outputs" tab to match the projector used in the show.',
    'いま開いている：<b style="color:var(--acc)">{0}</b>（変えると自動で上書き保存）':
        'Open now: <b style="color:var(--acc)">{0}</b> (changes are saved automatically)',
    'いまの状態には名前が付いていません。残しておくなら、名前を付けて保存してください。':
        'The current state has no name. To keep it, use "Save as".',
    '開いている': 'open',
    '開く': 'Open',
    '複製': 'Duplicate',
    'このセッティングを元に、新しい名前で作る': 'Make a new setup from this one',
    'ごみ箱へ移す': 'Move to the Recycle Bin',
    'まだありません。上で名前を入れて「名前を付けて保存」。': 'None yet. Enter a name above and press "Save as".',
    'いまの「{0}」は保存されています。': 'The current "{0}" is saved.',
    'いまの状態には名前がないので、開くと置き換わります（残すなら先に名前を付けて保存）。':
        'The current state has no name, so it will be replaced (to keep it, use "Save as" first).',
    'セッティング「{0}」を開きますか？\n\n{1}\nレイヤー・クリップ・マッピング・出力ウィンドウが切り替わります。':
        'Open the setup "{0}"?\n\n{1}\nThe layers, clips, mapping and output windows will change.',
    '複製は、いま開いているセッティングから作ります。先に「{0}」を開いてください。':
        'A duplicate is made from the setup that is open now. Open "{0}" first.',
    '新しい名前（いまの内容をこの名前で保存して、こちらを開いた状態にします）':
        'New name (the current content is saved under this name, and it becomes the open setup)',
    '{0} のコピー': '{0} copy',
    'セッティング「{0}」をごみ箱へ移しますか？（ごみ箱から戻せます）':
        'Move the setup "{0}" to the Recycle Bin? (You can restore it from there.)',
    'セッティング「{0}」をごみ箱へ移しますか？（ごみ箱から戻せます）\n\nいま開いているセッティングです。いまの状態は残りますが、名前なしになります。':
        'Move the setup "{0}" to the Recycle Bin? (You can restore it from there.)\n\n'
        'This setup is open now. The current state stays, but it will have no name.',
    '\\ / : * ? " < > | は名前に使えません': '\\ / : * ? " < > | cannot be used in a name',
    '「{0}」はもうあります。いまの状態で上書きしますか？': '"{0}" already exists. Overwrite it with the current state?',
    'VPT39 を終了しますか？（設定は保存されます）': 'Quit VPT39? (The settings are saved.)',
    'セッティング「{0}」に保存しました': 'Saved to the setup "{0}"',
    'セッティング「{0}」をごみ箱へ移しました': 'Moved the setup "{0}" to the Recycle Bin',
    'セッティング「{0}」を開きました': 'Opened the setup "{0}"',
    'セッティング「{0}」を開きました（{1}）': 'Opened the setup "{0}" ({1})',
    'セッティングが見つかりません: {0}': 'Setup not found: {0}',
    'その名前は使えません（\\ / : * ? " < > | は使えない・60文字まで）':
        'This name cannot be used (no \\ / : * ? " < > |, up to 60 characters)',
    '「{0}」はもうあります（上書きするときは確認のあと保存）': '"{0}" already exists (to overwrite, confirm and save)',
    '開けませんでした: {0}（{1}）': 'Could not open: {0} ({1})',
    'ごみ箱へ移せませんでした: {0}': 'Could not move to the Recycle Bin: {0}',
    'VPT39 の設定ファイルではありません': 'This is not a VPT39 setup file',
    '保存しました': 'Saved',
    '設定を保存できませんでした。少しあとでもう一度保存します': 'Could not save the settings. Trying again shortly.',
    'snapshot.png を保存しました': 'Saved snapshot.png',

    # ---- スマホ（操作パネル側） ----
    'スマホから操作': 'Control from a phone',
    'スクリーンの前で四隅を合わせる（セッティング）': 'Adjust the corners in front of the screen (setup mode)',
    'スマホからの操作をやめる': 'Stop phone control',
    'スマホから操作する': 'Control from a phone',
    'オン：同じ Wi-Fi のスマホから開けます': 'On: a phone on the same Wi-Fi can open it',
    'オンですが、このPCのアドレスが見つかりません（Wi-Fi を確認）': 'On, but this PC has no network address (check the Wi-Fi)',
    'オフ：このPCの中からだけ操作できます': 'Off: VPT39 can be controlled only from this PC',
    'QR コード': 'QR code',
    'Wi-Fi を変えたときなど': 'For example, after changing the Wi-Fi',
    'アドレスを調べ直す': 'Find the address again',
    '① PC とスマホを<b>同じ Wi-Fi</b> につなぐ　② スマホのカメラで QR コードを読み取る（または上のアドレスを入れる）　③ 開いたページで四隅を合わせる。<br>':
        '① Connect the PC and the phone to <b>the same Wi-Fi</b>&nbsp; ② Scan the QR code with the phone camera '
        '(or type the address above)&nbsp; ③ Adjust the corners on the page that opens.<br>',
    'QR コードのアドレスには合言葉が入っていて、それを読み取ったスマホだけが操作できます（スマホの画面の操作だけ。素材の削除などは PC からだけ）。合言葉は、オンにし直したときと VPT39 を起動し直したときに変わります（スマホで QR を読み直す）。自分のルーターかスマホのテザリングで使い、終わったら「やめる」を押してください。会場・学校・ホテルの Wi-Fi ではつながらないことがあります。':
        'The QR code address contains a key, so only phones that scanned it can control VPT39 (only what the phone page does; '
        'deleting media and so on works only from the PC). The key changes when you turn this on again or restart VPT39 '
        '(scan the QR code again). Use your own router or phone tethering, and press "Stop" when you are done. '
        'Venue, school or hotel Wi-Fi may not connect.',
    '押すと、同じ Wi-Fi のスマホから四隅の位置合わせができるようになります（起動し直す必要はありません）。初めてのときは Windows の「セキュリティの重要な警告」が出るので、「プライベート ネットワーク」にチェックして「アクセスを許可する」を押してください。':
        'Press it to adjust the corners from a phone on the same Wi-Fi (no restart needed). The first time, Windows shows '
        'a "Windows Security Alert": check "Private networks" and press "Allow access".',
    'スマホ用の受け口を開けませんでした（Wi-Fi・有線LAN につながっているか確認）':
        'Could not open the connection for phones (check that the PC is on Wi-Fi or wired LAN)',

    # ---- レイヤー ----
    '{#0}番目のクリップを全部の動画レイヤーで一斉に再生': 'Play clip {0} on all video layers at once',
    '＋動画レイヤー': '+ Video layer',
    '＋写真ウォール': '+ Photo wall',
    '右クリックでレイヤーを追加': 'Right-click to add a layer',
    '写真': 'Photo',
    '動画': 'Video',
    'L{#0} {1}': 'L{0} {1}',
    'L{#0}\n{1}': 'L{0}\n{1}',
    '— 停止中': '— stopped',
    '前のクリップ': 'Previous clip',
    '次のクリップ': 'Next clip',
    '次へ ▶': 'Next ▶',
    'このレイヤーをフェードアウトして消す': 'Fade out this layer',
    '消す': 'Clear',
    '映す／隠す（音は止まらない）': 'Show / hide (the sound keeps playing)',
    '表示': 'Show',
    '隠し中': 'Hidden',
    'このレイヤーの音をミュート': 'Mute this layer',
    '奥へ（上の行へ）': 'Back (move up a row)',
    '手前へ（下の行へ）': 'Front (move down a row)',
    '重なり順': 'Layer order',
    '{#0}（一番奥）': '{0} (back)',
    '{#0}（一番手前）': '{0} (front)',
    'このレイヤーを削除': 'Delete this layer',
    '不透明度': 'Opacity',
    'レイヤー「{0}」を削除しますか？': 'Delete the layer "{0}"?',
    'クリップ「{0}」を削除しますか？': 'Delete the clip "{0}"?',
    '何も映さないクリップを最後に追加（他のレイヤーが映している間、このレイヤーは休ませる）':
        'Add an empty clip at the end (this layer rests while other layers play)',
    '＋ 空白を追加': '+ Add blank',
    '素材は': 'Drag media',
    'ここへドラッグ': 'here',
    'ファイルを': 'Drop files',
    'ここへドロップ': 'here',
    '画像を': 'Drag images',
    '（入れないときは': '(if empty,',
    'フォルダの写真）': 'photos in the folder)',
    '次の写真 ▶': 'Next photo ▶',
    '写真を再生': 'Play photos',
    '一時停止': 'Pause',
    '先頭から再生': 'Play from start',
    '再生終了': 'Playback finished',
    '写真の再生設定': 'Photo playback settings',
    '写真1枚の表示時間（秒）': 'Time per photo (seconds)',
    '0＝定期切り替えなし': '0 = no timed advance',
    'ループ再生': 'Loop playback',
    'この行の画像1枚を出し続けます': 'Shows the one image in this row',
    'この行の画像を左から順に出します（最後まで行ったら最初へ）':
        'Shows the images in this row from left to right (then starts again)',
    'フォルダ：{0}': 'Folder: {0}',
    '　新着優先': '  newest first',
    '間隔：{0}': 'Interval: {0}',
    '{#0}秒': '{0} s',
    '自動で切り替えない': 'no automatic change',
    '　列で再生とは別に動きます': '  runs separately from "Play column"',
    '間隔：{0}　列で再生とは別に動きます': 'Interval: {0}  runs separately from "Play column"',
    '{#0}枚': '{0} photos',
    'フォルダが見つかりません': 'Folder not found',
    '{0}（押すとすぐ出す・ドラッグで並べ替え）': '{0} (click to show now, drag to reorder)',
    'この画像を写真ウォールから外す': 'Remove this image from the photo wall',
    '一緒に流す音声（キャプチャーボードの音声など）': 'Audio to play with it (such as the capture board audio)',
    '音声なし': 'No audio',
    'クリックで切り替え・ドラッグで並べ替え': 'Click to switch, drag to reorder',
    '消す（フェードアウト）': 'Clear (fade out)',
    'このクリップを削除': 'Delete this clip',
    '{0}（クリックで再生・ドラッグで並べ替え）': '{0} (click to play, drag to reorder)',
    '終了時の動き：{0}（押すと切り替え）': 'At the end: {0} (click to change)',
    'ドラッグで再生位置を移動': 'Drag to change the playback position',
    '頭出し': 'Restart',
    '再生／一時停止': 'Play / pause',
    '停止（消す）': 'Stop (clear)',
    'ループ': 'Loop',
    '最後で停止': 'Hold last frame',
    '終了で消える': 'Clear at end',
    '次へ進む': 'Go to next',
    '写真ウォールに入れられるのは画像だけです': 'Only images can go on a photo wall',
    '写真ウォールに入れられるのは画像だけです（動画・カメラなどは動画レイヤーへ）':
        'Only images can go on a photo wall (put videos, cameras and so on on a video layer)',
    '自分自身の映像は追加できません（別のレイヤーに追加してください）':
        'A layer cannot show itself (add it to another layer)',

    # ---- 素材 ----
    '素材': 'Media',
    'ファイル': 'Files',
    '空白・レイヤー・入力': 'Blank, layers, inputs',
    'フォルダで絞り込み': 'Filter by folder',
    'すべてのフォルダ': 'All folders',
    '（media の直下）': '(top of media)',
    'ファイル名で絞り込み': 'Filter by file name',
    'サムネイルの大きさ': 'Thumbnail size',
    'レイヤーの行へドラッグすると、好きな位置に追加。クリックで選んでいるレイヤーの最後に追加。パソコンの動画・画像をここへドロップすると media フォルダに取り込みます。✕ でごみ箱へ。':
        'Drag onto a layer row to add it at any position. Click to add it at the end of the selected layer. '
        'Drop videos or images from your PC here to copy them into the media folder. ✕ moves to the Recycle Bin.',
    'クリックで「L{#0} {1}」に追加': 'Click to add to "L{0} {1}"',
    '画像をクリックで写真ウォール「L{#0} {1}」に追加': 'Click an image to add it to the photo wall "L{0} {1}"',
    'レイヤーを選ぶと、クリックで追加できます': 'Select a layer, then click to add',
    '{#0}件': '{0} items',
    '{#0} / {#1}件': '{0} / {1} items',
    'この素材をごみ箱へ移す（ごみ箱から元に戻せます）': 'Move this file to the Recycle Bin (you can restore it from there)',
    'media フォルダに素材がありません。動画や画像を入れると数秒で表示されます。':
        'There is nothing in the media folder. Videos and images you add appear within a few seconds.',
    '「{0}」をごみ箱へ移しますか？\n\n（ごみ箱から元に戻せます）': 'Move "{0}" to the Recycle Bin?\n\n(You can restore it from there.)',
    '「{0}」をごみ箱へ移しますか？\n\nこの素材は {1} 個のクリップで使っています。クリップは残りますが、再生すると「ファイルが見つかりません」になります。\n\n（ごみ箱から元に戻せます）':
        'Move "{0}" to the Recycle Bin?\n\n{1} clips use this file. The clips stay, but they will show "File not found" '
        'when played.\n\n(You can restore it from there.)',
    '削除できませんでした': 'Could not delete',
    '取り込み中です。終わってからもう一度ドロップしてください': 'Still copying. Drop again when it is finished.',
    '差し替えるファイルを1つだけドロップしてください': 'Drop one file to replace this clip.',
    '{0} を素材に保存しましたが、レイヤーに反映できませんでした': 'Copied {0}, but could not update the layer.',
    '素材に保存しましたが、追加先のレイヤーが見つかりません': 'Copied the media, but the target layer no longer exists.',
    '取り込んだ素材が見つかりません': 'The imported media could not be found.',
    'クリップを追加しました': 'Clip added.',
    '素材に保存しましたが、クリップが変わっていたため差し替えませんでした': 'Copied the media. The target clip changed during the import and was left intact.',
    'クリップを差し替えました': 'Clip replaced.',
    '空き枠': 'Empty slot',
    '末尾にコラムを追加': 'Add a column at the end',
    'このコラムの前に追加': 'Insert a column before this one',
    'このコラムの後に追加': 'Insert a column after this one',
    'このコラムを再生': 'Play this column',
    'コラム名を変更': 'Rename column',
    'コラム名（空欄で番号のみ）': 'Column name (leave blank to show only the number)',
    'レイヤー名を変更': 'Rename layer',
    'レイヤー名': 'Layer name',
    'クリップを再生': 'Play clip',
    '右クリックでコラムの操作': 'Right-click for column actions',
    'デッキ {#0}': 'Deck {0}',
    'このデッキの前に追加': 'Insert a deck before this one',
    'このデッキの後に追加': 'Insert a deck after this one',
    'デッキ名': 'Deck name',
    'デッキ名を変更': 'Rename deck',
    'このデッキを削除': 'Delete this deck',
    'デッキ「{0}」とその中のクリップを削除しますか？': 'Delete deck “{0}” and its clips?',
    'デッキが切り替わりました。もう一度操作してください': 'The active deck changed. Try again.',
    'デッキ一覧が変わりました。もう一度操作してください': 'The deck list changed. Try again.',
    '素材に保存しましたが、操作先のデッキが切り替わりました': 'The media was imported, but the destination deck is no longer selected.',
    '動画レイヤーを追加': 'Add video layer',
    '写真ウォールを追加': 'Add photo wall',
    '空白クリップを追加': 'Add blank clip',
    '操作メニュー': 'Actions menu',
    'このコラムを削除': 'Delete this column',
    'コラム {0} を削除しますか？ 全動画レイヤーのこの列が消えます。': 'Delete column {0} from all video layers?',
    'コラムが変わりました。もう一度操作してください': 'The columns changed. Try again.',
    'レイヤー一覧が変わりました。もう一度操作してください': 'The layer list changed. Try again.',
    'ドラッグ中にクリップが変わったため、移動できませんでした': 'A clip changed during the drag, so it was left in place.',
    'クリップ設定': 'Clip settings',
    '反転': 'Flip',
    '左右反転': 'Flip horizontally',
    '上下反転': 'Flip vertically',
    '設定を送れませんでした': 'Could not send the settings.',
    '映像': 'Video',
    '音声': 'Audio',
    '設定': 'Settings',
    '設定 ▾': 'Settings ▾',
    '出力設定': 'Output settings',
    '出力FPSの上限': 'Output FPS limit',
    '横長につないだ先頭の出力に合わせる': 'Uses the first output’s FPS limit',
    '実測': 'Measured',
    'マッピング編集を終了': 'Exit mapping edit',
    '別タブで開く ↗': 'Open in new tab ↗',
    '右側のパネルを閉じる': 'Close the right panel',
    'メイン画面へ': 'Main panel',
    '音量 (%)': 'Volume (%)',
    'フェードイン (秒)': 'Fade in (s)',
    'フェードアウト (秒)': 'Fade out (s)',
    'キャンセル': 'Cancel',
    '適用': 'Apply',
    '挿入先が変わったため、コラムを追加できませんでした': 'The insertion point changed before the column could be added.',
    '動画・画像のファイルではありません': 'These are not video or image files',
    '取り込み中 {0}/{1}：{2}（{3}%）': 'Copying {0}/{1}: {2} ({3}%)',
    '{0} を取り込めませんでした': 'Could not copy {0}',
    '取り込みました：{0} 件': 'Copied: {0}',
    '取り込みました：{0} 件（動画・画像でない {1} 件は飛ばしました）': 'Copied: {0} (skipped {1} that are not videos or images)',
    '取り込みました：{0} 件→ {1}': 'Copied: {0} → {1}',
    '取り込みました：{0} 件（動画・画像でない {1} 件は飛ばしました）→ {2}':
        'Copied: {0} (skipped {1} that are not videos or images) → {2}',
    '動画・画像ではないので取り込みません: {0}': 'Not a video or image, so it was not copied: {0}',
    '大きさが分かりません': 'Unknown size',
    '保存先が media フォルダの外です': 'The destination is outside the media folder',
    'media フォルダの素材ではありません': 'This is not a file in the media folder',
    'ファイルが見つかりません': 'File not found',
    'ごみ箱へ移せませんでした: {0}（再生中・他のアプリで開いているなど）':
        'Could not move to the Recycle Bin: {0} (it may be playing or open in another app)',
    '保存できませんでした: {0}（{1}）': 'Could not save: {0} ({1})',
    '要求が読めません': 'Could not read the request',
    '途中で切れました': 'The transfer was cut off',
    '何も映さない': 'Show nothing',
    '空白': 'Blank',
    '空白（何も映さない・音も止める）': 'Blank (shows nothing and stops the sound)',
    '（空白）': '(blank)',
    'レイヤーの映像（同じ映像を同じタイミングで、別の場所・別の出力に）':
        'Layer picture (the same picture at the same time, in another place or output)',
    'L{#0} {1} の映像': 'Picture of L{0} {1}',
    'L{#0}の映像': 'Picture of L{0}',
    'レイヤー映像': 'Layer picture',
    '（元のレイヤーなし）': '(no source layer)',
    'カメラ・キャプチャーボード': 'Cameras and capture boards',
    '調べています…': 'Searching…',
    '一覧を更新': 'Refresh list',
    '見つかりません（つないでから「一覧を更新」）': 'None found (connect, then press "Refresh list")',
    'ウィンドウ（映像のみ。最小化すると止まることがあります）': 'Windows (picture only; may stop when minimized)',
    'ありません': 'None',
    'Spout（TouchDesigner・OBS・VJアプリなどから。遅れなし・透明あり）':
        'Spout (from TouchDesigner, OBS, VJ apps and so on; no delay, with transparency)',
    '送られていません（送る側で Spout 出力をオンにしてから「一覧を更新」）':
        'Nothing is being sent (turn on Spout output in the sending app, then press "Refresh list")',
    '{0}（{#1}台目）': '{0} (#{1})',
    'カメラ': 'Camera',
    'ウィンドウ': 'Window',
    '自分自身の映像は使えません': 'A layer cannot show itself',
    '自分自身の映像は追加できません': 'A layer cannot show itself',

    # ---- タブ・別ウィンドウ ----
    'レイヤー設定': 'Layer settings',
    '音声ミキサー': 'Audio mixer',
    '出力ウィンドウ': 'Output windows',
    'ミキサー': 'Mixer',
    '出力': 'Outputs',
    '{0}（別ウィンドウ）': '{0} (separate window)',
    'このウィンドウを閉じると、メイン画面のタブに戻ります': 'Close this window to put it back as a tab in the main screen',
    'メイン画面に戻す': 'Back to main screen',
    '別ウィンドウで開いています（押すとそのウィンドウを前に出します）':
        'Open in a separate window (click to bring it to the front)',
    '押すと切り替え／ウィンドウの外へドラッグすると別ウィンドウ': 'Click to switch / drag out of the window to open it separately',
    '別ウィンドウで開く': 'Open in a separate window',
    '別ウィンドウを開けませんでした（ブラウザのポップアップブロックを許可してください）':
        'Could not open a separate window (allow pop-ups for this page in the browser)',

    # ---- レイヤー設定 ----
    'マッピング': 'Mapping',
    '編集': 'Edit',
    '編集を終了': 'Finish editing',
    'テスト表示': 'Test pattern',
    '四隅リセット': 'Reset corners',
    '矢印キーで動かす所：': 'Arrow keys move:',
    '角をドラッグして変形、内側をドラッグして全体を移動。': 'Drag a corner to reshape the image, or drag inside to move the whole image.',
    'このレイヤーの映像と音を消す': 'Stop video and audio on this layer',
    '左上': 'Top left',
    '右上': 'Top right',
    '右下': 'Bottom right',
    '左下': 'Bottom left',
    '全体': 'All',
    '位置合わせする出力：': 'Output to adjust:',
    'このレイヤーの四隅を、画面いっぱいの四角に戻しますか？': "Reset this layer's corners to fill the screen?",
    '名前': 'Name',
    '合成方法': 'Blend mode',
    '通常': 'Normal',
    '加算': 'Add',
    'スクリーン': 'Screen',
    '乗算': 'Multiply',
    '映す出力': 'Outputs',
    '複数にチェックすると、同じ映像を同時に映します（位置合わせは出力ごと）':
        'Check more than one to show the same picture on each (mapping is set per output)',
    'このレイヤーの出力はここだけなので外せません（他の出力にチェックを入れてから外せます）':
        'This is the only output of this layer, so it cannot be unchecked (check another output first)',
    'フェード': 'Fade',
    '秒（切り替えにかける時間・音も同じ）': 's (time for the change; the sound follows)',
    'フォルダ': 'Folder',
    '間隔(秒)': 'Interval (s)',
    '0＝自動で切り替えない': '0 = no automatic change',
    '切り替え方': 'Transition',
    'フェード（じわっと）': 'Fade (gradual)',
    '静止（効果なし・パッと切り替え）': 'Cut (no effect, instant)',
    'スライド（右から左へ）': 'Slide (right to left)',
    'スライド（左から右へ）': 'Slide (left to right)',
    'スライド（下から上へ）': 'Slide (bottom to top)',
    'スライド（上から下へ）': 'Slide (top to bottom)',
    'ワイプ（左から順に）': 'Wipe (from the left)',
    'ズーム（少し寄りから）': 'Zoom (from slightly closer)',
    '白フチ': 'White border',
    '新着を優先して表示': 'Show the newest first',
    'エッジぼかし': 'Edge feather',

    # ---- 出力 ----
    '画面 {#0} 台接続中': 'Displays connected: {0}',
    '＋出力を追加': '+ Add output',
    '{0}×{1}・画面{#2}': '{0}×{1} · display {2}',
    '{0}×{1}・画面{#2}（メイン）': '{0}×{1} · display {2} (main)',
    'フルスクリーン中': 'Fullscreen',
    'フルスクリーン': 'Fullscreen',
    'ウィンドウに戻してメイン画面の中央へ': 'Back to a window in the middle of the main display',
    '手元に戻す': 'Bring back',
    'この出力を削除': 'Delete this output',
    '{0} を削除しますか？（その出力のレイヤーは出力1に移ります）': 'Delete {0}? (Its layers move to Output 1.)',
    '本番で使うプロジェクターの解像度。決めておくと、準備用の別のPC（小さいウィンドウ）でも本番と同じ大きさで描くので、四隅や重なりがそのまま使える':
        'The resolution of the projector used in the show. With this set, another PC used for preparation '
        '(a small window) draws at the same size, so the corners and overlaps can be used as they are.',
    '本番の解像度': 'Show resolution',
    '自動（ウィンドウの大きさ・いま {0}×{1}）': 'Auto (window size, now {0}×{1})',
    'いまの画面の大きさにする': 'Use the current window size',
    '自分で入れる…': 'Enter…',
    '（いまのウィンドウ {0}×{1} に縮小して表示）': '(scaled down to the current window, {0}×{1})',
    '本番の解像度（幅×高さ）を入れてください。例：1920x1080': 'Enter the show resolution (width x height). Example: 1920x1080',
    '2台のプロジェクターで1枚の横長の映像にする': 'Make one wide picture with two projectors',
    '出力{#0}の右につなげて横長にする': 'Join to the right of Output {0} (wide)',
    '重なり': 'Overlap',
    'px（ぼかしてつなぐ幅）': 'px (width of the soft blend)',
    'ガンマ': 'Gamma',
    '出力{#0}と出力{#1}のつなぎ目の明るさがそろわないときに調整（つなぎ目が明るすぎる→大きく、暗すぎる→小さく）':
        'Adjust when the seam between Output {0} and Output {1} is uneven (seam too bright → larger, too dark → smaller)',
    '（このつなぎ目だけ）': '(this seam only)',
    '映すレイヤー：': 'Layers shown:',
    '画面の配置': 'Display layout',
    '識別': 'Identify',
    '各画面の中央に番号を4秒間表示': 'Show a number in the middle of each display for 4 seconds',
    'ディスプレイ設定…': 'Display settings…',
    'Windows の「ディスプレイ」設定を開く': 'Open the Windows "Display" settings',
    '出力ウィンドウを投影したい画面へドラッグしてから「フルスクリーン」（出力ウィンドウ上の F・F11・ダブルクリックでも）。緑の枠＝出力ウィンドウがある画面。プロジェクターは拡大率 100% が確実。':
        'Drag the output window to the display you want, then choose "Fullscreen" (or use F, F11, or double-click on the '
        'output window). Green frame = a display with an output window. Scale 100% is the safest for projectors.',
    '出力{#0}': 'Output {0}',
    '（横長）': ' (wide)',
    '＋': '+',
    '（フルスクリーン）': ' (fullscreen)',
    'メイン': 'main',
    '（メイン）': ' (main)',
    '（Windows の番号 {0}）': ' (Windows number {0})',
    '{0}（Windows の番号 {#1}）': '{0} (Windows number {1})',
    '横長': 'wide',
    '拡大率 {0}%': 'scale {0}%',
    'Windows の番号 {0}': 'Windows number {0}',
    '{0}×{1}・拡大率 {2}%': '{0}×{1} · scale {2}%',
    '・Windows の番号 {0}': ' · Windows number {0}',
    '識別を表示できませんでした（{0}）': 'Could not show the display numbers ({0})',
    'ディスプレイ設定を開けませんでした（{0}）': 'Could not open the display settings ({0})',
    '画面{#0}が見つからないため、出力{#1}はウィンドウで開きました':
        'Display {0} was not found, so Output {1} opened as a window',
    '{0}：前回フルスクリーンにしていた画面が見つからないため、ウィンドウで表示しています':
        '{0}: the display it was fullscreen on last time was not found, so it is shown as a window',
    'VPT39 {0}（投影したい画面へ移動して「フルスクリーン」／F・F11／ダブルクリック）':
        'VPT39 {0} (move it to the display you want, then choose "Fullscreen", use F or F11, or double-click)',
    '出力ウィンドウは×では閉じません。減らすときは操作パネルの「出力」から削除してください':
        'Output windows do not close with ×. To remove one, delete it in the "Outputs" tab of the control panel.',

    # ---- ミキサー ----
    'ミュート': 'Mute',
    '音声マスター': 'Audio master',
    '{0} に映しているレイヤー全部の音量': 'Volume of all layers shown on {0}',

    # ---- エラー・状態（Python から来る文） ----
    '不明なクリップ: {0}': 'Unknown clip: {0}',
    'レイヤー映像: {0}': 'Layer picture: {0}',
    'レイヤー映像（元のレイヤーがありません）': 'Layer picture (the source layer is missing)',
    'カメラ: {0}': 'Camera: {0}',
    'ウィンドウ: {0}': 'Window: {0}',
    'ファイルが見つかりません: {0}': 'File not found: {0}',
    '読み込み失敗: {0} ({1})': 'Could not load: {0} ({1})',
    '開けません: {0} ({1})': 'Cannot open: {0} ({1})',
    '元のレイヤーが見つかりません（削除された可能性があります）': 'The source layer was not found (it may have been deleted)',
    'おたがいの映像を映し合う形になるので使えません（元のレイヤーが、このレイヤーの映像を映しています）':
        'Cannot be used because the two layers would show each other (the source layer shows this layer)',
    '再生できません（ファイルが壊れているか、対応していない形式）: {0}':
        'Cannot play (the file is broken or the format is not supported): {0}',
    '15秒たっても映像が出ないので、切り替えをやめました（読み込みが遅い・ファイルが壊れているなど）':
        'No picture after 15 seconds, so the switch was cancelled (slow loading or a broken file)',
    '元のレイヤーが削除されたため、レイヤー映像を映せません':'The source layer was deleted, so the layer picture cannot be shown',
    '画像を読み込めません: {0}': 'Cannot load the image: {0}',
    'カメラの映像が来ません: {0}（ケーブル・電源、または他のアプリやレイヤーが使用中）':
        'No picture from the camera: {0} (check the cable and power, or another app or layer may be using it)',
    'カメラの映像が止まっています: {0}（ケーブル・電源を確認。自動でつなぎ直しています）':
        'The camera picture has stopped: {0} (check the cable and power; reconnecting automatically)',
    'Spout の映像が来ません: {0}（送る側のアプリが止まっているなど）': 'No picture from Spout: {0} (the sending app may have stopped)',
    'Spout の部品（SpoutGL）がありません': 'The Spout component (SpoutGL) is not installed',
    'Spout を開けません（OpenGL）': 'Cannot open Spout (OpenGL)',
    'Spout の受け取りでエラー: {0}': 'Error while receiving Spout: {0}',
    'Spout の映像が止まっています: {0}（送る側のアプリが止まった・閉じたなど。同じ名前で送り直すと続きが映ります）':
        'The Spout picture has stopped: {0} (the sending app stopped or closed; send again with the same name to continue)',
    'ウィンドウの映像が来ません: {0}（閉じられた・最小化されたなど）': 'No picture from the window: {0} (it may be closed or minimized)',
    'ウィンドウ取り込みの部品（windows-capture）がありません': 'The window capture component (windows-capture) is not installed',
    'ウィンドウが見つかりません: {0}': 'Window not found: {0}',
    'ウィンドウを取り込めません: {0}': 'Cannot capture the window: {0}',
    'ウィンドウを取り込めません: {0}（{1}）': 'Cannot capture the window: {0} ({1})',
    'ウィンドウが閉じられました: {0}（最後の映像のままです）': 'The window was closed: {0} (the last picture stays)',
    '音声入力の部品（sounddevice）がありません': 'The audio input component (sounddevice) is not installed',
    '音声入力が見つかりません: {0}': 'Audio input not found: {0}',
    '音声入力を開けません: {0}（{1}）': 'Cannot open the audio input: {0} ({1})',
    '動画再生ライブラリがありません': 'No video playback library',
    '内部エラー: {0}': 'Internal error: {0}',
    '四隅を1つ前に戻しました（{0}）': 'Undid the last corner change ({0})',
    'VLC（音声あり）': 'VLC (audio)',
    'VLC（音声あり・音量は全レイヤー共通）': 'VLC (with audio; one volume for all layers)',
    'OpenCV（音声なし）': 'OpenCV (no audio)',
    'なし': 'none',
    'OpenGL（GLFW）を初期化できません': 'Cannot start OpenGL (GLFW)',
    'OpenGL 3.3 のウィンドウを作れません（グラボのドライバを確認してください）':
        'Cannot create an OpenGL 3.3 window (check the graphics driver)',
    '操作パネル用のポートが確保できません': 'No free port for the control panel',
    '操作パネル: {0}   （出力映像の再生: {1}、出力 {2}）': 'Control panel: {0}   (playback: {1}, outputs: {2})',

    # ---- 初めの名前 ----
    '動画レイヤー': 'Video layer',
    '写真ウォール': 'Photo wall',
    '背景動画': 'Background',
    'オーバーレイ': 'Overlay',

    # ---- スマホの画面 ----
    'VPT39 セッティング': 'VPT39 Setup',
    'PCとつながっていません（Wi-Fi・VPT39 を確認）': 'Not connected to the PC (check the Wi-Fi and VPT39)',
    'スクリーンに黄色い枠と赤い印を出す': 'Show a yellow frame and a red mark on the screen',
    '枠を表示': 'Show frame',
    '枠を表示中': 'Frame on',
    'レイヤー': 'Layer',
    '動かす所': 'What to move',
    '押す＝1回・長押し＝続けて': 'Tap = once, hold = repeat',
    '↶ 1つ戻す': '↶ Undo',
    'テスト模様': 'Test pattern',
    'ぼかし': 'Feather',
    '（写真）': ' (photo)',
    '{0}（写真）': '{0} (photo)',
    '「枠を表示」にすると、スクリーンの映像に黄色い枠と赤い印（いま動かしている所）が出ます。スクリーンを見ながら十字ボタンで合わせてください。図の角や内側を指でなぞっても動かせます。':
        'With "Show frame" on, a yellow frame and a red mark (the part you are moving) appear on the screen. '
        'Watch the screen and adjust with the arrow pad. You can also drag the corners or the inside of the picture with your finger.',
    'このレイヤーの四隅を、画面いっぱいの四角に戻しますか？（「1つ戻す」で元に戻せます）':
        'Reset this layer\'s corners to fill the screen? ("Undo" can bring them back.)',
}

# ---- 置き換えのしくみ（ブラウザ側の SCRIPT も同じ考え方） ----
_JA = re.compile(r'[　-ヿ一-鿿！-｠]')
_SEP = re.compile(r'(・|／| / |（|）|：|、|＋)')
_SEP_EN = {'・': ' · ', '／': ' / ', ' / ': ' / ', '（': ' (', '）': ')', '：': ': ', '、': ', ', '＋': '+'}


def _compile():
    pats = []
    for k, v in EN.items():
        if re.search(r'\{#?\d\}', k):
            rx = re.escape(k)
            rx = re.sub(r'\\\{\\?#(\d)\\\}', r'(?P<g\1>[0-9][0-9.,]*)', rx)  # {#n} は数字だけ
            rx = re.sub(r'\\\{(\d)\\\}', r'(?P<g\1>[\\s\\S]*?)', rx)
            pats.append((len(re.sub(r'\{#?\d\}', '', k)), re.compile('^' + rx + '$'), v))
    pats.sort(key=lambda p: -p[0])
    return [(r, v) for _, r, v in pats]


_PATS = _compile()
_lang = None


def _default_lang():
    try:
        import ctypes
        return 'ja' if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff == 0x11 else 'en'
    except Exception:
        return 'ja'


def get_lang():
    global _lang
    if _lang is None:
        try:
            with open(LANG_FILE, encoding='utf-8') as f:
                v = f.read().strip().lower()
            _lang = v if v in LANGS else _default_lang()
        except OSError:
            _lang = _default_lang()
    return _lang


def set_lang(v):
    global _lang
    if v not in LANGS:
        return
    _lang = v
    try:
        with open(LANG_FILE, 'w', encoding='utf-8') as f:
            f.write(v + '\n')
    except OSError as ex:
        print('language.txt を保存できません:', ex)


def translate(s, depth=0):
    """日本語の文を英語に（見つからなければそのまま）"""
    if not s or not _JA.search(s) or depth > 4:
        return s
    lead = s[:len(s) - len(s.lstrip())]
    tail = s[len(s.rstrip()):]
    core = s.strip()
    if core in EN:
        return lead + EN[core] + tail
    for rx, v in _PATS:
        m = rx.match(core)
        if m:
            out = v
            for name, val in m.groupdict().items():
                out = out.replace('{' + name[1:] + '}', translate(val, depth + 1))
            return lead + out + tail
    # 「・」「（）」などで区切った部分ごとなら訳せるとき
    parts = _SEP.split(core)
    if len(parts) > 1:
        res = []
        for p in parts:
            if p in _SEP_EN:
                res.append(_SEP_EN[p])
            elif _JA.search(p):
                t = translate(p, depth + 1)
                if t == p:
                    return s
                res.append(t)
            else:
                res.append(p)
        return lead + re.sub(r'  +', ' ', ''.join(res)).strip() + tail
    return s


def tr(s):
    """Python で作る文を、いまの言語にする"""
    return translate(s) if get_lang() == 'en' else s


# ---- ブラウザ側（操作パネル・スマホの画面に埋め込む） ----
SCRIPT = r"""<script>
const I18N=%s;
const I18N_JA=/[　-ヿ一-鿿！-｠]/, I18N_SEP=/(・|／| \/ |（|）|：|、|＋)/,
  I18N_SEP_EN={'・':' · ','／':' / ',' / ':' / ','（':' (','）':')','：':': ','、':', ','＋':'+'};
let LANG=null;
const I18N_PATS=Object.keys(I18N).filter(k=>/\{#?\d\}/.test(k)).map(k=>{
  const rx=k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\\\{#(\d)\\\}/g,'([0-9][0-9.,]*)').replace(/\\\{(\d)\\\}/g,'([\\s\\S]*?)');
  const order=[...k.matchAll(/\{#?(\d)\}/g)].map(m=>+m[1]);
  return {n:k.replace(/\{#?\d\}/g,'').length,rx:new RegExp('^'+rx+'$'),v:I18N[k],order};
}).sort((a,b)=>b.n-a.n);
const I18N_CACHE=new Map();
function trans(s,d){
  if(!s||!I18N_JA.test(s)||(d||0)>4) return s;
  const core=s.trim(); if(!core) return s;
  const lead=s.slice(0,s.indexOf(core[0])), tail=s.slice(lead.length+core.length);
  if(Object.prototype.hasOwnProperty.call(I18N,core)) return lead+I18N[core]+tail;
  for(const p of I18N_PATS){const m=p.rx.exec(core); if(m){let o=p.v;
    p.order.forEach((g,i)=>{o=o.split('{'+g+'}').join(trans(m[i+1],(d||0)+1));}); return lead+o+tail;}}
  const parts=core.split(I18N_SEP);
  if(parts.length>1){const r=[];
    for(const p of parts){ if(p in I18N_SEP_EN) r.push(I18N_SEP_EN[p]);
      else if(I18N_JA.test(p)){const t=trans(p,(d||0)+1); if(t===p) return s; r.push(t);} else r.push(p);}
    return lead+r.join('').replace(/  +/g,' ').trim()+tail;}
  return s;
}
// 日本語の文を、いまの言語にする（英語のときだけ置き換える）
function T(s){ if(LANG!=='en'||typeof s!=='string') return s;
  let v=I18N_CACHE.get(s); if(v===undefined){v=trans(s); if(I18N_CACHE.size>5000) I18N_CACHE.clear(); I18N_CACHE.set(s,v);} return v;}
function trNode(n){
  if(n.nodeType===3){const v=n.nodeValue; if(I18N_JA.test(v)){const t=T(v); if(t!==v) n.nodeValue=t;} return;}
  if(n.nodeType!==1||n.tagName==='SCRIPT'||n.tagName==='STYLE'||n.tagName==='TEXTAREA'||n.getAttribute('translate')==='no') return;
  for(const a of ['title','placeholder','alt']){const v=n.getAttribute&&n.getAttribute(a); if(v&&I18N_JA.test(v)){const t=T(v); if(t!==v) n.setAttribute(a,t);}}
  for(const c of n.childNodes) trNode(c);
}
const I18N_OBS=new MutationObserver(ms=>{ for(const m of ms){
  if(m.type==='characterData') trNode(m.target);
  else if(m.type==='attributes') trNode(m.target);
  else m.addedNodes.forEach(trNode);}});
const _alert=window.alert.bind(window), _confirm=window.confirm.bind(window), _prompt=window.prompt.bind(window);
window.alert=m=>_alert(T(m)); window.confirm=m=>_confirm(T(m)); window.prompt=(m,d)=>_prompt(T(m),d);
// 言語を決める（状態を受け取るたびに呼ぶ。途中で変わったら、日本語に戻すために読み込み直す）
function setLang(l){
  if(!l||l===LANG) return;
  if(LANG!==null){ location.reload(); return; }
  LANG=l; document.documentElement.lang=l;
  if(l==='en'){ document.title=T(document.title); trNode(document.body);
    I18N_OBS.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['title','placeholder','alt']}); }
}
</script>"""


def script():
    return SCRIPT % json.dumps(EN, ensure_ascii=False).replace('</', '<\\/')
