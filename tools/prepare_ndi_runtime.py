"""Download and verify the official NDI installer for Windows packaging."""

import os
from pathlib import Path
import subprocess
import tempfile


URL = 'https://downloads.ndi.tv/SDK/NDI_SDK/NDI%206%20Runtime.exe'
DOWNLOAD_AND_VERIFY = r"""
$ErrorActionPreference = 'Stop'
$file = $env:VPT39_NDI_INSTALLER_FILE
Invoke-WebRequest -Uri $env:VPT39_NDI_INSTALLER_URL -UseBasicParsing -OutFile $file -TimeoutSec 90
$signature = Get-AuthenticodeSignature -LiteralPath $file
if ($signature.Status -ne 'Valid' -or
    $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -ne 'Vizrt AG') {
    throw "The NDI installer did not have a valid Vizrt AG signature ($($signature.Status))."
}
"""


def main():
    folder = Path(__file__).resolve().parents[1] / 'runtime' / 'ndi'
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / 'NDI 6 Runtime.exe'
    with tempfile.TemporaryDirectory(prefix='ndi-download-', dir=folder) as temp:
        downloaded = Path(temp) / destination.name
        env = dict(os.environ, VPT39_NDI_INSTALLER_FILE=str(downloaded), VPT39_NDI_INSTALLER_URL=URL)
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', DOWNLOAD_AND_VERIFY],
                       env=env, check=True)
        downloaded.replace(destination)
    print(destination)


if __name__ == '__main__':
    main()
