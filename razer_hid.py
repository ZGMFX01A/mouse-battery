"""
雷蛇 USB 报文协议实现

通过 2.4GHz 无线 dongle 与雷蛇鼠标通信，查询电池状态。
参考：OpenRazer 项目 (https://github.com/openrazer/openrazer)
"""

import struct
import time
import logging
from dataclasses import dataclass
from typing import Optional

import hid

logger = logging.getLogger(__name__)

# ============================================================
# 常量定义
# ============================================================

RAZER_VID = 0x1532
RAZER_REPORT_LEN = 90  # 每个 USB 报文 90 字节

# 已知雷蛇无线 dongle PID
RAZER_WIRELESS_PIDS = {
    0x00AB: "巴塞利斯蛇 V3 (有线)",
    0x00B9: "巴塞利斯蛇 V3 Pro (有线)",
    0x00CD: "巴塞利斯蛇 V3 Pro (无线Dongle)",
    0x00A5: "DeathAdder V3 Pro (无线Dongle)",
    0x00B6: "DeathAdder V3 (无线Dongle)",
    0x00AA: "Viper V2 Pro (无线Dongle)",
    0x007A: "Viper Ultimate (无线Dongle)",
    0x0088: "Basilisk X Hyperspeed (无线Dongle)",
    0x0083: "Basilisk Ultimate (无线Dongle)",
    0x0090: "DeathAdder V2 Pro (无线Dongle)",
    0x008F: "Basilisk V3 (有线)",
    0x00B5: "巴塞利斯蛇 V3 Pro (蓝牙)",
}

# 能查询电池的 PID 集合（无线 dongle）
RAZER_BATTERY_CAPABLE_PIDS = {
    0x00CD,  # 巴塞利斯蛇 V3 Pro 无线 Dongle
    0x00A5,  # DeathAdder V3 Pro 无线 Dongle
    0x00B6,  # DeathAdder V3 无线 Dongle
    0x00AA,  # Viper V2 Pro 无线 Dongle
    0x007A,  # Viper Ultimate 无线 Dongle
    0x0088,  # Basilisk X Hyperspeed 无线 Dongle
    0x0083,  # Basilisk Ultimate 无线 Dongle
    0x0090,  # DeathAdder V2 Pro 无线 Dongle
}

# 报文命令
CMD_GET = 0x80  # Host -> Device (Get 方向)
CMD_SET = 0x00  # Host -> Device (Set 方向)

# 命令类别
CMD_CLASS_MISC = 0x07

# 命令 ID (去掉方向位)
CMD_BATTERY_LEVEL = 0x80    # 获取电量 (0x07, 0x80)
CMD_CHARGING_STATUS = 0x84  # 获取充电状态 (0x07, 0x84)

# Transaction ID
TRANSACTION_ID = 0x1F

# 报文状态
STATUS_NEW = 0x00
STATUS_BUSY = 0x01
STATUS_SUCCESS = 0x02
STATUS_FAILURE = 0x03
STATUS_TIMEOUT = 0x04
STATUS_NOT_SUPPORTED = 0x05


@dataclass
class RazerBatteryInfo:
    """雷蛇电池信息"""
    percentage: int = 0
    charging: bool = False
    status_text: str = "未知"


def _calculate_crc(data: bytes) -> int:
    """
    计算 Razer 报文 CRC
    对 status 到 arguments 最后一个字节进行 XOR
    (byte 0 到 byte 87, 共 88 字节, 第 88 字节是 CRC, 第 89 字节是 reserved)
    """
    crc = 0
    # XOR bytes 2 through 87 (transaction_id through last argument byte)
    for i in range(2, 88):
        crc ^= data[i]
    return crc


def _build_razer_report(command_class: int, command_id: int,
                        data_size: int = 0,
                        transaction_id: int = TRANSACTION_ID,
                        arguments: bytes = b'') -> bytes:
    """
    构建 90 字节的 Razer 报文

    结构:
    - status (1B): 0x00 = 新命令
    - transaction_id (1B): 0x1F
    - remaining_packets (2B): 0x0000
    - protocol_type (1B): 0x00
    - data_size (1B): 参数有效长度
    - command_class (1B): 命令类别
    - command_id (1B): 命令ID (包含方向位)
    - arguments (80B): 参数数据
    - crc (1B): XOR 校验
    - reserved (1B): 0x00
    """
    report = bytearray(RAZER_REPORT_LEN)

    report[0] = STATUS_NEW           # status
    report[1] = transaction_id       # transaction_id
    report[2] = 0x00                 # remaining_packets (high)
    report[3] = 0x00                 # remaining_packets (low)
    report[4] = 0x00                 # protocol_type
    report[5] = data_size            # data_size
    report[6] = command_class        # command_class
    report[7] = command_id           # command_id

    # 填充 arguments
    for i, b in enumerate(arguments):
        if i < 80:
            report[8 + i] = b

    # 计算 CRC
    report[88] = _calculate_crc(bytes(report))
    report[89] = 0x00  # reserved

    return bytes(report)


class RazerDevice:
    """
    雷蛇无线鼠标通信类
    """

    def __init__(self, device_info: dict):
        self.device_info = device_info
        self.path = device_info['path']
        self.product_id = device_info['product_id']
        self.product_name = RAZER_WIRELESS_PIDS.get(
            self.product_id,
            device_info.get('product_string', '未知雷蛇设备')
        )
        self._device: Optional[hid.device] = None

    def open(self) -> bool:
        """打开 HID 设备"""
        try:
            self._device = hid.device()
            self._device.open_path(self.path)
            self._device.set_nonblocking(True)
            logger.info(f"已打开雷蛇设备: {self.product_name} (PID: 0x{self.product_id:04X})")
            return True
        except Exception as e:
            logger.error(f"无法打开雷蛇设备: {e}")
            self._device = None
            return False

    def close(self):
        """关闭 HID 设备"""
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None

    def _send_report(self, report: bytes, timeout_ms: int = 3000) -> Optional[bytes]:
        """
        发送报文并等待响应

        发送时需要在报文前加 report_id = 0x00
        """
        if not self._device:
            return None

        try:
            # Windows 下 HID write 需要 report ID 前缀
            data_to_send = b'\x00' + report
            self._device.send_feature_report(data_to_send)

            # 等待响应
            time.sleep(0.1)  # 给设备一点时间处理

            start = time.monotonic()
            while (time.monotonic() - start) * 1000 < timeout_ms:
                try:
                    response = self._device.get_feature_report(0x00, RAZER_REPORT_LEN + 1)
                    if response and len(response) >= RAZER_REPORT_LEN:
                        resp = bytes(response)
                        # 去掉 report_id 前缀（如果有）
                        if len(resp) == RAZER_REPORT_LEN + 1:
                            resp = resp[1:]
                        # 检查状态
                        status = resp[0]
                        if status == STATUS_SUCCESS:
                            return resp
                        elif status == STATUS_BUSY:
                            time.sleep(0.05)
                            continue
                        elif status in (STATUS_FAILURE, STATUS_NOT_SUPPORTED):
                            logger.debug(f"Razer 命令失败, status=0x{status:02X}")
                            return None
                        else:
                            # 可能还没处理完，继续读取
                            return resp
                except Exception:
                    pass
                time.sleep(0.05)

            logger.debug("Razer 响应超时")
            return None

        except Exception as e:
            logger.error(f"Razer 通信错误: {e}")
            return None

    def get_battery_level(self) -> Optional[int]:
        """
        获取电池电量百分比
        command_class=0x07, command_id=0x80

        注意：Razer 返回的电量值范围是 0-255，需要换算为 0-100%
        例如：0x5F (95) → 95 * 100 / 255 ≈ 37%
        """
        report = _build_razer_report(
            command_class=CMD_CLASS_MISC,
            command_id=CMD_BATTERY_LEVEL,
            data_size=0x02
        )

        response = self._send_report(report)
        if response and len(response) >= 10:
            # 电量在 arguments[1] 字节，值域 0-255
            raw_level = response[9]  # offset 8 + arguments[1]
            level = round(raw_level * 100 / 255)
            level = max(0, min(100, level))  # 钳位到 0-100
            logger.info(f"Razer 电池电量: {level}% (原始值: 0x{raw_level:02X}={raw_level})")
            return level

        return None

    def get_charging_status(self) -> Optional[bool]:
        """
        获取充电状态
        command_class=0x07, command_id=0x84
        """
        report = _build_razer_report(
            command_class=CMD_CLASS_MISC,
            command_id=CMD_CHARGING_STATUS,
            data_size=0x02
        )

        response = self._send_report(report)
        if response and len(response) >= 10:
            charging = response[9]  # arguments[1]
            is_charging = charging != 0
            logger.info(f"Razer 充电状态: {'充电中' if is_charging else '未充电'}")
            return is_charging

        return None

    def get_battery(self) -> Optional[RazerBatteryInfo]:
        """获取完整的电池信息"""
        level = self.get_battery_level()
        if level is None:
            return None

        info = RazerBatteryInfo()
        info.percentage = level

        charging = self.get_charging_status()
        if charging is not None:
            info.charging = charging

        if info.charging:
            info.status_text = "充电中"
        elif info.percentage <= 5:
            info.status_text = "电量极低"
        elif info.percentage <= 20:
            info.status_text = "电量低"
        elif info.percentage >= 95:
            info.status_text = "已充满"
        else:
            info.status_text = "放电中"

        return info


def find_razer_devices() -> list[dict]:
    """
    扫描所有已连接的雷蛇无线鼠标 dongle

    关键：必须选择 interface_number=0 (MI_00) 的接口，
    这是唯一能正确收发 feature report 进行电池查询的接口。
    雷蛇 dongle 有多达 12 个子接口，只有 MI_00 支持控制通信。
    """
    devices = []
    seen_pids = set()

    try:
        all_devices = hid.enumerate(RAZER_VID, 0)
    except Exception as e:
        logger.error(f"枚举 HID 设备失败: {e}")
        return []

    # 第一轮：收集每个 PID 的所有候选接口
    candidates: dict[int, list[dict]] = {}
    for dev in all_devices:
        pid = dev['product_id']
        if pid not in RAZER_BATTERY_CAPABLE_PIDS:
            continue
        if pid not in candidates:
            candidates[pid] = []
        candidates[pid].append(dev)

    # 第二轮：为每个 PID 选取最佳接口
    for pid, devs in candidates.items():
        chosen = None

        # 优先选 interface_number=0（MI_00 主控制接口）
        for dev in devs:
            if dev.get('interface_number', -1) == 0:
                chosen = dev
                break

        # 备选：usage_page=0x0001 且 usage=0x0002（鼠标）
        if not chosen:
            for dev in devs:
                if (dev.get('usage_page', 0) == 0x0001
                        and dev.get('usage', 0) == 0x0002
                        and dev.get('interface_number', -1) != 1):
                    chosen = dev
                    break

        # 最终备选：取第一个
        if not chosen:
            chosen = devs[0]

        iface = chosen.get('interface_number', -1)
        usage_page = chosen.get('usage_page', 0)
        usage = chosen.get('usage', 0)
        logger.info(
            f"发现雷蛇设备: PID=0x{pid:04X} "
            f"({RAZER_WIRELESS_PIDS.get(pid, '未知')}), "
            f"interface={iface}, usage_page=0x{usage_page:04X}, "
            f"usage=0x{usage:04X}"
        )
        devices.append(chosen)

    return devices
