; Inno Setup 6 — 直聊小蜜安装包
; 构建前先运行 packaging/build.ps1 生成 dist\ZhiLiaoXiaoMi

#define MyAppName "直聊小蜜"
#define MyAppVersion "1.0.7"
#define MyAppPublisher "直聊小蜜"
#define MyAppExeName "ZhiLiaoXiaoMi.exe"

[Setup]
AppId={{B7E4D3F2-8C5A-4E7B-9F1D-2A6C8B0D4E5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ZhiLiaoXiaoMi
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installer
OutputBaseFilename=ZhiLiaoXiaoMi-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "..\dist\ZhiLiaoXiaoMi\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; runtime\ (portable Node + boss-cli) is included via the line above

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动直聊小蜜"; Flags: nowait postinstall skipifsilent







