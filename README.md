# Pyping GUI v0.4.0

Pyping 是一个基于 Python、Tkinter 与 `ping3` 的跨平台 Ping 桌面工具。本版本在保留原有 GUI 分区和操作顺序的基础上，重点优化界面一致性、长时间运行体验、日志管理、数据导出和 Windows 发布流程。

## v0.4.0 主要变化

### 保留布局的 GUI 优化

- 参数设置区、操作按钮区、会话统计区、实时输出区和底部状态区顺序保持不变。
- 使用统一的 Windows 11 / Fluent 风格浅色与深色调色板，分区背景、标签、统计卡片和日志不再出现混色。
- 输入框、按钮、边距和字体统一；同类输入框保持相同高度和可伸缩宽度。
- 启动窗口按实际工作区自适应：常规屏幕优先保证约 150 px 日志高度，小高度屏幕自动进入紧凑布局，避免日志和状态栏不可见。
- 新增会话统计卡片：总数、成功、超时、错误、失败率、平均/最小/最大延迟。
- 新增底部状态栏：运行状态、运行时间、数据库记录数、队列积压。
- 新增浅色、深色和跟随系统主题。
- 新增快捷键：
  - `Ctrl+Enter`：开始 Ping
  - `Esc`：停止
  - `Ctrl+G`：生成图表
  - `Ctrl+Shift+S`：导出 CSV

### 日志与数据

- 日志支持自动滚动开关、复制、清空和导出 TXT。
- 成功、超时、错误和状态信息采用不同颜色。
- 新增 CSV 流式导出，长会话不会一次性载入内存。
- CSV 导出使用独立 SQLite 只读连接，不阻塞正常数据库读取。
- 可清空当前会话统计和图表数据。

### 图表

- Ping 结束或停止后不会自动生成图表。
- 点击“生成图表”后选择时间范围：
  - 全部数据
  - 最近 1 分钟
  - 最近 5 分钟
  - 最近 15 分钟
  - 最近 1 小时
  - 最近 24 小时
  - 自定义起止时间
- 超过 5000 个显示点时按时间桶降采样；统计仍基于所选范围的全部原始记录。
- 超时和错误会中断折线。
- PNG 使用 Pillow 离屏渲染，不依赖屏幕截图。

### 测量与稳定性修复

- `ping3` 返回 `False` 时记录为网络错误，不再显示为 `0.00 ms`。
- 累计统计和图表窗口统计完全分离。
- 拒绝 `NaN`、`Inf`、异常整数和超大包。
- 使用 `time.monotonic()` 控制持续时间。
- 最后一次请求后不再额外等待一个发送间隔。
- 发送间隔可被停止事件立即打断。
- 支持 IPv4、IPv6 和双栈域名解析。
- 每次运行使用独立会话 ID、真实线程引用和有界队列。
- 实时日志限制为最近 5000 行。

## 环境要求

- Python 3.10 或更高版本
- Tkinter
- `ping3>=4.0.8,<6`
- `Pillow>=10,<13`

安装依赖：

```bash
python -m pip install -r requirements.txt
```

运行：

```bash
python PingTool.py
```

也可安装为命令：

```bash
python -m pip install .
pyping-gui
```

## 使用流程

1. 输入域名、IPv4 地址或 IPv6 地址。
2. 设置 ICMP 负载、发送间隔和单次超时。
3. 选择次数模式或持续时间模式。
4. 点击“开始 Ping”。运行期间参数会锁定。
5. 点击“停止”或等待任务自然结束。
6. 根据需要导出 CSV，或点击“生成图表”选择时间段。
7. 在图表窗口中保存 PNG。

运行期间也可对当前已写入数据库的记录生成图表或导出 CSV。

## 输入范围

| 参数 | 范围 |
|---|---|
| ICMP 负载大小 | 1–65500 字节 |
| 发送间隔 | 0.1–86400 秒 |
| 单次超时 | 0.05–300 秒 |
| 次数 | 1–10000000；0 或空表示无限 |
| 持续时间 | 大于 0，最长 31 天 |

## 数据存储

每次会话创建独立临时 SQLite 数据库：

- UI 线程批量写入结果；
- Ping 工作线程不直接操作 Tk 控件；
- 队列有容量上限；
- 新会话开始时清理上一会话数据库；
- 应用正常退出时删除临时数据库和 WAL 文件；
- CSV 采用批量读取和流式写入。

## 测试与验证

```bash
python -m unittest discover -s tests -p "test_*.py" -v
python tools/validate_project.py
```

Linux 安装 Xvfb 后可运行 GUI 冒烟测试：

```bash
PYTHONPATH=. xvfb-run -a python tests/gui_smoke.py
```

## Windows 打包

正式发布推荐采用：

1. PyInstaller `onedir`；
2. Inno Setup 安装程序；
3. ZIP 便携版；
4. 可选 `onefile` 单 EXE。

本地生成完整发布包（onedir、onefile、便携 ZIP、SHA256；安装 Inno Setup 时同时生成安装器）：

```text
packaging\build_windows.bat
```

脚本会自动查找 Python 3.10+，不会绑定某一个 Python 小版本。仅执行检查：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Mode check
```

清理生成目录：

```text
packaging\clean_windows.bat
```

便携单文件版：

```text
packaging\build_portable_onefile.bat
```

详细说明见：

```text
packaging\README_PACKAGING.md
```

仓库同时包含 GitHub Actions Windows 自动构建工作流：

```text
.github\workflows\build-windows.yml
```

> PyInstaller 不是交叉编译器。Windows EXE 必须在 Windows 环境中构建。

## 权限说明

`ping3` 使用 ICMP。部分 Linux/macOS 环境可能需要配置 ICMP 权限。应优先采用系统提供的最小权限配置，不建议长期以 root 或管理员身份运行整个 GUI。

## 项目地址

https://github.com/purrfecto114-lgtm/Pyping
