; Inno Setup script - https://jrsoftware.org/isinfo.php
; Run build.bat first (onedir mode), then open this file in Inno Setup and click Compile.
#define MyAppName "Danevo File Sorter"
#define MyAppVersion "1.2.0"
#define MyAppExe "Danevo File Sorter.exe"

[Setup]
AppId={{B7C1E5A4-3D2F-4F8A-9E61-DA11E70F5A01}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Danevo
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer
OutputBaseFilename=DanevoFileSorter-Setup-{#MyAppVersion}
SetupIconFile=danevo.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startup"; Description: "Start {#MyAppName} when Windows starts (runs quietly in the tray)"; GroupDescription: "Options:"

[Files]
Source: "dist\Danevo File Sorter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DanevoFileSorter"; ValueData: """{app}\{#MyAppExe}"" --minimized"; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
