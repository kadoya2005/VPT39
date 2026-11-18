@echo off
chcp 65001 > nul
cd /d %~dp0
if not exist .venv\Scripts\python.exe goto nosetup
.venv\Scripts\python.exe -c "pass" > nul 2>&1
if errorlevel 1 goto nosetup
rem 足りないライブラリがあれば入れる（入っていればすぐ終わる）
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
rem リハ・準備用：この回だけ全部の出力をウィンドウで開く（プロジェクター無しでOK）
.venv\Scripts\python.exe stage.py project.json --window
if errorlevel 1 pause
exit /b

:nosetup
echo VPT39 の準備がまだか、別のPCで準備した状態です。先に setup.bat をダブルクリックしてください。
echo VPT39 is not set up on this PC yet. Double-click setup.bat first.
pause
exit /b 1