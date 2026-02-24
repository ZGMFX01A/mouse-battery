"""
配置与持久化管理模块

通过 JSON 文件保存用户偏好设置。
并提供修改 Windows 注册表实现程序自启的功能。
"""
import os
import sys
import json
import logging
import winreg

import updater

logger = logging.getLogger(__name__)

APP_VERSION = "v1.3.0"

# 获取当前程序实际路径，如果被 PyInstaller 打包，获取的是生成的 exe 路径
if getattr(sys, 'frozen', False):
    APP_PATH = sys.executable
else:
    APP_PATH = os.path.abspath(sys.argv[0])

# 在同级目录存储配额
CONFIG_FILE = os.path.join(os.path.dirname(APP_PATH), "config.json")
REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "MouseBatteryMonitor"


class ConfigManager:
    """管理应用的用户配置和自启状态"""

    def __init__(self):
        # 启动时先清理上次热更新可能遗留的旧版执行文件被占用导致的残留
        updater.clean_old_version()
        
        # 默认配置
        self.config = {
            "low_battery_notify": 20, # 默认 20%
            "notified_levels": {}, # 记录每个鼠标上次被通知时的电量，防重复弹窗
            "auto_update": False # 默认不自动更新
        }
        self.load()

    def load(self):
        """读取配置文件"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config.update(data)
            except Exception as e:
                logger.error(f"读取配置异常: {e}")

    def save(self):
        """写入配置文件"""
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存配置异常: {e}")

    @property
    def low_battery_notify(self) -> int:
        return self.config.get("low_battery_notify", 20)
        
    @low_battery_notify.setter
    def low_battery_notify(self, val: int):
        self.config["low_battery_notify"] = val
        # 重置所有已经通知过的状态，即使用户修改了阈值，也能重新触发一次
        self.config["notified_levels"] = {}
        self.save()

    @property
    def auto_update(self) -> bool:
        return self.config.get("auto_update", False)
        
    @auto_update.setter
    def auto_update(self, val: bool):
        self.config["auto_update"] = val
        self.save()

    def check_autostart(self) -> bool:
        """检查是否已注册开机自启"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_READ)
            value, _ = winreg.QueryValueEx(key, REG_NAME)
            winreg.CloseKey(key)
            return value == APP_PATH
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.error(f"读取启动项错误: {e}")
            return False

    def set_autostart(self, enable: bool):
        """设定开机启动状态"""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH, 0, winreg.KEY_SET_VALUE)
            if enable:
                winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, APP_PATH)
                logger.info("已打开开机自启动")
            else:
                try:
                    winreg.DeleteValue(key, REG_NAME)
                    logger.info("已关闭开机自启动")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
        except Exception as e:
            logger.error(f"修改自启注册表失败: {e}")

    def should_notify(self, device_name: str, current_pct: int) -> bool:
        """
        判断此时是否应该弹出低电量警告。
        支持跌穿阈值后自动继续提醒：
        > 5% 时，每掉 5% 提醒一次
        <= 5% 时，每掉 1% 提醒一次
        """
        threshold = self.low_battery_notify
        if threshold == 0 or current_pct <= 0:
            return False # 0或负数表示永不通知、休眠或设备未就绪

        notified_levels = self.config.setdefault("notified_levels", {})
        last_notified = notified_levels.get(device_name, 101)

        # 如果充电拉回到了安全水位，重置通知标记
        if current_pct > threshold and last_notified <= threshold:
            notified_levels[device_name] = current_pct
            self.save()
            return False

        if current_pct <= threshold:
            # 首次跌穿阈值
            if last_notified > threshold:
                notified_levels[device_name] = current_pct
                self.save()
                return True
            
            # 已经跌穿过阈值，判断是否需要再次提醒
            # 1. 小于等于 5% 时，每掉 1% 提醒一次
            elif current_pct <= 5 and current_pct < last_notified:
                notified_levels[device_name] = current_pct
                self.save()
                return True
            
            # 2. 大于 5% 时，每掉 5% 提醒一次
            elif current_pct > 5 and (last_notified - current_pct) >= 5:
                notified_levels[device_name] = current_pct
                self.save()
                return True

        return False
