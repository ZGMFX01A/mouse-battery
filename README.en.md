# 🖱️ Mouse Battery Monitor

English | [简体中文](README.md)

A lightweight Windows tray utility for monitoring battery status of Logitech and Razer wireless mice, with quick access to refresh actions, settings, and low-battery alerts.

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)
![Usage](https://img.shields.io/badge/Usage-Non--Commercial-orange)

![Demo](assets/演示图.png)

## ✨ Features

- **Tray resident**: Runs in the Windows system tray and shows battery status at a glance.
- **Automatic refresh**: Detects connected devices and refreshes battery data periodically.
- **Multi-brand support**: Designed for Logitech and Razer 2.4G wireless mice.
- **Mechanical keyboard extension**: Supports battery reading and binding for Weikav (Huafenda) dual-8K mechanical keyboard solutions.
- **Quick tray actions**: Refresh now, open settings, or exit from the tray menu.
- **Low-battery alerts**: Helps you avoid unexpected power loss during use.
- **Persistent preferences**: Saves notification threshold, tray icon priority, auto update, and other settings.
- **Bilingual UI**: Supports switching between Chinese and English in the settings window.
- **Auto update**: Supports update checking and automatic update flow.

## 🖱️ Supported Devices

### Razer

| Device | Connection | Status |
| :--- | :--- | :--- |
| Basilisk V3 Pro | 2.4G wireless dongle | ✅ Verified |
| Viper V2 Pro | 2.4G wireless dongle | 🔧 Theoretically supported |
| Viper V3 Pro | 2.4G wireless dongle | 🔧 Theoretically supported |
| DeathAdder V3 Hyperspeed | 2.4G wireless dongle | 🔧 Theoretically supported |

### Logitech

| Device | Connection | Status |
| :--- | :--- | :--- |
| G903 / G703 | Lightspeed | 🔧 Theoretically supported |
| G502X | Lightspeed | 🔧 Theoretically supported |
| G Pro Wireless | Lightspeed | 🔧 Theoretically supported |

### Mechanical Keyboards

| Device / Solution | Connection | Status |
| :--- | :--- | :--- |
| Weikav (Huafenda) dual-8K mechanical keyboard solution | 2.4G receiver | ✅ Supported |

> Note: If Logitech battery data cannot be read, close Logitech G Hub first to avoid HID device conflicts.
>
> Note: Mechanical keyboard support currently targets the Weikav dual-8K receiver path. The keyboard must be connected through its 2.4G receiver and bound manually in the settings window.

## 🚀 Quick Start

1. Download the latest `MouseBattery.exe` from [Releases](../../releases).
2. Launch the program and look for its icon in the Windows system tray.
3. Connect your mouse through a 2.4G wireless receiver.
4. If the device is not detected, close vendor software that may occupy the HID interface. If needed, run the app as administrator.

## 📖 How to Use

### 1. Check battery status

- After launch, the app stays in the tray.
- Hover over the tray icon to see device name, battery percentage, and charging state.

### 2. Use the tray menu

- **Refresh now**: Trigger a manual device scan and battery refresh.
- **Open settings**: Open the settings window to adjust preferences.
- **Quit**: Exit the tray application.

### 3. Adjust settings

The settings window is mainly used to:

- change the low-battery notification threshold
- configure tray icon priority
- switch the UI language
- enable or disable auto update
- review currently detected device status

### 4. Bind a mechanical keyboard

To enable battery display for a Weikav dual-8K mechanical keyboard:

1. Make sure the keyboard is connected through its **2.4G receiver**.
2. Open the settings window.
3. Click **Add Keyboard** and wait for candidate scanning to finish.
4. Select the target keyboard from the list and complete the binding.
5. After binding succeeds, the keyboard card will appear in the settings window and the tray status will also include keyboard battery information.

## ❓ FAQ

### My mouse is not detected. What should I do?

1. Make sure the mouse is connected through a **2.4G wireless receiver**. Bluetooth-only scenarios are not the main target here.
2. Confirm that the model is listed above or belongs to a compatible protocol family.
3. Logitech users should close G Hub and try again.
4. If needed, run the app as administrator.

### Why does the tray show no battery value or `N/A`?

- The device may be sleeping, just connected, still waiting for the first scan, or temporarily unavailable for battery reading.
- Try **Refresh now** and wait for the next synchronization.

### Can it monitor multiple devices at the same time?

- Yes. Detected devices are shown through tray status and the settings window.

### Why does the mechanical keyboard need manual binding?

- A Weikav dual-8K keyboard may expose multiple HID interfaces. During the **Add Keyboard** flow, the app filters candidates automatically and binds the interface that is most suitable for battery reading.

### Is a small difference from the official driver normal?

- Yes. Minor differences can happen because of refresh timing or percentage conversion.

## 👤 Author

- **GitHub**: [ZGMFX01A](https://github.com/ZGMFX01A)
- **Email**: 839140758@qq.com

## © Copyright

All rights reserved. **Commercial use is prohibited.** This project is for personal learning and non-commercial use only.

Without explicit authorization from the author, this project and its derivative versions may not be used for:

- commercial sales
- paid distribution
- commercial integration
- paid internal enterprise deployment
- any other direct or indirect profit-making purpose

Please contact the author in advance if you need commercial authorization.
