"""
鼠标电量监控 - GUI 界面 (Flet 0.80+)

浅色极简科技感界面：
- 轻量 Windows 工具定位，不做电竞驱动风格
- 白色卡片 + 低对比边框 + 单一绿色强调色
- 电量展示改为大数字 + 横向进度条，减少圆环仪表盘带来的视觉噪音
"""

import os
import time
import threading
import logging
from typing import Optional

import flet as ft

from devices import (
    DeviceManager,
    MouseInfo,
    Brand,
    request_device_command,
    DEVICE_COMMAND_SCAN_KEYBOARD_CANDIDATES,
    DEVICE_COMMAND_BIND_KEYBOARD,
    DEVICE_COMMAND_UNBIND_KEYBOARD,
    DEVICE_COMMAND_REFRESH_TRAY_ICON,
)
from core_bridge import KeyboardInfo, KeyboardCandidate
from config import (
    ConfigManager,
    APP_VERSION,
    TRAY_ICON_PRIORITY_MOUSE_FIRST,
    TRAY_ICON_PRIORITY_KEYBOARD_FIRST,
    TRAY_ICON_PRIORITY_LOWEST_BATTERY,
)
import updater
from i18n import (
    LANGUAGE_EN_US,
    LANGUAGE_ZH_CN,
    translate,
    translate_brand_name,
    translate_runtime_text,
)

logger = logging.getLogger(__name__)

# ============================================================
# 颜色主题 — Light Minimal 设计系统
# 目标：浅色、克制、工具感、科技感，不依赖深色和霓虹色制造氛围。
# ============================================================

COLORS = {
    # —— 背景 / 表面 ——
    'bg_app': '#F6F8FB',           # 窗口底色：柔和浅灰
    'bg_card': '#FFFFFF',          # 主卡片：纯白
    'bg_card_soft': '#F9FAFB',     # 次级卡片 / 输入背景
    'bg_card_hover': '#F3F6FA',    # 卡片悬停
    'bg_line': '#E5E7EB',          # 分割线 / 边框
    'bg_line_soft': '#EEF2F7',     # 更轻的分割线
    'bg_input': '#FFFFFF',         # 下拉框 / 输入框

    # —— 主强调色 ——
    'accent_green': '#22C55E',     # 电量 / 开关 / 主操作
    'accent_green_dark': '#16A34A',
    'accent_green_soft': '#EAFBF1',

    # —— 文本层级 ——
    'text_primary': '#111827',
    'text_secondary': '#64748B',
    'text_dim': '#94A3B8',

    # —— 电量阶梯色 ——
    'battery_full': '#22C55E',
    'battery_good': '#65A30D',
    'battery_mid': '#EAB308',
    'battery_low': '#F97316',
    'battery_critical': '#DC2626',
    'charging': '#0EA5E9',
    'offline': '#94A3B8',

    # —— 语义色 ——
    'destructive': '#DC2626',

    # —— 品牌标识色 ——
    'logitech_blue': '#2563EB',
    'razer_green': '#16A34A',
}


# 右侧控件列宽统一常量：右侧只放轻量控件，不再放宽大的下拉框。
TRAILING_WIDTH = 104


def _alpha(hex_color: str, alpha_hex: str) -> str:
    """给 6 位 HEX 颜色追加透明度，便于保持 Flet 颜色写法统一。"""
    return hex_color + alpha_hex


def get_battery_color(percentage: int, charging: bool) -> str:
    """根据电量与充电状态返回对应阶梯颜色（充电态优先返回充电色）。"""
    if charging:
        return COLORS['charging']
    if percentage >= 80:
        return COLORS['battery_full']
    elif percentage >= 60:
        return COLORS['battery_good']
    elif percentage >= 35:
        return COLORS['battery_mid']
    elif percentage >= 15:
        return COLORS['battery_low']
    return COLORS['battery_critical']


def get_brand_color(brand: Brand) -> str:
    """根据品牌返回对应的官方强调色；浅色界面里降低品牌色存在感。"""
    if brand == Brand.LOGITECH:
        return COLORS['logitech_blue']
    return COLORS['razer_green']


# ============================================================
# 通用 UI 小组件
# ============================================================


def build_icon_box(icon_name, color: str = None, size: int = 38) -> ft.Container:
    """构建统一的浅绿色图标盒，避免每个设置项都出现高饱和大图标。"""
    icon_color = color or COLORS['accent_green_dark']
    return ft.Container(
        content=ft.Icon(icon_name, size=20, color=icon_color),
        width=size,
        height=size,
        border_radius=12,
        bgcolor=_alpha(icon_color, '10'),
        border=ft.Border.all(1, _alpha(icon_color, '1F')),
        alignment=ft.Alignment.CENTER,
    )


def build_card(content, padding=None, margin=None) -> ft.Container:
    """统一白色卡片样式：轻边框、轻阴影、较大圆角。"""
    return ft.Container(
        content=content,
        bgcolor=COLORS['bg_card'],
        border_radius=20,
        border=ft.Border.all(1, COLORS['bg_line']),
        padding=padding or ft.Padding.symmetric(horizontal=24, vertical=22),
        margin=margin,
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=18,
            color='#0000000A',
            offset=ft.Offset(0, 6),
        ),
        animate=ft.Animation(180, ft.AnimationCurve.EASE_OUT),
        on_hover=lambda e: _on_card_hover(e),
    )


def build_battery_bar(percentage: int, charging: bool, width: int = 132) -> ft.Column:
    """横向电量条：电量数字在左侧区域内水平/垂直居中展示。"""
    if percentage < 0:
        pct = 0
        color = COLORS['offline']
        value_text = '--'
    else:
        pct = max(0, min(100, percentage))
        color = get_battery_color(pct, charging)
        value_text = f'{pct}%'

    return ft.Column(
        controls=[
            # 用单个 Text 展示百分比，避免数字和 % 在 Flet 布局中发生错位/丢失。
            ft.Text(
                value_text,
                size=52,
                weight=ft.FontWeight.W_700,
                color=color,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Container(
                width=width,
                content=ft.ProgressBar(
                    value=pct / 100,
                    height=8,
                    color=color,
                    bgcolor=COLORS['bg_line'],
                    border_radius=8,
                ),
            ),
        ],
        spacing=12,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
    )

def build_status_dot(color: str) -> ft.Container:
    """状态圆点：比彩色大标签更轻。"""
    return ft.Container(width=8, height=8, border_radius=4, bgcolor=color)


def build_setting_row(icon_name, title: str, subtitle: str, trailing, icon_color: str = None) -> ft.Container:
    """设置列表行：统一行高、统一左图标、右侧控件固定列宽并居中对齐。"""
    return ft.Container(
        padding=ft.Padding.symmetric(vertical=5),
        content=ft.Row(
            controls=[
                build_icon_box(icon_name, color=icon_color),
                ft.Column(
                    controls=[
                        ft.Text(title, size=15, weight=ft.FontWeight.W_600, color=COLORS['text_primary']),
                        ft.Text(subtitle, size=12, color=COLORS['text_secondary']),
                    ],
                    spacing=3,
                    expand=True,
                ),
                trailing,
            ],
            spacing=14,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def build_select_box(value: str, options: list[tuple[str, str]], on_change=None) -> ft.Container:
    """构建与设置卡片右侧列宽一致的轻量选择器。

    当前项目锁定的 Flet 版本对 [`ft.Dropdown`](gui.py:216) 支持不稳定，
    继续使用下拉框会导致初始化报错或界面异常撑开。因此这里改成
    「单框点击轮换选项」方案：
    - 宽度仍严格使用 [`TRAILING_WIDTH`](gui.py:81)
    - 不引入额外弹层，避免再触发版本兼容问题
    - 每次点击在三种优先级间循环切换
    """
    option_map = {key: label for key, label in options}
    option_keys = [key for key, _ in options]
    current_value = value if value in option_map else option_keys[0]

    label_text = ft.Text(
        option_map[current_value],
        size=12,
        weight=ft.FontWeight.W_600,
        color=COLORS['text_primary'],
        text_align=ft.TextAlign.CENTER,
        max_lines=2,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    caret_icon = ft.Icon(ft.Icons.SYNC_ALT, size=14, color=COLORS['text_secondary'])

    control = ft.Container(
        width=TRAILING_WIDTH,
        height=42,
        bgcolor=COLORS['bg_card_soft'],
        border=ft.Border.all(1, COLORS['bg_line']),
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=8, vertical=0),
        content=ft.Row(
            controls=[
                ft.Container(content=label_text, expand=True, alignment=ft.Alignment.CENTER),
                caret_icon,
            ],
            spacing=4,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        alignment=ft.Alignment.CENTER,
    )

    # 通过给容器挂载 value，保持调用方仍可像读取普通控件一样获取当前值。
    control.value = current_value

    def handle_click(e):
        current = getattr(control, 'value', option_keys[0])
        try:
            current_index = option_keys.index(current)
        except ValueError:
            current_index = 0
        next_value = option_keys[(current_index + 1) % len(option_keys)]
        control.value = next_value
        label_text.value = option_map[next_value]
        try:
            control.update()
        except Exception:
            pass
        if on_change is not None:
            on_change(next_value)

    control.on_click = handle_click
    return control


def build_trailing_box(content, width: int = TRAILING_WIDTH) -> ft.Container:
    """设置项右侧统一占位：开关和数值控件共用固定列宽，右边缘稳定。"""
    return ft.Container(
        width=width,
        content=content,
        alignment=ft.Alignment.CENTER,
    )


def build_threshold_stepper(value: int, off_label: str = '关闭', on_decrease=None, on_increase=None) -> ft.Container:
    """
    低电量阈值调节器。
    用 - / 数值 / + 代替下拉框，避免下拉框在窄列里挤压文字，视觉上也更轻。
    """
    label = off_label if value <= 0 else f'{value}%'
    return ft.Container(
        width=TRAILING_WIDTH,
        height=42,
        bgcolor=COLORS['bg_card_soft'],
        border=ft.Border.all(1, COLORS['bg_line']),
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ft.Icon(ft.Icons.REMOVE, size=14, color=COLORS['text_secondary']),
                    width=26, height=30, border_radius=8,
                    alignment=ft.Alignment.CENTER,
                    on_click=on_decrease,
                ),
                ft.Container(
                    content=ft.Text(label, size=13, weight=ft.FontWeight.W_600, color=COLORS['text_primary'], text_align=ft.TextAlign.CENTER),
                    width=38,
                    alignment=ft.Alignment.CENTER,
                    expand=True,
                ),
                ft.Container(
                    content=ft.Icon(ft.Icons.ADD, size=14, color=COLORS['text_secondary']),
                    width=26, height=30, border_radius=8,
                    alignment=ft.Alignment.CENTER,
                    on_click=on_increase,
                ),
            ],
            spacing=0,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

def build_action_button(content, primary: bool = False, on_click=None, expand: bool = True):
    """底部动作按钮：统一白底圆角矩形，避免主按钮过度抢眼或变成胶囊形。"""
    return ft.Container(
        content=content,
        expand=expand,
        height=48,
        bgcolor=COLORS['bg_card'],
        border=ft.Border.all(1, COLORS['bg_line']),
        border_radius=12,
        alignment=ft.Alignment.CENTER,
        # 按钮内部去掉偏重的左右留白，避免视觉上出现“文字整体偏左”的错觉。
        padding=ft.Padding.symmetric(horizontal=6, vertical=0),
        shadow=ft.BoxShadow(
            spread_radius=0,
            blur_radius=10,
            color='#00000008',
            offset=ft.Offset(0, 4),
        ),
        on_click=on_click,
    )

# ============================================================
# 鼠标设备卡片
# ============================================================


def build_mouse_card(mouse: MouseInfo, app_ref: "MouseBatteryApp" = None) -> ft.Container:
    """
    构建鼠标设备信息卡片（浅色极简风格）。
    - 取消大圆环和左侧品牌竖条，避免驱动面板/电竞感
    - 左侧只保留大电量数字 + 横向进度条
    - 右侧展示品牌、设备名、状态和更新时间
    """
    brand_color = get_brand_color(mouse.brand)

    # 状态点颜色：离线 / 充电 / 正常 / 危险
    if not mouse.online:
        dot_color = COLORS['offline']
    elif mouse.charging:
        dot_color = COLORS['charging']
    elif mouse.percentage >= 20:
        dot_color = COLORS['battery_full']
    else:
        dot_color = COLORS['battery_critical']

    time_str = app_ref._t('device.waiting_update') if app_ref else '等待更新'
    if mouse.last_update > 0:
        time_str = (
            app_ref._t('device.updated_at', time=time.strftime('%H:%M:%S', time.localtime(mouse.last_update)))
            if app_ref else
            f"更新于 {time.strftime('%H:%M:%S', time.localtime(mouse.last_update))}"
        )

    brand_text = app_ref._translate_brand_name(mouse.brand.value) if app_ref else mouse.brand.value
    name_text = app_ref._translate_runtime_text(mouse.name) if app_ref else mouse.name
    status_text = app_ref._translate_runtime_text(mouse.status_text) if app_ref else mouse.status_text

    brand_badge = ft.Container(
        content=ft.Text(
            brand_text,
            size=12,
            weight=ft.FontWeight.W_600,
            color=brand_color,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=5),
        border_radius=14,
        bgcolor=_alpha(brand_color, '10'),
        border=ft.Border.all(1, _alpha(brand_color, '20')),
    )

    device_info = ft.Column(
        controls=[
            brand_badge,
            ft.Text(
                name_text,
                size=22,
                weight=ft.FontWeight.W_700,
                color=COLORS['text_primary'],
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Row(
                controls=[
                    build_status_dot(dot_color),
                    ft.Text(status_text, size=14, color=COLORS['text_secondary']),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(time_str, size=13, color=COLORS['text_dim']),
        ],
        spacing=10,
        alignment=ft.MainAxisAlignment.CENTER,
        expand=True,
    )

    return build_card(
        content=ft.Row(
            controls=[
                # 当前行按 4:7 分配：电量约 36%，设备详情约 64%。
                ft.Container(
                    content=build_battery_bar(mouse.percentage, mouse.charging, width=132),
                    expand=4,
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Container(width=1, height=112, bgcolor=COLORS['bg_line_soft']),
                ft.Container(
                    content=device_info,
                    expand=7,
                    padding=ft.Padding.only(left=22),
                    alignment=ft.Alignment.CENTER_LEFT,
                ),
            ],
            spacing=18,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=26, vertical=18),
        margin=ft.Margin.only(bottom=8),
    )


def build_keyboard_card(keyboard: KeyboardInfo, on_remove=None, app_ref: "MouseBatteryApp" = None) -> ft.Container:
    """构建键盘设备信息卡片，沿用鼠标卡片版式保持界面一致性。"""
    if not keyboard.online:
        dot_color = COLORS['offline']
    elif keyboard.charging:
        dot_color = COLORS['charging']
    elif keyboard.percentage >= 20:
        dot_color = COLORS['battery_full']
    else:
        dot_color = COLORS['battery_critical']

    time_str = app_ref._t('device.waiting_update') if app_ref else '等待更新'
    if keyboard.last_update > 0:
        time_str = (
            app_ref._t('device.updated_at', time=time.strftime('%H:%M:%S', time.localtime(keyboard.last_update)))
            if app_ref else
            f"更新于 {time.strftime('%H:%M:%S', time.localtime(keyboard.last_update))}"
        )

    brand_text = app_ref._translate_brand_name(keyboard.brand) if app_ref else keyboard.brand
    name_text = app_ref._translate_runtime_text(keyboard.name) if app_ref else keyboard.name
    status_text = app_ref._translate_runtime_text(keyboard.status_text) if app_ref else keyboard.status_text

    brand_color = COLORS['accent_green_dark']
    brand_badge = ft.Container(
        content=ft.Text(
            brand_text,
            size=12,
            weight=ft.FontWeight.W_600,
            color=brand_color,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=5),
        border_radius=14,
        bgcolor=_alpha(brand_color, '10'),
        border=ft.Border.all(1, _alpha(brand_color, '20')),
    )

    device_info = ft.Column(
        controls=[
            brand_badge,
            ft.Text(
                name_text,
                size=22,
                weight=ft.FontWeight.W_700,
                color=COLORS['text_primary'],
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Row(
                controls=[
                    build_status_dot(dot_color),
                    ft.Text(status_text, size=14, color=COLORS['text_secondary']),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(time_str, size=13, color=COLORS['text_dim']),
        ],
        spacing=10,
        alignment=ft.MainAxisAlignment.CENTER,
        expand=True,
    )

    remove_button = ft.Container(
        content=ft.Icon(ft.Icons.CLOSE, size=16, color=COLORS['text_dim']),
        width=28,
        height=28,
        border_radius=14,
        alignment=ft.Alignment.CENTER,
        bgcolor=COLORS['bg_card_soft'],
        border=ft.Border.all(1, COLORS['bg_line']),
        on_click=on_remove,
    )

    return build_card(
        content=ft.Column(
            controls=[
                ft.Row(
                    controls=[ft.Container(expand=True), remove_button],
                    spacing=0,
                    alignment=ft.MainAxisAlignment.END,
                ),
                ft.Row(
                    controls=[
                        ft.Container(
                            content=build_battery_bar(keyboard.percentage, keyboard.charging, width=132),
                            expand=4,
                            alignment=ft.Alignment.CENTER,
                        ),
                        ft.Container(width=1, height=112, bgcolor=COLORS['bg_line_soft']),
                        ft.Container(
                            content=device_info,
                            expand=7,
                            padding=ft.Padding.only(left=22),
                            alignment=ft.Alignment.CENTER_LEFT,
                        ),
                    ],
                    spacing=18,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=6,
        ),
        padding=ft.Padding.symmetric(horizontal=26, vertical=18),
        margin=ft.Margin.only(bottom=8),
    )


def _on_card_hover(e: ft.ControlEvent):
    """卡片悬停效果：浅色界面只做非常轻的背景变化，不抢主要信息。"""
    container = e.control
    if e.data == 'true':
        container.bgcolor = COLORS['bg_card_hover']
    else:
        container.bgcolor = COLORS['bg_card']
    try:
        container.update()
    except Exception:
        pass


# ============================================================
# 空状态
# ============================================================


def build_empty_state(title: str = '未发现鼠标设备',
                      message: str = '请确保鼠标已开机且无线接收器已插入\n如 G Hub / Synapse 正在运行，请先退出\n可能需要以管理员身份运行本程序',
                      icon_name=ft.Icons.MOUSE_OUTLINED) -> ft.Container:
    """构建空状态 / 加载态 / 错误态占位。

    统一用同一套占位卡片承载「无设备」「加载中」「读取失败」三类状态，
    避免不同分支各自拼文案导致界面反馈风格不一致。
    """
    icon_badge = ft.Container(
        content=ft.Icon(icon_name, size=42, color=COLORS['text_secondary']),
        width=84,
        height=84,
        border_radius=24,
        bgcolor=COLORS['bg_card_soft'],
        border=ft.Border.all(1, COLORS['bg_line']),
        alignment=ft.Alignment.CENTER,
    )
    return build_card(
        content=ft.Column(
            controls=[
                icon_badge,
                ft.Text(
                    title,
                    size=20,
                    weight=ft.FontWeight.W_700,
                    color=COLORS['text_primary'],
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    message,
                    size=13,
                    color=COLORS['text_secondary'],
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
        ),
        padding=ft.Padding.symmetric(vertical=46, horizontal=30),
        margin=ft.Margin.only(bottom=8),
    )


# ============================================================
# 主应用
# ============================================================


class MouseBatteryApp:
    """鼠标电量监控主应用。"""

    # 低电量提醒允许的档位：关闭(0)、10%、20%、30%。
    # -/+ 步进与 _set_notify_threshold 校验共用此常量，避免多处硬编码不一致。
    _NOTIFY_ALLOWED_VALUES = (0, 10, 20, 30)
    # GUI 只读取共享状态文件，不直接访问 HID；3 秒轮询能保持状态同步及时，
    # 同时比硬件轮询轻量很多，适合作为设置窗口的默认刷新周期。
    _GUI_STATE_REFRESH_INTERVAL = 3

    def __init__(self, device_manager: DeviceManager):
        self.device_manager = device_manager
        self.config_manager = ConfigManager()
        self.device_manager.set_on_update(self._on_device_update)
        self.page: Optional[ft.Page] = None
        self.card_list: Optional[ft.Column] = None
        self.scan_btn: Optional[ft.Container] = None
        self.scan_btn_row: Optional[ft.Row] = None
        self.refresh_btn: Optional[ft.Container] = None
        self.refresh_btn_row: Optional[ft.Row] = None
        self.check_update_btn: Optional[ft.Container] = None
        self.check_update_btn_row: Optional[ft.Row] = None
        self.add_keyboard_btn: Optional[ft.Container] = None
        self.add_keyboard_btn_row: Optional[ft.Row] = None
        self.notify_threshold_control: Optional[ft.Container] = None
        self.tray_icon_priority_control: Optional[ft.Container] = None
        self.status_text: Optional[ft.Text] = None
        self.auto_switch: Optional[ft.Switch] = None
        self._keyboard_dialog: Optional[ft.AlertDialog] = None
        self._keyboard_bind_action: Optional[ft.TextButton] = None
        self._keyboard_selected_device_id = ''
        self._keyboard_dialog_loading = False
        # 动作按钮忙碌标记：Container.disabled 在 Flet 中无法拦截 on_click，
        # 这里用显式锁替代，避免扫描/刷新/检查更新在执行中被重复点击触发并发。
        self._scan_busy = False
        self._refresh_busy = False
        self._check_update_busy = False
        # 禁用态下文字/图标统一用次级灰色，保证视觉与逻辑一致
        self._disabled_color = COLORS['text_dim']
        # 视图状态统一管理空态 / 加载态 / 错误态，避免多个入口各自拼接 UI 文案。
        self._view_state = 'idle'
        self._view_message = ''
        # 用渲染签名避免每次收到回调都重建整组卡片，减少 Flet 控件树抖动。
        self._last_render_signature = None

    def _effective_language(self) -> str:
        """返回当前 GUI 应使用的实际语言。"""
        return self.config_manager.effective_ui_language

    def _t(self, key: str, **kwargs) -> str:
        """按当前语言获取 GUI 静态文案。"""
        return translate(key, self._effective_language(), **kwargs)

    def _translate_runtime_text(self, text: str) -> str:
        """翻译运行时状态文案，避免底层中文原文直接暴露到英文界面。"""
        return translate_runtime_text(text, self._effective_language())

    def _translate_brand_name(self, name: str) -> str:
        """翻译品牌名，保证英文界面不直接展示中文品牌值。"""
        return translate_brand_name(name, self._effective_language())

    def _request_tray_refresh(self):
        """通知 tray 进程立即按最新配置刷新图标与菜单。"""
        try:
            request_device_command(DEVICE_COMMAND_REFRESH_TRAY_ICON)
        except Exception as ex:
            logger.error(f'提交托盘刷新命令失败: {ex}')

    def _rebuild_page(self):
        """语言切换后重建页面静态结构，确保所有文案立即生效。"""
        if not self.page:
            return

        # 自动刷新是当前 GUI 会话级状态，不做持久化；语言切换重建页面时需要原样保留。
        auto_refresh_enabled = bool(self.auto_switch.value) if self.auto_switch else True

        self.page.controls.clear()
        self.card_list = None
        self.scan_btn = None
        self.scan_btn_row = None
        self.refresh_btn = None
        self.refresh_btn_row = None
        self.check_update_btn = None
        self.check_update_btn_row = None
        self.add_keyboard_btn = None
        self.add_keyboard_btn_row = None
        self.notify_threshold_control = None
        self.tray_icon_priority_control = None
        self.status_text = None
        self.auto_switch = None
        self._keyboard_dialog = None
        self._keyboard_bind_action = None
        self._keyboard_selected_device_id = ''
        self._last_render_signature = None
        self.build(self.page, initial_scan=False, auto_refresh_enabled=auto_refresh_enabled)

    def _on_autoupdate_toggle(self, e):
        self.config_manager.auto_update = e.control.value

    def _on_language_toggle(self, e):
        """切换中英界面，并持久化为显式语言偏好。"""
        current = self._effective_language()
        next_language = LANGUAGE_EN_US if current == LANGUAGE_ZH_CN else LANGUAGE_ZH_CN
        self.config_manager.ui_language = next_language
        logger.info(f'界面语言切换为: {next_language}')
        self._request_tray_refresh()
        self._rebuild_page()

    def _show_dialog(self, title: str, message: str, actions: list = None):
        """统一的对话框构建与弹出，减少重复代码。返回对话框对象供外部控制。"""
        def close_dlg(e):
            dlg.open = False
            self._safe_update()

        if actions is None:
            actions = [ft.TextButton(self._t('dialog.ok'), on_click=close_dlg)]

        dlg = ft.AlertDialog(
            title=ft.Text(title, color=COLORS['text_primary']),
            content=ft.Text(message, size=13, selectable=True, color=COLORS['text_secondary']),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=14),
        )
        self.page.show_dialog(dlg)
        return dlg

    def _set_btn_disabled_visual(self, btn_row: Optional[ft.Row], disabled: bool, icon_default, label_default: str):
        """统一处理按钮视觉禁用状态。

        Container 没有 disabled 属性可拦截点击，因此用「置灰文字图标 + 忙碌锁」组合：
        - disabled=True：图标文字改为次级灰，提示当前不可点击
        - disabled=False：恢复为默认图标与标签、正常文字色
        同时保持 _update_btn_content 兼容忙碌中的提示文案替换。
        """
        if not btn_row:
            return
        if disabled:
            color = self._disabled_color
            btn_row.controls[0] = ft.Icon(icon_default, size=18, color=color)
            btn_row.controls[1] = ft.Text(label_default, size=14, weight=ft.FontWeight.W_500, color=color)
        else:
            color = COLORS['text_primary']
            btn_row.controls[0] = ft.Icon(icon_default, size=18, color=color)
            btn_row.controls[1] = ft.Text(label_default, size=14, weight=ft.FontWeight.W_500, color=color)

    def _set_view_state(self, state: str, message: str = ''):
        """记录当前界面状态。

        状态驱动顶部列表区占位内容：
        - loading：首次进入或手动同步中
        - empty：已完成读取但没有设备
        - error：读取共享状态失败
        - ready：有设备数据
        """
        self._view_state = state
        self._view_message = message

    def _shared_state_read_status(self) -> tuple[str, str]:
        """读取共享状态层的最近一次同步结果。

        GUI 只在设置窗口模式下依赖共享状态文件；这里通过弱依赖读取只读属性，
        避免把 [`MouseBatteryApp`](gui.py:425) 和具体实现强耦合。
        """
        state = getattr(self.device_manager, 'last_read_state', 'ok')
        message = getattr(self.device_manager, 'last_read_error', '')
        return state, self._translate_runtime_text(message)

    def _keyboard_snapshot(self) -> Optional[KeyboardInfo]:
        """弱依赖读取当前共享状态里的键盘快照。"""
        return getattr(self.device_manager, 'keyboard', None)

    def _keyboard_candidates_snapshot(self) -> list[KeyboardCandidate]:
        """弱依赖读取共享状态里的键盘候选列表。"""
        return list(getattr(self.device_manager, 'keyboard_candidates', []) or [])

    def _keyboard_scan_state(self) -> tuple[str, str]:
        """读取键盘候选枚举状态，驱动弹窗加载/错误/列表展示。"""
        return (
            getattr(self.device_manager, 'keyboard_scan_state', 'idle'),
            self._translate_runtime_text(getattr(self.device_manager, 'keyboard_scan_message', '')),
        )

    def _device_signature(self, mice: list[MouseInfo]):
        """生成当前设备列表的轻量签名，用于判断是否需要整列表重建。"""
        return tuple(
            (
                mouse.name,
                mouse.brand.value,
                mouse.percentage,
                mouse.charging,
                mouse.status_text,
                mouse.online,
                round(mouse.last_update, 2),
            )
            for mouse in mice
        )

    @staticmethod
    def _keyboard_signature(keyboard: Optional[KeyboardInfo]):
        """生成键盘快照签名，避免键盘状态变化时界面不刷新。"""
        if keyboard is None:
            return None
        return (
            keyboard.name,
            keyboard.percentage,
            keyboard.charging,
            keyboard.status_text,
            keyboard.online,
            round(keyboard.last_update, 2),
            keyboard.device_id,
        )

    def _build_device_view_controls(self, mice: list[MouseInfo], keyboard: Optional[KeyboardInfo]):
        """根据当前界面状态构建设备列表区域控件。"""
        if mice or keyboard:
            controls = [build_mouse_card(mouse, app_ref=self) for mouse in mice]
            if keyboard is not None:
                controls.append(build_keyboard_card(keyboard, on_remove=self._on_remove_keyboard_click, app_ref=self))
            return controls

        if self._view_state == 'loading':
            return [build_empty_state(
                title=self._t('view.loading.title'),
                message=self._view_message or self._t('view.loading.message'),
                icon_name=ft.Icons.SYNC,
            )]

        if self._view_state == 'error':
            return [build_empty_state(
                title=self._t('view.error.title'),
                message=self._view_message or self._t('view.error.message'),
                icon_name=ft.Icons.ERROR_OUTLINE,
            )]

        if self._view_state == 'empty' and self._view_message:
            return [build_empty_state(
                title=self._t('view.not_synced.title'),
                message=self._view_message,
                icon_name=ft.Icons.SYNC,
            )]

        return [build_empty_state(
            title=self._t('view.empty.default_title'),
            message=self._t('view.empty.default_message'),
        )]

    def _sync_action_buttons(self):
        """统一同步扫描/刷新按钮的禁用态与文案，避免不同分支各自恢复状态。"""
        scan_disabled = self._scan_busy or self._refresh_busy
        refresh_disabled = self._scan_busy or self._refresh_busy

        if self.scan_btn:
            self.scan_btn.disabled = scan_disabled
        if self.refresh_btn:
            self.refresh_btn.disabled = refresh_disabled

        if self._scan_busy:
            self._update_btn_content(self.scan_btn_row, ft.Icons.HOURGLASS_TOP, self._t('action.scan_loading'))
        else:
            self._set_btn_disabled_visual(self.scan_btn_row, scan_disabled, ft.Icons.SEARCH, self._t('action.scan'))

        if self._refresh_busy:
            self._update_btn_content(self.refresh_btn_row, ft.Icons.HOURGLASS_TOP, self._t('action.refresh_loading'))
        else:
            self._set_btn_disabled_visual(self.refresh_btn_row, refresh_disabled, ft.Icons.REFRESH, self._t('action.refresh'))

    def _status_bar_message(self, mice: list[MouseInfo], keyboard: Optional[KeyboardInfo]) -> str:
        """根据当前视图状态生成底部状态栏文案。"""
        if self._view_state == 'loading':
            return self._view_message or self._t('status.syncing')
        if self._view_state == 'error':
            return self._view_message or self._t('status.read_failed')
        if self._view_state == 'ready' and self._view_message:
            return self._view_message
        total = len(mice) + (1 if keyboard else 0)
        return self._t('status.devices_found', count=total) if total else self._t('status.no_devices')

    def _on_tray_icon_priority_change(self, value: str):
        """保存托盘图标显示逻辑，立即持久化给 tray 进程读取。"""
        self.config_manager.tray_icon_priority = value
        # 仅保存配置还不够：tray 进程只有在收到一次更新回调后才会重算图标。
        # 这里显式发出“刷新托盘图标”命令，让用户切换后立即看到图标变化。
        self._request_tray_refresh()

    def _on_keyboard_candidate_change(self, e):
        """记录当前弹窗里用户选中的键盘候选项。"""
        self._keyboard_selected_device_id = e.control.value or ''

    def _on_remove_keyboard_click(self, e):
        """点击键盘卡片右上角 X 后，解除当前键盘绑定。"""
        dialog_holder = {'dialog': None}

        def close_confirm(evt):
            dialog = dialog_holder['dialog']
            if dialog:
                dialog.open = False
            self._safe_update()

        def confirm_remove(evt):
            try:
                request_device_command(DEVICE_COMMAND_UNBIND_KEYBOARD)
            except Exception as ex:
                logger.error(f'提交解除键盘绑定命令失败: {ex}')
                self._show_dialog(
                    self._t('keyboard.remove.failed.title'),
                    self._t('keyboard.remove.failed.message', error=ex),
                )
                return
            close_confirm(evt)

        dialog_holder['dialog'] = self._show_dialog(
            self._t('keyboard.remove.title'),
            self._t('keyboard.remove.message'),
            actions=[
                ft.TextButton(self._t('dialog.remove'), on_click=confirm_remove),
                ft.TextButton(self._t('dialog.cancel'), on_click=close_confirm),
            ],
        )

    def _close_keyboard_dialog(self, e=None):
        """关闭键盘选择弹窗，并清理本轮交互状态。"""
        if self._keyboard_dialog:
            self._keyboard_dialog.open = False
        self._keyboard_bind_action = None
        self._keyboard_dialog_loading = False
        self._safe_update()

    def _build_keyboard_dialog_content(self):
        """根据共享状态动态构建键盘选择弹窗内容。"""
        scan_state, scan_message = self._keyboard_scan_state()
        candidates = self._keyboard_candidates_snapshot()
        keyboard = self._keyboard_snapshot()

        if not self._keyboard_selected_device_id:
            if keyboard and keyboard.device_id:
                self._keyboard_selected_device_id = keyboard.device_id
            elif candidates:
                self._keyboard_selected_device_id = candidates[0].device_id

        if self._keyboard_dialog_loading or scan_state == 'loading':
            return ft.Column(
                controls=[
                    ft.ProgressRing(width=26, height=26, color=COLORS['accent_green']),
                    ft.Text(scan_message or self._t('keyboard.dialog.loading'), size=13, color=COLORS['text_secondary']),
                ],
                tight=True,
                spacing=14,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            )

        if not candidates:
            return ft.Column(
                controls=[
                    ft.Text(self._t('keyboard.dialog.empty_title'), size=15, weight=ft.FontWeight.W_600, color=COLORS['text_primary']),
                    ft.Text(
                        scan_message or self._t('keyboard.dialog.empty_message'),
                        size=13,
                        color=COLORS['text_secondary'],
                    ),
                ],
                tight=True,
                spacing=10,
            )

        group = ft.RadioGroup(
            value=self._keyboard_selected_device_id,
            on_change=self._on_keyboard_candidate_change,
            content=ft.Column(
                controls=[
                    ft.Radio(
                        value=candidate.device_id,
                        label=(
                            self._t('keyboard.dialog.current_bound_option', name=candidate.display_name)
                            if keyboard and keyboard.device_id == candidate.device_id
                            else candidate.display_name
                        ),
                    )
                    for candidate in candidates
                ],
                tight=True,
                spacing=10,
            ),
        )

        return ft.Column(
            controls=[
                ft.Text(
                    self._t('keyboard.dialog.helper'),
                    size=13,
                    color=COLORS['text_secondary'],
                ),
                group,
            ],
            tight=True,
            spacing=12,
        )

    def _refresh_keyboard_dialog(self):
        """在共享状态变化后刷新已打开的键盘选择弹窗。"""
        if not self._keyboard_dialog or not self._keyboard_dialog.open:
            return
        candidates = self._keyboard_candidates_snapshot()
        scan_state, _ = self._keyboard_scan_state()
        if scan_state != 'loading':
            self._keyboard_dialog_loading = False
        self._keyboard_dialog.content = self._build_keyboard_dialog_content()
        if self._keyboard_bind_action:
            self._keyboard_bind_action.disabled = self._keyboard_dialog_loading or scan_state == 'loading' or not candidates

    def _on_bind_keyboard_click(self, e):
        """提交键盘绑定请求，由 tray 进程保存配置并刷新键盘电量。"""
        device_id = self._keyboard_selected_device_id.strip()
        if not device_id:
            self._show_dialog(self._t('keyboard.select_required.title'), self._t('keyboard.select_required.message'))
            return

        try:
            request_device_command(DEVICE_COMMAND_BIND_KEYBOARD, {'device_id': device_id})
        except Exception as ex:
            logger.error(f'提交键盘绑定命令失败: {ex}')
            self._show_dialog(self._t('keyboard.bind.failed.title'), self._t('keyboard.bind.failed.message', error=ex))
            return

        self._close_keyboard_dialog()

    def _open_keyboard_picker_dialog(self):
        """打开键盘选择弹窗，并等待 tray 进程回填候选列表。"""
        self._keyboard_bind_action = ft.TextButton(self._t('dialog.connect'), on_click=self._on_bind_keyboard_click)
        dialog = ft.AlertDialog(
            title=ft.Text(self._t('keyboard.select.title'), color=COLORS['text_primary']),
            content=self._build_keyboard_dialog_content(),
            actions=[
                self._keyboard_bind_action,
                ft.TextButton(self._t('dialog.cancel'), on_click=self._close_keyboard_dialog),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=14),
        )
        self._keyboard_dialog = dialog
        self._refresh_keyboard_dialog()
        self.page.show_dialog(dialog)

    def _on_add_keyboard_click(self, e):
        """请求 tray 进程枚举键盘候选接口，并弹出选择对话框。"""
        self._keyboard_dialog_loading = True
        try:
            request_device_command(DEVICE_COMMAND_SCAN_KEYBOARD_CANDIDATES)
        except Exception as ex:
            self._keyboard_dialog_loading = False
            logger.error(f'提交键盘扫描命令失败: {ex}')
            self._show_dialog(self._t('keyboard.add.failed.title'), self._t('keyboard.add.failed.message', error=ex))
            return
        self._keyboard_selected_device_id = ''
        self._open_keyboard_picker_dialog()

    def _on_check_update_click(self, e):
        """检查版本更新。

        通过显式忙碌锁 _check_update_busy 阻止重复点击（Container.disabled 在 Flet 中
        无法拦截 on_click），并在 watchdog 外层加 try/except，保证即便后台线程异常
        也能恢复按钮状态，避免按钮永久卡在「检查中...」。
        """
        if self._check_update_busy:
            return
        self._check_update_busy = True

        btn = e.control
        original_content = getattr(btn, 'content', None)
        btn.content = self._make_btn_content(ft.Icons.HOURGLASS_TOP, self._t('action.check_update_loading'), color=COLORS['text_primary'])
        self._safe_update()

        done_event = threading.Event()
        result_holder = [None]  # (has_update, latest, url, body)

        def check():
            try:
                result_holder[0] = updater.check_for_update(APP_VERSION)
            except Exception as ex:
                # 兜底：网络异常由 check_for_update 内部捕获，这里防御未预期异常
                logger.error(f'check_for_update 抛出异常: {ex}')
                result_holder[0] = (False, '', '', str(ex))
            finally:
                done_event.set()

        def watchdog():
            try:
                threading.Thread(target=check, daemon=True).start()
                finished = done_event.wait(timeout=10)

                btn.content = original_content or self._make_btn_content(ft.Icons.DOWNLOAD_OUTLINED, self._t('action.check_update'), color=COLORS['text_primary'])
                self._safe_update()

                if not finished:
                    self._safe_show_helper(lambda: self._show_dialog(
                        self._t('update.timeout.title'), self._t('update.timeout.message')
                    ))
                    return

                if result_holder[0] is None:
                    # 防御：结果未填充，视作网络故障
                    self._safe_show_helper(lambda: self._show_dialog(
                        self._t('update.network_error.title'), self._t('update.network_error.empty_response')
                    ))
                    return

                has_update, latest, url, body = result_holder[0]
                if has_update:
                    self._safe_show_helper(lambda: self._show_update_dialog(latest, url, body))
                else:
                    if latest:
                        msg = self._t('update.latest.message', version=APP_VERSION)
                        title = self._t('update.version_check.title')
                    else:
                        msg = self._t('update.network_error.message', error=body)
                        title = self._t('update.network_error.title')
                    self._safe_show_helper(lambda: self._show_dialog(title, msg))
            except Exception as ex:
                logger.error(f'检查更新 watchdog 异常: {ex}')
                # 兜底恢复：任何异常都要让按钮回到可点击状态
                try:
                    btn.content = original_content or self._make_btn_content(ft.Icons.DOWNLOAD_OUTLINED, self._t('action.check_update'), color=COLORS['text_primary'])
                    self._safe_update()
                except Exception:
                    pass
            finally:
                self._check_update_busy = False

        threading.Thread(target=watchdog, daemon=True).start()

    def _safe_show_helper(self, builder):
        """跨线程安全地执行 UI 构建并刷新页面。"""
        if not self.page:
            return
        try:
            builder()
            self.page.update()
        except Exception as e:
            logger.error(f'UI 弹窗失败: {e}')

    def _show_update_dialog(self, version: str, url: str, body: str):
        pb = ft.ProgressBar(width=400, color=COLORS['accent_green'], bgcolor=COLORS['bg_line'], value=0)
        status_txt = ft.Text(self._t('update.prepare', version=version), color=COLORS['text_secondary'], size=12)

        def do_update(e):
            dialog.actions[0].disabled = True
            dialog.actions[1].disabled = True
            self._safe_update()

            last_pct = [-1]

            def progress(pct, dl, total):
                if pct != last_pct[0]:
                    last_pct[0] = pct
                    pb.value = pct / 100.0
                    status_txt.value = self._t('update.downloading', percent=pct)
                    self._safe_update()

            def worker():
                host_pid = None
                host_pid_env = os.environ.get('MOUSE_BATTERY_HOST_PID', '').strip()
                if host_pid_env.isdigit():
                    host_pid = int(host_pid_env)

                success = updater.download_and_install(url, progress, host_pid=host_pid)
                if not success:
                    status_txt.value = self._t('update.failed_or_debug')
                    dialog.actions[1].disabled = False
                    self._safe_update()

            threading.Thread(target=worker, daemon=True).start()

        def close_dialog(e):
            dialog.open = False
            self._safe_update()

        dialog = ft.AlertDialog(
            title=ft.Text(self._t('update.new_version.title', version=version), color=COLORS['text_primary']),
            content=ft.Column([
                ft.Text(self._t('update.release_notes'), size=13, color=COLORS['text_primary']),
                ft.Container(
                    content=ft.Text(body, size=12, color=COLORS['text_secondary'], selectable=True),
                    height=100,
                ),
                ft.Container(height=5),
                status_txt,
                pb,
            ], tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton(self._t('update.install_now'), on_click=do_update),
                ft.TextButton(self._t('update.later'), on_click=close_dialog),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=14),
        )
        self.page.show_dialog(dialog)
        return dialog

    def _make_btn_content(self, icon_name, label: str, color: str = None) -> ft.Row:
        """创建按钮内部内容（icon + text）。"""
        text_color = color or COLORS['text_primary']
        return ft.Row(
            controls=[
                ft.Icon(icon_name, size=18, color=text_color),
                ft.Text(label, size=14, weight=ft.FontWeight.W_500, color=text_color),
            ],
            spacing=8,
            tight=True,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def build(self, page: ft.Page, initial_scan: bool = True, auto_refresh_enabled: bool = True):
        """
        构建主界面（浅色极简风格）。
        从上到下：头部 → 电量卡片 → 设置卡片 → 操作按钮 → 设备状态栏。
        """
        self.page = page

        # —— 窗口配置 ——
        page.title = self._t('app.window_title')
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.ico')
        if os.path.exists(ico_path):
            page.window.icon = ico_path
        page.window.width = 520
        # 增加默认高度，并配合滚动容器，避免用户首次打开时看不到底部区域。
        page.window.height = 860
        page.window.min_width = 460
        page.window.min_height = 780
        page.bgcolor = COLORS['bg_app']
        page.padding = 0
        page.theme_mode = ft.ThemeMode.LIGHT
        page.theme = ft.Theme(
            font_family='Segoe UI',
            color_scheme=ft.ColorScheme(
                primary=COLORS['accent_green'],
                on_primary='#FFFFFF',
                secondary=COLORS['accent_green_dark'],
                on_secondary='#FFFFFF',
                surface=COLORS['bg_card'],
                on_surface=COLORS['text_primary'],
                outline=COLORS['bg_line'],
                error=COLORS['destructive'],
            ),
        )

        # —— 顶部标题区 ——
        header_icon = ft.Container(
            content=ft.Icon(ft.Icons.BATTERY_CHARGING_FULL, color='#FFFFFF', size=24),
            width=48,
            height=48,
            border_radius=14,
            bgcolor=COLORS['accent_green'],
            alignment=ft.Alignment.CENTER,
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=20,
                color=_alpha(COLORS['accent_green'], '33'),
                offset=ft.Offset(0, 8),
            ),
        )
        header = ft.Container(
            content=ft.Row(
                controls=[
                    header_icon,
                    ft.Column(
                        controls=[
                            ft.Text(self._t('app.header_title'), size=24, weight=ft.FontWeight.W_700, color=COLORS['text_primary']),
                            ft.Text(self._t('app.header_subtitle'), size=14, color=COLORS['text_secondary']),
                        ],
                        spacing=6,
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.only(left=28, right=28, top=20, bottom=12),
        )

        # 设备卡片列表。通常只有一个设备，但保留多设备扩展能力。
        self.card_list = ft.Column(
            controls=[],
            spacing=0,
        )

        # 状态文本（底部状态栏左侧的设备计数）
        self.status_text = ft.Text('', size=13, color=COLORS['text_secondary'])

        # 扫描按钮 — 主操作
        self.scan_btn_row = self._make_btn_content(ft.Icons.SEARCH, self._t('action.scan'), color=COLORS['text_primary'])
        self.scan_btn = build_action_button(self.scan_btn_row, primary=False, on_click=self._on_scan_click)

        # 刷新按钮 — 次操作
        self.refresh_btn_row = self._make_btn_content(ft.Icons.REFRESH, self._t('action.refresh'), color=COLORS['text_primary'])
        self.refresh_btn = build_action_button(self.refresh_btn_row, primary=False, on_click=self._on_refresh_click)

        # 检查更新按钮 — 次操作
        self.check_update_btn_row = self._make_btn_content(ft.Icons.DOWNLOAD_OUTLINED, self._t('action.check_update'), color=COLORS['text_primary'])
        self.check_update_btn = build_action_button(self.check_update_btn_row, primary=False, on_click=self._on_check_update_click)

        # 新增键盘按钮：仅负责触发 tray 侧的候选枚举和绑定流程。
        self.add_keyboard_btn_row = self._make_btn_content(ft.Icons.KEYBOARD_OUTLINED, self._t('action.add_keyboard'), color=COLORS['text_primary'])
        self.add_keyboard_btn = build_action_button(self.add_keyboard_btn_row, primary=False, on_click=self._on_add_keyboard_click)

        # 自动刷新开关：会话级开关，默认开启。
        # 故意不持久化：与「开机自启」「自动检查更新」不同，此项控制的是当前 GUI 会话内
        # 是否周期刷新电量，重启后恢复默认开启更符合「插上鼠标就想看电量」的预期。
        # 切换会即时 start_auto_refresh(60)/stop_auto_refresh()，首次启动由 _start_scan 兜底开启。
        self.auto_switch = ft.Switch(
            value=auto_refresh_enabled,
            active_color=COLORS['accent_green'],
            on_change=self._on_auto_toggle,
        )

        # 语言切换按钮固定放在设置卡片标题右侧。
        # 业务目的：不额外占用设置项行，让用户在进入设置后即可一眼发现语言入口。
        language_toggle = ft.Container(
            content=ft.Icon(ft.Icons.LANGUAGE, color=COLORS['text_secondary'], size=18),
            width=36,
            height=36,
            border_radius=10,
            bgcolor=COLORS['bg_card_soft'],
            border=ft.Border.all(1, COLORS['bg_line']),
            alignment=ft.Alignment.CENTER,
            on_click=self._on_language_toggle,
        )

        # ========= 设置面板 =========
        settings_title = ft.Row(
            controls=[
                ft.Icon(ft.Icons.SETTINGS_OUTLINED, color=COLORS['text_secondary'], size=24),
                ft.Text(self._t('settings.title'), size=20, weight=ft.FontWeight.W_700, color=COLORS['text_primary']),
                ft.Container(expand=True),
                language_toggle,
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        autostart_switch = ft.Switch(
            value=self.config_manager.check_autostart(),
            active_color=COLORS['accent_green'],
            on_change=self._on_autostart_toggle,
        )

        self.notify_threshold_control = build_threshold_stepper(
            self.config_manager.low_battery_notify,
            off_label=self._t('settings.off'),
            on_decrease=self._on_notify_decrease,
            on_increase=self._on_notify_increase,
        )

        auto_update_switch = ft.Switch(
            value=self.config_manager.auto_update,
            active_color=COLORS['accent_green'],
            on_change=self._on_autoupdate_toggle,
        )

        self.tray_icon_priority_control = build_select_box(
            self.config_manager.tray_icon_priority,
            [
                (TRAY_ICON_PRIORITY_MOUSE_FIRST, self._t('settings.tray_priority.mouse_first')),
                (TRAY_ICON_PRIORITY_KEYBOARD_FIRST, self._t('settings.tray_priority.keyboard_first')),
                (TRAY_ICON_PRIORITY_LOWEST_BATTERY, self._t('settings.tray_priority.lowest_battery')),
            ],
            on_change=self._on_tray_icon_priority_change,
        )

        settings_card = build_card(
            content=ft.Column(
                controls=[
                    settings_title,
                    ft.Container(height=1, bgcolor=COLORS['bg_line'], margin=ft.Margin.only(top=6, bottom=4)),
                    build_setting_row(
                        ft.Icons.POWER_SETTINGS_NEW,
                        self._t('settings.autostart.title'),
                        self._t('settings.autostart.subtitle'),
                        build_trailing_box(autostart_switch),
                    ),
                    build_setting_row(
                        ft.Icons.SYNC,
                        self._t('settings.auto_update.title', version=APP_VERSION),
                        self._t('settings.auto_update.subtitle'),
                        build_trailing_box(auto_update_switch),
                    ),
                    build_setting_row(
                        ft.Icons.NOTIFICATIONS_NONE_OUTLINED,
                        self._t('settings.low_battery.title'),
                        self._t('settings.low_battery.subtitle'),
                        build_trailing_box(self.notify_threshold_control),
                    ),
                    build_setting_row(
                        ft.Icons.MONITOR_OUTLINED,
                        self._t('settings.tray_priority.title'),
                        self._t('settings.tray_priority.subtitle'),
                        build_trailing_box(self.tray_icon_priority_control),
                    ),
                ],
                spacing=6,
            ),
            padding=ft.Padding.symmetric(horizontal=24, vertical=16),
            margin=ft.Margin.only(bottom=10),
        )

        # 操作按钮区：不再做厚重固定底栏，改成轻按钮组。
        action_row = ft.Row(
            controls=[
                self.scan_btn,
                self.refresh_btn,
                self.add_keyboard_btn,
                self.check_update_btn,
            ],
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # 底部设备状态条。
        status_bar = ft.Container(
            content=ft.Row(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.MOUSE_OUTLINED, size=18, color=COLORS['text_secondary']),
                            self.status_text,
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(expand=True),
                    # 自动刷新文字和开关组成紧凑组，避免右侧空白过大。
                    ft.Row(
                        controls=[
                            ft.Text(self._t('status.auto_refresh'), size=13, color=COLORS['text_secondary']),
                            ft.Container(width=70, content=self.auto_switch, alignment=ft.Alignment.CENTER),
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=COLORS['bg_card'],
            border_radius=16,
            border=ft.Border.all(1, COLORS['bg_line']),
            padding=ft.Padding.symmetric(horizontal=18, vertical=8),
        )

        author_info = ft.Container(
            content=ft.Text(
                'Made by ZGMFX01A',
                size=11,
                color=COLORS['text_dim'],
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ft.Alignment.CENTER,
            padding=ft.Padding.only(top=2),
        )

        main_content = ft.Container(
            content=ft.Column(
                controls=[
                    self.card_list,
                    settings_card,
                    action_row,
                    status_bar,
                    author_info,
                ],
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            padding=ft.Padding.only(left=28, right=28, bottom=12),
            expand=True,
        )

        page.add(
            ft.Column(
                controls=[
                    header,
                    main_content,
                ],
                expand=True,
                spacing=0,
            )
        )

        if initial_scan:
            # 首次扫描
            self._set_view_state('loading', self._t('status.syncing'))
            self._start_scan()
        else:
            self._refresh_ui(force_rebuild=True)

    def _update_btn_content(self, btn_row: ft.Row, icon_name, label: str):
        """更新按钮内容。"""
        if btn_row and len(btn_row.controls) >= 2:
            color = COLORS['text_primary']
            btn_row.controls[0] = ft.Icon(icon_name, size=18, color=color)
            btn_row.controls[1] = ft.Text(label, size=14, weight=ft.FontWeight.W_500, color=color)

    def _start_scan(self):
        """后台扫描设备。

        使用 _scan_busy 锁防止扫描进行中被重复点击触发并发扫描（Container.disabled
        在 Flet 中无法拦截 on_click）。同时禁用刷新按钮，避免与刷新 HID 读写争抢。
        扫描结束由 _on_device_update 回调驱动 _refresh_ui 恢复按钮，但这里也兜底释放锁。
        """
        if self._scan_busy or self._refresh_busy:
            return
        self._scan_busy = True

        # GUI 进程只会从共享状态文件同步数据；当当前还没有可展示快照时，
        # 直接切到加载态占位，避免底部状态栏显示“正在扫描”但主体区域仍是旧空态。
        if not self.device_manager.mice:
            self._set_view_state('loading', self._t('status.syncing'))
        self._refresh_ui(force_rebuild=True)

        def worker():
            try:
                self.device_manager.scan_and_refresh()
                if self.auto_switch and self.auto_switch.value:
                    self.device_manager.start_auto_refresh(self._GUI_STATE_REFRESH_INTERVAL)
            except Exception as ex:
                logger.error(f'扫描设备异常: {ex}')
                self._set_view_state('error', self._t('view.error.message'))
                self._refresh_ui(force_rebuild=True)
            finally:
                self._scan_busy = False
                self._refresh_ui(force_rebuild=False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_device_update(self):
        if self.page:
            try:
                self._refresh_ui()
            except Exception as e:
                logger.error(f'UI 刷新错误: {e}')

    def _refresh_ui(self, force_rebuild: bool = False):
        if not self.card_list or not self.page:
            return

        mice = self.device_manager.mice
        keyboard = self._keyboard_snapshot()
        read_state, read_message = self._shared_state_read_status()

        if read_state == 'error':
            if mice or keyboard:
                # 已有旧快照时保留设备卡片，但需要在状态栏显式提示当前数据可能不是最新的。
                self._set_view_state('ready', read_message)
            else:
                self._set_view_state('error', read_message)
        elif mice or keyboard:
            self._set_view_state('ready')
        elif read_state == 'missing':
            # 缺失共享状态文件不再无限停留在 loading；首轮读取后明确展示“尚未同步”。
            self._set_view_state('empty', read_message)
        elif self._view_state not in ('loading', 'error'):
            self._set_view_state('empty')

        render_signature = (
            self._view_state,
            self._view_message,
            self._device_signature(mice),
            self._keyboard_signature(keyboard),
        )
        if force_rebuild or self._last_render_signature != render_signature:
            self.card_list.controls.clear()
            self.card_list.controls.extend(self._build_device_view_controls(mice, keyboard))
            self._last_render_signature = render_signature

        self._refresh_keyboard_dialog()

        # 按钮状态由统一入口恢复，避免扫描/刷新/自动更新互相覆盖文案。
        self._sync_action_buttons()

        if self.status_text:
            self.status_text.value = self._status_bar_message(mice, keyboard)

        self._safe_update()

    def _on_scan_click(self, e):
        self._start_scan()

    def _on_refresh_click(self, e):
        """刷新当前已连接设备的电量。

        使用 _refresh_busy 锁防止与扫描、与自身并发。出错时也要恢复按钮，
        因为 refresh_only 失败并不会触发 _refresh_ui（_notify_update 仍会回调，
        但回调内若抛异常按钮就不可恢复），这里兜底处理。
        """
        if self._refresh_busy or self._scan_busy:
            return
        self._refresh_busy = True
        if not self.device_manager.mice:
            self._set_view_state('loading', self._t('status.syncing'))
        self._refresh_ui(force_rebuild=not self.device_manager.mice)

        def worker():
            try:
                self.device_manager.refresh_only()
            except Exception as ex:
                logger.error(f'刷新电量异常: {ex}')
                # 没有现成设备快照时，错误态要在主体区域可见；
                # 若已有旧快照，则保留卡片，仅更新底部状态文案即可。
                self._set_view_state('error', self._t('view.error.message'))
                self._refresh_ui(force_rebuild=not self.device_manager.mice)
            finally:
                self._refresh_busy = False
                self._refresh_ui(force_rebuild=False)

        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_toggle(self, e):
        if self.auto_switch and self.auto_switch.value:
            self.device_manager.start_auto_refresh(self._GUI_STATE_REFRESH_INTERVAL)
        else:
            self.device_manager.stop_auto_refresh()

    def _on_autostart_toggle(self, e):
        self.config_manager.set_autostart(e.control.value)

    def _set_notify_threshold(self, value: int):
        """设置低电量提醒阈值，并同步刷新轻量步进控件。

        仅接受 _NOTIFY_ALLOWED_VALUES 中的合法档位，非法值（如来自旧下拉框的脏数据）直接拒绝。
        """
        if value not in self._NOTIFY_ALLOWED_VALUES:
            logger.warning(f'低电量阈值非法值: {value!r}')
            return

        self.config_manager.low_battery_notify = value
        logger.info(f'低电量提醒修改为: {value}%')

        # Flet 控件树里没有类似 React 的自动重绘，这里直接替换控件内容，避免只改文字导致布局状态残留。
        if self.notify_threshold_control:
            self.notify_threshold_control.content = build_threshold_stepper(
                value,
                off_label=self._t('settings.off'),
                on_decrease=self._on_notify_decrease,
                on_increase=self._on_notify_increase,
            ).content
            self._safe_update()

    def _on_notify_decrease(self, e):
        """低电量阈值向下切换：30 → 20 → 10 → 关闭。

        当前值若不在合法档位（配置被外部篡改等异常情况），默认回退到 20% 档（idx=2）。
        """
        current = self.config_manager.low_battery_notify
        try:
            idx = self._NOTIFY_ALLOWED_VALUES.index(current)
        except ValueError:
            idx = 2
        self._set_notify_threshold(self._NOTIFY_ALLOWED_VALUES[max(0, idx - 1)])

    def _on_notify_increase(self, e):
        """低电量阈值向上切换：关闭 → 10 → 20 → 30。

        顶部 30% 后再点 + 不再上升（钳制在最后一档），非法值默认回退到 20% 档。
        """
        current = self.config_manager.low_battery_notify
        try:
            idx = self._NOTIFY_ALLOWED_VALUES.index(current)
        except ValueError:
            idx = 2
        self._set_notify_threshold(
            self._NOTIFY_ALLOWED_VALUES[min(len(self._NOTIFY_ALLOWED_VALUES) - 1, idx + 1)]
        )

    def _on_notify_change(self, e):
        """兼容旧下拉框事件：当前界面已改用步进控件，但保留该入口兜底。

        对传入值做整数化和档位校验：_set_notify_threshold 内部已基于
        _NOTIFY_ALLOWED_VALUES 校验，这里仅做整数化，越界值由其拒绝。
        """
        try:
            val = int(e.control.value)
        except (ValueError, TypeError):
            logger.warning(f'低电量阈值非法值: {e.control.value!r}')
            return
        self._set_notify_threshold(val)

    def _safe_update(self):
        """安全的页面刷新，捕捉跨线程导致的异常。"""
        try:
            if self.page:
                self.page.update()
        except Exception as e:
            logger.debug(f'页面刷新已忽略: {e}')
