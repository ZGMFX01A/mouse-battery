"""公开兼容层：键盘 HID 读取实现已迁入私有核心包。

本文件只保留 GUI / tray / 共享状态仍需依赖的公开 DTO 与入口函数，
避免未来公开仓库继续包含协议细节与逆向实现正文。
"""

from mouse_battery_core.keyboard_hid import (
    KeyboardCandidate,
    KeyboardInfo,
    ParsedBatteryInfo,
    enumerate_keyboard_candidates,
    read_keyboard_battery,
    resolve_keyboard_candidate,
)

__all__ = [
    "KeyboardCandidate",
    "KeyboardInfo",
    "ParsedBatteryInfo",
    "enumerate_keyboard_candidates",
    "read_keyboard_battery",
    "resolve_keyboard_candidate",
]
