"""
鼠标电量监控 - 程序入口

检测罗技和雷蛇无线鼠标的电池状态，在系统托盘显示电量。
支持: 罗技 G903, G502X / 雷蛇 巴塞利斯蛇 V3 Pro 等
"""

import sys
import os
import time
import logging
import _thread
import ctypes
import subprocess
import atexit
import threading

from devices import DeviceManager
from tray import TrayApp
from config import ConfigManager
import updater


ERROR_ALREADY_EXISTS = 183
_instance_mutex_handle = None
_shutdown_for_update = False
_shutdown_skip_gui_pid = None


def acquire_single_instance(lock_name: str) -> bool:
    """基于 Windows 命名互斥体实现单实例。"""
    global _instance_mutex_handle
    if os.name != 'nt':
        return True

    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = ctypes.c_uint32

        handle = kernel32.CreateMutexW(None, False, lock_name)
        if not handle:
            return True

        _instance_mutex_handle = handle
        err = kernel32.GetLastError()
        if err == ERROR_ALREADY_EXISTS:
            return False
        return True
    except Exception:
        # 互斥锁异常时不阻塞主流程
        return True


@atexit.register
def release_single_instance():
    """进程退出时释放互斥体句柄。"""
    global _instance_mutex_handle
    if os.name != 'nt' or not _instance_mutex_handle:
        return
    try:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_instance_mutex_handle))
    except Exception:
        pass
    _instance_mutex_handle = None


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


_settings_processes = []


def start_update_shutdown_watchdog(current_pid: int):
    """监听热更新退出请求，尽量优雅关闭主进程。"""

    def worker():
        global _shutdown_for_update, _shutdown_skip_gui_pid
        while True:
            request = updater.consume_shutdown_request(current_pid)
            if request:
                _shutdown_for_update = request.get('reason') == 'update'
                skip_gui_pid = request.get('skip_gui_pid')
                _shutdown_skip_gui_pid = skip_gui_pid if isinstance(skip_gui_pid, int) and skip_gui_pid > 0 else None
                logging.getLogger(__name__).info(
                    f"收到热更新退出请求，准备优雅关闭主进程: skip_gui_pid={_shutdown_skip_gui_pid}"
                )
                _thread.interrupt_main()
                return
            time.sleep(0.5)

    threading.Thread(target=worker, daemon=True, name="update-shutdown-watchdog").start()


def open_settings_window():
    """用子进程打开 Flet 设置窗口（避免 signal 线程限制）"""
    global _settings_processes
    # 清理已经退出的子进程句柄
    _settings_processes = [p for p in _settings_processes if p.poll() is None]
    
    # 单窗口防护：如果已有活动的 GUI 子进程，不再重复打开
    if _settings_processes:
        logging.getLogger(__name__).info("设置窗口已打开，不重复创建")
        return
    
    try:
        # 当打包成 exe 后，sys.executable 会变成当前的 exe 路径
        # 因此通过传入 --gui 参数，再次启动本程序，但进入 GUI 逻辑
        env = os.environ.copy()
        env['MOUSE_BATTERY_HOST_PID'] = str(os.getpid())
        p = subprocess.Popen(
            [sys.executable, '--gui'],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            env=env
        )
        _settings_processes.append(p)
    except Exception as e:
        logging.getLogger(__name__).error(f"设置窗口启动失败: {e}")


@atexit.register
def cleanup_settings_windows():
    """退出主程序时，确保拉起的独立 GUI 进程被关闭"""
    global _settings_processes
    for p in _settings_processes:
        if p.poll() is None:
            try:
                if _shutdown_for_update and _shutdown_skip_gui_pid == p.pid:
                    logging.getLogger(__name__).info(
                        f"热更新退出时跳过强杀 GUI 子进程: pid={p.pid}"
                    )
                    continue
                if os.name == 'nt':
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(p.pid)],
                                    creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    p.terminate()
            except Exception:
                pass
    _settings_processes.clear()


def launch_gui_mode():
    """GUI 模式启动逻辑"""
    import flet as ft
    from gui import MouseBatteryApp
    
    # 确定资源基准目录
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    def _set_window_icon(title: str):
        """通过 Windows API 强制设置窗口图标（Flet 自身不支持桌面窗口图标）"""
        import time
        user32 = ctypes.windll.user32
        ico_path = os.path.join(base_dir, 'app.ico')
        if not os.path.exists(ico_path):
            logging.warning(f"图标文件不存在: {ico_path}")
            return
        hicon_big = user32.LoadImageW(None, ico_path, 1, 0, 0, 0x10)
        hicon_small = user32.LoadImageW(None, ico_path, 1, 16, 16, 0x10)
        if not hicon_big:
            return
        # 轮询等待窗口出现
        import time
        for _ in range(10):
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                user32.SendMessageW(hwnd, 0x80, 1, hicon_big)   # WM_SETICON, ICON_BIG
                user32.SendMessageW(hwnd, 0x80, 0, hicon_small) # WM_SETICON, ICON_SMALL
                logging.info("窗口图标已通过 Windows API 设置")
                return
            time.sleep(0.5)
    
    def _flet_main(page: ft.Page):
        page.title = "鼠标电量监控"
        # 后台线程设置窗口图标
        import threading
        threading.Thread(target=_set_window_icon, args=(page.title,), daemon=True).start()
        
        # 使用只读的共享状态 DeviceManager，不打开任何 HID 设备
        from devices import SharedStateDeviceManager
        dm = SharedStateDeviceManager()
        app = MouseBatteryApp(dm)
        app.build(page)
        
    logging.info("启动 Flet 设置界面...")
    assets_path = os.path.join(base_dir, 'assets')
    ft.app(target=_flet_main, assets_dir=assets_path)


if __name__ == '__main__':
    setup_logging()
    logger = logging.getLogger(__name__)

    # 如果带有 --gui 参数，则启动 Flet GUI 设置页面
    if len(sys.argv) > 1 and sys.argv[1] == '--gui':
        if not acquire_single_instance("Global\\MouseBattery_GUI_SingleInstance"):
            logger.info("设置窗口实例已存在，本次启动已忽略")
            sys.exit(0)
        try:
            launch_gui_mode()
        except KeyboardInterrupt:
            logger.info("GUI 进程收到热更新退出信号，已开始优雅退出")
        sys.exit(0)

    if not acquire_single_instance("Global\\MouseBattery_Main_SingleInstance"):
        logger.info("主程序实例已存在，本次启动已忽略")
        sys.exit(0)

    logger.info("=" * 50)
    logger.info("鼠标电量监控程序启动")
    logger.info("=" * 50)

    if not check_admin():
        logger.warning("未以管理员身份运行，可能无法访问部分 HID 设备")

    # 初始化设备管理器与配置管理器
    device_manager = DeviceManager()
    config_manager = ConfigManager()

    # 以托盘模式启动（阻塞）
    tray = TrayApp(
        device_manager=device_manager,
        config_manager=config_manager,
        on_open_settings=open_settings_window,
    )
    start_update_shutdown_watchdog(os.getpid())
    tray.start()
