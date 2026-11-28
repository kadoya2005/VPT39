"""Build the VPT39 Windows x64 installer with Python 3.13, .NET 10 and Inno Setup."""

import argparse
from importlib.metadata import distributions
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import urlretrieve

from prepare_ndi_runtime import main as prepare_ndi


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build'
TOOLS = BUILD / 'windows-tools'
DIST = ROOT / 'dist'
WEBVIEW_URL = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'


def run(*args, env=None):
    subprocess.run(args, cwd=ROOT, env=env, check=True)


def verify_signature(path, publisher):
    run('powershell.exe', '-NoProfile', '-NonInteractive', '-Command', r"""
        $ErrorActionPreference = 'Stop'
        $sig = Get-AuthenticodeSignature -LiteralPath $env:VPT39_VERIFY_FILE
        if ($sig.Status -ne 'Valid' -or
            $sig.SignerCertificate.GetNameInfo('SimpleName', $false) -ne $env:VPT39_VERIFY_PUBLISHER) {
            throw "Invalid signature on $env:VPT39_VERIFY_FILE ($($sig.Status))."
        }
        """, env=dict(os.environ, VPT39_VERIFY_FILE=str(path), VPT39_VERIFY_PUBLISHER=publisher))


def copy_samples(payload):
    paths = subprocess.check_output(['git', 'ls-tree', '-rz', '--name-only', 'HEAD', '--', 'media', 'photos'],
                                    cwd=ROOT).decode('utf-8').split('\0')
    for name in filter(None, paths):
        path = Path(name)
        if len(path.parts) < 3 or path.parts[1] != 'テスト用':
            continue
        destination = payload / 'samples' / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Use tracked content so a personal file cannot accidentally be included in a release.
        destination.write_bytes(subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=ROOT))


def copy_notices(payload, nuget_dir):
    for name in ('LICENSE', 'THIRD_PARTY.md', 'README.md', 'README.ja.md'):
        shutil.copy2(ROOT / name, payload / name)
    dest = payload / 'licenses'
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    dest.mkdir(exist_ok=True)
    shutil.copy2(python_license, dest / 'Python-LICENSE.txt')
    for package in distributions():
        for entry in package.files or ():
            if not entry.name.lower().startswith(('license', 'licence', 'copying', 'notice', 'third-party')):
                continue
            source = entry.locate()
            if source.is_file():
                target = dest / package.metadata['Name'] / entry.name
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(source, target)
    for name in ('microsoft.web.webview2', 'microsoft.netcore.app.runtime.win-x64',
                 'microsoft.windowsdesktop.app.runtime.win-x64'):
        for package in (nuget_dir / name).glob('*'):
            for source in package.iterdir():
                if source.is_file() and source.name.lower().startswith(('license', 'notice', 'third-party')):
                    target = dest / name
                    target.mkdir(exist_ok=True)
                    shutil.copy2(source, target / source.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--iscc', type=Path, help='Path to ISCC.exe')
    parser.add_argument('--refresh-ndi', action='store_true', help='Download the current official NDI installer')
    args = parser.parse_args()
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 13) or sys.maxsize <= 2**32:
        parser.error('Build with 64-bit Python 3.13 on Windows.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Use a version like 0.1.0.')

    local_iscc = TOOLS / 'Inno' / 'ISCC.exe'
    installed_iscc = Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6' / 'ISCC.exe'
    iscc = args.iscc or next((p for p in (local_iscc, installed_iscc) if p.is_file()), shutil.which('ISCC'))
    if not iscc or not Path(iscc).is_file():
        parser.error('Install Inno Setup 6 or pass --iscc with its compiler path.')
    dotnet = shutil.which('dotnet') or Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'dotnet' / 'dotnet.exe'

    TOOLS.mkdir(parents=True, exist_ok=True)
    webview = TOOLS / 'MicrosoftEdgeWebview2Setup.exe'
    if not webview.is_file():
        temporary = webview.with_suffix('.download')
        urlretrieve(WEBVIEW_URL, temporary)
        verify_signature(temporary, 'Microsoft Corporation')
        temporary.replace(webview)
    verify_signature(webview, 'Microsoft Corporation')
    ndi = ROOT / 'runtime' / 'ndi' / 'NDI 6 Runtime.exe'
    if args.refresh_ndi or not ndi.is_file():
        prepare_ndi()
    verify_signature(ndi, 'Vizrt AG')

    # Stage each build in its own directory. Windows can keep old DLLs locked.
    staging = Path(tempfile.mkdtemp(prefix='release-', dir=BUILD))
    payload = staging / 'VPT39'
    run(sys.executable, '-m', 'PyInstaller', '--windowed', '--onedir',
        '--name', 'VPT39', '--specpath', str(BUILD), '--workpath', str(BUILD / 'pyinstaller'),
        '--distpath', str(staging), '--hidden-import', 'glcontext.wgl', '--collect-binaries', 'glfw',
        '--noupx', 'stage.py')
    # windows-capture imports OpenCV, while the installer supplies VLC for media playback.
    for ffmpeg in payload.rglob('opencv_videoio_ffmpeg*.dll'):
        ffmpeg.unlink()
    nuget_dir = BUILD / 'nuget'
    env = dict(os.environ, NUGET_PACKAGES=str(nuget_dir), DOTNET_CLI_HOME=str(BUILD),
               DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false')
    run(dotnet, 'publish', 'desktop/VPT39.Desktop.csproj', '-c', 'Release', '-r', 'win-x64',
        '--self-contained', 'true', '-o', str(payload / 'desktop'), env=env)
    copy_samples(payload)
    copy_notices(payload, nuget_dir)
    run(Path(iscc).resolve(), f'/DAppVersion={args.version}', f'/DAppPayload={payload}',
        f'/DOutputFolder={DIST}', f'/DWebViewInstaller={webview}', f'/DNdiRuntimeSource={ndi}',
        ROOT / 'packaging' / 'VPT39.iss')
    print(DIST / f'VPT39-Setup-{args.version}-x64.exe')
    print(f'Build staging: {payload}')


if __name__ == '__main__':
    main()
