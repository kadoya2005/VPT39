#ifndef AppVersion
  #error Pass AppVersion from tools/build_windows.py.
#endif

#define VlcFile "vlc-3.0.23-win64.msi"
#define VlcUrl "https://download.videolan.org/pub/videolan/vlc/3.0.23/win64/" + VlcFile
#define VlcSha256 "bc4b902a480b98a4a5479327a7f210f06369a59bf727649e320faba5b4ef1f5e"

[Setup]
AppId={{75E7BC1B-79F3-47BB-A656-A70EF7DA3184}
AppName=VPT39
AppVersion={#AppVersion}
DefaultDirName={autopf}\VPT39
DefaultGroupName=VPT39
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19045
PrivilegesRequired=admin
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputFolder}
OutputBaseFilename=VPT39-Setup-{#AppVersion}-x64

[Languages]
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
ja.SetupVlcError=VLC を準備できませんでした。ネット接続を確認して再試行してください。
en.SetupVlcError=Could not set up VLC. Check your internet connection and try again.
ja.SetupWebViewError=WebView2 を準備できませんでした。ネット接続を確認して再試行してください。
en.SetupWebViewError=Could not set up WebView2. Check your internet connection and try again.
ja.LaunchApp=VPT39 を起動
en.LaunchApp=Launch VPT39
ja.InstallNdi=NDI 6 Runtime をインストール（NDI入力・出力を使う場合）
en.InstallNdi=Install NDI 6 Runtime for NDI input and output

[Files]
Source: "{#AppPayload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#WebViewInstaller}"; DestName: "MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy

[Icons]
Name: "{autoprograms}\VPT39"; Filename: "{app}\VPT39.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\VPT39.exe"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent runasoriginaluser

#include "ndi-runtime.iss"

[Code]
function HasVLC: Boolean;
var
  Folder: String;
begin
  Result :=
    (RegQueryStringValue(HKLM64, 'Software\VideoLAN\VLC', 'InstallDir', Folder) and
      FileExists(AddBackslash(Folder) + 'libvlc.dll')) or
    FileExists(ExpandConstant('{commonpf64}\VideoLAN\VLC\libvlc.dll'));
end;

function HasWebView2: Boolean;
var
  Key, Version: String;
begin
  Key := 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKLM32, Key, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, Key, 'pv', Version) and
    (Version <> '') and (Version <> '0.0.0.0'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  VlcPath: String;
begin
  Result := '';
  if not HasVLC then
  begin
    try
      DownloadTemporaryFile('{#VlcUrl}', '{#VlcFile}', '{#VlcSha256}', nil);
      VlcPath := ExpandConstant('{tmp}\{#VlcFile}');
      if not Exec(ExpandConstant('{sys}\msiexec.exe'), '/i "' + VlcPath +
        '" /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
        RaiseException(SysErrorMessage(ExitCode));
      if (ExitCode <> 0) and (ExitCode <> 3010) then
        RaiseException('VLC MSI exit code: ' + IntToStr(ExitCode));
      NeedsRestart := NeedsRestart or (ExitCode = 3010);
      if not HasVLC and not NeedsRestart then
        RaiseException('VLC installation could not be detected.');
    except
      Log(GetExceptionMessage);
      Result := CustomMessage('SetupVlcError');
      exit;
    end;
  end;

  if not HasWebView2 then
  begin
    try
      ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
      if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'),
        '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ExitCode) then
        RaiseException(SysErrorMessage(ExitCode));
      if not HasWebView2 then
        RaiseException('WebView2 setup exit code: ' + IntToStr(ExitCode));
    except
      Log(GetExceptionMessage);
      Result := CustomMessage('SetupWebViewError');
    end;
  end;
end;
