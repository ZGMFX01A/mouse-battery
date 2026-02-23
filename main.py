"""
鼠标电量监控 - 程序入口

检测罗技和雷蛇无线鼠标的电池状态，在系统托盘显示电量。
支持: 罗技 G903, G502X / 雷蛇 巴塞利斯蛇 V3 Pro 等
"""

import sys
import os
import logging
import ctypes
import subprocess

from devices import DeviceManager
from tray import TrayApp


def setup_logging():
    """配置日志"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
        handlers=[
            logging.StreamHandler(sys.stdout),
        ]
    )


def check_admin():
    """检查是否有管理员权限"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def open_settings_window():
    """用子进程打开 Flet 设置窗口（避免 signal 线程限制）"""
    try:
        # 当打包成 exe 后，sys.executable 会变成当前的 exe 路径
        # 因此通过传入 --gui 参数，再次启动本程序，但进入 GUI 逻辑
        subprocess.Popen([sys.executable, '--gui'],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except Exception as e:
        logging.getLogger(__name__).error(f"设置窗口启动失败: {e}")


def launch_gui_mode():
    """GUI 模式启动逻辑"""
    import flet as ft
    from gui import MouseBatteryApp
    
    def _flet_main(page: ft.Page):
        # 独立的 DeviceManager
        dm = DeviceManager()
        app = MouseBatteryApp(dm)
        app.build(page)
        
    logging.info("启动 Flet 设置界面...")
    ft.app(target=_flet_main)


if __name__ == '__main__':
    setup_logging()
    logger = logging.getLogger(__name__)

    # 如果带有 --gui 参数，则启动 Flet GUI 设置页面
    if len(sys.argv) > 1 and sys.argv[1] == '--gui':
        launch_gui_mode()
        sys.exit(0)

    logger.info("=" * 50)
    logger.info("鼠标电量监控程序启动")
    logger.info("=" * 50)

    if not check_admin():
        logger.warning("未以管理员身份运行，可能无法访问部分 HID 设备")

    # 初始化设备管理器
    device_manager = DeviceManager()

    # 以托盘模式启动（阻塞）
    tray = TrayApp(
        device_manager,
        on_open_settings=open_settings_window,
    )
    tray.start()
