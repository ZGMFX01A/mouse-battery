# 🖱️ 鼠标电量监控 (Mouse Battery Monitor)

Windows 系统托盘应用，实时监控无线鼠标电池电量。

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ 功能

- **系统托盘常驻** — 动态图标实时显示电量数字和颜色
- **自动刷新** — 每 60 秒自动检测电池状态变化
- **多品牌支持** — 支持罗技和雷蛇无线鼠标
- **右键菜单** — 查看设备列表、手动刷新、打开设置界面
- **设置面板** — 基于 Flet 的现代化 GUI（从托盘右键双击打开）
- **开机自启功能** — 支持开机跟随 Windows 启动，在后台静默运行并监控
- **低电量自动弹窗** — 用户可自定义 10%、20%、30% 档位，触发阈值时原生系统级右下角告警

## 🖱️ 支持设备

### 雷蛇 (Razer)

| 设备 | PID | 连接方式 | 状态 |
| :--- | :--- | :--- | :--- |
| 巴塞利斯蛇 V3 Pro (无线 Dongle) | 0x00AB / 0x00CD | 2.4G 无线 | ✅ 已验证 |
| 毒蝰 V2 Pro (无线 Dongle) | 0x007D / 0x00AF | 2.4G 无线 | 🔧 理论支持 |
| 毒蝰 V3 Pro (无线 Dongle) | 0x00C4 / 0x00C6 | 2.4G 无线 | 🔧 理论支持 |
| 蝰蛇 V3 Hyperspeed | 0x00B4 / 0x00B6 | 2.4G 无线 | 🔧 理论支持 |

### 罗技 (Logitech)

| 设备 | 接收器 PID | 连接方式 | 状态 |
| :--- | :--- | :--- | :--- |
| G903 / G703 | 0xC541 | Lightspeed | 🔧 理论支持 |
| G502X | 0xC547 | Lightspeed | 🔧 理论支持 |
| G Pro Wireless | 0xC539 | Lightspeed | 🔧 理论支持 |

> **注意**：罗技设备需关闭 Logitech G Hub 软件，否则 HID 设备会被占用。

## 📦 安装运行

### 方式一：直接运行 EXE（推荐）

从 [Releases](../../releases) 下载 `MouseBattery.exe`，双击运行即可。

### 方式二：从源码运行

```powershell
# 克隆仓库
git clone <repo-url>
cd mouse-battery

# 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 运行
python main.py
```

### 方式三：GitHub Actions 自动构建 (开发者推荐)

本项目已配置 GitHub Actions 自动构建 CI：

1. 克隆并提交修改到你自己的仓库后。
2. 每次发布新版本，只需给代码打上 `v` 开头的 Tag：

   ```sh
   git tag v1.0.0
   git push origin v1.0.0
   ```

3. GitHub Actions 的服务器会自动执行 Windows 环境下的 PyInstaller 打包，并在你的仓库生成一个带版本号（例如 `MouseBattery-v1.0.0.exe`）的 GitHub Release 供用户下载。

### 从源码手动打包 EXE

```powershell
pip install pyinstaller
python build.py
# 输出: dist/MouseBattery.exe
```

## 🔧 运行依赖

| 依赖 | 版本 | 用途 |
| :--- | :--- | :--- |
| [hidapi](https://pypi.org/project/hidapi/) | ≥0.14 | HID 设备通信 |
| [Pillow](https://pypi.org/project/Pillow/) | ≥10.0 | 绘制托盘图标 |
| [pystray](https://pypi.org/project/pystray/) | ≥0.19 | 系统托盘功能 |
| [flet](https://flet.dev/) | ≥0.80 | GUI 设置界面 |

## 🏗️ 项目结构

```text
mouse-battery/
├── main.py          # 程序入口（托盘模式）
├── tray.py          # 系统托盘图标与菜单
├── gui.py           # Flet GUI 设置界面
├── devices.py       # 设备管理器（扫描、刷新）
├── razer_hid.py     # 雷蛇 USB 报文协议
├── logitech_hid.py  # 罗技 HID++ 2.0 协议
├── build.py         # PyInstaller 打包脚本
├── requirements.txt # Python 依赖
└── README.md
```

## 🔬 技术实现

### 雷蛇协议

通过 USB HID Feature Report 与雷蛇无线接收器通信，使用 90 字节的自定义报文格式：

- **接口选择**：必须选择 `interface_number=0`（MI_00 主控制接口）
- **电量查询**：`command_class=0x07, command_id=0x80`
- **充电查询**：`command_class=0x07, command_id=0x84`
- **电量换算**：原始值范围 0-255，需 `raw × 100 ÷ 255` 转换为百分比
- **Transaction ID**：默认 `0x1F`

### 罗技协议

通过 HID++ 2.0 协议与 Lightspeed 接收器通信：

- **UNIFIED_BATTERY** (Feature 0x1004)
- **BATTERY_STATUS** (Feature 0x1000)
- **BATTERY_VOLTAGE** (Feature 0x1001)

## ✅ 手动验证清单

下面这份清单适合在修改 [`main.py`](main.py)、[`devices.py`](devices.py)、[`gui.py`](gui.py)、[`updater.py`](updater.py)、[`logitech_hid.py`](logitech_hid.py)、[`razer_hid.py`](razer_hid.py) 后做回归验证。

### 1. 托盘模式

- 启动 [`main.py`](main.py) 后，确认系统托盘图标能够正常出现。
- 首次启动时确认托盘标题会从“正在扫描...”切换到实际设备状态。
- 右键菜单中的“立即刷新”“打开设置”“退出”都可以正常工作。
- 重复启动程序时，确认单实例保护生效，不会出现多个托盘实例。

### 2. GUI 模式

- 从托盘打开设置窗口，确认只会打开一个 GUI 窗口。
- GUI 初次打开时，确认会先显示统一的加载态，然后切换到设备卡片或空状态。
- 没有设备时，确认界面展示“未发现鼠标设备”占位卡片，而不是空白区域。
- 刷新失败或共享状态读取失败时，确认界面会展示错误态提示，而不是静默无响应。
- 打开自动刷新后，确认设备状态会周期同步；关闭后，确认不会继续后台刷新。

### 3. 共享状态文件

- 删除 [`.device_state.json`](.device_state.json) 后重新打开 GUI，确认不会崩溃，并能回到空状态或等待下一次同步。
- 手动将 [`.device_state.json`](.device_state.json) 改成非法 JSON，确认 GUI 不会崩溃，并会沿用上次有效快照或展示错误提示。
- 托盘刷新设备状态时，确认 GUI 不会因为读取半截文件而出现解析异常或列表闪烁。

### 4. 设备与边界场景

- 在未连接任何鼠标接收器时启动程序，确认托盘与 GUI 都能稳定显示无设备状态。
- 鼠标休眠后再唤醒，确认 [`DeviceManager`](devices.py:47) 能在后续刷新中恢复状态，不会长期停留在错误值。
- 罗技设备上确认关闭 G Hub 后仍能稳定读取；雷蛇设备上确认无线 dongle 场景可持续刷新。
- 长时间运行后，确认不会出现不断新增刷新线程、界面卡顿或明显的 CPU 异常占用。

### 5. 更新流程

- 在 GUI 中点击“检查更新”，确认按钮忙碌态、超时提示、无更新提示都符合预期。
- 若存在新版本，确认更新下载时进度条会前进，且不会因为主进程提前退出导致更新中断。
- 热更新触发后，确认主进程会优雅退出，旧版本文件可被替换，新版本能够重新拉起。
- 更新失败时，确认日志里能看到失败原因，并且不会留下无法解释的静默残留状态。

### 6. 建议重点回归的系统场景

- Windows 睡眠 → 唤醒后立刻观察托盘和 GUI 的电量状态。
- 插拔无线接收器后执行一次“立即刷新”。
- 在网络不稳定或离线状态下执行“检查更新”。
- 启动 GUI 后保持窗口打开，再从托盘或 GUI 触发刷新与更新流程，观察按钮状态是否一致。

## ❓ 常见问题

**Q: 检测不到我的鼠标怎么办?**

1. 确认鼠标通过 2.4G 无线接收器连接（不支持蓝牙）
2. 雷蛇用户：确认设备型号在支持列表中
3. 罗技用户：关闭 Logitech G Hub 后重试

**Q: 需要管理员权限吗?**
通常不需要。如果检测不到设备，可尝试右键"以管理员身份运行"。

**Q: 电量数值和官方驱动不一致?**
应与官方驱动一致（±1% 舍入误差）。如有较大差异请提交 Issue。

## 👤 作者

- **GitHub**: [ZGMFX01A](https://github.com/ZGMFX01A)
- **Email**: 839140758@qq.com

## 📄 许可

MIT License
