"""
设备扫描与管理模块

统一管理罗技和雷蛇鼠标设备的扫描、连接和电池状态查询。
"""

import json
import logging
import os
import sys
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
        self._write_shared_state()
        for cb in self._on_update_callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"更新回调出错: {e}")

    def _write_shared_state(self):
        """将当前设备状态写入共享 JSON 文件，供 GUI 子进程读取"""
        state_file = get_shared_state_path()
        try:
            with self._lock:
                data = []
                for m in self._mice:
                    data.append({
                        'name': m.name,
                        'brand': m.brand.value if hasattr(m.brand, 'value') else str(m.brand),
                        'percentage': m.percentage,
                        'charging': m.charging,
                        'status_text': m.status_text,
                        'online': m.online,
                        'last_update': m.last_update,
                    })
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"写入共享状态文件失败: {e}")

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
                # 按 PID 分支：G903 (0xC539) 和 G502X (0xC547) 走专用长报文路径，其他设备走原始短报文
                if receiver.product_id in (0xC539, 0xC547):
                    battery = receiver.get_battery_legacy_long()
                else:
                    battery = receiver.get_battery()
                with self._lock:
                    mouse = self._mice[idx]
                    if battery:
                        # 硬件层面：任何返回 0 的结果几乎肯定是设备未就绪/深度休眠
                        if battery.percentage <= 0:
                            mouse.percentage = -1
                            mouse.charging = False
                            mouse.status_text = "休眠或连接中断"
                            mouse.online = False
                        else:
                            mouse.percentage = battery.percentage
                            mouse.charging = battery.charging
                            mouse.status_text = battery.status_text
                            mouse.online = True
                    else:
                        mouse.status_text = "休眠中"
                        # 罗技接收器在就始终显示，不设 online=False
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
                        if battery.percentage <= 0:
                            mouse.percentage = -1
                            mouse.charging = False
                            mouse.status_text = "休眠或连接中断"
                            mouse.online = False
                        else:
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
            0xC539: "G903 (Lightspeed)",
            0xC53F: "G305 (Lightspeed)",
            0xC53A: "Lightspeed 鼠标",
            0xC53D: "Lightspeed 鼠标",
            0xC545: "Lightspeed 鼠标",
            0xC548: "Bolt 鼠标",
            0xC52B: "Unifying 鼠标",
        }
        return names.get(receiver_pid, f"罗技鼠标 (0x{receiver_pid:04X})")


def get_shared_state_path() -> str:
    """获取共享状态文件路径"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, '.device_state.json')


class SharedStateDeviceManager:
    """
    只读的设备管理器，通过读取共享状态文件获取数据。
    供 GUI 子进程使用，不打开任何 HID 设备，避免句柄争抢。
    """

    def __init__(self):
        self._mice: list[MouseInfo] = []
        self._lock = threading.Lock()
        self._on_update_callbacks: list[Callable] = []
        self._auto_refresh_running = False
        self._auto_refresh_thread: Optional[threading.Thread] = None
        self._refresh_interval = 3

    @property
    def mice(self) -> list[MouseInfo]:
        with self._lock:
            return list(self._mice)

    def set_on_update(self, callback: Callable):
        if callback not in self._on_update_callbacks:
            self._on_update_callbacks.append(callback)

    def add_on_update(self, callback: Callable):
        if callback not in self._on_update_callbacks:
            self._on_update_callbacks.append(callback)

    def remove_on_update(self, callback: Callable):
        if callback in self._on_update_callbacks:
            self._on_update_callbacks.remove(callback)

    def _notify_update(self):
        for cb in self._on_update_callbacks:
            try:
                cb()
            except Exception:
                pass

    def _read_shared_state(self):
        """从共享状态文件读取设备数据"""
        state_file = get_shared_state_path()
        try:
            if not os.path.exists(state_file):
                return
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            mice = []
            for item in data:
                try:
                    brand = Brand(item.get('brand', '罗技'))
                except (ValueError, KeyError):
                    brand = Brand.LOGITECH
                mouse = MouseInfo(
                    name=item.get('name', '未知设备'),
                    brand=brand,
                    percentage=item.get('percentage', -1),
                    charging=item.get('charging', False),
                    status_text=item.get('status_text', '未知'),
                    online=item.get('online', False),
                    last_update=item.get('last_update', 0),
                )
                mice.append(mouse)
            with self._lock:
                self._mice = mice
        except Exception as e:
            logger.debug(f"读取共享状态文件失败: {e}")

    def scan_and_refresh(self):
        self._read_shared_state()
        self._notify_update()

    def refresh_only(self):
        self._read_shared_state()
        self._notify_update()

    def start_auto_refresh(self, interval: int = 3):
        self._refresh_interval = interval
        if self._auto_refresh_running:
            return
        self._auto_refresh_running = True
        self._auto_refresh_thread = threading.Thread(
            target=self._auto_refresh_loop, daemon=True
        )
        self._auto_refresh_thread.start()

    def stop_auto_refresh(self):
        self._auto_refresh_running = False

    def _auto_refresh_loop(self):
        while self._auto_refresh_running:
            time.sleep(self._refresh_interval)
            if not self._auto_refresh_running:
                break
            try:
                self.refresh_only()
            except Exception:
                pass

    def shutdown(self):
        self.stop_auto_refresh()
