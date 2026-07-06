"""公开兼容层：雷蛇 HID 协议实现已迁入私有核心包。

本文件只保留稳定导入面，避免未来公开仓库再次携带协议正文。
真正实现位于私有依赖 `mouse_battery_core` 中。
"""

from mouse_battery_core.razer_hid import RazerBatteryInfo, RazerDevice, find_razer_devices

__all__ = [
    "RazerBatteryInfo",
    "RazerDevice",
    "find_razer_devices",
]
