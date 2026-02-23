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
- **设置界面** — 基于 Flet 的现代化 GUI（从托盘菜单打开）

## 🖱️ 支持设备

### 雷蛇 (Razer)

| 设备 | PID | 连接方式 | 状态 |
|------|-----|----------|------|
| 巴塞利斯蛇 V3 Pro (无线 Dongle) | 0x00AB / 0x00CD | 2.4G 无线 | ✅ 已验证 |
| 毒蝰 V2 Pro (无线 Dongle) | 0x007D / 0x00AF | 2.4G 无线 | 🔧 理论支持 |
| 毒蝰 V3 Pro (无线 Dongle) | 0x00C4 / 0x00C6 | 2.4G 无线 | 🔧 理论支持 |
| 蝰蛇 V3 Hyperspeed | 0x00B4 / 0x00B6 | 2.4G 无线 | 🔧 理论支持 |

### 罗技 (Logitech)

| 设备 | 接收器 PID | 连接方式 | 状态 |
|------|------------|----------|------|
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

### 从源码打包 EXE

```powershell
pip install pyinstaller
python build.py
# 输出: dist/MouseBattery.exe
```

## 🔧 运行依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| [hidapi](https://pypi.org/project/hidapi/) | ≥0.14 | HID 设备通信 |
| [Pillow](https://pypi.org/project/Pillow/) | ≥10.0 | 绘制托盘图标 |
| [pystray](https://pypi.org/project/pystray/) | ≥0.19 | 系统托盘功能 |
| [flet](https://flet.dev/) | ≥0.80 | GUI 设置界面 |

## 🏗️ 项目结构

```
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

## ❓ 常见问题

**Q: 检测不到我的鼠标怎么办?**
1. 确认鼠标通过 2.4G 无线接收器连接（不支持蓝牙）
2. 雷蛇用户：确认设备型号在支持列表中
3. 罗技用户：关闭 Logitech G Hub 后重试

**Q: 需要管理员权限吗?**
通常不需要。如果检测不到设备，可尝试右键"以管理员身份运行"。

**Q: 电量数值和官方驱动不一致?**
应与官方驱动一致（±1% 舍入误差）。如有较大差异请提交 Issue。

## 📄 许可

MIT License
