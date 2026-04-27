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
        self._io_lock = threading.Lock()  # 串行化 scan/refresh，避免并发读写 HID
        self._consecutive_failures: dict[str, int] = {}
        self._reconnect_failure_threshold = 3
        self._reconnect_cooldown_sec = 30
        self._last_reconnect_time = 0.0
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
        with self._io_lock:
            self._close_all()
            self._scan_devices()
            self._refresh_battery()
            self._notify_update()

    def refresh_only(self):
        """仅刷新已连接设备的电池状态"""
        with self._io_lock:
            self._refresh_battery()

            # 唤醒后若连续失败且当前无任何有效电量，则触发一次自动重连/重扫
            if self._should_reconnect_after_refresh():
                logger.warning("检测到连续读取失败，触发自动重连恢复流程")
                self._recover_connections_locked()

            self._notify_update()

    def _recover_connections_locked(self):
        """自动恢复连接（调用方需持有 _io_lock）。"""
        try:
            self._close_all()
            self._scan_devices()
            self._refresh_battery()
            self._last_reconnect_time = time.time()
            logger.info("自动重连/重扫完成")
        except Exception as e:
            logger.error(f"自动重连恢复失败: {e}")

    def _should_reconnect_after_refresh(self) -> bool:
        """判断是否需要执行自动重连。"""
        now = time.time()
        if now - self._last_reconnect_time < self._reconnect_cooldown_sec:
            return False

        with self._lock:
            mice = list(self._mice)

        # 无设备时不做重连（由用户手动扫描或后续周期处理）
        if not mice:
            return False

        # 只要有一个设备拿到有效电量，说明链路未整体失效
        if any(m.percentage >= 0 for m in mice):
            return False

        # 当前设备全部无有效电量，且都达到连续失败阈值，判定为需要重连
        all_reached_threshold = True
        for idx, mouse in enumerate(mice):
            key = self._device_key(mouse, idx)
            fail_count = self._consecutive_failures.get(key, 0)
            if fail_count < self._reconnect_failure_threshold:
                all_reached_threshold = False
                break

        if all_reached_threshold:
            logger.warning(
                "所有设备连续读取失败达到阈值，可能发生睡眠唤醒后句柄失效/路径变化，准备自动重连"
            )
            return True

        return False

    def _scan_devices(self):
        """扫描所有已连接设备"""
        with self._lock:
            self._mice.clear()
        self._consecutive_failures.clear()

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

    @staticmethod
    def _safe_path_text(path) -> str:
        """将 HID path 安全转成日志可读字符串。"""
        if isinstance(path, bytes):
            return path.decode('utf-8', errors='ignore')
        return str(path)

    @staticmethod
    def _device_key(mouse: MouseInfo, idx: int) -> str:
        return f"{mouse.brand.value}|{mouse.name}|0x{mouse.product_id:04X}|{idx}"

    @staticmethod
    def _is_low_confidence_logitech_source(source: str) -> bool:
        """判断罗技电量来源是否为休眠期容易漂移的低可信源。"""
        return source in {
            "legacy_long:0x1001",
            "short:0x1001",
            "legacy_long:0x1000",
            "short:0x1000",
        }

    @classmethod
    def _should_keep_last_logitech_battery(cls, mouse: MouseInfo, source: str,
                                           percentage: int, charging: bool) -> bool:
        """休眠期若收到低可信且大幅跳变的样本，则保留上次有效电量。"""
        if mouse.percentage < 0:
            return False

        if not cls._is_low_confidence_logitech_source(source):
            return False

        if charging:
            return False

        delta = abs(percentage - mouse.percentage)
        return delta >= 20

    def _mark_failure(self, key: str, reason: str, detail: str = ""):
        """记录连续失败次数并输出诊断日志。"""
        count = self._consecutive_failures.get(key, 0) + 1
        self._consecutive_failures[key] = count

        # 第一次失败和每 5 次失败打 warning，避免日志完全刷屏
        if count == 1 or count % 5 == 0:
            logger.warning(
                f"电量读取失败[{count}] {key}: {reason}"
                + (f" | {detail}" if detail else "")
            )

        # 连续失败较多时给出明确诊断提示（不改变业务行为）
        if count == 3:
            logger.warning(
                f"{key} 连续失败达到 3 次，可能是系统睡眠唤醒后 HID 句柄失效、"
                f"设备路径变化或端点未就绪（当前仅 refresh，不会自动重连）。"
            )

    def _mark_success(self, key: str, pct: int, charging: bool):
        """读取成功后清理失败计数并输出恢复日志。"""
        fail_count = self._consecutive_failures.pop(key, 0)
        if fail_count > 0:
            logger.info(
                f"电量读取恢复 {key}: {pct}% charging={charging} "
                f"(此前连续失败 {fail_count} 次)"
            )

    def _refresh_battery(self):
        """刷新所有设备的电池状态"""
        idx = 0

        # 刷新罗技设备
        for receiver in self._logitech_receivers:
            if idx >= len(self._mice):
                break
            try:
                with self._lock:
                    mouse = self._mice[idx]
                    key = self._device_key(mouse, idx)
                    prev_online = mouse.online

                # 按 PID 分支：G903 (0xC539) 和 G502X (0xC547) 走专用长报文路径，其他设备走原始短报文
                if receiver.product_id in (0xC539, 0xC547):
                    battery = receiver.get_battery_legacy_long()
                    sample_source = "legacy_long"
                else:
                    battery = receiver.get_battery()
                    sample_source = "standard"

                with self._lock:
                    if battery:
                        battery_source = getattr(battery, 'source', sample_source)
                        prev_pct = mouse.percentage
                        prev_chg = mouse.charging
                        delta = abs(battery.percentage - prev_pct) if prev_pct >= 0 else -1
                        logger.debug(
                            f"电量样本[罗技] key={key} source={sample_source} "
                            f"prev={prev_pct}%/{prev_chg} -> new={battery.percentage}%/{battery.charging} "
                            f"delta={delta} status={battery.status_text} "
                            f"pid=0x{receiver.product_id:04X} path={self._safe_path_text(receiver.path)}"
                        )
                        battery_source = getattr(battery, 'source', sample_source)
                        if self._should_keep_last_logitech_battery(
                            mouse,
                            battery_source,
                            battery.percentage,
                            battery.charging,
                        ):
                            logger.warning(
                                f"休眠期低可信电量帧已忽略[罗技] key={key} source={battery_source} "
                                f"prev={prev_pct}%/{prev_chg} -> new={battery.percentage}%/{battery.charging} "
                                f"delta={delta} pid=0x{receiver.product_id:04X} "
                                f"path={self._safe_path_text(receiver.path)}"
                            )
                            mouse.status_text = "休眠中，沿用上次有效电量"
                            mouse.last_update = time.time()
                            self._mark_failure(
                                key,
                                "休眠期低可信电量帧被忽略",
                                f"source={battery_source} pid=0x{receiver.product_id:04X} "
                                f"path={self._safe_path_text(receiver.path)}"
                            )
                            idx += 1
                            continue
                        if (
                            prev_pct >= 0
                            and not battery.charging
                            and self._is_low_confidence_logitech_source(battery_source)
                            and delta >= 20
                        ):
                            logger.warning(
                                f"检测到低可信电量源覆盖风险[罗技] key={key} source={battery_source} "
                                f"prev={prev_pct}%/{prev_chg} -> new={battery.percentage}%/{battery.charging} "
                                f"delta={delta} status={battery.status_text} pid=0x{receiver.product_id:04X} "
                                f"path={self._safe_path_text(receiver.path)}"
                            )
                        if prev_pct >= 0 and delta >= 20:
                            logger.info(
                                f"大幅电量跳变诊断[罗技] key={key} prev={prev_pct}%/{prev_chg} "
                                f"new={battery.percentage}%/{battery.charging} delta={delta} "
                                f"accepted_by_threshold={delta <= 40 or battery.charging} source={battery_source} "
                                f"status={battery.status_text} pid=0x{receiver.product_id:04X}"
                            )
                        if not self._is_battery_sample_valid(mouse, battery.percentage, battery.charging):
                            logger.warning(
                                f"异常帧触发[罗技] key={key} source={sample_source} "
                                f"prev={prev_pct}%/{prev_chg} -> new={battery.percentage}%/{battery.charging} "
                                f"delta={delta} pid=0x{receiver.product_id:04X} "
                                f"path={self._safe_path_text(receiver.path)}"
                            )
                            mouse.status_text = "检测到异常帧，沿用上次有效电量"
                            mouse.last_update = time.time()
                            self._mark_failure(
                                key,
                                "异常帧被过滤",
                                f"pid=0x{receiver.product_id:04X} path={self._safe_path_text(receiver.path)}"
                            )
                            idx += 1
                            continue
                        # 硬件层面：任何返回 0 的结果几乎肯定是设备未就绪/深度休眠
                        if battery.percentage <= 0:
                            mouse.percentage = -1
                            mouse.charging = False
                            mouse.status_text = "休眠或连接中断"
                            mouse.online = False
                            self._mark_failure(
                                key,
                                "返回电量<=0",
                                f"pid=0x{receiver.product_id:04X} path={self._safe_path_text(receiver.path)}"
                            )
                        else:
                            mouse.percentage = battery.percentage
                            mouse.charging = battery.charging
                            mouse.status_text = battery.status_text
                            mouse.online = True
                            self._mark_success(key, battery.percentage, battery.charging)
                            if not prev_online:
                                logger.info(
                                    f"设备状态恢复在线: {key}, pid=0x{receiver.product_id:04X}, "
                                    f"path={self._safe_path_text(receiver.path)}"
                                )
                    else:
                        mouse.status_text = "休眠中"
                        # 罗技接收器在就始终显示，不设 online=False
                        self._mark_failure(
                            key,
                            "电量读取返回空",
                            f"pid=0x{receiver.product_id:04X} path={self._safe_path_text(receiver.path)}"
                        )
                    mouse.last_update = time.time()
            except Exception as e:
                logger.error(f"刷新罗技设备电池失败: {e}")
                with self._lock:
                    mouse = self._mice[idx]
                    key = self._device_key(mouse, idx)
                    mouse.status_text = f"读取错误"
                    mouse.online = False
                    self._mark_failure(
                        key,
                        "抛出异常",
                        f"pid=0x{receiver.product_id:04X} path={self._safe_path_text(receiver.path)} err={type(e).__name__}: {e}"
                    )
            idx += 1

        # 刷新雷蛇设备
        for device in self._razer_devices:
            if idx >= len(self._mice):
                break
            try:
                with self._lock:
                    mouse = self._mice[idx]
                    key = self._device_key(mouse, idx)
                    prev_online = mouse.online

                battery = device.get_battery()
                with self._lock:
                    if battery:
                        prev_pct = mouse.percentage
                        prev_chg = mouse.charging
                        delta = abs(battery.percentage - prev_pct) if prev_pct >= 0 else -1
                        logger.debug(
                            f"电量样本[雷蛇] key={key} prev={prev_pct}%/{prev_chg} "
                            f"-> new={battery.percentage}%/{battery.charging} delta={delta} "
                            f"status={battery.status_text} pid=0x{device.product_id:04X} "
                            f"path={self._safe_path_text(device.path)}"
                        )
                        if not self._is_battery_sample_valid(mouse, battery.percentage, battery.charging):
                            logger.warning(
                                f"异常帧触发[雷蛇] key={key} prev={prev_pct}%/{prev_chg} "
                                f"-> new={battery.percentage}%/{battery.charging} delta={delta} "
                                f"pid=0x{device.product_id:04X} path={self._safe_path_text(device.path)}"
                            )
                            mouse.status_text = "检测到异常帧，沿用上次有效电量"
                            mouse.last_update = time.time()
                            self._mark_failure(
                                key,
                                "异常帧被过滤",
                                f"pid=0x{device.product_id:04X} path={self._safe_path_text(device.path)}"
                            )
                            idx += 1
                            continue
                        if battery.percentage <= 0:
                            mouse.percentage = -1
                            mouse.charging = False
                            mouse.status_text = "休眠或连接中断"
                            mouse.online = False
                            self._mark_failure(
                                key,
                                "返回电量<=0",
                                f"pid=0x{device.product_id:04X} path={self._safe_path_text(device.path)}"
                            )
                        else:
                            mouse.percentage = battery.percentage
                            mouse.charging = battery.charging
                            mouse.status_text = battery.status_text
                            mouse.online = True
                            self._mark_success(key, battery.percentage, battery.charging)
                            if not prev_online:
                                logger.info(
                                    f"设备状态恢复在线: {key}, pid=0x{device.product_id:04X}, "
                                    f"path={self._safe_path_text(device.path)}"
                                )
                    else:
                        # 通信暂时失败时保留最后一次有效电量，避免误判离线导致电量闪烁
                        mouse.status_text = "读取超时，沿用上次有效电量"
                        self._mark_failure(
                            key,
                            "电量读取返回空",
                            f"pid=0x{device.product_id:04X} path={self._safe_path_text(device.path)}"
                        )
                    mouse.last_update = time.time()
            except Exception as e:
                logger.error(f"刷新雷蛇设备电池失败: {e}")
                with self._lock:
                    mouse = self._mice[idx]
                    key = self._device_key(mouse, idx)
                    mouse.status_text = f"读取错误"
                    mouse.online = False
                    self._mark_failure(
                        key,
                        "抛出异常",
                        f"pid=0x{device.product_id:04X} path={self._safe_path_text(device.path)} err={type(e).__name__}: {e}"
                    )
            idx += 1

    @staticmethod
    def _is_battery_sample_valid(mouse: MouseInfo, percentage: int, charging: bool) -> bool:
        """校验电量样本合法性，过滤明显异常跳变。"""
        if percentage < 0 or percentage > 100:
            logger.warning(
                f"过滤电量样本: 超出范围 {mouse.name} value={percentage} charging={charging}"
            )
            return False

        # 没有历史值时，只做范围检查
        if mouse.percentage < 0:
            return True

        delta = abs(percentage - mouse.percentage)
        logger.debug(
            f"电量样本校验: {mouse.name} prev={mouse.percentage}% -> new={percentage}% "
            f"delta={delta} charging={charging} thresholds=(40,60)"
        )

        # 非充电状态下，单次跳变超过 40% 基本可判定为噪声帧
        if not charging and delta > 40:
            logger.warning(
                f"过滤异常电量跳变: {mouse.name} {mouse.percentage}% -> {percentage}% "
                f"delta={delta} threshold=40 charging={charging}"
            )
            return False

        # 充电状态下允许稍大波动，但超过 60% 仍视为异常
        if charging and delta > 60:
            logger.warning(
                f"过滤异常充电跳变: {mouse.name} {mouse.percentage}% -> {percentage}% "
                f"delta={delta} threshold=60 charging={charging}"
            )
            return False

        return True

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
