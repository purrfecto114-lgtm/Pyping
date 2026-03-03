# Pyping
A GUI-based Ping tool with real-time output, packet loss statistics, duration/count modes, and lightweight chart visualization. Built with Python (tkinter + ping3).   一个带图形界面的 Ping 工具，支持实时输出、丢包率统计、次数/持续时间双模式，以及轻量级图表可视化。基于 Python (tkinter + ping3) 开发。
# Ping 可视化工具 / Ping Visualizer
[![GitHub Repo](https://img.shields.io/badge/GitHub-Pyping-181717?logo=github)](https://github.com/purrfecto114-lgtm/Pyping/)
[![Python Version](https://img.shields.io/badge/python-3.6%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

一个简单易用的 Ping 工具，提供图形界面、实时数据展示、丢包统计以及延迟可视化图表。  
A simple and user-friendly Ping tool with GUI, real-time output, packet loss statistics, and latency visualization charts.

---

## 功能特性 / Features

- **图形化参数设置**：可自定义目标主机、包大小、间隔，并支持两种模式（按次数 Ping 或按持续时间 Ping）。  
  **Graphical parameter settings**: Customize target host, packet size, interval, and choose between two modes (count-based or duration-based pinging).
- **域名预检**：开始 Ping 前自动解析域名，无效域名会弹出错误提示。  
  **Domain pre-check**: Automatically resolve domain before starting; shows error for invalid domains.
- **实时输出**：每次 Ping 结果显示具体延迟或失败原因（超时、解析失败、网络错误等）。  
  **Real-time output**: Display specific latency or failure reason (timeout, resolution failure, network error, etc.) for each ping.
- **丢包率统计**：底部实时更新总包数、丢包数和丢包百分比。  
  **Packet loss statistics**: Real-time update of total packets, lost packets, and loss rate at the bottom.
- **双模式支持**：
  - 次数模式：可设置固定次数（0 或留空表示无限）。
  - 持续时间模式：可设置持续秒数，时间到达后自动停止。  
  **Dual mode support**:
  - Count mode: Set a fixed number of pings (0 or empty for unlimited).
  - Duration mode: Set duration in seconds, automatically stops after the specified time.
- **轻量级图表**：Ping 结束后自动弹出内置的 tkinter Canvas 图表，清晰展示延迟曲线和丢包标记，并支持保存为图片（PostScript 格式）。  
  **Lightweight chart**: Automatically pops up a built-in tkinter Canvas chart after pinging, clearly showing latency curve and packet loss markers, with image saving support (PostScript format).
- **多图表管理**：若已有图表窗口未关闭，再次生成图表时会弹出超时保存对话框，避免数据丢失。  
  **Multi-chart management**: If an existing chart window is open, a timeout save dialog will appear to prevent data loss.
- **中文字体支持**：内置开源字体配置，确保中文显示正常。  
  **Chinese font support**: Built-in font configuration to ensure proper Chinese display.

---

## 截图 / Screenshot
见Release
Please visit Release

---

## 安装 / Installation

### 环境要求 / Requirements
- Python 3.6 或更高版本 / Python 3.6 or higher
- 依赖库 / Dependencies: `ping3` (需管理员/root 权限发送 ICMP 包)

### 步骤 / Steps
1. 克隆仓库 / Clone the repository:
   ```bash
   git clone https://github.com/yourusername/ping-visualizer.git
   cd ping-visualizer
安装依赖 / Install dependencies:

bash
pip install -r requirements.txt
或者手动安装 / Or install manually:

bash
pip install ping3
运行程序 / Run the program:

bash
python ping.py
注意：在 Linux 系统上，可能需要以 root 权限运行才能使用 ping3（例如 sudo python ping.py）。
Note: On Linux systems, you may need to run with root privileges to use ping3 (e.g., sudo python ping.py).

使用方法 / Usage
启动程序：运行 python ping.py，主窗口将打开。

设置参数：

输入目标主机（域名或 IP）。

输入包大小（字节，必须为正整数）。

输入间隔（秒）。

选择模式：次数模式或持续时间模式。

根据模式填写对应值（次数或秒数）。

开始 Ping：点击“开始 Ping”按钮。程序会先解析域名，成功后开始逐次 Ping。

实时观察：输出区域会逐行显示每次结果，底部状态栏同步更新总包、丢包和丢包率。

手动停止：可随时点击“停止”按钮结束测试（对无限模式尤其重要）。

查看图表：Ping 结束后（自动完成或手动停止），如果有数据，会弹出图表窗口。图表上蓝色线为延迟，红色“✕”为丢包点，底部显示统计摘要。

保存图表：点击图表窗口中的“保存为图片”按钮，可将当前图表保存为 PostScript 文件（可用图片查看器打开）。

依赖 / Dependencies
ping3 - 发送 ICMP 包并获取延迟

tkinter（Python 标准库，无需额外安装）

threading, queue, socket, datetime（均为 Python 标准库）

所有依赖均可在 requirements.txt 中找到。
All dependencies are listed in requirements.txt.

许可证 / License
本项目采用 MIT 许可证。详情请参见 LICENSE 文件。
This project is licensed under the MIT License. See the LICENSE file for details.

贡献 / Contributing
---

## 致谢 / Acknowledgement

- 本项目的部分代码由 AI 辅助生成。  
- Part of this project's code was generated with the assistance of AI.
欢迎提交 Issue 或 Pull Request 来改进这个工具！
Feel free to submit issues or pull requests to improve this tool!
