"""公开壳到私有核心的运行时桥接层。

这个模块的职责不是重复实现协议，而是把公开仓库真正需要依赖的
 DTO、扫描入口、生命周期动作和读电入口统一收口到一个稳定表面。

这样后续即使私有核心内部继续拆模块或调整实现，公开壳也只需要
 维持对这个桥接层的依赖，而不再散落直接 import 私有实现细节。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from mouse_battery_core.logitech_hid import (
    BatteryInfo,
    LogitechReceiver,
    find_logitech_receivers,
)
from mouse_battery_core.razer_hid import (
    RazerBatteryInfo,
    RazerDevice,
    find_razer_devices,
)
from mouse_battery_core.keyboard_hid import (
    KeyboardCandidate,
    KeyboardInfo,
    enumerate_keyboard_candidates,
    read_keyboard_battery,
)


# 公开壳只需要知道“这是哪个品牌的后端”，
# 不需要感知私有核心内部更细的模块拆分。
MouseBackendBrand = Literal["logitech", "razer"]


@dataclass
class MouseBackendHandle:
    """公开壳持有的鼠标后端句柄。

    - `brand`：用于公开壳按品牌决定展示名和少量诊断逻辑
    - `device`：私有核心里的真实设备对象，仅桥接层和设备管理器内部流转
    - `product_id` / `product_name` / `path`：供日志和公开壳状态编排使用
    """

    brand: MouseBackendBrand
    device: LogitechReceiver | RazerDevice
    product_id: int
    product_name: str
    path: object


def enumerate_mouse_backends() -> list[MouseBackendHandle]:
    """枚举并打开当前全部可用鼠标后端。"""
    handles: list[MouseBackendHandle] = []

    for dev_info in find_logitech_receivers():
        receiver = LogitechReceiver(dev_info)
        if receiver.open():
            handles.append(
                MouseBackendHandle(
                    brand="logitech",
                    device=receiver,
                    product_id=dev_info["product_id"],
                    product_name=receiver.product_string,
                    path=receiver.path,
                )
            )

    for dev_info in find_razer_devices():
        device = RazerDevice(dev_info)
        if device.open():
            handles.append(
                MouseBackendHandle(
                    brand="razer",
                    device=device,
                    product_id=dev_info["product_id"],
                    product_name=device.product_name,
                    path=device.path,
                )
            )

    return handles


def close_mouse_backend(handle: MouseBackendHandle):
    """关闭单个鼠标后端。"""
    handle.device.close()


def read_mouse_battery(handle: MouseBackendHandle) -> Optional[BatteryInfo | RazerBatteryInfo]:
    """统一读取鼠标后端电量。

    公开壳不再直接分品牌 import 私有对象，只通过桥接层拿到读电结果。
    """
    if handle.brand == "logitech":
        receiver = handle.device
        if receiver.product_id in (0xC539, 0xC547):
            return receiver.get_battery_legacy_long()
        return receiver.get_battery()

    return handle.device.get_battery()


def keyboard_binding_from_candidate(candidate: KeyboardCandidate) -> dict:
    """把公开候选 DTO 转成可持久化的绑定结构。"""
    return {
        "device_id": candidate.device_id,
        "vendor_id": candidate.vendor_id,
        "product_id": candidate.product_id,
        "usage_page": candidate.usage_page,
        "usage": candidate.usage,
        "interface_number": candidate.interface_number,
        "product_name": candidate.product_name,
    }


def keyboard_binding_from_info(keyboard: KeyboardInfo) -> dict:
    """把键盘快照 DTO 转成可持久化绑定结构。

    读取成功后，tray 进程会用当前真实可读接口回写配置，
    让后续重插或系统重枚举时仍能定位到最新路径。
    """
    return {
        "device_id": keyboard.device_id,
        "vendor_id": keyboard.vendor_id,
        "product_id": keyboard.product_id,
        "usage_page": keyboard.usage_page,
        "usage": keyboard.usage,
        "interface_number": keyboard.interface_number,
        "product_name": keyboard.product_name,
    }
