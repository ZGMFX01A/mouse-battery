"""
GUI 设置窗口独立启动器

由 main.py 通过子进程调用，避免 Flet 的 signal 线程限制。
"""
import sys
import logging

import flet as ft

from devices import DeviceManager
from gui import MouseBatteryApp


def main(page: ft.Page):
    dm = DeviceManager()
    app = MouseBatteryApp(dm)
    app.build(page)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )
    ft.app(target=main)
