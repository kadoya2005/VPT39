; Include from the Windows installer script after defining NdiRuntimeSource.
; The NDI redistributable handles its own installation and license screen.

#ifndef NdiRuntimeSource
  #error Define NdiRuntimeSource as the path to the official NDI 6 Runtime installer.
#endif

[Tasks]
Name: "ndiruntime"; Description: "Install NDI 6 Runtime for video output"; Check: IsWin64

[Files]
Source: "{#NdiRuntimeSource}"; DestDir: "{tmp}"; DestName: "NDI 6 Runtime.exe"; Flags: deleteafterinstall; Tasks: ndiruntime

[Run]
Filename: "{tmp}\NDI 6 Runtime.exe"; StatusMsg: "Installing NDI 6 Runtime..."; Flags: waituntilterminated; Tasks: ndiruntime
