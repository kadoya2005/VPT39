@echo off
chcp 65001 > nul
cd /d "%~dp0" || exit /b 1
if not exist ".venv\Scripts\python.exe" goto nosetup
".venv\Scripts\python.exe" -c "pass"
if errorlevel 1 goto nosetup
setlocal
set "output_mode=--window"
if /i "%~1"=="--fullscreen" set "output_mode="
".venv\Scripts\python.exe" stage.py project.json %output_mode%
set "launch_status=%errorlevel%"
if "%launch_status%"=="0" exit /b 0
echo.
echo VPT39 の起動に失敗しました。上のエラーを確認してください。
echo ModuleNotFoundError が出た場合は、setup.bat を実行してください。
pause
exit /b %launch_status%

:nosetup
echo VPT39 の準備がまだか、別のPCで準備した状態です。先に setup.bat をダブルクリックしてください。
echo VPT39 is not set up on this PC yet. Double-click setup.bat first.
pause
exit /b 1
