; InnoSetup script for CerePulse.
;
; Per-user install (no elevation): the app writes only to %LOCALAPPDATA% and registers only
; a per-user startup entry, so asking for admin would be asking for more than it needs.
;
; Values marked with a default are overridden on the command line by tools/build_all.py, so
; the version lives in __about__.py and nowhere else.

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\CerePulse"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

#define AppName        "CerePulse"
#define AppPublisher   "Viren Hirpara"
#define AppURL         "https://github.com/viru185/CerePulse"
#define AppExeName     "CerePulse.exe"

[Setup]
AppId={{8E2C4F31-9A64-4B7D-B0E5-3D6C1A9F2E48}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}

; Per-user: no UAC prompt, and the install lands in the user's own AppData.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no

OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-{#AppVersion}-Setup
SetupIconFile=..\src\cerepulse\ui\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
WizardStyle=modern
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Refuse to install over a running copy rather than leaving a half-updated folder.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
  GroupDescription: "Shortcuts:"
Name: "startupicon"; Description: "Start {#AppName} when I sign in to Windows"; \
  GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID must match what the app sets on itself at startup (ui/app.py). Windows
; attributes toast notifications to this identity: when the shortcut and the process
; disagree — or neither declares one — the app can fail to appear under Settings → System
; → Notifications at all, leaving a user whose toasts are switched off nothing to switch on.
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
  AppUserModelID: "VirenHirpara.CerePulse"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon; \
  AppUserModelID: "VirenHirpara.CerePulse"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[Registry]
; Start-with-Windows uses the HKCU Run key, the same mechanism the app's own Settings
; toggle writes to. A Startup-folder shortcut would work equally well on its own, but then
; the installer and the app would be using two different mechanisms and the Settings
; checkbox would misreport whether startup is actually enabled.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueName: "{#AppName}"; ValueType: string; ValueData: """{app}\{#AppExeName}"""; \
  Flags: uninsdeletevalue; Tasks: startupicon

; Clean the entry up on uninstall even when the task was never selected, since the app may
; have written it itself. No `deletevalue` here: that fires during *installation*, which
; would silently disable startup every time the user updates.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueName: "{#AppName}"; ValueType: none; Flags: uninsdeletevalue

; Cached attendance data in %LOCALAPPDATA% is deliberately left in place on uninstall, so a
; reinstall keeps its history and does not need to re-sync months of detail.
