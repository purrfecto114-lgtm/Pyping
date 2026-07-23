# Pyping v0.4.0 Windows 安全打包说明

## 推荐产物

`release` 模式生成：

- `dist\Pyping\`：PyInstaller onedir；
- `dist-onefile\Pyping.exe`：单 EXE 便携版；
- `release\Pyping-v0.4.0-Windows-x64-portable.zip`；
- `release\Pyping-v0.4.0-Windows-x64-onefile.exe`；
- `release\Pyping-Setup-0.4.0-x64.exe`：存在 Inno Setup 时生成；
- `release\release-manifest.json`：文件名、大小和 SHA-256；
- `release\SHA256SUMS.txt`：所有可发布文件的独立校验值。

正式安装发行建议使用 **PyInstaller onedir + Inno Setup**。onefile 适合临时携带，但不是默认安装器输入。

## 系统要求

- Windows 10/11 x64；
- 64 位 Python 3.10–3.13；
- Inno Setup 6/7，仅安装器需要。

脚本拒绝 32 位 Python。它依次尝试 Python Launcher 的 3.13、3.12、3.11、3.10 x64，再尝试 `python` 与 `python3`。

## 一键发布构建

```text
packaging\build_windows.bat
```

或：

```powershell
powershell -NoLogo -NoProfile -File packaging\build_windows.ps1 -Mode release
```

强制要求安装器：

```powershell
powershell -NoLogo -NoProfile -File packaging\build_windows.ps1 -Mode release -RequireInstaller
```

## 可用模式

```powershell
# 测试、静态安全策略、依赖和版本一致性检查
.\packaging\build_windows.ps1 -Mode check

# 只构建 onedir
.\packaging\build_windows.ps1 -Mode onedir

# 只构建 onefile
.\packaging\build_windows.ps1 -Mode onefile

# 构建 onedir 并强制生成安装器
.\packaging\build_windows.ps1 -Mode installer

# 清理构建产物和缓存，保留构建虚拟环境
.\packaging\build_windows.ps1 -Mode clean

# 同时删除构建虚拟环境
.\packaging\build_windows.ps1 -Mode clean -DeepClean
```

重新创建构建环境：

```powershell
.\packaging\build_windows.ps1 -Mode release -RecreateEnvironment
```

## 依赖策略

正式 Windows 构建使用：

```text
packaging\requirements-windows.lock
```

该文件完整固定运行时和 PyInstaller 传递依赖，并列出 Python 3.10–3.13 Windows x64 wheel 的 SHA-256。构建命令使用：

- `--require-hashes`；
- `--force-reinstall`；
- `--no-cache-dir`；
- `--only-binary=:all:`。

安装后，`tools\verify_build_environment.py` 会从虚拟环境的 site-packages 读取发行包清单，只允许锁文件中的包和 venv 自带的 `pip`；发现额外包、缺失包或版本漂移即终止构建。

`requirements-runtime.txt` 仅为只读 CI 提供精确运行时版本；发布构建只能使用完整哈希锁。旧的 `requirements-build.txt` 已删除，避免与锁文件双重维护和版本漂移。更新依赖时必须从官方索引核对文件哈希、重新运行测试，并审核发布说明。

## 清理安全

清理脚本只允许删除项目根目录内的已知生成路径，并拒绝项目根目录本身或外部路径。递归缓存清理跳过 `.git`、构建虚拟环境和 reparse point，防止目录联接或符号链接将删除范围引向仓库外部。

## Inno Setup

安装器脚本通过自身 `SourcePath` 推导项目根目录，不依赖终端当前目录。构建完成后，脚本会检查精确的安装器文件名；仅看到 `ISCC.exe` 返回成功并不足以判定安装器有效。
简体中文消息文件通过 `compiler:Languages\ChineseSimplified.isl` 从 Inno Setup 编译器目录加载，源码仓库不再保留容易过期的语言文件副本。

## GitHub Actions 安全模型

### 只读验证

`.github\workflows\ci.yml` 在 PR、主分支推送和手动触发时运行。它只获得 `contents: read`，不使用依赖缓存，不允许 `pull_request_target`。

### Windows 构建

`.github\workflows\build-windows.yml` 的 `build` 作业：

- 固定使用 `windows-2022`；
- 只有 `contents: read`；
- checkout 设置 `persist-credentials: false` 和完整历史读取；
- 发布标签必须指向默认分支可达的提交；
- 不使用 Chocolatey 或运行时下载 Inno Setup；
- 使用 runner 已安装的 Inno Setup；
- 上传精确列出的五个文件；
- 所有外部 Actions 固定到 40 位 commit SHA。

### 发布隔离

`publish` 作业仅在版本标签运行：

- 单独获得 `contents: write`；
- 不 checkout、不安装依赖、不执行仓库构建脚本；
- 仅下载前一个只读作业生成的 artifact；
- 校验标签与版本、manifest 和 SHA-256；
- 使用 runner 自带 GitHub CLI 上传精确文件列表。

请在仓库设置中为 `release` Environment 配置 required reviewers，并限制版本标签创建权限。也可启用“Require actions to be pinned to a full-length commit SHA”。

## 发布前检查

- 在干净 Windows 10 与 Windows 11 x64 虚拟机安装、启动和卸载；
- 测试 100%、125%、150%、200% 缩放；
- 测试 1366×768 和 1920×1080；
- 验证 IPv4、IPv6、域名解析、停止、CSV 和 PNG；
- 使用组织代码签名证书签名 EXE 和安装器；
- 运行 `tools\verify_release.py`；
- 在另一台机器核对 `SHA256SUMS.txt`。

PyInstaller 需要在目标操作系统上构建；Linux 环境不能生成可信的 Windows EXE。
