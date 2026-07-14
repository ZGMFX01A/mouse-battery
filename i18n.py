"""
应用界面国际化支持。

当前仅提供简体中文与英文两套文案，供 GUI 与 tray 共用，
并负责把底层状态模块吐出的中文运行时文案按当前语言进行展示层翻译。
"""

from __future__ import annotations

import ctypes
import locale
import logging
import re

logger = logging.getLogger(__name__)


# 语言偏好枚举值：
# - auto：默认跟随系统 UI 语言
# - zh-CN / en-US：用户手动覆盖系统语言的显式选择
LANGUAGE_AUTO = 'auto'
LANGUAGE_ZH_CN = 'zh-CN'
LANGUAGE_EN_US = 'en-US'
SUPPORTED_UI_LANGUAGE_VALUES = {LANGUAGE_AUTO, LANGUAGE_ZH_CN, LANGUAGE_EN_US}


def normalize_ui_language(value: str | None, allow_auto: bool = True) -> str:
    """规范化语言值，统一兼容大小写、下划线和空值。"""
    raw = str(value or '').strip()
    if not raw:
        return LANGUAGE_AUTO if allow_auto else LANGUAGE_ZH_CN

    normalized = raw.replace('_', '-').lower()
    if normalized == 'auto':
        return LANGUAGE_AUTO if allow_auto else LANGUAGE_ZH_CN
    if normalized.startswith('zh'):
        return LANGUAGE_ZH_CN
    if normalized.startswith('en'):
        return LANGUAGE_EN_US
    return LANGUAGE_AUTO if allow_auto else LANGUAGE_ZH_CN


def detect_system_language() -> str:
    """检测当前 Windows UI 语言，并收敛到本项目支持的语言集合。"""
    try:
        # 业务含义：Windows UI 语言比进程 locale 更贴近用户看到的系统界面语言。
        language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        locale_name = locale.windows_locale.get(language_id, '')
        normalized = normalize_ui_language(locale_name, allow_auto=False)
        return normalized if normalized in (LANGUAGE_ZH_CN, LANGUAGE_EN_US) else LANGUAGE_EN_US
    except Exception as ex:
        logger.debug(f'检测系统语言失败，回退英文: {ex}')
        return LANGUAGE_EN_US


def resolve_ui_language(preference: str | None) -> str:
    """把用户偏好解析为最终生效语言。"""
    normalized = normalize_ui_language(preference, allow_auto=True)
    if normalized == LANGUAGE_AUTO:
        return detect_system_language()
    return normalized


_TRANSLATIONS = {
    'app.window_title': {
        LANGUAGE_ZH_CN: '鼠标电量监控',
        LANGUAGE_EN_US: 'Mouse Battery Monitor',
    },
    'app.header_title': {
        LANGUAGE_ZH_CN: '鼠标电量监控',
        LANGUAGE_EN_US: 'Mouse Battery Monitor',
    },
    'app.header_subtitle': {
        LANGUAGE_ZH_CN: '实时查看无线鼠标电量',
        LANGUAGE_EN_US: 'Live battery status for wireless devices',
    },
    'settings.title': {
        LANGUAGE_ZH_CN: '设置',
        LANGUAGE_EN_US: 'Settings',
    },
    'settings.autostart.title': {
        LANGUAGE_ZH_CN: '开机自动启动',
        LANGUAGE_EN_US: 'Launch at Startup',
    },
    'settings.autostart.subtitle': {
        LANGUAGE_ZH_CN: '跟随 Windows 启动，在后台静默运行',
        LANGUAGE_EN_US: 'Start with Windows and keep running quietly in the background',
    },
    'settings.auto_update.title': {
        LANGUAGE_ZH_CN: '自动检查更新 · 当前 {version}',
        LANGUAGE_EN_US: 'Auto Update Check · Current {version}',
    },
    'settings.auto_update.subtitle': {
        LANGUAGE_ZH_CN: '启动时自动下载新版本并静默升级',
        LANGUAGE_EN_US: 'Automatically download new releases at startup and upgrade silently',
    },
    'settings.low_battery.title': {
        LANGUAGE_ZH_CN: '低电量提醒',
        LANGUAGE_EN_US: 'Low Battery Alert',
    },
    'settings.low_battery.subtitle': {
        LANGUAGE_ZH_CN: '系统右下角弹出通知，阶梯防漏式告警',
        LANGUAGE_EN_US: 'Show a desktop notification with step-based repeat alerts',
    },
    'settings.tray_priority.title': {
        LANGUAGE_ZH_CN: '托盘图标显示逻辑',
        LANGUAGE_EN_US: 'Tray Icon Priority',
    },
    'settings.tray_priority.subtitle': {
        LANGUAGE_ZH_CN: '控制鼠标与键盘同时存在时托盘图标优先显示哪台设备',
        LANGUAGE_EN_US: 'Choose which device the tray icon should prefer when mouse and keyboard are both present',
    },
    'settings.tray_priority.mouse_first': {
        LANGUAGE_ZH_CN: '优先鼠标',
        LANGUAGE_EN_US: 'Mouse First',
    },
    'settings.tray_priority.keyboard_first': {
        LANGUAGE_ZH_CN: '优先键盘',
        LANGUAGE_EN_US: 'Keyboard First',
    },
    'settings.tray_priority.lowest_battery': {
        LANGUAGE_ZH_CN: '低电量优先',
        LANGUAGE_EN_US: 'Lowest Battery First',
    },
    'settings.off': {
        LANGUAGE_ZH_CN: '关闭',
        LANGUAGE_EN_US: 'Off',
    },
    'status.auto_refresh': {
        LANGUAGE_ZH_CN: '自动刷新',
        LANGUAGE_EN_US: 'Auto Refresh',
    },
    'action.scan': {
        LANGUAGE_ZH_CN: '扫描设备',
        LANGUAGE_EN_US: 'Scan Devices',
    },
    'action.scan_loading': {
        LANGUAGE_ZH_CN: '扫描中...',
        LANGUAGE_EN_US: 'Scanning...',
    },
    'action.refresh': {
        LANGUAGE_ZH_CN: '刷新电量',
        LANGUAGE_EN_US: 'Refresh Battery',
    },
    'action.refresh_loading': {
        LANGUAGE_ZH_CN: '刷新中...',
        LANGUAGE_EN_US: 'Refreshing...',
    },
    'action.check_update': {
        LANGUAGE_ZH_CN: '检查更新',
        LANGUAGE_EN_US: 'Check Updates',
    },
    'action.check_update_loading': {
        LANGUAGE_ZH_CN: '检查中...',
        LANGUAGE_EN_US: 'Checking...',
    },
    'action.add_keyboard': {
        LANGUAGE_ZH_CN: '新增键盘',
        LANGUAGE_EN_US: 'Add Keyboard',
    },
    'action.add_bluetooth': {
        LANGUAGE_ZH_CN: '添加蓝牙设备',
        LANGUAGE_EN_US: 'Add Bluetooth Device',
    },
    'dialog.ok': {
        LANGUAGE_ZH_CN: '确定',
        LANGUAGE_EN_US: 'OK',
    },
    'dialog.cancel': {
        LANGUAGE_ZH_CN: '取消',
        LANGUAGE_EN_US: 'Cancel',
    },
    'dialog.remove': {
        LANGUAGE_ZH_CN: '移除',
        LANGUAGE_EN_US: 'Remove',
    },
    'dialog.connect': {
        LANGUAGE_ZH_CN: '连接',
        LANGUAGE_EN_US: 'Connect',
    },
    'dialog.add': {
        LANGUAGE_ZH_CN: '添加',
        LANGUAGE_EN_US: 'Add',
    },
    'view.empty.default_title': {
        LANGUAGE_ZH_CN: '未发现鼠标设备',
        LANGUAGE_EN_US: 'No Mouse Device Found',
    },
    'view.empty.default_message': {
        LANGUAGE_ZH_CN: '请确保鼠标已开机且无线接收器已插入\n如 G Hub / Synapse 正在运行，请先退出\n可能需要以管理员身份运行本程序',
        LANGUAGE_EN_US: 'Make sure the mouse is powered on and the wireless receiver is connected\nIf G Hub or Synapse is running, please exit it first\nYou may also need to run this app as administrator',
    },
    'view.loading.title': {
        LANGUAGE_ZH_CN: '正在同步设备状态',
        LANGUAGE_EN_US: 'Syncing Device Status',
    },
    'view.loading.message': {
        LANGUAGE_ZH_CN: '正在从托盘进程读取最新电量信息，请稍候…',
        LANGUAGE_EN_US: 'Reading the latest battery data from the tray process. Please wait…',
    },
    'view.error.title': {
        LANGUAGE_ZH_CN: '读取设备状态失败',
        LANGUAGE_EN_US: 'Failed to Read Device Status',
    },
    'view.error.message': {
        LANGUAGE_ZH_CN: '请确认托盘进程仍在运行，然后点击“刷新电量”重试。',
        LANGUAGE_EN_US: 'Please make sure the tray process is still running, then click “Refresh Battery” to try again.',
    },
    'view.not_synced.title': {
        LANGUAGE_ZH_CN: '尚未同步到设备状态',
        LANGUAGE_EN_US: 'Device Status Not Synced Yet',
    },
    'status.syncing': {
        LANGUAGE_ZH_CN: '正在同步设备状态...',
        LANGUAGE_EN_US: 'Syncing device status...',
    },
    'status.read_failed': {
        LANGUAGE_ZH_CN: '读取设备状态失败',
        LANGUAGE_EN_US: 'Failed to read device status',
    },
    'status.devices_found': {
        LANGUAGE_ZH_CN: '已发现 {count} 个设备',
        LANGUAGE_EN_US: '{count} device(s) detected',
    },
    'status.no_devices': {
        LANGUAGE_ZH_CN: '未发现设备',
        LANGUAGE_EN_US: 'No devices detected',
    },
    'device.waiting_update': {
        LANGUAGE_ZH_CN: '等待更新',
        LANGUAGE_EN_US: 'Waiting for update',
    },
    'device.updated_at': {
        LANGUAGE_ZH_CN: '更新于 {time}',
        LANGUAGE_EN_US: 'Updated at {time}',
    },
    'device.unknown_mouse': {
        LANGUAGE_ZH_CN: '未知鼠标',
        LANGUAGE_EN_US: 'Unknown Mouse',
    },
    'device.bolt_mouse': {
        LANGUAGE_ZH_CN: 'Bolt 鼠标',
        LANGUAGE_EN_US: 'Bolt Mouse',
    },
    'device.unifying_mouse': {
        LANGUAGE_ZH_CN: 'Unifying 鼠标',
        LANGUAGE_EN_US: 'Unifying Mouse',
    },
    'device.logitech_mouse_with_pid': {
        LANGUAGE_ZH_CN: '罗技鼠标 ({pid})',
        LANGUAGE_EN_US: 'Logitech Mouse ({pid})',
    },
    'brand.logitech': {
        LANGUAGE_ZH_CN: '罗技',
        LANGUAGE_EN_US: 'Logitech',
    },
    'brand.razer': {
        LANGUAGE_ZH_CN: '雷蛇',
        LANGUAGE_EN_US: 'Razer',
    },
    'status.disconnected': {
        LANGUAGE_ZH_CN: '未连接',
        LANGUAGE_EN_US: 'Disconnected',
    },
    'status.connected_reading': {
        LANGUAGE_ZH_CN: '已连接，读取中...',
        LANGUAGE_EN_US: 'Connected, reading...',
    },
    'status.sleep_or_disconnected': {
        LANGUAGE_ZH_CN: '休眠或连接中断',
        LANGUAGE_EN_US: 'Sleeping or disconnected',
    },
    'status.sleeping': {
        LANGUAGE_ZH_CN: '休眠中',
        LANGUAGE_EN_US: 'Sleeping',
    },
    'status.read_timeout_keep_last': {
        LANGUAGE_ZH_CN: '读取超时，沿用上次有效电量',
        LANGUAGE_EN_US: 'Read timed out, keeping the last valid battery value',
    },
    'status.read_error': {
        LANGUAGE_ZH_CN: '读取错误',
        LANGUAGE_EN_US: 'Read error',
    },
    'status.invalid_frame_keep_last': {
        LANGUAGE_ZH_CN: '检测到异常帧，沿用上次有效电量',
        LANGUAGE_EN_US: 'Abnormal frame detected, keeping the last valid battery value',
    },
    'status.charging': {
        LANGUAGE_ZH_CN: '充电中',
        LANGUAGE_EN_US: 'Charging',
    },
    'status.full': {
        LANGUAGE_ZH_CN: '已充满',
        LANGUAGE_EN_US: 'Fully Charged',
    },
    'status.good': {
        LANGUAGE_ZH_CN: '电量良好',
        LANGUAGE_EN_US: 'Battery Good',
    },
    'status.low': {
        LANGUAGE_ZH_CN: '电量低',
        LANGUAGE_EN_US: 'Low Battery',
    },
    'status.critical': {
        LANGUAGE_ZH_CN: '电量极低',
        LANGUAGE_EN_US: 'Critical Battery',
    },
    'status.discharging': {
        LANGUAGE_ZH_CN: '放电中',
        LANGUAGE_EN_US: 'Discharging',
    },
    'keyboard.bound_not_found': {
        LANGUAGE_ZH_CN: '未找到已绑定键盘',
        LANGUAGE_EN_US: 'Bound keyboard not found',
    },
    'keyboard.read_failed': {
        LANGUAGE_ZH_CN: '读取失败',
        LANGUAGE_EN_US: 'Read failed',
    },
    'shared_state.missing': {
        LANGUAGE_ZH_CN: '尚未收到托盘进程写入的设备状态，请确认主程序正在运行。',
        LANGUAGE_EN_US: 'No device state has been written by the tray process yet. Please make sure the main app is running.',
    },
    'shared_state.read_failed_keep_last': {
        LANGUAGE_ZH_CN: '读取共享状态失败，当前显示上次有效结果。请稍后重试或确认托盘进程是否正常。',
        LANGUAGE_EN_US: 'Failed to read shared state. The last valid result is still shown. Please try again later or confirm the tray process is healthy.',
    },
    'keyboard.scan.loading_runtime': {
        LANGUAGE_ZH_CN: '正在扫描键盘候选设备...',
        LANGUAGE_EN_US: 'Scanning keyboard candidates...',
    },
    'keyboard.scan.found_runtime': {
        LANGUAGE_ZH_CN: '已发现 {count} 个键盘候选设备',
        LANGUAGE_EN_US: '{count} keyboard candidate(s) found',
    },
    'keyboard.scan.none_runtime': {
        LANGUAGE_ZH_CN: '未发现可绑定的键盘候选设备',
        LANGUAGE_EN_US: 'No bindable keyboard candidates found',
    },
    'keyboard.bind.failed_not_found_runtime': {
        LANGUAGE_ZH_CN: '绑定失败：未找到对应的键盘设备',
        LANGUAGE_EN_US: 'Binding failed: matching keyboard device was not found',
    },
    'keyboard.bound_runtime': {
        LANGUAGE_ZH_CN: '已绑定键盘：{name}',
        LANGUAGE_EN_US: 'Keyboard bound: {name}',
    },
    'keyboard.removed_runtime': {
        LANGUAGE_ZH_CN: '已移除当前键盘绑定',
        LANGUAGE_EN_US: 'Current keyboard binding removed',
    },
    'keyboard.remove.title': {
        LANGUAGE_ZH_CN: '移除键盘',
        LANGUAGE_EN_US: 'Remove Keyboard',
    },
    'keyboard.remove.message': {
        LANGUAGE_ZH_CN: '确认移除当前绑定的键盘吗？移除后键盘卡片会消失，如需恢复请重新点击“新增键盘”。',
        LANGUAGE_EN_US: 'Remove the currently bound keyboard? The keyboard card will disappear. Click “Add Keyboard” again if you need to bind it later.',
    },
    'keyboard.remove.failed.title': {
        LANGUAGE_ZH_CN: '移除失败',
        LANGUAGE_EN_US: 'Remove Failed',
    },
    'keyboard.remove.failed.message': {
        LANGUAGE_ZH_CN: '提交解除绑定请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the unbind request: {error}',
    },
    'keyboard.dialog.loading': {
        LANGUAGE_ZH_CN: '正在扫描键盘候选设备...',
        LANGUAGE_EN_US: 'Scanning keyboard candidates...',
    },
    'keyboard.dialog.empty_title': {
        LANGUAGE_ZH_CN: '未发现可绑定的键盘设备',
        LANGUAGE_EN_US: 'No Bindable Keyboard Found',
    },
    'keyboard.dialog.empty_message': {
        LANGUAGE_ZH_CN: '请确认键盘已通过 2.4G 接收器连接，然后重新点击“新增键盘”。',
        LANGUAGE_EN_US: 'Make sure the keyboard is connected through its 2.4G receiver, then click “Add Keyboard” again.',
    },
    'keyboard.dialog.helper': {
        LANGUAGE_ZH_CN: '当前列表已经按规则自动去重，并且只保留了这把键盘最可信的控制接口。 如果只看到 1 个选项，直接连接即可。',
        LANGUAGE_EN_US: 'This list is already deduplicated automatically and keeps only the most reliable control interface for this keyboard. If you only see one option, you can connect it directly.',
    },
    'keyboard.dialog.current_bound_option': {
        LANGUAGE_ZH_CN: '{name}（当前已绑定）',
        LANGUAGE_EN_US: '{name} (currently bound)',
    },
    'keyboard.select_required.title': {
        LANGUAGE_ZH_CN: '请选择设备',
        LANGUAGE_EN_US: 'Select a Device',
    },
    'keyboard.select_required.message': {
        LANGUAGE_ZH_CN: '请先在列表中选择一个键盘设备。',
        LANGUAGE_EN_US: 'Please choose a keyboard device from the list first.',
    },
    'keyboard.bind.failed.title': {
        LANGUAGE_ZH_CN: '绑定失败',
        LANGUAGE_EN_US: 'Bind Failed',
    },
    'keyboard.bind.failed.message': {
        LANGUAGE_ZH_CN: '提交键盘绑定请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the keyboard bind request: {error}',
    },
    'keyboard.select.title': {
        LANGUAGE_ZH_CN: '选择键盘设备',
        LANGUAGE_EN_US: 'Select a Keyboard Device',
    },
    'keyboard.add.failed.title': {
        LANGUAGE_ZH_CN: '新增键盘失败',
        LANGUAGE_EN_US: 'Add Keyboard Failed',
    },
    'keyboard.add.failed.message': {
        LANGUAGE_ZH_CN: '提交键盘扫描请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the keyboard scan request: {error}',
    },
    'bluetooth.dialog.loading': {
        LANGUAGE_ZH_CN: '正在读取 Windows 已配对蓝牙设备...',
        LANGUAGE_EN_US: 'Reading paired Bluetooth devices from Windows...',
    },
    'bluetooth.dialog.empty_title': {
        LANGUAGE_ZH_CN: '未发现已配对蓝牙设备',
        LANGUAGE_EN_US: 'No Paired Bluetooth Devices',
    },
    'bluetooth.dialog.empty_message': {
        LANGUAGE_ZH_CN: '请先在 Windows 设置中完成蓝牙配对，然后重试。',
        LANGUAGE_EN_US: 'Pair the device in Windows Settings first, then try again.',
    },
    'bluetooth.dialog.helper': {
        LANGUAGE_ZH_CN: '列表包含当前未连接或休眠的设备。仅支持公开标准 BLE Battery Service（0x180F）的设备。',
        LANGUAGE_EN_US: 'The list includes disconnected or sleeping devices. Only the standard BLE Battery Service (0x180F) is supported.',
    },
    'bluetooth.dialog.option': {
        LANGUAGE_ZH_CN: '{name} · {status}',
        LANGUAGE_EN_US: '{name} · {status}',
    },
    'bluetooth.status.connected': {
        LANGUAGE_ZH_CN: '当前已连接',
        LANGUAGE_EN_US: 'Connected',
    },
    'bluetooth.status.sleeping': {
        LANGUAGE_ZH_CN: '未连接 / 可能休眠',
        LANGUAGE_EN_US: 'Disconnected / may be sleeping',
    },
    'bluetooth.status.added': {
        LANGUAGE_ZH_CN: ' · 已添加',
        LANGUAGE_EN_US: ' · Added',
    },
    'bluetooth.state.paired_sleeping': {
        LANGUAGE_ZH_CN: '已配对，未连接或休眠',
        LANGUAGE_EN_US: 'Paired, disconnected or sleeping',
    },
    'bluetooth.state.not_paired': {
        LANGUAGE_ZH_CN: '未在 Windows 已配对设备中找到',
        LANGUAGE_EN_US: 'Not found in Windows paired devices',
    },
    'bluetooth.state.no_service': {
        LANGUAGE_ZH_CN: '设备未公开标准 BLE Battery Service（0x180F）。',
        LANGUAGE_EN_US: 'The device does not expose the standard BLE Battery Service (0x180F).',
    },
    'bluetooth.state.no_level': {
        LANGUAGE_ZH_CN: '设备未公开可读的 Battery Level（0x2A19）。',
        LANGUAGE_EN_US: 'The device does not expose a readable Battery Level (0x2A19).',
    },
    'bluetooth.select.title': {
        LANGUAGE_ZH_CN: '添加蓝牙设备',
        LANGUAGE_EN_US: 'Add Bluetooth Device',
    },
    'bluetooth.select_required.title': {
        LANGUAGE_ZH_CN: '请选择设备',
        LANGUAGE_EN_US: 'Select a Device',
    },
    'bluetooth.select_required.message': {
        LANGUAGE_ZH_CN: '请先选择一个尚未添加的蓝牙设备。',
        LANGUAGE_EN_US: 'Select a Bluetooth device that has not been added yet.',
    },
    'bluetooth.bind.failed.title': {
        LANGUAGE_ZH_CN: '添加蓝牙设备失败',
        LANGUAGE_EN_US: 'Add Bluetooth Device Failed',
    },
    'bluetooth.bind.failed.message': {
        LANGUAGE_ZH_CN: '提交蓝牙绑定请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the Bluetooth bind request: {error}',
    },
    'bluetooth.add.failed.title': {
        LANGUAGE_ZH_CN: '扫描蓝牙设备失败',
        LANGUAGE_EN_US: 'Bluetooth Scan Failed',
    },
    'bluetooth.add.failed.message': {
        LANGUAGE_ZH_CN: '提交蓝牙扫描请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the Bluetooth scan request: {error}',
    },
    'bluetooth.remove.title': {
        LANGUAGE_ZH_CN: '移除蓝牙设备',
        LANGUAGE_EN_US: 'Remove Bluetooth Device',
    },
    'bluetooth.remove.message': {
        LANGUAGE_ZH_CN: '确认移除这台蓝牙设备吗？之后可从已配对设备列表重新添加。',
        LANGUAGE_EN_US: 'Remove this Bluetooth device? You can add it again from the paired-device list.',
    },
    'bluetooth.remove.failed.title': {
        LANGUAGE_ZH_CN: '移除蓝牙设备失败',
        LANGUAGE_EN_US: 'Remove Bluetooth Device Failed',
    },
    'bluetooth.remove.failed.message': {
        LANGUAGE_ZH_CN: '提交移除请求失败：{error}',
        LANGUAGE_EN_US: 'Failed to submit the remove request: {error}',
    },
    'update.timeout.title': {
        LANGUAGE_ZH_CN: '网络超时',
        LANGUAGE_EN_US: 'Network Timeout',
    },
    'update.timeout.message': {
        LANGUAGE_ZH_CN: '检查更新超时，请检查网络连接后重试。',
        LANGUAGE_EN_US: 'Update checking timed out. Please check your network connection and try again.',
    },
    'update.network_error.title': {
        LANGUAGE_ZH_CN: '网络故障',
        LANGUAGE_EN_US: 'Network Error',
    },
    'update.network_error.empty_response': {
        LANGUAGE_ZH_CN: '检查更新失败，未获得有效响应。',
        LANGUAGE_EN_US: 'Update checking failed because no valid response was received.',
    },
    'update.version_check.title': {
        LANGUAGE_ZH_CN: '版本检查',
        LANGUAGE_EN_US: 'Version Check',
    },
    'update.latest.message': {
        LANGUAGE_ZH_CN: '当前版本 {version} 已经是最新版！',
        LANGUAGE_EN_US: 'Version {version} is already the latest release.',
    },
    'update.network_error.message': {
        LANGUAGE_ZH_CN: '检查更新失败，请检查网络设置。\n错误信息: {error}',
        LANGUAGE_EN_US: 'Update checking failed. Please verify your network settings.\nError: {error}',
    },
    'update.prepare': {
        LANGUAGE_ZH_CN: '准备升级到 {version}...',
        LANGUAGE_EN_US: 'Preparing to upgrade to {version}...',
    },
    'update.connecting.official': {
        LANGUAGE_ZH_CN: '正在连接 GitHub 下载源...',
        LANGUAGE_EN_US: 'Connecting to the GitHub download source...',
    },
    'update.connecting.mirror': {
        LANGUAGE_ZH_CN: '正在连接加速下载源...',
        LANGUAGE_EN_US: 'Connecting to the accelerated download source...',
    },
    'update.retrying': {
        LANGUAGE_ZH_CN: '连接失败，正在重试...',
        LANGUAGE_EN_US: 'Connection failed. Retrying...',
    },
    'update.fallback': {
        LANGUAGE_ZH_CN: 'GitHub 下载不可用，正在切换加速源...',
        LANGUAGE_EN_US: 'GitHub download is unavailable. Switching to the accelerated source...',
    },
    'update.downloading': {
        LANGUAGE_ZH_CN: '正在下载... {percent}%',
        LANGUAGE_EN_US: 'Downloading... {percent}%',
    },
    'update.downloading_unknown': {
        LANGUAGE_ZH_CN: '正在下载...',
        LANGUAGE_EN_US: 'Downloading...',
    },
    'update.verifying': {
        LANGUAGE_ZH_CN: '下载完成，正在校验更新文件...',
        LANGUAGE_EN_US: 'Download complete. Verifying the update file...',
    },
    'update.failed': {
        LANGUAGE_ZH_CN: '更新失败：{error}',
        LANGUAGE_EN_US: 'Update failed: {error}',
    },
    'update.new_version.title': {
        LANGUAGE_ZH_CN: '发现新版本 {version}',
        LANGUAGE_EN_US: 'New Version {version} Found',
    },
    'update.release_notes': {
        LANGUAGE_ZH_CN: '发版更新记录：',
        LANGUAGE_EN_US: 'Release Notes:',
    },
    'update.install_now': {
        LANGUAGE_ZH_CN: '立即热更新',
        LANGUAGE_EN_US: 'Install Now',
    },
    'update.later': {
        LANGUAGE_ZH_CN: '稍后',
        LANGUAGE_EN_US: 'Later',
    },
    'tray.app_name': {
        LANGUAGE_ZH_CN: '鼠标电量监控',
        LANGUAGE_EN_US: 'Mouse Battery Monitor',
    },
    'tray.scanning': {
        LANGUAGE_ZH_CN: '正在扫描...',
        LANGUAGE_EN_US: 'Scanning...',
    },
    'tray.no_device_or_sleep': {
        LANGUAGE_ZH_CN: '未发现设备或已休眠',
        LANGUAGE_EN_US: 'No device found or devices are sleeping',
    },
    'tray.notification.low_battery_title': {
        LANGUAGE_ZH_CN: '设备电量告警',
        LANGUAGE_EN_US: 'Device Battery Alert',
    },
    'tray.notification.low_battery_message': {
        LANGUAGE_ZH_CN: '{name} 当前电量只有 {percent}%，请及时充电！',
        LANGUAGE_EN_US: '{name} is at only {percent}% battery. Please recharge it soon!',
    },
    'tray.menu.no_device': {
        LANGUAGE_ZH_CN: '未发现设备',
        LANGUAGE_EN_US: 'No Devices Found',
    },
    'tray.menu.refresh_now': {
        LANGUAGE_ZH_CN: '🔄 立即刷新',
        LANGUAGE_EN_US: '🔄 Refresh Now',
    },
    'tray.menu.open_settings': {
        LANGUAGE_ZH_CN: '⚙️ 打开设置',
        LANGUAGE_EN_US: '⚙️ Open Settings',
    },
    'tray.menu.quit': {
        LANGUAGE_ZH_CN: '❌ 退出',
        LANGUAGE_EN_US: '❌ Quit',
    },
    'tray.menu.charging_suffix': {
        LANGUAGE_ZH_CN: ' ⚡充电中',
        LANGUAGE_EN_US: ' ⚡Charging',
    },
}


_RUNTIME_TEXT_KEYS = {
    '未知鼠标': 'device.unknown_mouse',
    'Bolt 鼠标': 'device.bolt_mouse',
    'Unifying 鼠标': 'device.unifying_mouse',
    '未连接': 'status.disconnected',
    '已连接，读取中...': 'status.connected_reading',
    '休眠或连接中断': 'status.sleep_or_disconnected',
    '休眠中': 'status.sleeping',
    '读取超时，沿用上次有效电量': 'status.read_timeout_keep_last',
    '读取错误': 'status.read_error',
    '检测到异常帧，沿用上次有效电量': 'status.invalid_frame_keep_last',
    '充电中': 'status.charging',
    '已充满': 'status.full',
    '电量良好': 'status.good',
    '电量低': 'status.low',
    '电量极低': 'status.critical',
    '放电中': 'status.discharging',
    '未找到已绑定键盘': 'keyboard.bound_not_found',
    '读取失败': 'keyboard.read_failed',
    '已连接': 'bluetooth.status.connected',
    '已配对，未连接或休眠': 'bluetooth.state.paired_sleeping',
    '未在 Windows 已配对设备中找到': 'bluetooth.state.not_paired',
    '设备未公开标准 BLE Battery Service（0x180F）。': 'bluetooth.state.no_service',
    '设备未公开可读的 Battery Level（0x2A19）。': 'bluetooth.state.no_level',
    '尚未收到托盘进程写入的设备状态，请确认主程序正在运行。': 'shared_state.missing',
    '读取共享状态失败，当前显示上次有效结果。请稍后重试或确认托盘进程是否正常。': 'shared_state.read_failed_keep_last',
    '正在扫描键盘候选设备...': 'keyboard.scan.loading_runtime',
    '未发现可绑定的键盘候选设备': 'keyboard.scan.none_runtime',
    '绑定失败：未找到对应的键盘设备': 'keyboard.bind.failed_not_found_runtime',
    '已移除当前键盘绑定': 'keyboard.removed_runtime',
}

_RUNTIME_TEXT_PATTERNS = [
    (re.compile(r'^更新于 (?P<time>.+)$'), 'device.updated_at'),
    (re.compile(r'^已发现 (?P<count>\d+) 个设备$'), 'status.devices_found'),
    (re.compile(r'^已发现 (?P<count>\d+) 个键盘候选设备$'), 'keyboard.scan.found_runtime'),
    (re.compile(r'^已绑定键盘：(?P<name>.+)$'), 'keyboard.bound_runtime'),
    (re.compile(r'^罗技鼠标 \((?P<pid>0x[0-9A-F]+)\)$'), 'device.logitech_mouse_with_pid'),
]


def translate(key: str, language: str, **kwargs) -> str:
    """按当前语言获取文案，缺失时回退中文模板。"""
    normalized = normalize_ui_language(language, allow_auto=False)
    bucket = _TRANSLATIONS.get(key, {})
    template = bucket.get(normalized) or bucket.get(LANGUAGE_ZH_CN) or key
    return template.format(**kwargs)


def translate_brand_name(name: str, language: str) -> str:
    """翻译品牌名，避免 enum 中文值直接出现在英文界面中。"""
    brand_key = {
        '罗技': 'brand.logitech',
        '雷蛇': 'brand.razer',
    }.get(str(name or '').strip())
    if not brand_key:
        return str(name or '')
    return translate(brand_key, language)


def translate_runtime_text(text: str, language: str) -> str:
    """翻译运行时产生的原始中文状态文案。

    底层设备模块和共享状态当前仍以中文源文案输出；
    这里在展示层做一次轻量映射，避免为本次需求大范围改动底层协议模块。
    """
    raw = str(text or '')
    if not raw:
        return raw

    normalized = normalize_ui_language(language, allow_auto=False)
    if normalized == LANGUAGE_ZH_CN:
        return raw

    literal_key = _RUNTIME_TEXT_KEYS.get(raw)
    if literal_key:
        return translate(literal_key, normalized)

    brand_text = translate_brand_name(raw, normalized)
    if brand_text != raw:
        return brand_text

    for pattern, key in _RUNTIME_TEXT_PATTERNS:
        matched = pattern.match(raw)
        if matched:
            return translate(key, normalized, **matched.groupdict())

    return raw
