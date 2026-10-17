# 内网 IP 活跃度审计工具（IP Activity Auditor）

[![Build](https://github.com/miralexand/IPActivityAuditor/actions/workflows/build.yml/badge.svg)](https://github.com/miralexand/IPActivityAuditor/actions/workflows/build.yml)

通过 ARP 扫描指定内网网段，记录每个 IP 的最后活跃日期，并生成“7 天未使用”“15 天未使用”的 CSV 清单。提供现代化图形界面，同时保留完整的命令行模式，适合人工巡检与任务计划无人值守运行。

> **授权使用声明**：本工具仅可用于你拥有管理权限、且已获得明确授权的内网。程序只扫描 `config.ini` 中的 CIDR，不会自动扩展网段。请勿用于未经授权的网络探测。

## 功能亮点

- **现代图形界面**：明亮主题、橙色强调色、更大字号、概览卡片、在线率环图、14 天趋势图、库存/扫描记录表格、报表预览与设置中心
- **双版本交付**：Windows 安装版（Inno Setup）与免安装便携版（ZIP）
- **GUI + CLI 同一程序**：双击打开界面；带参数运行时自动进入命令行模式，兼容任务计划
- **托盘静默运行**：关闭窗口自动隐藏到系统托盘；支持托盘菜单扫描/报表/退出、开机自启（静默）和后台完成通知
- **无需 Npcap**：默认使用 Windows 原生 SendARP 扫描，只统计有 ARP 响应的 IP；可选 Scapy 后端（需 Npcap）
- 每日定时 ARP 扫描，支持无人值守运行
- SQLite 保存 IP 库存、扫描记录和每日在线历史
- **扫描成功才更新 `last_seen_date`**；扫描失败只写失败记录，不误判为“未使用”
- 生成 UTF-8 BOM 编码 CSV，Excel 直接打开中文不乱码
- 严格限定配置中的 CIDR，默认不扫描其他网段
- 内置“以管理员身份重启”“创建/删除每日任务”“打开日志/报表目录”等系统集成

## 交付版本

| 版本 | 文件 | 说明 |
| --- | --- | --- |
| 安装版 | `dist\IPActivityAuditor-Setup-0.4.0.exe` | 安装到 `Program Files`，创建开始菜单快捷方式，可选创建每日任务和桌面快捷方式 |
| 便携版 | `dist\IPActivityAuditor-portable-0.4.0.zip` | 解压后直接运行，数据保存在程序目录 |
| 便携目录 | `dist\IPActivityAuditor-portable\` | 未压缩的便携版目录，便于本地测试 |

安装版将配置与数据放在：

```text
C:\ProgramData\IPActivityAuditor\
├─ config.ini
├─ run_scan.cmd
├─ data\ip_activity.db
├─ reports\
└─ logs\ip_activity_auditor.log
```

便携版将配置与数据放在程序所在目录，均为可写位置即可。

## 环境要求

- Windows 10/11（64 位）
- 默认 SendARP 后端无需管理员权限；**Scapy 后端、安装版安装、SYSTEM 每日任务**需要管理员权限
- **默认无需 Npcap**：`backend = auto` 在 Windows 上使用系统原生 SendARP
- 可选 Scapy 后端需要 [Npcap](https://npcap.com/)，安装时勾选 **Install Npcap in WinPcap API-compatible Mode**
- 源码运行需要 Python 3.11+；打包后的 exe 不要求目标机器安装 Python
- 目标机器需与待扫描网段处于同一二层广播域（例如在同一 VLAN）
- 构建安装版需要 Inno Setup 6；构建便携 ZIP 可选 7-Zip

## 快速开始

### 方式一：安装版

1. 运行 `IPActivityAuditor-Setup-0.4.0.exe`。
2. 按需勾选“创建桌面快捷方式”和“创建每日自动扫描任务”。
3. 安装完成后启动程序。
4. 首次启动会自动创建 `config.ini` 和所需目录。
5. 进入 **设置**，把“扫描网段（CIDR）”改成实际网段并保存。
6. 在 **概览** 点击“开始扫描”。

> 关闭窗口后程序会隐藏到系统托盘继续运行，不会直接退出；要从托盘菜单或设置页选择“退出程序”。默认 SendARP 后端无需 Npcap，也无需管理员权限。

### 方式二：便携版

1. 解压 `IPActivityAuditor-portable-0.4.0.zip` 到可写目录（不要放在只读目录）。
2. 双击 `IPActivityAuditor.exe`。
3. 在 **设置** 中修改 CIDR 并保存。
4. 点击“开始扫描”。

> 便携版建议放在 `D:\Tools\IPActivityAuditor\` 这类可写目录。

### 方式三：源码运行

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 src\main.py --gui
```

首次运行会在项目根目录创建 `config.ini`、`data\`、`reports\`、`logs\`。

## 图形界面

启动后左侧为导航栏，右侧为内容区：

| 页面 | 功能 |
| --- | --- |
| **概览** | 库存总数、当前在线、7/15 天未使用、在线率环图、最近扫描、快捷操作 |
| **IP 库存** | 按 IP 搜索、按在线/离线/从未活跃筛选、导出 CSV |
| **扫描记录** | 扫描总次数/成功率、近 14 天在线趋势、历次扫描详情 |
| **报表中心** | 自定义天数生成报表、浏览报表文件、预览前 200 行、直接用 Excel 打开 |
| **设置** | 修改 CIDR/扫描后端/超时/重试/路径、查看权限与后端状态、托盘与开机自启、创建或删除每日任务 |

快捷操作：

- **开始扫描**：后台执行 ARP 扫描，完成后自动刷新界面；可勾选“扫描完成后自动生成报表”
- **初始化库存**：按当前 CIDR 写入所有可用 IP，不进行网络扫描
- **生成报表**：使用配置中的天数或报表中心输入的天数
- **F5**：刷新当前页面数据
- 右下角会显示操作结果提示

## 托盘静默运行

- **关闭窗口即隐藏到托盘**：程序继续在后台运行，已启动的扫描/报表任务不会中断
- **静默启动**：`IPActivityAuditor.exe --tray --config "配置文件路径"`
- **托盘菜单**：打开主界面、立即扫描、生成报表、退出
- **后台通知**：隐藏状态下扫描或报表完成后，通过托盘通知显示结果
- **开机自启**：设置页勾选“开机自动启动（静默到托盘）”，写入当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
- **彻底退出**：托盘菜单“退出”，或设置页“退出程序”
- **单实例**：重复启动会自动唤出已运行的主界面，不会产生多个托盘图标

> 开机自启命令会记录当前 exe 和配置文件路径。便携版移动目录后，请重新勾选一次开机自启。
>
> 无人值守的每日扫描仍建议使用“每日自动扫描”任务计划（SYSTEM 最高权限）；托盘模式适合当前用户交互与后台驻留。

## 命令行

带任意参数启动时进入命令行模式；**不带参数启动时打开图形界面**。显式打开界面可使用 `--gui`。

```powershell
IPActivityAuditor.exe --gui
IPActivityAuditor.exe --tray --config "C:\ProgramData\IPActivityAuditor\config.ini"
IPActivityAuditor.exe --config config.ini
IPActivityAuditor.exe --scan
IPActivityAuditor.exe --report --days 7,15
IPActivityAuditor.exe --init
IPActivityAuditor.exe --version
```

| 参数 | 说明 |
| --- | --- |
| `--gui` | 打开图形界面，可与其他参数组合指定配置文件 |
| `--tray` | 启动图形界面并隐藏到系统托盘静默运行 |
| `--config PATH` | 配置文件路径；默认优先使用 exe 同目录 `config.ini`，否则使用 `%ProgramData%\IPActivityAuditor\config.ini` |
| `--init` | 将 CIDR 内所有可用 IP 写入库存表，`last_seen_date` 为空 |
| `--scan` | 执行一次 ARP 扫描并保存结果 |
| `--report` | 生成未使用 IP 报表 |
| `--days 7,15` | 覆盖配置中的报表天数 |
| `--version` | 显示版本号 |

行为约定：

- **不带任何参数**时打开图形界面
- **带 `--gui` 或 `--tray`** 时打开图形界面
- **其他参数**执行命令行逻辑
- `--config` 单独使用等同于“扫描 + 生成配置中的报表”，兼容旧任务计划
- `--scan` 只扫描，不生成报表
- 默认流程中如果扫描失败，会跳过报表生成并返回非 0，避免产出误导性清单
- 相对路径以**配置文件所在目录**为基准

## 配置说明

```ini
[scan]
cidr = 192.168.0.0/24
backend = auto
timeout_ms = 1500
retries = 1

[storage]
db_path = data/ip_activity.db

[report]
report_dir = reports
unused_days = 7,15

[log]
log_dir = logs
```

- `cidr`：仅支持 IPv4，且必须是规范网段（如 `192.168.0.1/24` 会因包含主机位而被拒绝）
- `backend`：扫描后端，`auto`（默认）、`sendarp` 或 `scapy`
  - `auto`：Windows 上使用 SendARP，其他平台使用 Scapy
  - `sendarp`：Windows 原生 ARP 探测，无需 Npcap，使用系统 ARP 超时
  - `scapy`：传统 Scapy 广播 ARP，需要 Npcap 和 WinPcap 兼容模式
- `timeout_ms`：Scapy 后端的 ARP 等待超时（毫秒）；SendARP 使用系统 ARP 超时
- `retries`：对未响应主机的重试次数，不做高频循环
- `unused_days`：报表天数列表，逗号分隔
- 所有日期使用本地时区，格式 `YYYY-MM-DD`

图形界面“设置”页保存配置时会进行完整校验；非法值不会写入。

## 扫描与判定逻辑

1. 检查扫描后端权限：Scapy 后端需要管理员权限，无权限返回退出码 `2`；默认 SendARP 后端普通权限即可运行。
2. 读取并校验配置，解析 CIDR。
3. 确定扫描网卡；若本机没有该网段的地址，直接判定失败（防止扫描落空后全量误判）。
4. 按配置选择扫描后端：
   - **SendARP**（默认）：并行调用 Windows `SendARP` 探测 CIDR 内每个 IPv4 地址，记录有 MAC 响应的主机
   - **Scapy**：发送广播 ARP 请求（目的 MAC `ff:ff:ff:ff:ff:ff`），只统计有响应的 IP
5. 扫描成功后，在一个事务中：
   - 在线 IP：更新 `last_seen_date`、`last_status='online'`，首次活跃时写入 `first_seen_date`
   - 未在线 IP：**不修改 `last_seen_date`**，只将 `last_status` 置为 `offline`
   - 写入 `scan_runs` 成功记录和 `daily_online` 在线历史
6. 扫描或保存失败时：
   - 只写入 `scan_runs` 失败记录和错误信息
   - **不更新任何 IP 的 `last_seen_date`**
   - 命令行返回退出码 `4`，GUI 显示失败提示

未使用判定（`D` 为扫描当天）：

```text
last_seen_date IS NULL OR last_seen_date <= D - N 天
```

`last_seen_date IS NULL` 表示从未活跃，会出现在报表中。

## 报表

输出示例：

```text
reports/IP未使用清单_7天_YYYYMMDD.csv
reports/IP未使用清单_15天_YYYYMMDD.csv
```

字段：

| 字段 | 说明 |
| --- | --- |
| `ip_address` | IP 地址，按数值自然排序（`.2` 在 `.10` 前） |
| `last_seen_date` | 最后活跃日期；从未活跃为空 |
| `days_unused` | 距最后活跃的天数；从未活跃为空 |
| `status` | `从未活跃` 或 `未使用` |

CSV 使用 `utf-8-sig` 编码，Excel 可直接打开。报表中心支持直接预览和打开文件。

## 数据库

`ip_inventory`：IP 库存与活跃状态。

| 字段 | 说明 |
| --- | --- |
| `ip_address` | 主键 |
| `cidr` | 所属网段（便于后续扩展多网段与变更过滤） |
| `first_seen_date` | 首次扫描到在线的日期 |
| `last_seen_date` | 最后扫描到在线的日期，可为空 |
| `last_status` | `online` / `offline` |
| `updated_at` | 本地时间戳 |

`scan_runs`：每次扫描记录（`success` / `failed`、在线数、耗时、错误）。

`daily_online(scan_date, ip_address)`：每日在线历史，用于趋势图与审计。

## 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 一般错误（数据库、日志、未处理异常） |
| `2` | 没有管理员权限 |
| `3` | 配置错误 |
| `4` | 扫描失败（未更新任何活跃日期） |
| `5` | 报表生成失败 |

## 日志

- 文件：`logs/ip_activity_auditor.log`
- 按大小轮转，单个文件 5 MB，保留 5 份
- 记录启动、配置摘要、扫描开始/结果、报表生成、异常与退出码
- GUI 的“设置”页可直接打开日志目录
- 单文件 exe 运行时无控制台窗口；命令行参数模式会尝试附加到调用它的控制台

## 打包构建

先安装依赖：

```powershell
py -3.11 -m pip install -r requirements.txt
```

### 一键构建

```powershell
.\build.ps1
```

脚本会依次完成：

1. 检测 Python 3.11+ 并安装依赖
2. 生成图标（`assets\icon.ico`、`assets\logo.png`）
3. 使用 PyInstaller 构建单文件 exe（GUI 模式自动隐藏控制台，命令行模式保持输出与退出码同步）
4. 生成便携目录与 ZIP
5. 调用 Inno Setup 生成安装版
6. 输出构建产物清单

可选参数：

```powershell
.\build.ps1 -SkipInstall                  # 跳过 pip install
.\build.ps1 -SkipInstaller                # 只构建 exe 和便携版
.\build.ps1 -SkipInstall -PythonExe python  # 指定 Python（CI 常用）
```

构建安装版需要安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。脚本会自动查找常见安装路径，也可通过 `winget install JRSoftware.InnoSetup` 安装。

### GitHub Actions 自动构建

工作流文件：`.github/workflows/build.yml`

- 推送到 `main`、提交 PR 或手动触发时：运行 16 项测试，构建 exe、便携版 ZIP 和安装版，并上传为 Actions Artifacts
- 推送 `v*` 标签时：自动创建 GitHub Release 并附加三个构建产物

发布新版本：

```powershell
git tag v0.4.0
git push origin v0.4.0
```

> `dist/` 已被 `.gitignore` 排除，构建产物通过 Actions Artifacts 或 GitHub Release 分发，不进入源码仓库。

### 手动构建

```powershell
py -3.11 -m PyInstaller --noconfirm --clean --onefile --console --hide-console hide-early `
  --name IPActivityAuditor `
  --icon assets\icon.ico `
  --version-file assets\version_info.txt `
  --add-data "assets;assets" `
  --collect-all scapy `
  --collect-all customtkinter `
  --collect-all pystray `
  src\main.py
```

安装版：

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" installer\setup.iss
```

> 单文件 PyInstaller 程序可能被杀毒软件误报，请按单位安全策略加白名单或进行代码签名。

## 任务计划程序

### 方式一：安装时自动创建

安装时勾选“创建每日自动扫描任务”，安装程序会：

- 在 `%ProgramData%\IPActivityAuditor\run_scan.cmd` 写入启动脚本
- 创建任务 `IPActivityAuditor`
- 每天 23:30 以 `SYSTEM` 最高权限运行扫描并生成报表

### 方式二：程序内创建

进入 **设置** → **每日自动扫描**，设置时间后点击“创建 / 更新任务”。需要管理员权限，程序会提示提权重启。

### 方式三：PowerShell 脚本

以管理员身份运行：

```powershell
.\install_task.ps1 -ExePath .\dist\IPActivityAuditor.exe -ConfigPath .\dist\config.ini -Time 23:30
```

### 方式四：手动创建

1. 打开“任务计划程序”，创建基本任务。
2. 触发器：每天，低峰时间（如 23:30）。
3. 操作：启动程序
   - 程序：`C:\Program Files\IPActivityAuditor\IPActivityAuditor.exe`
   - 参数：`--config "C:\ProgramData\IPActivityAuditor\config.ini"`
   - **起始于**：`C:\Program Files\IPActivityAuditor`
4. 常规选项卡勾选“使用最高权限运行”。
5. 选择“不管用户是否登录都要运行”。
6. 设置选项卡勾选“如果任务已在运行，则不再启动新实例”，并勾选“错过计划开始时间后尽快启动”。

## 验收自测

1. 运行 `--init`，确认库存中包含 `.1` 至 `.254`，且 `last_seen_date` 为空。
2. 有设备在线时运行 `--scan`，确认在线 IP 的 `last_seen_date` 为当天。
3. 将某 IP 的 `last_seen_date` 改为 8 天前，运行报表：出现在 7 天清单，不出现在 15 天清单。
4. 将某 IP 改为 16 天前：同时出现在 7 天和 15 天清单。
5. 从未活跃 IP 出现在报表中，`last_seen_date` 为空。
6. 模拟扫描失败：

   ```powershell
   $env:IPAA_FORCE_SCAN_FAIL = "1"
   py -3.11 src\main.py --config config.ini --scan
   echo $LASTEXITCODE
   Remove-Item Env:\IPAA_FORCE_SCAN_FAIL
   ```

   确认退出码为 `4`，且所有 `last_seen_date` 均未被修改，`scan_runs` 中新增 `failed` 记录。

7. 在未安装 Python、未安装 Npcap 的机器上运行 exe，确认默认 SendARP 后端可完成扫描。
8. 任务计划执行后检查当天的日志与报表。

## 测试

```powershell
py -3.11 -m unittest discover -s tests -v
```

测试覆盖库存初始化、成功扫描更新、失败扫描不修改活跃日期、7/15 天判定、自然排序、CSV 编码与文件名。

## 常见问题

- **提示没有管理员权限**：仅 Scapy 后端需要提权；默认 SendARP 可直接扫描。任务计划请检查是否勾选最高权限。
- **关闭窗口后程序不见了**：程序已隐藏到系统托盘继续运行；双击托盘图标或托盘菜单“打开主界面”即可恢复。
- **如何彻底退出**：托盘右键菜单选择“退出”，或在设置页点击“退出程序”。
- **开机没有自动启动**：在设置页重新勾选“开机自动启动（静默到托盘）”；便携版移动目录后需重新勾选。
- **扫描结果为 0**：检查本机是否有该网段地址、是否与目标同 VLAN、是否在 ARP 广播域内；默认 SendARP 后端不需要 Npcap。
- **提示无法加载 Scapy/Npcap**：只有 `backend = scapy` 才需要 Npcap；可在设置中改用“自动”或“SendARP”，或安装 Npcap 并勾选 WinPcap 兼容模式。
- **SendARP 报错**：确认本机有该 CIDR 内的地址，且扫描的是本地二层网段；跨网段扫描需使用同一 VLAN 内的机器。
- **安装版配置在哪里**：`C:\ProgramData\IPActivityAuditor\config.ini`；可通过开始菜单快捷方式启动或设置页打开。
- **便携版无法写数据**：把整个目录移动到可写位置，例如 `D:\Tools\`。
- **报表中文乱码**：请直接用 Excel 打开生成的 CSV；文件本身是 `utf-8-sig`。
- **杀毒软件误报**：单文件 PyInstaller 程序可能被误报，请加白名单或进行代码签名。

## 目录结构

```text
IPAA/
├─ .github/
│  └─ workflows/
│     └─ build.yml       # GitHub Actions 自动构建与 Release
├─ src/
│  ├─ main.py            # 入口、CLI/GUI 分发、日志、流程编排
│  ├─ gui.py             # 现代化图形界面
│  ├─ config.py          # 配置解析、校验与保存
│  ├─ scanner.py         # SendARP/Scapy 扫描后端
│  ├─ db.py              # SQLite 表结构与读写
│  ├─ report.py          # CSV 报表生成
│  ├─ apputil.py         # 路径、日志、提权、控制台辅助
│  └─ version.py         # 版本信息
├─ tests/
│  ├─ test_db_report.py
│  ├─ test_config_db_extra.py
│  └─ test_scanner_backend.py
├─ assets/
│  ├─ icon.ico
│  ├─ logo.png
│  └─ version_info.txt
├─ installer/
│  ├─ setup.iss          # Inno Setup 安装版脚本
│  └─ ChineseSimplified.isl
├─ tools/
│  └─ make_icon.py       # 图标生成脚本
├─ config.ini.example
├─ requirements.txt
├─ build.ps1             # 一键构建 exe + 便携版 + 安装版
├─ install_task.ps1
├─ README.md
└─ read.txt
```
