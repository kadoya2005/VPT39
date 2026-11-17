@echo off
chcp 65001 > nul
cd /d %~dp0
echo ============================================
echo   VPT39 セットアップ
echo ============================================
echo.
echo [1/4] Python を確かめています...
rem Microsoft Store の「python」（中身のない入口）だと、ここで失敗する
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" > nul 2>&1
if errorlevel 1 goto nopython
python --version
python -c "import sys; sys.exit(1 if sys.version_info >= (3, 14) else 0)" > nul 2>&1
if errorlevel 1 echo   （注意）この Python では Spout が使えません。Spout も使うなら Python 3.13 を入れてください。
echo.
echo [2/4] 専用の環境（.venv）を作っています...
rem 別のPCで作った .venv（中の Python の場所が違う）は動かないので作り直す
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "pass" > nul 2>&1 || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  python -m venv .venv || goto fail
)
call .venv\Scripts\activate.bat
echo.
echo [3/4] 必要な部品をダウンロードして入れています（数分かかります）...
python -m pip install -q --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
echo.
echo [4/4] フォルダを用意しています...
if not exist media mkdir media
if not exist photos mkdir photos
echo.
echo ============================================
echo   成功しました。run.bat で起動できます。
echo ============================================
echo   音声付きで再生するには VLC メディアプレイヤー（64bit）も必要です。
echo   https://www.videolan.org/vlc/
echo.
pause
exit /b 0

:nopython
echo.
echo ############################################
echo   失敗：Python が見つかりません
echo ############################################
echo   1. https://www.python.org/ から Python（3.10 以上・64bit）を入れてください。
echo   2. 入れるときに「Add python.exe to PATH」にチェックを入れてください。
echo   3. 入れたあと、この画面を閉じて setup.bat をもう一度実行してください。
echo.
pause
exit /b 1

:fail
echo.
echo ############################################
echo   失敗：部品を入れられませんでした
echo ############################################
echo   ・インターネットにつながっているか確かめてください。
echo   ・上に出ている赤い文字（ERROR）が原因です。写真に撮って相談してください。
echo   ・直したら、setup.bat をもう一度実行してください（続きから入ります）。
echo.
pause
exit /b 1
