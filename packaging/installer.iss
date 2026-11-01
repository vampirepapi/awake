; Inno Setup script for the Windows installer.
; Built by scripts\build_windows.bat after PyInstaller (requires Inno Setup 6: `choco install innosetup`).
; Per-user install — no administrator rights required.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6B0E7C2A-4F5D-4C3B-9E59-2D8A9E1F7A01}
AppName=Awake
AppVersion={#AppVersion}
AppVerName=Awake {#AppVersion}
AppPublisher=Awake contributors
AppPublisherURL=https://github.com/vampirepapi/awake
AppCopyright=Copyright (c) 2026 Awake contributors
DefaultDirName={localappdata}\Programs\Awake
DefaultGroupName=Awake
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Awake-{#AppVersion}-Setup
SetupIconFile=..\awake\assets\icons\app\Awake.ico
UninstallDisplayIcon={app}\Awake.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
CloseApplications=yes
VersionInfoVersion={#AppVersion}
VersionInfoProductName=Awake

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Awake\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Awake"; Filename: "{app}\Awake.exe"
Name: "{autodesktop}\Awake"; Filename: "{app}\Awake.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Awake.exe"; Description: "Launch Awake"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the per-user "launch at sign-in" entry if the user enabled it.
Filename: "{sys}\reg.exe"; Parameters: "delete HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Awake /f"; Flags: runhidden; RunOnceId: "RemoveAutostart"
