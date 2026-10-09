; Instalador de Windows (Inno Setup 6). Se compila en GitHub Actions.
#define MyAppName "Traductor de Libros"
#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{6F1D3C2E-8B7A-4E59-9C41-3A2B7D9E5F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Traductor de Libros (código abierto)
AppPublisherURL=https://github.com/Willyssj3/TraductordeLibros
DefaultDirName={autopf}\TraductorDeLibros
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\instalador
OutputBaseFilename=TraductorDeLibros-{#MyAppVersion}-instalador
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\TraductorDeLibros.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\TraductorDeLibros\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\TraductorDeLibros.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\TraductorDeLibros.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TraductorDeLibros.exe"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
