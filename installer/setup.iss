#ifndef MyAppVersion
  #define MyAppVersion "0.4.0"
#endif

#define MyAppName "IP 活跃度审计"
#define MyAppPublisher "IP Activity Auditor"
#define MyAppExeName "IPActivityAuditor.exe"
#define MyAppId "{{8A2B7C6E-5D4F-4A93-9E21-7C3F0B6D1A45}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\IPActivityAuditor
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=IPActivityAuditor-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupIconFile=..\assets\icon.ico
MinVersion=10.0
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked
Name: "installtask"; Description: "创建每日自动扫描任务（每天 23:30，SYSTEM 最高权限）"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\IPActivityAuditor.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{commonappdata}\IPActivityAuditor"; Permissions: users-modify
Name: "{commonappdata}\IPActivityAuditor\data"; Permissions: users-modify
Name: "{commonappdata}\IPActivityAuditor\reports"; Permissions: users-modify
Name: "{commonappdata}\IPActivityAuditor\logs"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--gui --config ""{commonappdata}\IPActivityAuditor\config.ini"""
Name: "{group}\IP 活跃度审计（静默托盘）"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--tray --config ""{commonappdata}\IPActivityAuditor\config.ini"""
Name: "{group}\IP 活跃度审计 - 报表目录"; Filename: "{commonappdata}\IPActivityAuditor\reports"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--gui --config ""{commonappdata}\IPActivityAuditor\config.ini"""; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--gui --config ""{commonappdata}\IPActivityAuditor\config.ini"""; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""IPActivityAuditor"" /F"; Flags: runhidden; RunOnceId: "DeleteIPAAAuditTask"

[UninstallDelete]
Type: files; Name: "{commonappdata}\IPActivityAuditor\run_scan.cmd"

[Code]
const
  DefaultConfig =
    '; IP Activity Auditor 配置文件' + #13#10 +
    '; 相对路径以本配置文件所在目录为基准' + #13#10 +
    '; 仅允许扫描本文件中的 CIDR，请确保已获得网络扫描授权' + #13#10 +
    '' + #13#10 +
    '[scan]' + #13#10 +
    'cidr = 192.168.0.0/24' + #13#10 +
    'backend = auto' + #13#10 +
    'timeout_ms = 1500' + #13#10 +
    'retries = 1' + #13#10 +
    '' + #13#10 +
    '[storage]' + #13#10 +
    'db_path = data/ip_activity.db' + #13#10 +
    '' + #13#10 +
    '[report]' + #13#10 +
    'report_dir = reports' + #13#10 +
    'unused_days = 7,15' + #13#10 +
    '' + #13#10 +
    '[log]' + #13#10 +
    'log_dir = logs' + #13#10;

procedure EnsureDefaultConfig;
var
  ConfigPath: String;
begin
  ConfigPath := ExpandConstant('{commonappdata}\IPActivityAuditor\config.ini');
  if not FileExists(ConfigPath) then
    SaveStringToFile(ConfigPath, DefaultConfig, False);
end;

function CreateDailyTask: Boolean;
var
  ResultCode: Integer;
  LauncherPath: String;
  Arguments: String;
begin
  LauncherPath := ExpandConstant('{commonappdata}\IPActivityAuditor\run_scan.cmd');
  Arguments := '/Create /TN "IPActivityAuditor" /TR "' + LauncherPath +
    '" /SC DAILY /ST 23:30 /RL HIGHEST /RU SYSTEM /F';
  Result := Exec(ExpandConstant('{sys}\schtasks.exe'), Arguments, '',
    SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

procedure WriteTaskLauncher;
var
  ExePath: String;
  ConfigPath: String;
  LauncherPath: String;
  Command: String;
begin
  ExePath := ExpandConstant('{app}\IPActivityAuditor.exe');
  ConfigPath := ExpandConstant('{commonappdata}\IPActivityAuditor\config.ini');
  LauncherPath := ExpandConstant('{commonappdata}\IPActivityAuditor\run_scan.cmd');
  Command := '@echo off' + #13#10 +
    'chcp 65001 >nul' + #13#10 +
    '"' + ExePath + '" --config "' + ConfigPath + '"' + #13#10 +
    'exit /b %ERRORLEVEL%' + #13#10;
  SaveStringToFile(LauncherPath, Command, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    EnsureDefaultConfig;
    if WizardIsTaskSelected('installtask') then
    begin
      WriteTaskLauncher;
      if not CreateDailyTask then
        MsgBox('每日任务创建失败，可在程序“设置”页中重试。', mbError, MB_OK);
    end;
  end;
end;
