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
from config import ConfigManager, APP_VERSION
import updater

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

    def start(self):
        """启动托盘图标（阻塞，主线程）"""
        self._running = True
        self.device_manager.add_on_update(self._update_icon)

        self._tray = pystray.Icon(
            name="MouseBattery",
            icon=create_battery_icon(-1),
            title="鼠标电量监控\n正在扫描...",
            menu=self._build_menu(),
        )

        def boot():
            time.sleep(0.5)
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
        if not mice:
            self._tray.icon = create_battery_icon(-1)
            self._tray.title = "鼠标电量监控\n未发现设备"
        else:
            low = min(mice, key=lambda m: m.percentage if m.percentage >= 0 else 999)
            self._tray.icon = create_battery_icon(low.percentage, low.charging)
            lines = ["鼠标电量监控"]
            for m in mice:
                p = f"{m.percentage}%" if m.percentage >= 0 else "N/A"
                c = " ⚡" if m.charging else ""
                lines.append(f"{m.name}: {p}{c}")
                
                # 判断是否需要低电量通知 (跌穿阈值 且 未充电)
                if not m.charging and self.config_manager.should_notify(m.name, m.percentage):
                    try:
                        self._tray.notify(
                            f"{m.name} 当前电量只有 {m.percentage}%，请及时充电！",
                            title="鼠标电量告警"
                        )
                        logger.info(f"触发低电量弹窗: {m.name} {m.percentage}%")
                    except Exception as e:
                        logger.error(f"弹窗通知失败: {e}")
                        
            self._tray.title = "\n".join(lines)[:127]
        self._tray.menu = self._build_menu()

    def _build_menu(self) -> Menu:
        items = []
        mice = self.device_manager.mice
        if mice:
            for m in mice:
                p = f"{m.percentage}%" if m.percentage >= 0 else "N/A"
                c = " ⚡充电中" if m.charging else ""
                items.append(MenuItem(
                    f"[{m.brand.value}] {m.name}: {p}{c}", None, enabled=False
                ))
            items.append(Menu.SEPARATOR)
        else:
            items.append(MenuItem("未发现设备", None, enabled=False))
            items.append(Menu.SEPARATOR)

        items.append(MenuItem("🔄 立即刷新", self._on_refresh))
        if self.on_open_settings:
            items.append(MenuItem("⚙️ 打开设置", self._on_open_settings_click))
        items.append(Menu.SEPARATOR)
        items.append(MenuItem("❌ 退出", self._on_quit))
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
