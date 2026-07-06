"""
鼠标电量监控 - 系统托盘图标

使用 pystray + Pillow 在 Windows 托盘区显示动态电量图标。

图标设计：圆环
- 圆环弧度按电量比例绘制（满电 = 完整绿色圆环）
- 圆环中心显示粗体白色百分比数字
- 颜色随电量变化：绿 → 黄 → 橙 → 红
- 充电时蓝色 + 闪电
"""

import threading
import logging
import time
import math
from typing import Optional, Callable

from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem, Menu

from devices import DeviceManager, MouseInfo, Brand
from config import (
    ConfigManager,
    APP_VERSION,
    TRAY_ICON_PRIORITY_MOUSE_FIRST,
    TRAY_ICON_PRIORITY_KEYBOARD_FIRST,
    TRAY_ICON_PRIORITY_LOWEST_BATTERY,
)
import updater
from i18n import translate, translate_brand_name, translate_runtime_text

logger = logging.getLogger(__name__)

# ============================================================
# 颜色
# ============================================================

COLORS = {
    'full':     (76, 175, 80),      # ≥60%
    'good':     (139, 195, 74),     # 40-59%
    'mid':      (255, 193, 7),      # 20-39%
    'low':      (255, 152, 0),      # 10-19%
    'critical': (244, 67, 54),      # <10%
    'charging': (33, 150, 243),     # 充电蓝
    'ring_bg':  (60, 60, 60),       # 圆环底色
    'unknown':  (100, 100, 100),
}


def _level_color(pct: int, charging: bool) -> tuple:
    if charging:
        return COLORS['charging']
    if pct >= 60:  return COLORS['full']
    if pct >= 40:  return COLORS['good']
    if pct >= 20:  return COLORS['mid']
    if pct >= 10:  return COLORS['low']
    return COLORS['critical']


# ============================================================
# 字体
# ============================================================

_font_cache: dict[tuple, ImageFont.FreeTypeFont] = {}

def _font(size: int, bold: bool = True):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    names = ["segoeuib.ttf", "arialbd.ttf"] if bold else []
    names += ["segoeui.ttf", "arial.ttf"]
    for n in names:
        try:
            f = ImageFont.truetype(n, size)
            _font_cache[key] = f
            return f
        except Exception:
            pass
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# ============================================================
# 圆环图标
# ============================================================

def create_battery_icon(percentage: int = -1, charging: bool = False,
                        size: int = 64) -> Image.Image:
    """
    生成圆环电量图标。

    在 S×S 画布绘制，缩放到 size。
    """
    S = 128
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    ring_width = 16
    pad = 0  # 满铺画布
    bbox = [pad, pad, S - pad - 1, S - pad - 1]

    if percentage < 0:
        draw.arc(bbox, 0, 360, fill=COLORS['ring_bg'], width=ring_width)
        _draw_center_text(draw, "?", S, COLORS['unknown'])
    else:
        pct = max(0, min(100, percentage))
        color = _level_color(pct, charging)

        # 底环
        draw.arc(bbox, 0, 360, fill=COLORS['ring_bg'], width=ring_width)

        # 彩色弧（从12点顺时针）
        if pct > 0:
            sweep = pct * 360 / 100
            start = -90
            end = start + sweep
            draw.arc(bbox, start, end, fill=color, width=ring_width)

        # 中心数字
        _draw_center_text(draw, str(pct), S, (255, 255, 255))

        # 充电闪电（右下角）
        if charging:
            bf = _font(32, bold=True)
            draw.text((S - 36, S - 38), "⚡", fill=(255, 235, 59), font=bf)

    return img.resize((size, size), Image.LANCZOS)


def _draw_center_text(draw: ImageDraw.Draw, text: str, S: int, color: tuple):
    """圆环中心绘制文字（精确居中 + 深色描边）"""
    # 字体加大：1-2位数用 72，3位数用 52
    fsize = 72 if len(text) <= 2 else 52
    f = _font(fsize, bold=True)

    cx, cy = S // 2, S // 2

    # 描边加粗
    for dx in range(-4, 5):
        for dy in range(-4, 5):
            if dx == 0 and dy == 0:
                continue
            draw.text((cx + dx, cy + dy), text, font=f,
                      fill=(0, 0, 0, 200), anchor="mm")
    # 正文
    draw.text((cx, cy), text, font=f, fill=color, anchor="mm")


# ============================================================
# 托盘应用
# ============================================================

class TrayApp:
    """系统托盘电量监控"""

    def __init__(self, device_manager: DeviceManager,
                 config_manager: ConfigManager,
                 on_open_settings: Optional[Callable] = None):
        self.device_manager = device_manager
        self.config_manager = config_manager
        self.on_open_settings = on_open_settings
        self._tray: Optional[pystray.Icon] = None
        self._running = False
        self._stopping = False

    def _effective_language(self) -> str:
        """返回当前 tray 应采用的实际语言。"""
        return self.config_manager.effective_ui_language

    def _t(self, key: str, **kwargs) -> str:
        """按当前语言获取托盘静态文案。"""
        return translate(key, self._effective_language(), **kwargs)

    def _translate_runtime_text(self, text: str) -> str:
        """翻译运行时状态文案，避免英文模式仍展示中文状态。"""
        return translate_runtime_text(text, self._effective_language())

    def _translate_brand_name(self, name: str) -> str:
        """翻译品牌名，保持菜单与 tooltip 语言一致。"""
        return translate_brand_name(name, self._effective_language())

    def start(self):
        """启动托盘图标（阻塞，主线程）"""
        self._running = True
        self.device_manager.add_on_update(self._update_icon)

        self._tray = pystray.Icon(
            name="MouseBattery",
            icon=create_battery_icon(-1),
            title=f"{self._t('tray.app_name')}\n{self._t('tray.scanning')}",
            menu=self._build_menu(),
        )

        def boot():
            time.sleep(0.5)
            self.device_manager.start_command_listener()
            self.device_manager.scan_and_refresh()
            self.device_manager.start_auto_refresh(60)

            # 后台静默检查更新
            if self.config_manager.auto_update:
                def auto_check():
                    time.sleep(10) # 延迟10秒执行，避免抢占启动阶段资源
                    has_update, _, url, _ = updater.check_for_update(APP_VERSION)
                    if has_update:
                        logger.info("系统后台发现新版本，开始静默下载升级...")
                        updater.download_and_install(url)
                threading.Thread(target=auto_check, daemon=True).start()
        threading.Thread(target=boot, daemon=True).start()

        logger.info("托盘图标已启动")
        try:
            self._tray.run()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            logger.error(f"托盘运行异常: {e}")
        finally:
            self.stop()

    def stop(self):
        if self._stopping:
            return
        self._stopping = True
        self._running = False

        # 移除回调避免在停止时被调用
        self.device_manager.remove_on_update(self._update_icon)

        self.device_manager.shutdown()

        if self._tray:
            try:
                self._tray.visible = False
                self._tray.stop()
            except Exception as e:
                logger.debug(f"托盘停止异常: {e}")

    # ---- 图标 & 菜单 ----

    def _update_icon(self):
        if not self._tray:
            return
        mice = self.device_manager.mice
        keyboard = self.device_manager.keyboard
        if not mice and not keyboard:
            self._tray.icon = create_battery_icon(-1)
            self._tray.title = f"{self._t('tray.app_name')}\n{self._t('tray.no_device_or_sleep')}"
        else:
            valid_mice = [m for m in mice if m.percentage >= 0]
            valid_keyboard = keyboard if keyboard and keyboard.percentage >= 0 else None
            icon_target = self._select_icon_target(valid_mice, valid_keyboard)
            if not icon_target:
                self._tray.icon = create_battery_icon(-1)
            else:
                self._tray.icon = create_battery_icon(icon_target['percentage'], icon_target['charging'])

            lines = [self._t('tray.app_name')]
            for m in mice:
                p = f"{m.percentage}%" if m.percentage >= 0 else "N/A"
                c = " ⚡" if m.charging else ""
                lines.append(f"{self._translate_runtime_text(m.name)}: {p}{c}")

                # 仅在已读到有效电量、且未充电时才判断低电量通知。
                # percentage<0 表示休眠/未就绪，不应触发告警避免误报。
                if m.percentage >= 0 and not m.charging:
                    if self.config_manager.should_notify(m.name, m.percentage):
                        try:
                            self._tray.notify(
                                self._t('tray.notification.low_battery_message', name=self._translate_runtime_text(m.name), percent=m.percentage),
                                title=self._t('tray.notification.low_battery_title')
                            )
                            logger.info(f"触发低电量弹窗: {m.name} {m.percentage}%")
                        except Exception as e:
                            logger.error(f"弹窗通知失败: {e}")

            if keyboard:
                p = f"{keyboard.percentage}%" if keyboard.percentage >= 0 else "N/A"
                c = " ⚡" if keyboard.charging else ""
                lines.append(f"{self._translate_runtime_text(keyboard.name)}: {p}{c}")

            # Windows tooltip 上限约 128 字符，做安全截断避免乱码
            self._tray.title = "\n".join(lines)[:120]
        self._tray.menu = self._build_menu()

    def _select_icon_target(self, valid_mice: list[MouseInfo], valid_keyboard) -> Optional[dict]:
        """根据配置选择当前托盘图标要显示哪一台设备的电量。

        三种策略分别对应用户在设置面板中的选择：
        - 优先鼠标：有有效鼠标时优先显示鼠标，否则退回键盘
        - 优先键盘：有有效键盘时优先显示键盘，否则退回鼠标
        - 低电量优先：在所有有效设备中选电量最低者
        """
        priority = self.config_manager.tray_icon_priority
        mouse_low = min(valid_mice, key=lambda item: item.percentage) if valid_mice else None
        keyboard_target = None
        if valid_keyboard:
            keyboard_target = {
                'percentage': valid_keyboard.percentage,
                'charging': valid_keyboard.charging,
            }

        if priority == TRAY_ICON_PRIORITY_KEYBOARD_FIRST:
            if keyboard_target:
                return keyboard_target
            if mouse_low:
                return {'percentage': mouse_low.percentage, 'charging': mouse_low.charging}
            return None

        if priority == TRAY_ICON_PRIORITY_LOWEST_BATTERY:
            samples = []
            if mouse_low:
                samples.append({'percentage': mouse_low.percentage, 'charging': mouse_low.charging})
            if keyboard_target:
                samples.append(keyboard_target)
            if not samples:
                return None
            return min(samples, key=lambda item: item['percentage'])

        # 默认策略：优先鼠标，再退回键盘。
        if mouse_low:
            return {'percentage': mouse_low.percentage, 'charging': mouse_low.charging}
        return keyboard_target

    def _build_menu(self) -> Menu:
        items = []
        mice = self.device_manager.mice
        if mice:
            for m in mice:
                p = f"{m.percentage}%" if m.percentage >= 0 else "N/A"
                c = self._t('tray.menu.charging_suffix') if m.charging else ""
                items.append(MenuItem(
                    f"[{self._translate_brand_name(m.brand.value)}] {self._translate_runtime_text(m.name)}: {p}{c}", None, enabled=False
                ))
            items.append(Menu.SEPARATOR)
        else:
            items.append(MenuItem(self._t('tray.menu.no_device'), None, enabled=False))
            items.append(Menu.SEPARATOR)

        items.append(MenuItem(self._t('tray.menu.refresh_now'), self._on_refresh))
        if self.on_open_settings:
            items.append(MenuItem(self._t('tray.menu.open_settings'), self._on_open_settings_click))
        items.append(Menu.SEPARATOR)
        items.append(MenuItem(self._t('tray.menu.quit'), self._on_quit))
        return Menu(*items)

    # ---- 菜单回调 ----

    def _on_refresh(self, icon, item):
        threading.Thread(
            target=self.device_manager.scan_and_refresh, daemon=True
        ).start()

    def _on_open_settings_click(self, icon, item):
        if self.on_open_settings:
            threading.Thread(target=self.on_open_settings, daemon=True).start()

    def _on_quit(self, icon, item):
        logger.info("用户退出程序")
        self.stop()
