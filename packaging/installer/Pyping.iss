#define MyAppName "Pyping GUI"
#define MyAppVersion "0.4.0"
#define MyAppPublisher "Pyping contributors"
#define MyAppExeName "Pyping.exe"
#define ProjectRoot AddBackslash(SourcePath) + "..\.."

[Setup]
AppId={{6C7B3AF3-A40C-4DD6-BD42-A2EE62154040}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Pyping
DefaultGroupName=Pyping
DisableProgramGroupPage=yes
SourceDir={#ProjectRoot}
OutputDir=release
OutputBaseFilename=Pyping-Setup-{#MyAppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=pyping_app\assets\pyping.ico
LicenseFile=LICENSE
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimp"; MessagesFile: "packaging\installer\Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\Pyping\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Pyping"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Pyping"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
