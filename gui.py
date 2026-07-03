"""
鼠标电量监控 - GUI 界面 (Flet 0.80+)

基于 Flet (Flutter for Python) 的现代化界面，展示鼠标电池状态。
"""

import os
import time
import threading
import logging
from typing import Optional

import flet as ft

from devices import DeviceManager, MouseInfo, Brand
from config import ConfigManager, APP_VERSION
import updater

logger = logging.getLogger(__name__)

# ============================================================
# 颜色主题 — Dark Mode (OLED) 设计系统
# 深蓝近黑底 (slate-900/950) + 绿色正向指标 + 状态色阶梯
# 参考: ui-ux-pro-max 推荐的 OLED Dark 方案（高对比、护眼、低功耗）
# ============================================================

COLORS = {
    # —— 背景层（由浅到深）——
    'bg_dark': '#020617',          # 窗口底色: slate-950 近黑
    'bg_card': '#0F172A',          # 卡片底色: slate-900
    'bg_card_hover': '#1E293B',    # 卡片悬浮: slate-800
    'bg_card_border': '#334155',   # 卡片描边: slate-700
    'bg_muted': '#1A1E2F',         # 次级表面: muted slate
    'bg_input': '#1E293B',         # 输入/下拉框填充

    # —— 强调色（品牌主色与功能色）——
    'accent_green': '#22C55E',     # 主强调/正向指标: green-500
    'accent_blue': '#3B82F6',      # 次强调: blue-500
    'accent_cyan': '#06B6D4',      # 信息/充电: cyan-500

    # —— 文字层级 ——
    'text_primary': '#F8FAFC',     # 主文字: slate-50（WCAG AAA 对比）
    'text_secondary': '#94A3B8',   # 次文字: slate-400（>=4.5:1）
    'text_dim': '#64748B',         # 弱化文字: slate-500（>=3:1）

    # —— 电量阶梯色（绿→黄→红）——
    'battery_full': '#22C55E',     # >=80%: green-500
    'battery_good': '#84CC16',     # >=60%: lime-500
    'battery_mid': '#FACC15',      # >=35%: yellow-400
    'battery_low': '#F97316',      # >=15%: orange-500
    'battery_critical': '#EF4444', # <15%: red-500
    'charging': '#06B6D4',         # 充电中: cyan-500
    'offline': '#475569',          # 离线: slate-600

    # —— 语义色 / 破坏性操作 ——
    'destructive': '#EF4444',

    # —— 品牌标识色 ——
    'logitech_blue': '#00B8FC',    # 罗技官方蓝
    'razer_green': '#44D62C',      # 雷蛇官方绿
}


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
    else:
        return COLORS['battery_critical']


def get_brand_color(brand: Brand) -> str:
    """根据品牌返回对应的官方强调色。"""
    if brand == Brand.LOGITECH:
        return COLORS['logitech_blue']
    return COLORS['razer_green']


# ============================================================
# 圆环电量指示器
# ============================================================

def build_battery_ring(percentage: int, charging: bool, size: int = 120) -> ft.Stack:
    """
    用 ProgressRing + Stack 构建圆环电量指示器。
    支持离线/未知电态：电量环置灰，避免负值导致环越界。
    """
    if percentage < 0:
        pct = 0
        color = COLORS['offline']
    else:
        pct = max(0, min(100, percentage))
        color = get_battery_color(pct, charging)

    ring = ft.ProgressRing(
        value=pct / 100,
        width=size,
        height=size,
        stroke_width=8,
        color=color,
        bgcolor=COLORS['bg_card_border'],
    )

    # 数字与百分号垂直堆叠，由外层 Column 居中（保持居中效果）
    center_controls = [
        ft.Text(
            f"{pct}" if percentage >= 0 else "--",
            size=30, weight=ft.FontWeight.W_700,
            color=color,
            text_align=ft.TextAlign.CENTER,
        ),
        ft.Text(
            "%" if percentage >= 0 else "",
            size=11, color=COLORS['text_secondary'],
            text_align=ft.TextAlign.CENTER,
        ),
    ]
    if charging:
        # 充电态叠加一个矢量闪电图标，避免依赖字体 emoji（跨平台一致性）
        center_controls.append(
            ft.Icon(ft.Icons.BOLT, size=16,
                    color=COLORS['charging'])
        )

    center_content = ft.Column(
        controls=center_controls,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=0,
    )

    return ft.Stack(
        controls=[
            ring,
            ft.Container(
                content=center_content,
                width=size,
                height=size,
                alignment=ft.Alignment(0, 0),
            ),
        ],
        width=size,
        height=size,
    )


# ============================================================
# 鼠标设备卡片
# ============================================================

def build_mouse_card(mouse: MouseInfo, app_ref: "MouseBatteryApp" = None) -> ft.Container:
    """
    构建鼠标设备信息卡片（Dark OLED 风格）。
    - 品牌色左边框作为视觉强调，与电量色呼应
    - 电量环居中，右侧为名称/状态/时间信息区
    - 悬停切到更亮的次级表面，并用品牌色边框收边
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

    # 更新时间（仅在有线时间才展示）
    time_str = ""
    if mouse.last_update > 0:
        time_str = f"更新于 {time.strftime('%H:%M:%S', time.localtime(mouse.last_update))}"

    ring_widget = build_battery_ring(mouse.percentage, mouse.charging, size=110)

    # 品牌标签徽章：圆角小药丸，复用品牌色
    brand_badge = ft.Container(
        content=ft.Text(
            mouse.brand.value,
            size=11, weight=ft.FontWeight.W_600,
            color=brand_color,
        ),
        padding=ft.Padding.symmetric(horizontal=8, vertical=3),
        border_radius=10,
        bgcolor=brand_color + "1F",  # 12% 透明品牌色填充
        border=ft.Border.all(1, brand_color + "59"),  # 35% 透明色描边
    )

    right_info = ft.Column(
        controls=[
            brand_badge,
            ft.Text(
                mouse.name,
                size=18, weight=ft.FontWeight.W_700,
                color=COLORS['text_primary'],
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Row(
                controls=[
                    # 状态点：小圆点闪烁呼吸感由 animate 提供
                    ft.Container(width=8, height=8, border_radius=4, bgcolor=dot_color),
                    ft.Text(mouse.status_text, size=13, color=COLORS['text_secondary']),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(time_str, size=11, color=COLORS['text_dim']),
        ],
        spacing=6,
        alignment=ft.MainAxisAlignment.CENTER,
    )

    # 卡片主体：左边品牌色竖条 + 内容
    card = ft.Container(
        content=ft.Row(
            controls=[
                # 品牌色左侧竖条（视觉锚点，宽 4）
                ft.Container(width=4, height=96, border_radius=2, bgcolor=brand_color),
                ft.Container(
                    content=ring_widget,
                    padding=ft.Padding.only(left=16, right=12, top=5, bottom=5),
                ),
                ft.Container(content=right_info, expand=True,
                             padding=ft.Padding.only(right=18)),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=0,
        ),
        bgcolor=COLORS['bg_card'],
        border_radius=14,
        border=ft.Border.all(1, COLORS['bg_card_border']),
        padding=ft.Padding.symmetric(vertical=12),
        # 悬停过渡：250ms ease-out，符合微交互 150-300ms 区间
        animate=ft.Animation(250, ft.AnimationCurve.EASE_OUT),
        on_hover=lambda e: _on_card_hover(e),
    )
    return card


def _on_card_hover(e: ft.ControlEvent):
    """鼠标卡片悬停效果：切换到更亮的次级表面并描品牌色边。"""
    container = e.control
    if e.data == "true":
        container.bgcolor = COLORS['bg_card_hover']
        container.border = ft.Border.all(1, COLORS['accent_blue'] + "66")
    else:
        container.bgcolor = COLORS['bg_card']
        container.border = ft.Border.all(1, COLORS['bg_card_border'])
    container.update()


# ============================================================
# 空状态
# ============================================================

def build_empty_state() -> ft.Container:
    """未发现设备时的空状态占位（柔和引导文案 + 圆角图标徽章）。"""
    # 用一个带描边的圆角容器包裹图标，营造"空位卡"质感
    icon_badge = ft.Container(
        content=ft.Icon(ft.Icons.MOUSE_OUTLINED, size=48, color=COLORS['text_dim']),
        width=88,
        height=88,
        border_radius=44,
        bgcolor=COLORS['bg_muted'],
        border=ft.Border.all(1, COLORS['bg_card_border']),
        alignment=ft.Alignment.CENTER,
    )
    return ft.Container(
        content=ft.Column(
            controls=[
                icon_badge,
                ft.Text(
                    "未发现鼠标设备",
                    size=20, weight=ft.FontWeight.W_700,
                    color=COLORS['text_primary'],
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=6),
                ft.Text(
                    "请确保鼠标已开机且无线接收器已插入\n"
                    "如 G Hub / Synapse 正在运行，请先退出\n"
                    "可能需要以管理员身份运行本程序\n"
                    "点击下方「扫描设备」按钮重试",
                    size=13,
                    color=COLORS['text_secondary'],
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
        padding=ft.Padding.symmetric(vertical=54, horizontal=30),
        alignment=ft.Alignment.CENTER,
    )


# ============================================================
# 主应用
# ============================================================

class MouseBatteryApp:
    """鼠标电量监控主应用"""

    def __init__(self, device_manager: DeviceManager):
        self.device_manager = device_manager
        self.config_manager = ConfigManager()
        self.device_manager.set_on_update(self._on_device_update)
        self.page: Optional[ft.Page] = None
        self.card_list: Optional[ft.Column] = None
        self.scan_btn: Optional[ft.ElevatedButton] = None
        self.scan_btn_row: Optional[ft.Row] = None
        self.refresh_btn: Optional[ft.OutlinedButton] = None
        self.refresh_btn_row: Optional[ft.Row] = None
        self.status_text: Optional[ft.Text] = None
        self.auto_switch: Optional[ft.Switch] = None

    def _on_autoupdate_toggle(self, e):
        self.config_manager.auto_update = e.control.value

    def _show_dialog(self, title: str, message: str, actions: list = None):
        """统一的对话框构建与弹出，减少重复代码。返回对话框对象供外部控制。"""
        def close_dlg(e):
            dlg.open = False
            self._safe_update()

        if actions is None:
            actions = [ft.TextButton("确定", on_click=close_dlg)]

        dlg = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Text(message, size=13, selectable=True),
            actions=actions,
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=10)
        )
        self.page.show_dialog(dlg)
        return dlg

    def _on_check_update_click(self, e):
        btn = e.control
        btn.disabled = True
        btn.content = ft.Text("检查中...", size=13)
        self._safe_update()

        done_event = threading.Event()
        result_holder = [None]  # (has_update, latest, url, body)

        def check():
            result_holder[0] = updater.check_for_update(APP_VERSION)
            done_event.set()

        def watchdog():
            threading.Thread(target=check, daemon=True).start()
            finished = done_event.wait(timeout=10)

            # 恢复按钮
            btn.content = ft.Text("检查", size=13)
            btn.disabled = False
            self._safe_update()

            if not finished:
                # 网络超时
                self._safe_show_helper(lambda: self._show_dialog(
                    "网络超时", "检查更新超时，请检查网络连接后重试。"
                ))
                return

            has_update, latest, url, body = result_holder[0]
            if has_update:
                self._safe_show_helper(lambda: self._show_update_dialog(latest, url, body))
            else:
                # 区分已是最新 vs 网络错误
                if latest:
                    msg = f"当前版本 {APP_VERSION} 已经是最新版！"
                    title = "版本检查"
                else:
                    msg = f"检查更新失败，请检查网络设置。\n错误信息: {body}"
                    title = "网络故障"
                self._safe_show_helper(lambda: self._show_dialog(title, msg))

        threading.Thread(target=watchdog, daemon=True).start()

    def _safe_show_helper(self, builder):
        """跨线程安全地执行 UI 构建并刷新页面。"""
        if not self.page:
            return
        try:
            builder()
            self.page.update()
        except Exception as e:
            logger.error(f"UI 弹窗失败: {e}")

    def _show_update_dialog(self, version: str, url: str, body: str):
        # 进度条用主强调色（绿色），轨道用暗色描边保证暗底下可见
        pb = ft.ProgressBar(width=400, color=COLORS['accent_green'],
                            bgcolor=COLORS['bg_muted'], value=0)
        status_txt = ft.Text(f"准备升级到 {version}...",
                             color=COLORS['text_secondary'], size=12)

        def do_update(e):
            dialog.actions[0].disabled = True
            dialog.actions[1].disabled = True
            self._safe_update()

            last_pct = [-1]
            def progress(pct, dl, total):
                if pct != last_pct[0]:
                    last_pct[0] = pct
                    pb.value = pct / 100.0
                    status_txt.value = f"正在下载... {pct}%"
                    self._safe_update()

            def worker():
                host_pid = None
                host_pid_env = os.environ.get('MOUSE_BATTERY_HOST_PID', '').strip()
                if host_pid_env.isdigit():
                    host_pid = int(host_pid_env)

                success = updater.download_and_install(url, progress, host_pid=host_pid)
                if not success:
                    status_txt.value = "更新失败或仍在调试环境中，请直接去 GitHub 下载"
                    dialog.actions[1].disabled = False  # 允许关闭
                    self._safe_update()
            threading.Thread(target=worker, daemon=True).start()

        def close_dialog(e):
            dialog.open = False
            self._safe_update()

        dialog = ft.AlertDialog(
            title=ft.Text(f"发现新版本 {version}"),
            content=ft.Column([
                ft.Text("发版更新记录：", size=13),
                ft.Container(
                    content=ft.Text(body, size=12, color=COLORS['text_dim'], selectable=True),
                    height=100,
                ),
                ft.Container(height=5),
                status_txt,
                pb
            ], tight=True, scroll=ft.ScrollMode.AUTO),
            actions=[
                ft.TextButton("立即热更新", on_click=do_update),
                ft.TextButton("稍后", on_click=close_dialog)
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=10)
        )
        self.page.show_dialog(dialog)
        return dialog

    def _make_btn_content(self, icon_name, label: str) -> ft.Row:
        """创建按钮内部内容（icon + text）"""
        return ft.Row(
            controls=[
                ft.Icon(icon_name, size=18),
                ft.Text(label, size=13, weight=ft.FontWeight.W_500),
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def build(self, page: ft.Page):
        """
        构建主界面（Dark OLED 风格）。
        从上到下：品牌头部 → 分割线 → 可滚动内容区(设备卡片+设置+作者) → 固定底部操作栏。
        """
        self.page = page

        # —— 窗口配置 ——
        page.title = "鼠标电量监控"
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.ico')
        if os.path.exists(ico_path):
            page.window.icon = ico_path
        page.window.width = 520
        page.window.height = 760
        page.window.min_width = 460
        page.window.min_height = 540
        page.bgcolor = COLORS['bg_dark']
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        # 主题统一使用较清晰的 Segoe UI（Flet 桌面端默认字体），保证中文渲染
        # 主题统一使用较清晰的 Segoe UI（Flet 桌面端默认字体），保证中文渲染
        # ColorScheme 仅使用 Flet 0.80 合法字段，遵循 Material 3 规范
        page.theme = ft.Theme(
            font_family="Segoe UI",
            color_scheme=ft.ColorScheme(
                primary=COLORS['accent_green'],          # 主强调色：正向指标绿
                on_primary=COLORS['bg_dark'],            # 主色上的前景文字
                secondary=COLORS['accent_blue'],         # 次强调色：蓝色
                on_secondary=COLORS['text_primary'],
                surface=COLORS['bg_card'],                # 卡片表面色
                on_surface=COLORS['text_primary'],        # 表面上的前景文字
                outline=COLORS['bg_card_border'],         # 描边/边框色
                error=COLORS['destructive'],              # 错误/破坏性色
            ),
        )

        # —— 顶部标题区 ——
        # 电池图标放进圆角徽章里，与按钮主色一致，强化品牌识别
        header_icon = ft.Container(
            content=ft.Icon(ft.Icons.BATTERY_CHARGING_FULL,
                           color=COLORS['accent_green'], size=22),
            width=40, height=40,
            border_radius=12,
            bgcolor=COLORS['accent_green'] + "1A",  # 10% 透明主色填充
            border=ft.Border.all(1, COLORS['accent_green'] + "40"),
            alignment=ft.Alignment.CENTER,
        )
        header = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            header_icon,
                            ft.Text(
                                "鼠标电量监控",
                                size=22, weight=ft.FontWeight.W_700,
                                color=COLORS['text_primary'],
                            ),
                        ],
                        spacing=12,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Mouse Battery Monitor · 实时无线鼠标电池状态",
                        size=12, color=COLORS['text_secondary'],
                    ),
                ],
                spacing=4,
            ),
            padding=ft.Padding.only(left=24, top=24, bottom=10, right=24),
        )

        # —— 分割线（更柔和的暗色细线）——
        divider = ft.Container(
            height=1,
            bgcolor=COLORS['bg_card'] + "00",  # 透明背景
            margin=ft.Margin.symmetric(horizontal=24),
            border=ft.Border.only(bottom=ft.BorderSide(1, COLORS['bg_card_border'] + "AA")),
        )

        # 设备卡片列表
        self.card_list = ft.Column(
            controls=[],
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
        )
        card_container = ft.Container(
            content=self.card_list,
            expand=True,
            padding=ft.Padding.only(left=16, right=16, top=12, bottom=6),
        )

        # 状态文本（底部状态栏左侧的设备计数）
        self.status_text = ft.Text("", size=12, color=COLORS['text_secondary'])

        # 扫描按钮 — 主操作（绿色强调，实心填充，Flet 0.80 content API）
        self.scan_btn_row = self._make_btn_content(ft.Icons.SEARCH, "扫描设备")
        self.scan_btn = ft.ElevatedButton(
            content=self.scan_btn_row,
            style=ft.ButtonStyle(
                bgcolor=COLORS['accent_green'],
                color=COLORS['bg_dark'],  # 深底字，在亮绿按钮上保持高对比
                shape=ft.RoundedRectangleBorder(radius=10),
                padding=ft.Padding.symmetric(horizontal=18, vertical=9),
                elevation=0,
            ),
            on_click=self._on_scan_click,
        )

        # 刷新按钮 — 次操作（描边样式）
        self.refresh_btn_row = self._make_btn_content(ft.Icons.REFRESH, "刷新电量")
        self.refresh_btn = ft.OutlinedButton(
            content=self.refresh_btn_row,
            style=ft.ButtonStyle(
                color=COLORS['text_primary'],
                shape=ft.RoundedRectangleBorder(radius=10),
                side=ft.BorderSide(1, COLORS['bg_card_border']),
                padding=ft.Padding.symmetric(horizontal=18, vertical=9),
            ),
            on_click=self._on_refresh_click,
        )

        # 自动刷新开关
        self.auto_switch = ft.Switch(
            label="自动刷新",
            label_text_style=ft.TextStyle(size=12, color=COLORS['text_secondary']),
            value=True,
            active_color=COLORS['accent_green'],
            on_change=self._on_auto_toggle,
        )

        # 底部操作栏（固定在窗口底部，与内容区分离避免遮挡）
        bottom_bar = ft.Container(
            content=ft.Row(
                controls=[
                    self.scan_btn,
                    self.refresh_btn,
                    ft.Container(expand=True),
                    self.status_text,
                    self.auto_switch,
                ],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            bgcolor=COLORS['bg_card'],
            border_radius=ft.BorderRadius.only(top_left=16, top_right=16),
            border=ft.Border.only(top=ft.BorderSide(1, COLORS['bg_card_border'])),
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
        )

        # ========= 偏好设置面板 =========
        # 设置组标题去掉 emoji，改用 Settings 图标徽章 + 文字；统一用 token 颜色
        settings_title = ft.Row(
            controls=[
                ft.Container(
                    content=ft.Icon(ft.Icons.SETTINGS,
                                    color=COLORS['text_secondary'], size=18),
                    width=32, height=32,
                    border_radius=10,
                    bgcolor=COLORS['bg_muted'],
                    border=ft.Border.all(1, COLORS['bg_card_border']),
                    alignment=ft.Alignment.CENTER,
                ),
                ft.Text("个性化设置", size=17,
                        weight=ft.FontWeight.W_700, color=COLORS['text_primary']),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        settings_card = ft.Card(
            bgcolor=COLORS['bg_card'],
            elevation=0,
            content=ft.Container(
                padding=ft.Padding.symmetric(horizontal=18, vertical=16),
                content=ft.Column([
                    settings_title,
                    ft.Container(height=4),
                    ft.Divider(height=1, color=COLORS['bg_card_border']),
                    ft.Container(height=2),

                    # 1. 开机自启
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.ROCKET_LAUNCH,
                                        color=COLORS['accent_green']),
                        title=ft.Text("开机自动启动",
                                      color=COLORS['text_primary'], size=14,
                                      weight=ft.FontWeight.W_500),
                        subtitle=ft.Text("跟随 Windows 启动，在后台静默运行",
                                         color=COLORS['text_secondary'], size=12),
                        trailing=ft.Switch(
                            value=self.config_manager.check_autostart(),
                            active_color=COLORS['accent_green'],
                            on_change=self._on_autostart_toggle
                        )
                    ),

                    # 2. 低电量提醒
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.BATTERY_ALERT,
                                        color=COLORS['battery_low']),
                        title=ft.Text("低电量弹窗提醒",
                                      color=COLORS['text_primary'], size=14,
                                      weight=ft.FontWeight.W_500),
                        subtitle=ft.Text("系统右下角弹出通知，阶梯防漏式告警",
                                         color=COLORS['text_secondary'], size=12),
                        trailing=ft.Dropdown(
                            width=104,
                            value=str(self.config_manager.low_battery_notify),
                            options=[
                                ft.DropdownOption(key="0", text="关闭"),
                                ft.DropdownOption(key="10", text="10%"),
                                ft.DropdownOption(key="20", text="20%"),
                                ft.DropdownOption(key="30", text="30%"),
                            ],
                            text_style=ft.TextStyle(size=13,
                                                    color=COLORS['text_primary']),
                            border_color=COLORS['bg_card_border'],
                            border_radius=ft.BorderRadius.all(8),
                            bgcolor=COLORS['bg_input'],
                            on_select=self._on_notify_change
                        )
                    ),

                    # 3. 自动更新与版本
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT,
                                        color=COLORS['accent_cyan']),
                        title=ft.Text(f"版本更新 · 当前 {APP_VERSION}",
                                      color=COLORS['text_primary'], size=14,
                                      weight=ft.FontWeight.W_500),
                        subtitle=ft.Text("启动时自动下载新版本并静默升级",
                                         color=COLORS['text_secondary'], size=12),
                        trailing=ft.Container(
                            width=145,
                            content=ft.Row(
                                [
                                    ft.ElevatedButton(
                                        content=ft.Text("检查", size=13,
                                                        weight=ft.FontWeight.W_500),
                                        on_click=self._on_check_update_click,
                                        style=ft.ButtonStyle(
                                            bgcolor=COLORS['accent_cyan'],
                                            color=COLORS['bg_dark'],
                                            padding=ft.Padding.symmetric(
                                                horizontal=12, vertical=7),
                                            shape=ft.RoundedRectangleBorder(radius=8),
                                            elevation=0,
                                        )
                                    ),
                                    ft.Switch(
                                        value=self.config_manager.auto_update,
                                        active_color=COLORS['accent_green'],
                                        on_change=self._on_autoupdate_toggle
                                    )
                                ],
                                alignment=ft.MainAxisAlignment.END,
                                spacing=6
                            )
                        )
                    )
                ], spacing=6)
            )
        )

        # 作者信息（底部水印，居中弱化）
        author_info = ft.Container(
            content=ft.Text(
                "Made by ZGMFX01A · 839140758@qq.com",
                size=11, color=COLORS['text_dim'],
                text_align=ft.TextAlign.CENTER,
                width=460,
            ),
            padding=ft.Padding.only(bottom=8),
        )

        # 将鼠标设备列表与设置面板打包放入可滚动区域，防止挤占底部状态栏
        scrollable_content = ft.Column(
            controls=[
                card_container,
                settings_card,
                author_info,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=12,
        )

        # 组装页面
        page.add(
            ft.Column(
                controls=[
                    header,
                    divider,
                    scrollable_content,
                    bottom_bar
                ],
                expand=True,
                spacing=0,
            )
        )

        # 首次扫描
        self._start_scan()

    def _update_btn_content(self, btn_row: ft.Row, icon_name, label: str):
        """更新按钮内容"""
        if btn_row and len(btn_row.controls) >= 2:
            btn_row.controls[0] = ft.Icon(icon_name, size=18)
            btn_row.controls[1] = ft.Text(label, size=13, weight=ft.FontWeight.W_500)

    def _start_scan(self):
        """后台扫描设备"""
        if self.scan_btn:
            self.scan_btn.disabled = True
        self._update_btn_content(self.scan_btn_row, ft.Icons.HOURGLASS_TOP, "扫描中...")
        if self.refresh_btn:
            self.refresh_btn.disabled = True
        if self.status_text:
            self.status_text.value = "正在扫描设备..."
        self._safe_update()

        def worker():
            self.device_manager.scan_and_refresh()
            if self.auto_switch and self.auto_switch.value:
                self.device_manager.start_auto_refresh(60)

        threading.Thread(target=worker, daemon=True).start()

    def _on_device_update(self):
        if self.page:
            try:
                self._refresh_ui()
            except Exception as e:
                logger.error(f"UI 刷新错误: {e}")

    def _refresh_ui(self):
        if not self.card_list or not self.page:
            return

        mice = self.device_manager.mice
        self.card_list.controls.clear()

        if not mice:
            self.card_list.controls.append(build_empty_state())
        else:
            for mouse in mice:
                self.card_list.controls.append(build_mouse_card(mouse))

        # 恢复按钮状态
        if self.scan_btn:
            self.scan_btn.disabled = False
        self._update_btn_content(self.scan_btn_row, ft.Icons.SEARCH, "扫描设备")
        if self.refresh_btn:
            self.refresh_btn.disabled = False
        self._update_btn_content(self.refresh_btn_row, ft.Icons.REFRESH, "刷新电量")

        if self.status_text:
            count = len(mice)
            self.status_text.value = f"已发现 {count} 个设备" if mice else "未发现设备"

        self._safe_update()

    def _on_scan_click(self, e):
        self._start_scan()

    def _on_refresh_click(self, e):
        if self.refresh_btn:
            self.refresh_btn.disabled = True
        self._update_btn_content(self.refresh_btn_row, ft.Icons.HOURGLASS_TOP, "刷新中...")
        if self.status_text:
            self.status_text.value = "正在刷新电量..."
        self._safe_update()

        def worker():
            self.device_manager.refresh_only()

        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_toggle(self, e):
        if self.auto_switch and self.auto_switch.value:
            self.device_manager.start_auto_refresh(60)
        else:
            self.device_manager.stop_auto_refresh()

    def _on_autostart_toggle(self, e):
        self.config_manager.set_autostart(e.control.value)

    def _on_notify_change(self, e):
        """低电量提醒阈值变更，带边界保护防止非数字值。"""
        try:
            val = int(e.control.value)
        except (ValueError, TypeError):
            logger.warning(f"低电量阈值非法值: {e.control.value!r}")
            return
        if not 0 <= val <= 100:
            logger.warning(f"低电量阈值越界: {val}，已忽略")
            return
        self.config_manager.low_battery_notify = val
        logger.info(f"低电量提醒修改为: {val}%")

    def _safe_update(self):
        """安全的页面刷新，捕捉跨线程导致的异常。"""
        try:
            if self.page:
                self.page.update()
        except Exception:
            pass
