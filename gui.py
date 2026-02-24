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
# 颜色主题
# ============================================================

COLORS = {
    'bg_dark': '#0D0D1A',
    'bg_card': '#161628',
    'bg_card_border': '#252545',
    'bg_card_hover': '#1E1E38',
    'accent_blue': '#4A6CF7',
    'accent_purple': '#7B5CF7',
    'accent_cyan': '#00D4FF',
    'text_primary': '#FFFFFF',
    'text_secondary': '#9090B0',
    'text_dim': '#505070',
    'battery_full': '#00E676',
    'battery_good': '#66BB6A',
    'battery_mid': '#FFC107',
    'battery_low': '#FF9800',
    'battery_critical': '#FF3D00',
    'charging': '#00E5FF',
    'offline': '#404060',
    'logitech_blue': '#00B8FC',
    'razer_green': '#44D62C',
}


def get_battery_color(percentage: int, charging: bool) -> str:
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
    if brand == Brand.LOGITECH:
        return COLORS['logitech_blue']
    return COLORS['razer_green']


# ============================================================
# 圆环电量指示器
# ============================================================

def build_battery_ring(percentage: int, charging: bool, size: int = 120) -> ft.Stack:
    """用 ProgressRing + Stack 构建圆环电量指示器"""
    pct = max(0, min(100, percentage)) if percentage >= 0 else 0
    color = get_battery_color(pct, charging) if percentage >= 0 else COLORS['offline']

    ring = ft.ProgressRing(
        value=pct / 100 if percentage >= 0 else 0,
        width=size,
        height=size,
        stroke_width=8,
        color=color,
        bgcolor=COLORS['bg_card_border'],
    )

    charging_items = []
    if charging:
        charging_items.append(
            ft.Text("⚡", size=13, color=COLORS['charging'],
                     text_align=ft.TextAlign.CENTER)
        )

    center_content = ft.Column(
        controls=[
            ft.Text(
                f"{pct}" if percentage >= 0 else "--",
                size=28, weight=ft.FontWeight.BOLD,
                color=color,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.Text(
                "%" if percentage >= 0 else "",
                size=11, color=COLORS['text_secondary'],
                text_align=ft.TextAlign.CENTER,
            ),
        ] + charging_items,
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

def build_mouse_card(mouse: MouseInfo) -> ft.Container:
    """构建鼠标设备信息卡片"""
    brand_color = get_brand_color(mouse.brand)

    # 状态点颜色
    if not mouse.online:
        dot_color = COLORS['offline']
    elif mouse.charging:
        dot_color = COLORS['charging']
    elif mouse.percentage >= 20:
        dot_color = COLORS['battery_full']
    else:
        dot_color = COLORS['battery_critical']

    # 更新时间
    time_str = ""
    if mouse.last_update > 0:
        time_str = f"更新于 {time.strftime('%H:%M:%S', time.localtime(mouse.last_update))}"

    ring_widget = build_battery_ring(mouse.percentage, mouse.charging, size=110)

    right_info = ft.Column(
        controls=[
            ft.Text(
                f"● {mouse.brand.value}",
                size=12, weight=ft.FontWeight.BOLD,
                color=brand_color,
            ),
            ft.Text(
                mouse.name,
                size=17, weight=ft.FontWeight.BOLD,
                color=COLORS['text_primary'],
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            ft.Row(
                controls=[
                    ft.Container(width=8, height=8, border_radius=4, bgcolor=dot_color),
                    ft.Text(mouse.status_text, size=13, color=COLORS['text_secondary']),
                ],
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Text(time_str, size=11, color=COLORS['text_dim']),
        ],
        spacing=4,
        alignment=ft.MainAxisAlignment.CENTER,
    )

    card = ft.Container(
        content=ft.Row(
            controls=[
                ft.Container(
                    content=ring_widget,
                    padding=ft.padding.only(left=18, right=10, top=5, bottom=5),
                ),
                ft.Container(content=right_info, expand=True, padding=ft.padding.only(right=18)),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=COLORS['bg_card'],
        border_radius=16,
        border=ft.border.all(1, COLORS['bg_card_border']),
        padding=ft.padding.symmetric(vertical=14),
        animate=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
        on_hover=lambda e: _on_card_hover(e),
    )
    return card


def _on_card_hover(e: ft.ControlEvent):
    container = e.control
    if e.data == "true":
        container.bgcolor = COLORS['bg_card_hover']
        container.border = ft.border.all(1, COLORS['accent_blue'] + "55")
    else:
        container.bgcolor = COLORS['bg_card']
        container.border = ft.border.all(1, COLORS['bg_card_border'])
    container.update()


# ============================================================
# 空状态
# ============================================================

def build_empty_state() -> ft.Container:
    return ft.Container(
        content=ft.Column(
            controls=[
                ft.Icon(ft.Icons.MOUSE_OUTLINED, size=56, color=COLORS['text_dim']),
                ft.Text(
                    "未发现鼠标设备",
                    size=20, weight=ft.FontWeight.BOLD,
                    color=COLORS['text_primary'],
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=8),
                ft.Text(
                    "• 请确保鼠标已开机且无线接收器已插入\n"
                    "• 如果 G Hub / Synapse 正在运行，请先退出\n"
                    "• 可能需要以管理员身份运行本程序\n"
                    "• 点击下方「扫描设备」按钮重试",
                    size=13,
                    color=COLORS['text_secondary'],
                    text_align=ft.TextAlign.LEFT,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=6,
        ),
        padding=ft.padding.symmetric(vertical=60, horizontal=30),
        alignment=ft.alignment.center,
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

    def _on_check_update_click(self, e):
        btn = e.control
        btn.disabled = True
        btn.content = ft.Text("检查中...", size=13)
        self.page.update()
        
        def check():
            has_update, latest, url, body = updater.check_for_update(APP_VERSION)
            btn.content = ft.Text("检查", size=13)
            btn.disabled = False
            self.page.update()
            
            if has_update:
                self._show_update_dialog(latest, url, body)
            else:
                # 区分是已是最新还是网络错误
                if latest:
                    msg = f"当前版本 {APP_VERSION} 已经是最新版！"
                else:
                    msg = f"检查更新失败，请检查网络设置。\n错误信息: {body}"
                    
                def close_dlg(e):
                    dlg.open = False
                    self.page.update()
                    
                dlg = ft.AlertDialog(
                    title=ft.Text("版本检查" if latest else "网络故障"),
                    content=ft.Text(msg, size=13),
                    actions=[ft.TextButton("确定", on_click=close_dlg)],
                    actions_alignment=ft.MainAxisAlignment.END,
                    shape=ft.RoundedRectangleBorder(radius=10)
                )
                self.page.show_dialog(dlg)
                
        threading.Thread(target=check, daemon=True).start()

    def _show_update_dialog(self, version: str, url: str, body: str):
        pb = ft.ProgressBar(width=400, color=COLORS['accent_blue'], value=0)
        status_txt = ft.Text(f"准备升级到 {version}...", color=COLORS['text_dim'], size=12)
        
        def do_update(e):
            dialog.actions[0].disabled = True
            dialog.actions[1].disabled = True
            self.page.update()
            
            def progress(pct, dl, total):
                pb.value = pct / 100.0
                status_txt.value = f"正在下载... {pct}%"
                self.page.update()
                
            def worker():
                success = updater.download_and_install(url, progress)
                if not success:
                    status_txt.value = "更新失败或仍在调试环境中，请直接去 GitHub 下载"
                    dialog.actions[1].disabled = False # 允许关闭
                    self.page.update()
            threading.Thread(target=worker, daemon=True).start()

        def close_dialog(e):
            dialog.open = False
            self.page.update()

        dialog = ft.AlertDialog(
            title=ft.Text(f"发现新版本 {version}"),
            content=ft.Column([
                ft.Text("发版更新记录：", size=13),
                ft.Container(
                    content=ft.Text(body, size=12, color=COLORS['text_dim'], selectable=True),
                    height=100,
                    # 支持简单滚动
                ),
                ft.Container(height=5),
                status_txt,
                pb
            ], tight=True),
            actions=[
                ft.TextButton("立即热更新", on_click=do_update),
                ft.TextButton("稍后", on_click=close_dialog)
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            shape=ft.RoundedRectangleBorder(radius=10)
        )
        self.page.show_dialog(dialog)

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
        self.page = page

        # 窗口配置
        page.title = "鼠标电量监控"
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app.ico')
        if os.path.exists(ico_path):
            page.window.icon = ico_path
        page.window.width = 520
        page.window.height = 700
        page.window.min_width = 460
        page.window.min_height = 500
        page.bgcolor = COLORS['bg_dark']
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(font_family="Segoe UI")

        # 顶部标题
        header = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ft.Icon(ft.Icons.BATTERY_CHARGING_FULL,
                                    color=COLORS['accent_cyan'], size=28),
                            ft.Text(
                                "鼠标电量监控",
                                size=24, weight=ft.FontWeight.BOLD,
                                color=COLORS['text_primary'],
                            ),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Text(
                        "Mouse Battery Monitor",
                        size=12, color=COLORS['text_dim'],
                    ),
                ],
                spacing=2,
            ),
            padding=ft.padding.only(left=25, top=20, bottom=8, right=25),
        )

        # 分割线
        divider = ft.Container(
            height=1,
            bgcolor=COLORS['bg_card_border'],
            margin=ft.margin.symmetric(horizontal=25),
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
            padding=ft.padding.only(left=15, right=15, top=10, bottom=5),
        )

        # 状态文本
        self.status_text = ft.Text("", size=11, color=COLORS['text_dim'])

        # 扫描按钮 — 使用 content= (Flet 0.80 API)
        self.scan_btn_row = self._make_btn_content(ft.Icons.SEARCH, "扫描设备")
        self.scan_btn = ft.ElevatedButton(
            content=self.scan_btn_row,
            style=ft.ButtonStyle(
                bgcolor=COLORS['accent_blue'],
                color=COLORS['text_primary'],
                shape=ft.RoundedRectangleBorder(radius=8),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
                elevation=0,
            ),
            on_click=self._on_scan_click,
        )

        # 刷新按钮
        self.refresh_btn_row = self._make_btn_content(ft.Icons.REFRESH, "刷新电量")
        self.refresh_btn = ft.OutlinedButton(
            content=self.refresh_btn_row,
            style=ft.ButtonStyle(
                color=COLORS['text_secondary'],
                shape=ft.RoundedRectangleBorder(radius=8),
                side=ft.BorderSide(1, COLORS['bg_card_border']),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
            ),
            on_click=self._on_refresh_click,
        )

        # 自动刷新开关
        self.auto_switch = ft.Switch(
            label="自动",
            label_text_style=ft.TextStyle(size=12, color=COLORS['text_secondary']),
            value=True,
            active_color=COLORS['accent_blue'],
            on_change=self._on_auto_toggle,
        )

        # 底部操作栏
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
            border_radius=ft.border_radius.only(top_left=12, top_right=12),
            border=ft.border.only(top=ft.BorderSide(1, COLORS['bg_card_border'])),
            padding=ft.padding.symmetric(horizontal=15, vertical=10),
        )

        # ========= 偏好设置 ========= 
        settings_card = ft.Card(
            bgcolor=COLORS['bg_card'],
            elevation=2,
            content=ft.Container(
                padding=20,
                content=ft.Column([
                    ft.Text("⚙️ 个性化设置", size=18, weight=ft.FontWeight.W_600, color="white"),
                    ft.Divider(height=1, color=COLORS['bg_card_border']),
                    
                    # 1. 开机自启
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.ROCKET_LAUNCH, color="#8888AA"),
                        title=ft.Text("开机自动启动", color="white", size=14),
                        subtitle=ft.Text("跟随 Windows 启动，在后台静默运行", color="#8888AA", size=12),
                        trailing=ft.Switch(
                            value=self.config_manager.check_autostart(),
                            active_color=COLORS['accent_blue'],
                            on_change=self._on_autostart_toggle
                        )
                    ),
                    
                    # 2. 低电量提醒
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.BATTERY_ALERT, color=COLORS['battery_low']),
                        title=ft.Text("低电量弹窗提醒", color="white", size=14),
                        subtitle=ft.Text("系统右下角弹出通知，阶梯防漏式告警", color="#8888AA", size=12),
                        trailing=ft.Dropdown(
                            width=100,
                            value=str(self.config_manager.low_battery_notify),
                            options=[
                                ft.dropdown.Option("0", "关闭"),
                                ft.dropdown.Option("10", "10%"),
                                ft.dropdown.Option("20", "20%"),
                                ft.dropdown.Option("30", "30%"),
                            ],
                            text_style=ft.TextStyle(size=14),
                            on_select=self._on_notify_change
                        )
                    ),

                    # 3. 自动更新与版本
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.SYSTEM_UPDATE_ALT, color="#8888AA"),
                        title=ft.Text(f"版本更新 (当前 {APP_VERSION})", color="white", size=14),
                        subtitle=ft.Text("允许程序在启动时自动下载新版本并静默升级", color="#8888AA", size=12),
                        trailing=ft.Container(
                            width=135,
                            content=ft.Row(
                                [
                                    ft.ElevatedButton(
                                        content=ft.Text("检查", size=13),
                                        on_click=self._on_check_update_click,
                                        style=ft.ButtonStyle(
                                            padding=ft.padding.symmetric(horizontal=12, vertical=8),
                                            shape=ft.RoundedRectangleBorder(radius=6)
                                        )
                                    ),
                                    ft.Switch(
                                        value=self.config_manager.auto_update,
                                        active_color=COLORS['accent_blue'],
                                        on_change=self._on_autoupdate_toggle
                                    )
                                ],
                                alignment=ft.MainAxisAlignment.END,
                                spacing=5
                            )
                        )
                    )
                ], spacing=10)
            )
        )

        # 将鼠标设备列表与设置面板打包放入可滚动的区域中，防止挤占底部状态栏
        scrollable_content = ft.Column(
            controls=[
                card_container,
                settings_card,
            ],
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=15,
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
        val = int(e.control.value)
        self.config_manager.low_battery_notify = val
        logger.info(f"低电量提醒修改为: {val}%")

    def _safe_update(self):
        try:
            if self.page:
                self.page.update()
        except Exception:
            pass
