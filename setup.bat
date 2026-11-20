@echo off
chcp 65001 > nul
cd /d "%~dp0" || exit /b 1
echo ============================================
echo   VPT39 セットアップ / Setup
echo ============================================
echo.
echo [1/4] Python を確かめています... / Checking Python...
rem Microsoft Store の「python」（中身のない入口）だと、ここで失敗する
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) and sys.maxsize > 2**32 else 1)" > nul 2>&1
if errorlevel 1 goto nopython
python --version
python -c "import sys; sys.exit(1 if sys.version_info >= (3, 14) else 0)" > nul 2>&1
if errorlevel 1 echo   （注意）この Python では Spout が使えません。Spout も使うなら Python 3.13 を入れてください。
if errorlevel 1 echo   (Note) Spout does not work with this Python. Install Python 3.13 to use Spout.
echo.
echo [2/4] 専用の環境（.venv）を作っています... / Creating the environment (.venv)...
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
echo       Downloading and installing the components (takes a few minutes)...
python -m pip install -q --disable-pip-version-check --upgrade pip
python -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto fail
echo.
echo [4/4] フォルダを用意しています... / Preparing folders...
if not exist media mkdir media
if not exist photos mkdir photos
echo.
echo ============================================
echo   成功しました。run.bat で起動できます。
echo   Success. Start VPT39 with run.bat.
echo ============================================
echo   音声付きで再生するには VLC メディアプレイヤー（64bit）も必要です。
echo   To play videos with sound, also install VLC media player (64-bit).
echo   https://www.videolan.org/vlc/
echo.
pause
exit /b 0

:nopython
echo.
echo ############################################
echo   失敗：Python が見つかりません / Failed: Python not found
echo ############################################
echo   1. https://www.python.org/ から Python（3.10 以上・64bit）を入れてください。
echo   2. 入れるときに「Add python.exe to PATH」にチェックを入れてください。
echo   3. 入れたあと、この画面を閉じて setup.bat をもう一度実行してください。
echo.
echo   1. Install Python (3.10 or later, 64-bit) from https://www.python.org/
echo   2. Check "Add python.exe to PATH" during the installation.
echo   3. Then close this window and run setup.bat again.
echo.
pause
exit /b 1

:fail
echo.
echo ############################################
echo   失敗：部品を入れられませんでした / Failed: could not install the components
echo ############################################
echo   ・インターネットにつながっているか確かめてください。
echo   ・上に出ている赤い文字（ERROR）が原因です。写真に撮って相談してください。
echo   ・直したら、setup.bat をもう一度実行してください（続きから入ります）。
echo.
echo   - Check the internet connection.
echo   - The red text (ERROR) above shows the cause.
echo   - After fixing it, run setup.bat again (it continues where it stopped).
echo.
pause
exit /b 1
