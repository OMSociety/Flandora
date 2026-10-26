; Flandora 安装脚本（Inno Setup）
;
; 由 build.py 调用 ISCC.exe 编译，载荷取自 dist\Flandora-v<版本>\（与
; portable zip 同一份内容）。装到 %LOCALAPPDATA%，全程不需要管理员。
;
; 两处与运行期约定的耦合，改动时要一起改：
;   1. 开机自启是当前用户 Run 项下的一条值：HKCU\Software\Microsoft\Windows\
;      CurrentVersion\Run 里名为 Flandora 的字符串，指向 {app}\Flandora.exe。
;      网页面板「安全与服务 → 开机启动」读写的就是同一条值（flandora/autostart.py），
;      两边的勾选状态因此永远一致。卸载时由 uninsdeletevalue 清掉。
;   2. data\ 下的文件一律 uninsneveruninstall，卸载时是否删除由
;      InitializeUninstall() 里的对话框决定（默认删除）。

; 版本号与载荷目录由 tools/sync_version.py 写进 version.iss（唯一来源是
; flandora/__init__.py 的 __version__），本文件不写这两个值。
#include "version.iss"

#define AppName "Flandora"
#define AppPublisher "OMSociety"
#define ManagerExe "Flandora.exe"
#define BridgeExe "Flandora-Bridge.exe"
#define KeyToolExe "Flandora-KeyTool.exe"
#define AutostartValue "Flandora"

[Setup]
AppId={{80f185a4-624d-40dc-bb9f-59a793ff3859}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Flandora
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=Flandora-v{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#ManagerExe}
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "autostart"; Description: "开机自动启动（登录后自动运行管理器；微信由 bridge 在隔离桌面上拉起，不占用你的桌面）"; Flags: unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; Flags: unchecked

[Files]
; 程序与文档：每次安装都覆盖，写死的路径与随包资源必须跟着版本走
Source: "{#PayloadDir}\{#BridgeExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\{#ManagerExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\tools\{#KeyToolExe}"; DestDir: "{app}\tools"; Flags: ignoreversion
Source: "{#PayloadDir}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#PayloadDir}\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
; data\ 是可写数据目录（config.json、数据库口令、图片密钥、日志），
; 由程序首次启动时自己生成，升级安装不能碰它

[Registry]
; 与网页面板「开机启动」共用这一条：值名、路径、命令行三者必须一致
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AutostartValue}"; ValueData: """{app}\{#ManagerExe}"""; Tasks: autostart; Flags: uninsdeletevalue

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ManagerExe}"
Name: "{group}\使用说明"; Filename: "{app}\README.md"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ManagerExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#ManagerExe}"; Description: "立即运行 {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: dirifempty; Name: "{app}\data"
Type: dirifempty; Name: "{app}\docs\third-party-licenses"
Type: dirifempty; Name: "{app}\docs"
Type: dirifempty; Name: "{app}\tools"
Type: dirifempty; Name: "{app}"

[Code]
function InitializeUninstall(): Boolean;
var
  DataDir: String;
begin
  Result := True;
  DataDir := ExpandConstant('{app}\data');
  if DirExists(DataDir) then
  begin
    { 用 SuppressibleMsgBox 而不是 MsgBox：后者在 /VERYSILENT 下照样弹框，
      会把无人值守的卸载卡死。静默时按第四个参数取值，默认删除。 }
    if SuppressibleMsgBox('是否同时删除配置与已保存的微信口令？' + #13#10 + #13#10
              + '「是」删除 ' + DataDir + '，里面有 config.json、微信数据库口令、'
              + '图片密钥和日志。' + #13#10
              + '「否」保留它们，以后重新安装可以接着用。',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON1, IDYES) = IDYES then
      DelTree(DataDir, True, True, True);
  end;
end;
