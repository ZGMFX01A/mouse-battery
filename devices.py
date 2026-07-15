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
from dataclasses import dataclass
from typing import Optional, Callable
from enum import Enum

from config import ConfigManager
from core_bridge import (
    BatteryInfo,
    BluetoothCandidate,
    BluetoothInfo,
    KeyboardCandidate,
    KeyboardInfo,
    MouseBackendHandle,
    RazerBatteryInfo,
    close_mouse_backend,
    bluetooth_binding_from_candidate,
    enumerate_bluetooth_candidates,
    enumerate_keyboard_candidates,
    enumerate_mouse_backends,
    keyboard_binding_from_candidate,
    keyboard_binding_from_info,
    read_keyboard_battery,
    probe_bluetooth_candidate,
    read_bluetooth_batteries,
    read_mouse_battery,
)

logger = logging.getLogger(__name__)


# GUI -> tray 的轻量命令动作：请求枚举键盘候选接口。
DEVICE_COMMAND_SCAN_KEYBOARD_CANDIDATES = 'scan_keyboard_candidates'
# GUI -> tray 的轻量命令动作：绑定指定的键盘 HID 设备。
DEVICE_COMMAND_BIND_KEYBOARD = 'bind_keyboard'
# GUI -> tray 的轻量命令动作：解除当前键盘绑定。
DEVICE_COMMAND_UNBIND_KEYBOARD = 'unbind_keyboard'
DEVICE_COMMAND_SCAN_BLUETOOTH_CANDIDATES = 'scan_bluetooth_candidates'
DEVICE_COMMAND_BIND_BLUETOOTH = 'bind_bluetooth'
DEVICE_COMMAND_UNBIND_BLUETOOTH = 'unbind_bluetooth'
# GUI -> tray 的轻量命令动作：立即按最新配置重算托盘图标。
DEVICE_COMMAND_REFRESH_TRAY_ICON = 'refresh_tray_icon'


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


def get_device_command_path() -> str:
    """获取 GUI/托盘之间使用的轻量命令文件路径。"""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, '.device_command.json')


def request_device_command(action: str, payload: Optional[dict] = None):
    """由 GUI 写入一次性命令文件，请求 tray 进程执行硬件相关动作。

    这里刻意不让 GUI 进程直接触碰 HID：
    - GUI 只负责发出用户意图（枚举候选 / 绑定设备）
    - tray 进程负责真正执行 HID 枚举与电量探测
    从而保持项目既有的进程边界不被打破。
    """
    command_file = get_device_command_path()
    temp_file = f'{command_file}.{os.getpid()}.tmp'
    request_id = time.time_ns()
    data = {
        'request_id': request_id,
        'action': action,
        'payload': payload or {},
        'requested_at': time.time(),
    }
    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, command_file)
    return request_id


class DeviceManager:
    """
    设备管理器

    负责扫描设备、维护连接、定期刷新电池状态。
    使用 _mouse_to_device 映射精确关联 MouseInfo 与底层 HID 对象，
    替代原先按 idx 顺序匹配的脆弱做法。
    """

    def __init__(self, config_manager: Optional[ConfigManager] = None):
        self.config_manager = config_manager or ConfigManager()
        self._mice: list[MouseInfo] = []
        # 鼠标后端统一通过桥接层句柄流转，避免公开壳继续持有私有品牌类。
        self._mouse_backends: list[MouseBackendHandle] = []
        self._keyboard: Optional[KeyboardInfo] = None
        self._keyboard_candidates: list[KeyboardCandidate] = []
        self._keyboard_scan_state = 'idle'
        self._keyboard_scan_message = ''
        self._bluetooth_devices: list[BluetoothInfo] = []
        self._bluetooth_candidates: list[BluetoothCandidate] = []
        self._bluetooth_scan_state = 'idle'
        self._bluetooth_scan_message = ''
        self._bluetooth_request_id = 0
        # _mice[i] 对应的桥接后端句柄：
        # 刷新时通过遍历该映射读电，避免 idx 错位
        self._mouse_to_device: list[tuple[Brand, MouseBackendHandle]] = []
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()  # 串行化 scan/refresh，避免并发读写 HID
        self._consecutive_failures: dict[str, int] = {}
        self._reconnect_failure_threshold = 3
        self._reconnect_cooldown_sec = 30
        self._last_reconnect_time = 0.0
        self._auto_refresh_thread: Optional[threading.Thread] = None
        self._auto_refresh_running = False
        self._auto_refresh_stop_event = threading.Event()
        self._auto_refresh_thread_lock = threading.Lock()
        self._refresh_interval = 60  # 秒
        self._command_thread: Optional[threading.Thread] = None
        self._command_stop_event = threading.Event()
        self._command_thread_lock = threading.Lock()
        self._on_update_callbacks: list[Callable] = []

    @property
    def mice(self) -> list[MouseInfo]:
        with self._lock:
            return list(self._mice)

    @property
    def keyboard(self) -> Optional[KeyboardInfo]:
        with self._lock:
            return self._keyboard

    @property
    def keyboard_candidates(self) -> list[KeyboardCandidate]:
        with self._lock:
            return list(self._keyboard_candidates)

    @property
    def keyboard_scan_state(self) -> str:
        with self._lock:
            return self._keyboard_scan_state

    @property
    def keyboard_scan_message(self) -> str:
        with self._lock:
            return self._keyboard_scan_message

    @property
    def bluetooth_devices(self) -> list[BluetoothInfo]:
        with self._lock:
            return list(self._bluetooth_devices)

    @property
    def bluetooth_candidates(self) -> list[BluetoothCandidate]:
        with self._lock:
            return list(self._bluetooth_candidates)

    @property
    def bluetooth_scan_state(self) -> str:
        with self._lock:
            return self._bluetooth_scan_state

    @property
    def bluetooth_scan_message(self) -> str:
        with self._lock:
            return self._bluetooth_scan_message

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
        callbacks = list(self._on_update_callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"更新回调出错: {e}")

    def _write_shared_state(self):
        """将当前设备状态原子写入共享 JSON 文件，供 GUI 子进程读取。

        共享状态从旧版“纯鼠标列表”扩展为对象结构：
        - mice: 鼠标快照列表
        - keyboard: 已绑定键盘快照
        - keyboard_candidates: 弹窗候选 HID 接口
        - keyboard_scan_state / message: GUI 弹窗状态
        保持写入原子性，避免 GUI 读到半截 JSON。
        """
        state_file = get_shared_state_path()
        temp_file = f"{state_file}.{os.getpid()}.tmp"
        try:
            with self._lock:
                data = {
                    'mice': [_serialize_mouse_state(m) for m in self._mice],
                    'keyboard': _serialize_keyboard_state(self._keyboard),
                    'keyboard_candidates': [_serialize_keyboard_candidate(candidate) for candidate in self._keyboard_candidates],
                    'keyboard_scan_state': self._keyboard_scan_state,
                    'keyboard_scan_message': self._keyboard_scan_message,
                    'bluetooth_devices': [_serialize_bluetooth_state(item) for item in self._bluetooth_devices],
                    'bluetooth_candidates': [_serialize_bluetooth_candidate(item) for item in self._bluetooth_candidates],
                    'bluetooth_scan_state': self._bluetooth_scan_state,
                    'bluetooth_scan_message': self._bluetooth_scan_message,
                    'bluetooth_request_id': self._bluetooth_request_id,
                }

            # 先写临时文件再替换正式文件，避免 GUI 在读取时遇到半截 JSON。
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, state_file)
        except Exception as e:
            logger.error(f"写入共享状态文件失败: {type(e).__name__}: {e}")
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as cleanup_error:
                logger.debug(f"清理共享状态临时文件失败: {cleanup_error}")

    def scan_and_refresh(self):
        """扫描设备并刷新电池状态"""
        with self._io_lock:
            self._close_all()
            self._scan_devices()
            self._refresh_battery()
            self._refresh_keyboard_locked()
            self._refresh_bluetooth_locked()
        self._notify_update()

    def refresh_only(self):
        """仅刷新已连接设备的电池状态"""
        with self._io_lock:
            self._refresh_battery()
            self._refresh_keyboard_locked()
            self._refresh_bluetooth_locked()

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
            self._refresh_keyboard_locked()
            self._refresh_bluetooth_locked()
            self._last_reconnect_time = time.time()
            logger.info("自动重连/重扫完成")
        except Exception as e:
            logger.error(f"自动重连恢复失败: {e}")

    def _refresh_keyboard_locked(self):
        """刷新当前绑定键盘的电量快照（调用方需持有 _io_lock）。"""
        binding = self.config_manager.keyboard_binding
        if not binding:
            with self._lock:
                self._keyboard = None
            return

        keyboard = read_keyboard_battery(binding)
        if keyboard.online:
            # 读取成功后，把当前真实接口 path 回写到配置，避免重插后继续沿用旧 path。
            self.config_manager.keyboard_binding = keyboard_binding_from_info(keyboard)

        with self._lock:
            self._keyboard = keyboard

    def _set_keyboard_scan_state(self, state: str, message: str = ''):
        """记录键盘候选枚举阶段状态，供 GUI 弹窗展示。"""
        with self._lock:
            self._keyboard_scan_state = state
            self._keyboard_scan_message = message

    def _refresh_bluetooth_locked(self):
        bindings = self.config_manager.bluetooth_bindings
        if not bindings:
            with self._lock:
                self._bluetooth_devices = []
            return
        try:
            snapshots = read_bluetooth_batteries(bindings)
        except Exception as exc:
            logger.error('刷新蓝牙设备失败: %s: %s', type(exc).__name__, exc)
            snapshots = [
                BluetoothInfo(
                    device_id=item['device_id'],
                    name=item['name'],
                    status_text=f'蓝牙刷新失败：{type(exc).__name__}',
                    last_update=time.time(),
                )
                for item in bindings
            ]
        with self._lock:
            self._bluetooth_devices = snapshots

    def _scan_bluetooth_candidates(self, request_id: int = 0):
        with self._lock:
            self._bluetooth_scan_state = 'loading'
            self._bluetooth_scan_message = '正在读取 Windows 已配对蓝牙设备...'
            self._bluetooth_request_id = request_id
        self._notify_update()
        try:
            with self._io_lock:
                candidates = enumerate_bluetooth_candidates()
        except Exception as exc:
            logger.error('枚举蓝牙设备失败: %s', exc)
            with self._lock:
                self._bluetooth_candidates = []
                self._bluetooth_scan_state = 'error'
                self._bluetooth_scan_message = f'扫描失败：{type(exc).__name__}: {exc}'
            self._notify_update()
            return

        with self._lock:
            self._bluetooth_candidates = candidates
            self._bluetooth_scan_state = 'ready'
            self._bluetooth_scan_message = (
                f'已发现 {len(candidates)} 个 Windows 已配对蓝牙设备'
                if candidates else '未发现 Windows 已配对蓝牙设备'
            )
        self._notify_update()

    def _bind_bluetooth(self, device_id: str, request_id: int = 0):
        candidates = self.bluetooth_candidates or enumerate_bluetooth_candidates()
        target = next((item for item in candidates if item.device_id == device_id), None)
        if target is None:
            raise ValueError('未找到对应的 Windows 已配对蓝牙设备。')
        if any(item['device_id'] == device_id for item in self.config_manager.bluetooth_bindings):
            raise ValueError('该蓝牙设备已经添加。')

        with self._lock:
            self._bluetooth_scan_state = 'binding'
            self._bluetooth_scan_message = f'正在读取蓝牙设备电量：{target.name}'
            self._bluetooth_request_id = request_id
        self._notify_update()
        with self._io_lock:
            snapshot = probe_bluetooth_candidate(target)
        self.config_manager.add_bluetooth_binding(bluetooth_binding_from_candidate(target))
        with self._lock:
            self._bluetooth_devices.append(snapshot)
            self._bluetooth_scan_state = 'bound'
            self._bluetooth_scan_message = f'已添加蓝牙设备：{target.name}'
        self._notify_update()

    def _unbind_bluetooth(self, device_id: str):
        self.config_manager.remove_bluetooth_binding(device_id)
        with self._lock:
            self._bluetooth_devices = [item for item in self._bluetooth_devices if item.device_id != device_id]
            self._bluetooth_scan_message = '已移除蓝牙设备'
        self._notify_update()

    def _scan_keyboard_candidates(self):
        """由 tray 进程枚举可绑定的键盘候选接口。"""
        self._set_keyboard_scan_state('loading', '正在扫描键盘候选设备...')
        self._notify_update()

        try:
            candidates = enumerate_keyboard_candidates()
        except Exception as e:
            # 候选枚举失败时必须显式落成 error 状态，
            # 否则 GUI 会一直停留在 loading，用户既看不到失败原因，也无法判断是否需要重试。
            logger.error(f'枚举键盘候选失败: {e}')
            with self._lock:
                self._keyboard_candidates = []
                self._keyboard_scan_state = 'error'
                self._keyboard_scan_message = f'扫描失败：{type(e).__name__}: {e}'
            self._notify_update()
            return

        with self._lock:
            self._keyboard_candidates = candidates
            self._keyboard_scan_state = 'ready'
            if candidates:
                self._keyboard_scan_message = f'已发现 {len(candidates)} 个键盘候选设备'
            else:
                self._keyboard_scan_message = '未发现可绑定的键盘候选设备'
        self._notify_update()

    def _bind_keyboard(self, device_id: str):
        """保存指定键盘绑定，并立即刷新一份电量快照。"""
        candidates = self.keyboard_candidates or enumerate_keyboard_candidates()
        target = next((candidate for candidate in candidates if candidate.device_id == device_id), None)
        if target is None:
            logger.warning('未找到待绑定的键盘候选: %s', device_id)
            with self._lock:
                self._keyboard_scan_state = 'error'
                self._keyboard_scan_message = '绑定失败：未找到对应的键盘设备'
            self._notify_update()
            return

        self.config_manager.keyboard_binding = keyboard_binding_from_candidate(target)

        with self._io_lock:
            self._refresh_keyboard_locked()

        with self._lock:
            self._keyboard_scan_state = 'ready'
            self._keyboard_scan_message = f'已绑定键盘：{target.product_name}'
        self._notify_update()

    def _unbind_keyboard(self):
        """解除当前键盘绑定，并立即清空共享状态中的键盘快照。"""
        self.config_manager.keyboard_binding = None
        with self._lock:
            self._keyboard = None
            self._keyboard_scan_state = 'idle'
            self._keyboard_scan_message = '已移除当前键盘绑定'
        self._notify_update()

    def _consume_device_command(self):
        """轮询并消费 GUI 写入的一次性命令文件。"""
        command_file = get_device_command_path()
        if not os.path.exists(command_file):
            return

        try:
            with open(command_file, 'r', encoding='utf-8') as f:
                command = json.load(f)
        except Exception as e:
            logger.error(f'读取设备命令文件失败: {e}')
            try:
                os.remove(command_file)
            except OSError:
                pass
            return

        try:
            os.remove(command_file)
        except OSError:
            pass

        action = str(command.get('action', '') or '')
        request_id = int(command.get('request_id', 0) or 0)
        payload = command.get('payload') if isinstance(command.get('payload'), dict) else {}
        if action == DEVICE_COMMAND_SCAN_KEYBOARD_CANDIDATES:
            self._scan_keyboard_candidates()
            return
        if action == DEVICE_COMMAND_BIND_KEYBOARD:
            device_id = str(payload.get('device_id', '') or '').strip()
            if device_id:
                self._bind_keyboard(device_id)
            return
        if action == DEVICE_COMMAND_UNBIND_KEYBOARD:
            self._unbind_keyboard()
            return
        if action == DEVICE_COMMAND_SCAN_BLUETOOTH_CANDIDATES:
            self._scan_bluetooth_candidates(request_id)
            return
        if action == DEVICE_COMMAND_BIND_BLUETOOTH:
            device_id = str(payload.get('device_id', '') or '').strip()
            if device_id:
                try:
                    self._bind_bluetooth(device_id, request_id)
                except Exception as exc:
                    logger.error('绑定蓝牙设备失败: %s', exc)
                    with self._lock:
                        self._bluetooth_scan_state = 'error'
                        self._bluetooth_scan_message = f'绑定失败：{exc}'
                        self._bluetooth_request_id = request_id
                    self._notify_update()
            return
        if action == DEVICE_COMMAND_UNBIND_BLUETOOTH:
            device_id = str(payload.get('device_id', '') or '').strip()
            if device_id:
                self._unbind_bluetooth(device_id)
            return
        if action == DEVICE_COMMAND_REFRESH_TRAY_ICON:
            # 托盘图标显示逻辑属于纯配置切换，不需要重扫 HID；
            # 这里只需重新广播一次当前快照，让 tray 重新执行图标选择策略即可。
            self._notify_update()
            return

    def start_command_listener(self):
        """启动 GUI 命令监听线程，保持 HID 相关动作都在 tray 进程执行。"""
        with self._command_thread_lock:
            if self._command_thread and self._command_thread.is_alive():
                return
            self._command_stop_event.clear()
            self._command_thread = threading.Thread(
                target=self._command_loop,
                daemon=True,
                name='device-command-listener',
            )
            self._command_thread.start()

    def stop_command_listener(self):
        """停止 GUI 命令监听线程。"""
        thread = None
        with self._command_thread_lock:
            self._command_stop_event.set()
            thread = self._command_thread

        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        with self._command_thread_lock:
            if self._command_thread is thread and (thread is None or not thread.is_alive()):
                self._command_thread = None

    def _command_loop(self):
        """持续监听 GUI 发来的命令文件。"""
        while not self._command_stop_event.wait(0.8):
            try:
                self._consume_device_command()
            except Exception as e:
                logger.error(f'处理设备命令失败: {e}')

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
            self._mouse_to_device.clear()
        self._consecutive_failures.clear()

        self._mouse_backends = enumerate_mouse_backends()
        for backend in self._mouse_backends:
            if backend.brand == 'logitech':
                brand = Brand.LOGITECH
                name = self._get_logitech_name(backend.product_id)
            else:
                brand = Brand.RAZER
                name = backend.product_name

            mouse = MouseInfo(
                name=name,
                brand=brand,
                product_id=backend.product_id,
                status_text="已连接，读取中...",
            )
            with self._lock:
                self._mice.append(mouse)
                self._mouse_to_device.append((brand, backend))

            logger.info(f"已添加{brand.value}设备: {mouse.name}")

        total = len(self._mouse_backends)
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
        """
        刷新所有设备的电池状态。

        通过 _mouse_to_device 映射逐个读取，替代原先按 idx 顺序匹配的脆弱逻辑。
        罗技/雷蛇的公共处理逻辑统一收口，仅在获取 BatteryInfo 实现上分叉。
        """
        # 在锁内复制一份映射快照，避免刷新过程中列表被其他线程修改
        with self._lock:
            snapshot = list(zip(self._mice, self._mouse_to_device))

        for idx, (mouse, (brand, device_obj)) in enumerate(snapshot):
            try:
                with self._lock:
                    prev_online = mouse.online

                # 按品牌分叉获取 BatteryInfo
                if brand == Brand.LOGITECH:
                    battery = self._get_logitech_battery_safe(device_obj)
                else:
                    battery = self._get_razer_battery_safe(device_obj)

                # 统一处理结果更新
                if battery:
                    if not self._is_battery_sample_valid(mouse, battery.percentage, battery.charging):
                        with self._lock:
                            mouse.status_text = "检测到异常帧，沿用上次有效电量"
                            mouse.last_update = time.time()
                        self._mark_failure(
                            self._device_key(mouse, idx),
                            "异常帧被过滤",
                            f"pid=0x{mouse.product_id:04X} path={self._safe_path_text(device_obj.path)}"
                        )
                        continue

                    if battery.percentage < 0:
                        # 只有明确的负值才视为无效样本；合法的 0% 需要保留给 UI/托盘展示，
                        # 否则会把真实低电量误判成断连并丢失告警语义。
                        with self._lock:
                            mouse.percentage = -1
                            mouse.charging = False
                            mouse.status_text = "休眠或连接中断"
                            mouse.online = False
                            mouse.last_update = time.time()
                        self._mark_failure(
                            self._device_key(mouse, idx),
                            "返回电量<=0",
                            f"pid=0x{mouse.product_id:04X} path={self._safe_path_text(device_obj.path)}"
                        )
                    else:
                        with self._lock:
                            mouse.percentage = battery.percentage
                            mouse.charging = battery.charging
                            mouse.status_text = battery.status_text
                            mouse.online = True
                            mouse.last_update = time.time()
                        self._mark_success(self._device_key(mouse, idx), battery.percentage, battery.charging)
                        if not prev_online:
                            logger.info(
                                f"设备状态恢复在线: {self._device_key(mouse, idx)}, "
                                f"pid=0x{mouse.product_id:04X}"
                            )
                else:
                    # 通信暂时失败时保留最后一次有效电量，避免误判离线导致电量闪烁
                    with self._lock:
                        if brand == Brand.LOGITECH:
                            mouse.status_text = "休眠中"
                        else:
                            mouse.status_text = "读取超时，沿用上次有效电量"
                        mouse.last_update = time.time()
                    self._mark_failure(
                        self._device_key(mouse, idx),
                        "电量读取返回空",
                        f"pid=0x{mouse.product_id:04X} path={self._safe_path_text(device_obj.path)}"
                    )
            except Exception as e:
                logger.error(f"刷新{brand.value}设备电池失败: {e}")
                with self._lock:
                    mouse.percentage = -1
                    mouse.status_text = "读取错误"
                    mouse.online = False
                    mouse.last_update = time.time()
                self._mark_failure(
                    self._device_key(mouse, idx),
                    "抛出异常",
                    f"pid=0x{mouse.product_id:04X} path={self._safe_path_text(device_obj.path)} err={type(e).__name__}: {e}"
                )

    @staticmethod
    def _get_logitech_battery_safe(receiver: MouseBackendHandle) -> Optional[BatteryInfo]:
        """通过桥接层安全获取罗技设备电量。"""
        battery = read_mouse_battery(receiver)
        return battery if isinstance(battery, BatteryInfo) else None

    @staticmethod
    def _get_razer_battery_safe(device: MouseBackendHandle) -> Optional[RazerBatteryInfo]:
        """通过桥接层安全获取雷蛇设备电量。"""
        battery = read_mouse_battery(device)
        return battery if isinstance(battery, RazerBatteryInfo) else None

    @staticmethod
    def _is_battery_sample_valid(mouse: MouseInfo, percentage: int, charging: bool) -> bool:
        """校验电量样本合法性，过滤明显异常跳变。"""
        if percentage < 0 or percentage > 100:
            return False

        # 没有历史值时，只做范围检查
        if mouse.percentage < 0:
            return True

        delta = abs(percentage - mouse.percentage)

        # 非充电状态下，单次跳变超过 40% 基本可判定为噪声帧
        if not charging and delta > 40:
            logger.warning(
                f"过滤异常电量跳变: {mouse.name} {mouse.percentage}% -> {percentage}% (charging={charging})"
            )
            return False

        # 充电状态下允许稍大波动，但超过 60% 仍视为异常
        if charging and delta > 60:
            logger.warning(
                f"过滤异常充电跳变: {mouse.name} {mouse.percentage}% -> {percentage}% (charging={charging})"
            )
            return False

        return True

    def start_auto_refresh(self, interval: int = 60):
        """启动自动刷新线程。

        使用 stop event + 单线程锁控制生命周期，避免快速停启时产生多个刷新线程。
        """
        safe_interval = max(1, int(interval))
        with self._auto_refresh_thread_lock:
            self._refresh_interval = safe_interval
            if self._auto_refresh_thread and self._auto_refresh_thread.is_alive():
                logger.debug(f"自动刷新线程已存在，仅更新间隔为 {safe_interval} 秒")
                return

            self._auto_refresh_stop_event.clear()
            self._auto_refresh_running = True
            self._auto_refresh_thread = threading.Thread(
                target=self._auto_refresh_loop,
                daemon=True,
                name="device-auto-refresh",
            )
            self._auto_refresh_thread.start()
        logger.info(f"自动刷新已启动，间隔 {safe_interval} 秒")

    def stop_auto_refresh(self):
        """停止自动刷新，并尽量等待后台线程收尾。"""
        thread = None
        with self._auto_refresh_thread_lock:
            self._auto_refresh_running = False
            self._auto_refresh_stop_event.set()
            thread = self._auto_refresh_thread

        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.5)

        with self._auto_refresh_thread_lock:
            if self._auto_refresh_thread is thread and (thread is None or not thread.is_alive()):
                self._auto_refresh_thread = None
        logger.info("自动刷新已停止")

    def _auto_refresh_loop(self):
        """自动刷新循环"""
        while not self._auto_refresh_stop_event.wait(self._refresh_interval):
            try:
                self.refresh_only()
            except Exception as e:
                logger.error(f"自动刷新出错: {e}")
        with self._auto_refresh_thread_lock:
            self._auto_refresh_running = False
            if threading.current_thread() is self._auto_refresh_thread:
                self._auto_refresh_thread = None

    def _close_all(self):
        """关闭所有设备连接"""
        for backend in self._mouse_backends:
            close_mouse_backend(backend)
        self._mouse_backends.clear()
        with self._lock:
            self._mouse_to_device.clear()

    def shutdown(self):
        """关闭管理器，先停线程，再串行关闭 HID 连接。"""
        self.stop_auto_refresh()
        self.stop_command_listener()
        with self._io_lock:
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


def _serialize_mouse_state(mouse: MouseInfo) -> dict:
    """将设备状态序列化为共享 JSON 可写入的数据结构。"""
    return {
        'name': mouse.name,
        'brand': mouse.brand.value if hasattr(mouse.brand, 'value') else str(mouse.brand),
        'percentage': mouse.percentage,
        'charging': mouse.charging,
        'status_text': mouse.status_text,
        'online': mouse.online,
        'last_update': mouse.last_update,
    }


def _serialize_keyboard_candidate(candidate: KeyboardCandidate) -> dict:
    """序列化键盘候选接口，供 GUI 弹窗展示。"""
    return {
        'device_id': candidate.device_id,
        'vendor_id': candidate.vendor_id,
        'product_id': candidate.product_id,
        'usage_page': candidate.usage_page,
        'usage': candidate.usage,
        'interface_number': candidate.interface_number,
        'product_name': candidate.product_name,
        'display_name': candidate.display_name,
    }


def _serialize_keyboard_state(keyboard: Optional[KeyboardInfo]) -> Optional[dict]:
    """序列化键盘电量快照，供 GUI 只读进程恢复。"""
    if keyboard is None:
        return None
    return {
        'device_id': keyboard.device_id,
        'name': keyboard.name,
        'brand': keyboard.brand,
        'percentage': keyboard.percentage,
        'charging': keyboard.charging,
        'status_text': keyboard.status_text,
        'online': keyboard.online,
        'last_update': keyboard.last_update,
        'vendor_id': keyboard.vendor_id,
        'product_id': keyboard.product_id,
        'usage_page': keyboard.usage_page,
        'usage': keyboard.usage,
        'interface_number': keyboard.interface_number,
        'product_name': keyboard.product_name,
    }


def _serialize_bluetooth_candidate(candidate: BluetoothCandidate) -> dict:
    return {
        'device_id': candidate.device_id,
        'name': candidate.name,
        'connected': candidate.connected,
    }


def _serialize_bluetooth_state(device: BluetoothInfo) -> dict:
    return {
        'device_id': device.device_id,
        'name': device.name,
        'percentage': device.percentage,
        'charging': device.charging,
        'status_text': device.status_text,
        'online': device.online,
        'last_update': device.last_update,
    }


def _coerce_shared_bool(value) -> bool:
    """把共享状态里的布尔字段转成稳定布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return False


def _coerce_shared_percentage(value) -> int:
    """把共享状态里的电量值约束在项目约定范围内。"""
    try:
        pct = int(value)
    except (TypeError, ValueError):
        return -1
    if pct < -1 or pct > 100:
        return -1
    return pct


def _deserialize_mouse_state(item: dict, index: int) -> Optional[MouseInfo]:
    """把共享 JSON 条目恢复为 [`MouseInfo`](devices.py:28)；脏条目会被跳过并记录日志。"""
    if not isinstance(item, dict):
        logger.warning(f"共享状态第 {index} 项不是对象，已忽略: {item!r}")
        return None

    try:
        brand = Brand(item.get('brand', '罗技'))
    except (ValueError, KeyError, TypeError):
        brand = Brand.LOGITECH

    try:
        last_update = float(item.get('last_update', 0) or 0)
    except (TypeError, ValueError):
        last_update = 0.0

    return MouseInfo(
        name=str(item.get('name', '未知设备')),
        brand=brand,
        percentage=_coerce_shared_percentage(item.get('percentage', -1)),
        charging=_coerce_shared_bool(item.get('charging', False)),
        status_text=str(item.get('status_text', '未知')),
        online=_coerce_shared_bool(item.get('online', False)),
        last_update=last_update,
    )


def _deserialize_keyboard_candidate(item: dict) -> Optional[KeyboardCandidate]:
    """把共享状态中的键盘候选条目恢复为候选对象。"""
    if not isinstance(item, dict):
        return None
    device_id = str(item.get('device_id', '') or '').strip()
    if not device_id:
        return None
    return KeyboardCandidate(
        device_id=device_id,
        vendor_id=int(item.get('vendor_id', 0) or 0),
        product_id=int(item.get('product_id', 0) or 0),
        usage_page=int(item.get('usage_page', 0) or 0),
        usage=int(item.get('usage', 0) or 0),
        interface_number=int(item.get('interface_number', -1) or -1),
        product_name=str(item.get('product_name', '') or ''),
        display_name=str(item.get('display_name', item.get('product_name', '未知键盘')) or '未知键盘'),
    )


def _deserialize_keyboard_state(item: dict | None) -> Optional[KeyboardInfo]:
    """把共享状态中的键盘快照恢复为键盘对象。"""
    if not isinstance(item, dict):
        return None
    try:
        last_update = float(item.get('last_update', 0) or 0)
    except (TypeError, ValueError):
        last_update = 0.0
    return KeyboardInfo(
        device_id=str(item.get('device_id', '') or ''),
        name=str(item.get('name', 'NUT75 2.4G') or 'NUT75 2.4G'),
        brand=str(item.get('brand', 'NUT') or 'NUT'),
        percentage=_coerce_shared_percentage(item.get('percentage', -1)),
        charging=_coerce_shared_bool(item.get('charging', False)),
        status_text=str(item.get('status_text', '未连接') or '未连接'),
        online=_coerce_shared_bool(item.get('online', False)),
        last_update=last_update,
        vendor_id=int(item.get('vendor_id', 0) or 0),
        product_id=int(item.get('product_id', 0) or 0),
        usage_page=int(item.get('usage_page', 0) or 0),
        usage=int(item.get('usage', 0) or 0),
        interface_number=int(item.get('interface_number', -1) or -1),
        product_name=str(item.get('product_name', '') or ''),
    )


def _deserialize_bluetooth_candidate(item: dict) -> Optional[BluetoothCandidate]:
    if not isinstance(item, dict):
        return None
    device_id = str(item.get('device_id', '') or '').strip()
    if not device_id:
        return None
    return BluetoothCandidate(
        device_id=device_id,
        name=str(item.get('name', '') or '未知蓝牙设备'),
        connected=_coerce_shared_bool(item.get('connected', False)),
    )


def _deserialize_bluetooth_state(item: dict) -> Optional[BluetoothInfo]:
    if not isinstance(item, dict):
        return None
    device_id = str(item.get('device_id', '') or '').strip()
    if not device_id:
        return None
    try:
        last_update = float(item.get('last_update', 0) or 0)
    except (TypeError, ValueError):
        last_update = 0.0
    return BluetoothInfo(
        device_id=device_id,
        name=str(item.get('name', '') or '未知蓝牙设备'),
        percentage=_coerce_shared_percentage(item.get('percentage', -1)),
        charging=_coerce_shared_bool(item.get('charging', False)),
        status_text=str(item.get('status_text', '') or '未连接'),
        online=_coerce_shared_bool(item.get('online', False)),
        last_update=last_update,
    )


class SharedStateDeviceManager:
    """
    只读的设备管理器，通过读取共享状态文件获取数据。
    供 GUI 子进程使用，不打开任何 HID 设备，避免句柄争抢。
    """

    def __init__(self):
        self._mice: list[MouseInfo] = []
        self._keyboard: Optional[KeyboardInfo] = None
        self._keyboard_candidates: list[KeyboardCandidate] = []
        self._bluetooth_devices: list[BluetoothInfo] = []
        self._bluetooth_candidates: list[BluetoothCandidate] = []
        self._bluetooth_scan_state = 'idle'
        self._bluetooth_scan_message = ''
        self._bluetooth_request_id = 0
        self._lock = threading.Lock()
        self._on_update_callbacks: list[Callable] = []
        self._auto_refresh_running = False
        self._auto_refresh_thread: Optional[threading.Thread] = None
        self._auto_refresh_stop_event = threading.Event()
        self._auto_refresh_thread_lock = threading.Lock()
        self._refresh_interval = 3
        # 最近一次共享状态读取结果，供 GUI 明确区分「空状态」和「读取失败」。
        self._last_read_state = 'idle'
        self._last_read_error = ''

    @property
    def mice(self) -> list[MouseInfo]:
        with self._lock:
            return list(self._mice)

    @property
    def keyboard(self) -> Optional[KeyboardInfo]:
        with self._lock:
            return self._keyboard

    @property
    def keyboard_candidates(self) -> list[KeyboardCandidate]:
        with self._lock:
            return list(self._keyboard_candidates)

    @property
    def keyboard_scan_state(self) -> str:
        with self._lock:
            return getattr(self, '_keyboard_scan_state', 'idle')

    @property
    def keyboard_scan_message(self) -> str:
        with self._lock:
            return getattr(self, '_keyboard_scan_message', '')

    @property
    def bluetooth_devices(self) -> list[BluetoothInfo]:
        with self._lock:
            return list(self._bluetooth_devices)

    @property
    def bluetooth_candidates(self) -> list[BluetoothCandidate]:
        with self._lock:
            return list(self._bluetooth_candidates)

    @property
    def bluetooth_scan_state(self) -> str:
        with self._lock:
            return self._bluetooth_scan_state

    @property
    def bluetooth_scan_message(self) -> str:
        with self._lock:
            return self._bluetooth_scan_message

    @property
    def bluetooth_request_id(self) -> int:
        with self._lock:
            return self._bluetooth_request_id

    @property
    def last_read_state(self) -> str:
        """最近一次共享状态读取结果：idle / ok / missing / error。"""
        with self._lock:
            return self._last_read_state

    @property
    def last_read_error(self) -> str:
        """最近一次共享状态读取的用户可见提示。"""
        with self._lock:
            return self._last_read_error

    def _set_last_read_result(self, state: str, error: str = ''):
        """记录最近一次共享状态读取结果，供 GUI 决定展示空态还是错误态。"""
        with self._lock:
            self._last_read_state = state
            self._last_read_error = error

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
        callbacks = list(self._on_update_callbacks)
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"共享状态更新回调出错: {e}")

    def _read_shared_state(self):
        """从共享状态文件读取设备数据。

        读取时先完整解析再整体替换内存快照，避免损坏文件把当前 UI 状态打成半截数据。
        """
        state_file = get_shared_state_path()
        try:
            if not os.path.exists(state_file):
                with self._lock:
                    self._mice = []
                    self._last_read_state = 'missing'
                    self._last_read_error = '尚未收到托盘进程写入的设备状态，请确认主程序正在运行。'
                return

            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            mice = []
            keyboard = None
            keyboard_candidates: list[KeyboardCandidate] = []
            keyboard_scan_state = 'idle'
            keyboard_scan_message = ''
            bluetooth_devices: list[BluetoothInfo] = []
            bluetooth_candidates: list[BluetoothCandidate] = []
            bluetooth_scan_state = 'idle'
            bluetooth_scan_message = ''
            bluetooth_request_id = 0

            # 兼容旧版共享状态：根节点为纯鼠标数组。
            if isinstance(data, list):
                mouse_items = data
            elif isinstance(data, dict):
                mouse_items = data.get('mice', [])
                keyboard = _deserialize_keyboard_state(data.get('keyboard'))
                keyboard_candidates = [
                    candidate for candidate in (
                        _deserialize_keyboard_candidate(item) for item in data.get('keyboard_candidates', [])
                    )
                    if candidate is not None
                ]
                keyboard_scan_state = str(data.get('keyboard_scan_state', 'idle') or 'idle')
                keyboard_scan_message = str(data.get('keyboard_scan_message', '') or '')
                bluetooth_devices = [
                    device for device in (
                        _deserialize_bluetooth_state(item) for item in data.get('bluetooth_devices', [])
                    )
                    if device is not None
                ]
                bluetooth_candidates = [
                    candidate for candidate in (
                        _deserialize_bluetooth_candidate(item) for item in data.get('bluetooth_candidates', [])
                    )
                    if candidate is not None
                ]
                bluetooth_scan_state = str(data.get('bluetooth_scan_state', 'idle') or 'idle')
                bluetooth_scan_message = str(data.get('bluetooth_scan_message', '') or '')
                bluetooth_request_id = int(data.get('bluetooth_request_id', 0) or 0)
            else:
                raise ValueError(f"共享状态文件根节点类型不支持: {type(data).__name__}")

            for idx, item in enumerate(mouse_items):
                mouse = _deserialize_mouse_state(item, idx)
                if mouse is not None:
                    mice.append(mouse)

            with self._lock:
                self._mice = mice
                self._keyboard = keyboard
                self._keyboard_candidates = keyboard_candidates
                self._keyboard_scan_state = keyboard_scan_state
                self._keyboard_scan_message = keyboard_scan_message
                self._bluetooth_devices = bluetooth_devices
                self._bluetooth_candidates = bluetooth_candidates
                self._bluetooth_scan_state = bluetooth_scan_state
                self._bluetooth_scan_message = bluetooth_scan_message
                self._bluetooth_request_id = bluetooth_request_id
                self._last_read_state = 'ok'
                self._last_read_error = ''
        except Exception as e:
            self._set_last_read_result(
                'error',
                '读取共享状态失败，当前显示上次有效结果。请稍后重试或确认托盘进程是否正常。'
            )
            logger.warning(f"读取共享状态文件失败，沿用上次有效快照: {type(e).__name__}: {e}")

    def scan_and_refresh(self):
        self._read_shared_state()
        self._notify_update()

    def refresh_only(self):
        self._read_shared_state()
        self._notify_update()

    def start_auto_refresh(self, interval: int = 3):
        """启动共享状态轮询线程，避免 GUI 重复拉起多个读取线程。"""
        safe_interval = max(1, int(interval))
        with self._auto_refresh_thread_lock:
            self._refresh_interval = safe_interval
            if self._auto_refresh_thread and self._auto_refresh_thread.is_alive():
                logger.debug(f"共享状态刷新线程已存在，仅更新间隔为 {safe_interval} 秒")
                return
            self._auto_refresh_stop_event.clear()
            self._auto_refresh_running = True
            self._auto_refresh_thread = threading.Thread(
                target=self._auto_refresh_loop,
                daemon=True,
                name="shared-state-auto-refresh",
            )
            self._auto_refresh_thread.start()

    def stop_auto_refresh(self):
        """停止共享状态轮询线程。"""
        thread = None
        with self._auto_refresh_thread_lock:
            self._auto_refresh_running = False
            self._auto_refresh_stop_event.set()
            thread = self._auto_refresh_thread

        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)

        with self._auto_refresh_thread_lock:
            if self._auto_refresh_thread is thread and (thread is None or not thread.is_alive()):
                self._auto_refresh_thread = None

    def _auto_refresh_loop(self):
        while not self._auto_refresh_stop_event.wait(self._refresh_interval):
            try:
                self.refresh_only()
            except Exception as e:
                logger.error(f"共享状态自动刷新出错: {e}")
        with self._auto_refresh_thread_lock:
            self._auto_refresh_running = False
            if threading.current_thread() is self._auto_refresh_thread:
                self._auto_refresh_thread = None

    def shutdown(self):
        self.stop_auto_refresh()
