; Instalador de Windows (Inno Setup 6). Se compila en GitHub Actions.
#define MyAppName "Jerónimo"
#ifndef MyAppVersion
  #define MyAppVersion "0.2.0"
#endif

[Setup]
AppId={{6F1D3C2E-8B7A-4E59-9C41-3A2B7D9E5F10}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=Jerónimo (código abierto)
AppPublisherURL=https://github.com/Willyssj3/TraductordeLibros
DefaultDirName={autopf}\Jeronimo
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\instalador
OutputBaseFilename=Jeronimo-{#MyAppVersion}-instalador
SetupIconFile=jeronimo.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Jeronimo.exe

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Jeronimo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\Jeronimo.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\Jeronimo.exe"; Tasks: desktopicon

[InstallDelete]
; La app se llamaba «Traductor de Libros»: se quitan el ejecutable y los accesos antiguos.
Type: files; Name: "{app}\TraductorDeLibros.exe"
Type: files; Name: "{group}\Traductor de Libros.lnk"
Type: files; Name: "{autodesktop}\Traductor de Libros.lnk"

[Run]
Filename: "{app}\Jeronimo.exe"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
