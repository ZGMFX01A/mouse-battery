"""公开兼容层：罗技 HID 协议实现已迁入私有核心包。

本文件只保留稳定导入面，避免未来公开仓库再次携带协议正文。
真正实现位于私有依赖 `mouse_battery_core` 中。
"""

from mouse_battery_core.logitech_hid import BatteryInfo, LogitechReceiver, find_logitech_receivers

__all__ = [
    "BatteryInfo",
    "LogitechReceiver",
    "find_logitech_receivers",
]
