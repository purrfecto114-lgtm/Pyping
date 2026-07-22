# Pyping v0.4.0 Windows 打包说明

## 推荐产物

默认发布流程生成：

- `dist\Pyping\`：PyInstaller onedir，可直接启动和排查依赖；
- `dist-onefile\Pyping.exe`：单 EXE 便携版；
- `release\Pyping-v0.4.0-Windows-x64-portable.zip`：推荐便携分发包；
- `release\Pyping-v0.4.0-Windows-x64-onefile.exe`：单文件副本；
- `release\Pyping-Setup-0.4.0-x64.exe`：检测到 Inno Setup 时生成；
- `release\SHA256SUMS.txt`：发布产物校验值。

正式安装发行建议采用 **PyInstaller onedir + Inno Setup**。单 EXE 适合临时携带，但不是默认安装器输入。

## 系统要求

- Windows 10/11 x64；
- 任意可用的 64 位 Python 3.10 或更高版本；
- Inno Setup 6/7（仅安装器需要）。

脚本依次尝试 Python Launcher 的 3.13、3.12、3.11、3.10，再尝试 `python` 和 `python3`，不再要求机器必须存在 `py -3.12`。

## 一键发布构建

双击：

```text
packaging\build_windows.bat
```

或运行：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Mode release
```

`release` 模式会构建 onedir 和 onefile。未安装 Inno Setup 时，脚本发出警告但仍保留便携包；需要把安装器作为强制条件时使用：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1 -Mode release -RequireInstaller
```

## 可用模式

```powershell
# 只运行测试和静态验证
.\packaging\build_windows.ps1 -Mode check

# 只构建 onedir
.\packaging\build_windows.ps1 -Mode onedir

# 只构建 onefile
.\packaging\build_windows.ps1 -Mode onefile

# 构建 onedir 并强制生成 Inno Setup 安装器
.\packaging\build_windows.ps1 -Mode installer

# 清理生成目录和缓存，保留构建虚拟环境
.\packaging\build_windows.ps1 -Mode clean

# 连构建虚拟环境一起删除
.\packaging\build_windows.ps1 -Mode clean -DeepClean
```

重新创建隔离构建环境：

```powershell
.\packaging\build_windows.ps1 -Mode release -RecreateEnvironment
```

构建脚本使用独立 `.venv-build`，每个外部命令均检查退出码。批处理包装器会把失败码返回给终端或 CI。

## Inno Setup 路径

安装脚本通过 `SourcePath` 推导项目根目录，并设置 `SourceDir`，因此无论从资源管理器、项目根目录还是其他终端目录调用，均从以下位置读取 onedir：

```text
dist\Pyping\
```

## GitHub Actions

`.github\workflows\build-windows.yml` 在 `windows-latest` 上调用与本地相同的 `build_windows.ps1`：

- `actions/checkout@v7`
- `actions/setup-python@v7`
- `actions/upload-artifact@v4`
- Python 3.12 x64
- Inno Setup

推送 `v*` 标签或手动触发即可生成发布产物。

## 发布前检查

- 在干净 Windows 10 与 Windows 11 虚拟机安装、启动、卸载；
- 测试 100%、125%、150%、200% 缩放；
- 测试 1366×768 和 1920×1080；
- 验证 IPv4、IPv6、域名解析、停止、CSV 和 PNG；
- 对 EXE 与安装器进行代码签名；
- 核对 `SHA256SUMS.txt`。

PyInstaller 需要在目标操作系统上构建；Linux 环境不能生成可信的 Windows EXE。
