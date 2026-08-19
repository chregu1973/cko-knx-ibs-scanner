#define AppName "CKO KNX IBS Scanner"
#define AppVersion "0.3.0"
#define AppPublisher "CKO Toolbox"
#define AppExeName "CKO-KNX-IBS.exe"

[Setup]
AppId={{7395BC2E-89BD-4C87-9912-3C299D79AC65}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\CKO KNX IBS Scanner
DefaultGroupName=CKO Toolbox
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=CKO-KNX-IBS-Scanner-Setup
SetupIconFile=..\assets\cko-toolbox.ico
UninstallDisplayIcon={app}\CKO-KNX-IBS.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
VersionInfoProductName={#AppName}

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Desktop-Verknüpfung erstellen"; GroupDescription: "Zusätzliche Symbole:"; Flags: unchecked

[Files]
Source: "..\dist\CKO-KNX-IBS\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\cko-toolbox.ico"; DestDir: "{app}"; DestName: "CKO-KNX-IBS.ico"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\CKO-KNX-IBS.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\CKO-KNX-IBS.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{#AppName} starten"; Flags: nowait postinstall skipifsilent
