"""
设备扫描与管理模块

统一管理罗技和雷蛇鼠标设备的扫描、连接和电池状态查询。
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

from logitech_hid import LogitechReceiver, find_logitech_receivers, BatteryInfo
from razer_hid import RazerDevice, find_razer_devices, RazerBatteryInfo

logger = logging.getLogger(__name__)


class Brand(Enum):
    LOGITECH = "罗技"
    RAZER = "雷蛇"


@dataclass
class MouseInfo:
    """鼠标设备信息"""
    name: str = "未知鼠标"
    brand: Brand = Brand.LOGITECH
    percentage: int = -1          # -1 表示未获取到
    charging: bool = False
    status_text: str = "未连接"
    online: bool = False
    last_update: float = 0.0
    product_id: int = 0

    @property
    def display_percentage(self) -> str:
        if self.percentage < 0:
            return "--"
        return f"{self.percentage}%"


class DeviceManager:
    """
    设备管理器

    负责扫描设备、维护连接、定期刷新电池状态。
    """

    def __init__(self):
        self._mice: list[MouseInfo] = []
        self._logitech_receivers: list[LogitechReceiver] = []
        self._razer_devices: list[RazerDevice] = []
        self._lock = threading.Lock()
        self._auto_refresh_thread: Optional[threading.Thread] = None
        self._auto_refresh_running = False
        self._refresh_interval = 60  # 秒
        self._on_update_callbacks: list[Callable] = []

    @property
    def mice(self) -> list[MouseInfo]:
        with self._lock:
            return list(self._mice)

    def set_on_update(self, callback: Callable):
        """设置数据更新回调（向后兼容，添加到回调列表）"""
        if callback not in self._on_update_callbacks:
            self._on_update_callbacks.append(callback)

    def add_on_update(self, callback: Callable):
        """添加数据更新回调"""
        if callback not in self._on_update_callbacks:
            self._on_update_callbacks.append(callback)

    def remove_on_update(self, callback: Callable):
        """移除数据更新回调"""
        if callback in self._on_update_callbacks:
            self._on_update_callbacks.remove(callback)

    def _notify_update(self):
        """通知所有订阅者数据已更新"""
        for cb in self._on_update_callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"更新回调出错: {e}")

    def scan_and_refresh(self):
        """扫描设备并刷新电池状态"""
        self._close_all()
        self._scan_devices()
        self._refresh_battery()
        self._notify_update()

    def refresh_only(self):
        """仅刷新已连接设备的电池状态"""
        self._refresh_battery()
        self._notify_update()

    def _scan_devices(self):
        """扫描所有已连接设备"""
        with self._lock:
            self._mice.clear()

        # 扫描罗技接收器
        logi_devs = find_logitech_receivers()
        for dev_info in logi_devs:
            receiver = LogitechReceiver(dev_info)
            if receiver.open():
                self._logitech_receivers.append(receiver)

                pid = dev_info['product_id']
                mouse = MouseInfo(
                    name=self._get_logitech_name(pid),
                    brand=Brand.LOGITECH,
                    product_id=pid,
                    status_text="已连接，读取中...",
                )
                with self._lock:
                    self._mice.append(mouse)

                logger.info(f"已添加罗技设备: {mouse.name}")

        # 扫描雷蛇设备
        razer_devs = find_razer_devices()
        for dev_info in razer_devs:
            device = RazerDevice(dev_info)
            if device.open():
                self._razer_devices.append(device)

                mouse = MouseInfo(
                    name=device.product_name,
                    brand=Brand.RAZER,
                    product_id=dev_info['product_id'],
                    status_text="已连接，读取中...",
                )
                with self._lock:
                    self._mice.append(mouse)

                logger.info(f"已添加雷蛇设备: {mouse.name}")

        total = len(self._logitech_receivers) + len(self._razer_devices)
        logger.info(f"设备扫描完成，共发现 {total} 个设备")

    def _refresh_battery(self):
        """刷新所有设备的电池状态"""
        idx = 0

        # 刷新罗技设备
        for receiver in self._logitech_receivers:
            if idx >= len(self._mice):
                break
            try:
                battery = receiver.get_battery()
                with self._lock:
                    mouse = self._mice[idx]
                    if battery:
                        mouse.percentage = battery.percentage
                        mouse.charging = battery.charging
                        mouse.status_text = battery.status_text
                        mouse.online = True
                    else:
                        mouse.status_text = "无法读取电量"
                        mouse.online = False
                    mouse.last_update = time.time()
            except Exception as e:
                logger.error(f"刷新罗技设备电池失败: {e}")
                with self._lock:
                    self._mice[idx].status_text = f"读取错误"
                    self._mice[idx].online = False
            idx += 1

        # 刷新雷蛇设备
        for device in self._razer_devices:
            if idx >= len(self._mice):
                break
            try:
                battery = device.get_battery()
                with self._lock:
                    mouse = self._mice[idx]
                    if battery:
                        mouse.percentage = battery.percentage
                        mouse.charging = battery.charging
                        mouse.status_text = battery.status_text
                        mouse.online = True
                    else:
                        mouse.status_text = "无法读取电量"
                        mouse.online = False
                    mouse.last_update = time.time()
            except Exception as e:
                logger.error(f"刷新雷蛇设备电池失败: {e}")
                with self._lock:
                    self._mice[idx].status_text = f"读取错误"
                    self._mice[idx].online = False
            idx += 1

    def start_auto_refresh(self, interval: int = 60):
        """启动自动刷新线程"""
        self._refresh_interval = interval
        if self._auto_refresh_running:
            return

        self._auto_refresh_running = True
        self._auto_refresh_thread = threading.Thread(
            target=self._auto_refresh_loop, daemon=True
        )
        self._auto_refresh_thread.start()
        logger.info(f"自动刷新已启动，间隔 {interval} 秒")

    def stop_auto_refresh(self):
        """停止自动刷新"""
        self._auto_refresh_running = False
        logger.info("自动刷新已停止")

    def _auto_refresh_loop(self):
        """自动刷新循环"""
        while self._auto_refresh_running:
            time.sleep(self._refresh_interval)
            if not self._auto_refresh_running:
                break
            try:
                self.refresh_only()
            except Exception as e:
                logger.error(f"自动刷新出错: {e}")

    def _close_all(self):
        """关闭所有设备连接"""
        for receiver in self._logitech_receivers:
            receiver.close()
        for device in self._razer_devices:
            device.close()
        self._logitech_receivers.clear()
        self._razer_devices.clear()

    def shutdown(self):
        """关闭管理器"""
        self.stop_auto_refresh()
        self._close_all()
        logger.info("设备管理器已关闭")

    @staticmethod
    def _get_logitech_name(receiver_pid: int) -> str:
        """根据接收器 PID 猜测鼠标名称"""
        names = {
            0xC541: "G903 / G703 (Lightspeed)",
            0xC547: "G502X (Lightspeed)",
            0xC539: "G Pro Wireless (Lightspeed)",
            0xC53F: "G305 (Lightspeed)",
            0xC53A: "Lightspeed 鼠标",
            0xC53D: "Lightspeed 鼠标",
            0xC545: "Lightspeed 鼠标",
            0xC548: "Bolt 鼠标",
            0xC52B: "Unifying 鼠标",
        }
        return names.get(receiver_pid, f"罗技鼠标 (0x{receiver_pid:04X})")
