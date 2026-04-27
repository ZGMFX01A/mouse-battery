"""
罗技 HID++ 2.0 协议实现

通过 Lightspeed 接收器与罗技无线鼠标通信，查询电池状态。
参考：Solaar 项目 (https://github.com/pwr-Solaar/Solaar)
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

LOGITECH_VID = 0x046D

# 已知 Lightspeed 接收器 PID
LIGHTSPEED_RECEIVER_PIDS = {
    0xC539,  # Lightspeed Receiver (G Pro Wireless)
    0xC53A,  # Lightspeed Receiver
    0xC53D,  # Lightspeed Receiver
    0xC53F,  # Lightspeed Receiver (G305)
    0xC541,  # Lightspeed Receiver (G903 / G703)
    0xC545,  # Lightspeed Receiver
    0xC547,  # Lightspeed Receiver (G502X Plus)
    0xC548,  # Bolt Receiver
    0xC52B,  # Unifying Receiver
}

# HID++ 报文类型
HIDPP_SHORT_MSG = 0x10  # 7 字节
HIDPP_LONG_MSG = 0x11   # 20 字节

# HID++ Feature ID
FEATURE_ROOT = 0x0000
FEATURE_FEATURE_SET = 0x0001
FEATURE_BATTERY_STATUS = 0x1000
FEATURE_BATTERY_VOLTAGE = 0x1001
FEATURE_UNIFIED_BATTERY = 0x1004

# 设备号：Lightspeed 一般第一个设备是 0x01
DEVICE_INDEX_FIRST = 0x01

# 电池电压 -> 百分比映射表 (用于 BATTERY_VOLTAGE feature)
VOLTAGE_TO_PERCENT = [
    (4186, 100), (4067, 90), (3989, 80), (3922, 70),
    (3859, 60), (3811, 50), (3778, 40), (3751, 30),
    (3717, 20), (3671, 10), (3646, 5), (3579, 2),
    (3500, 0),
]


@dataclass
class BatteryInfo:
    """电池信息"""
    percentage: int = 0
    charging: bool = False
    status_text: str = "未知"
    source: str = "unknown"


class LogitechReceiver:
    """
    罗技 Lightspeed / Unifying 接收器通信类
    """

    def __init__(self, device_info: dict):
        self.device_info = device_info
        self.path = device_info['path']
        self.product_id = device_info['product_id']
        self.product_string = device_info.get('product_string', '未知接收器')
        self._device: Optional[hid.device] = None
        self._feature_cache: dict[int, int] = {}

    def open(self) -> bool:
        """打开 HID 设备"""
        try:
            self._device = hid.device()
            self._device.open_path(self.path)
            self._device.set_nonblocking(True)
            logger.info(f"已打开罗技接收器: {self.product_string} (PID: 0x{self.product_id:04X})")
            return True
        except Exception as e:
            logger.error(f"无法打开罗技接收器: {e}")
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
            self._feature_cache.clear()

    def _send_short(self, device_index: int, feature_index: int,
                    function: int, *params) -> Optional[bytes]:
        """
        发送 HID++ 短报文 (7 bytes) 并读取响应
        """
        if not self._device:
            return None

        # 构建报文: [report_id, device_index, feature_index, function<<4 | sw_id, p0, p1, p2]
        data = [HIDPP_SHORT_MSG, device_index, feature_index,
                (function << 4) | 0x0A]  # sw_id = 0x0A

        # 填充参数到 3 字节
        for i in range(3):
            data.append(params[i] if i < len(params) else 0x00)

        return self._send_and_receive(bytes(data))

    def _send_long(self, device_index: int, feature_index: int,
                   function: int, *params) -> Optional[bytes]:
        """
        发送 HID++ 长报文 (20 bytes) 并读取响应
        """
        if not self._device:
            return None

        # 构建报文
        data = [HIDPP_LONG_MSG, device_index, feature_index,
                (function << 4) | 0x0A]

        # 填充参数到 16 字节
        for i in range(16):
            data.append(params[i] if i < len(params) else 0x00)

        return self._send_and_receive(bytes(data))

    def _send_and_receive(self, data: bytes, timeout_ms: int = 2000) -> Optional[bytes]:
        """发送数据并等待响应"""
        if not self._device:
            return None

        try:
            self._device.write(data)

            # 等待响应
            start = time.monotonic()
            while (time.monotonic() - start) * 1000 < timeout_ms:
                response = self._device.read(64)
                if response:
                    resp = bytes(response)
                    # 仅接受与本次请求严格匹配的 HID++ 响应帧，忽略其它输入事件/异步帧
                    if len(resp) < 4:
                        time.sleep(0.01)
                        continue

                    # 仅接受 HID++ 短/长报文
                    if resp[0] not in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG):
                        continue

                    # 必须是同一个 device_index
                    if resp[1] != data[1]:
                        continue

                    req_sw_id = data[3] & 0x0F

                    # 错误响应: feature byte=0x8F，且 SW-ID 匹配当前请求
                    if resp[2] == 0x8F:
                        if (resp[3] & 0x0F) == req_sw_id:
                            logger.debug(f"HID++ 错误响应: {resp.hex()}")
                            return None
                        continue

                    # 必须匹配 feature_index、function 高 4 位、SW-ID 低 4 位
                    if resp[2] != data[2]:
                        continue
                    if (resp[3] & 0xF0) != (data[3] & 0xF0):
                        continue
                    if (resp[3] & 0x0F) != req_sw_id:
                        continue

                    if len(resp) >= len(data):
                        return resp
                time.sleep(0.01)

            logger.debug("HID++ 响应超时")
            return None
        except Exception as e:
            logger.error(f"HID++ 通信错误: {e}")
            return None

    @staticmethod
    def _is_valid_percentage(value: int) -> bool:
        """校验电量百分比是否在有效范围内。"""
        return 0 <= value <= 100

    def get_feature_index(self, device_index: int, feature_id: int) -> Optional[int]:
        """
        通过 ROOT feature (0x0000) 查询指定 Feature 的 index
        """
        cache_key = (device_index << 16) | feature_id
        if cache_key in self._feature_cache:
            return self._feature_cache[cache_key]

        # Feature 0x0000, Function 0x00: getFeatureID
        # 参数: feature_id 高字节, feature_id 低字节
        response = self._send_short(
            device_index, 0x00, 0x00,
            (feature_id >> 8) & 0xFF,
            feature_id & 0xFF
        )

        if response and len(response) >= 5:
            feature_index = response[4]
            if feature_index != 0:
                self._feature_cache[cache_key] = feature_index
                logger.debug(f"Feature 0x{feature_id:04X} -> index {feature_index}")
                return feature_index

        return None

    def get_battery_unified(self, device_index: int) -> Optional[BatteryInfo]:
        """
        通过 UNIFIED_BATTERY (0x1004) 获取电池状态
        Function 0: get_capabilities
        Function 1: get_status
        """
        idx = self.get_feature_index(device_index, FEATURE_UNIFIED_BATTERY)
        if idx is None:
            return None

        # Function 0x01: get_status
        response = self._send_short(device_index, idx, 0x00)
        if response and len(response) >= 7:
            percentage = response[4]
            level = response[5]  # 1=critical, 2=low, 4=good, 8=full
            charging = response[6]

            if not self._is_valid_percentage(percentage):
                logger.debug(f"忽略异常 UNIFIED_BATTERY 百分比: {percentage}, raw={response.hex()}")
                return None
            if level & 0xF0:
                logger.debug(f"忽略异常 UNIFIED_BATTERY level: 0x{level:02X}, raw={response.hex()}")
                return None

            info = BatteryInfo()
            info.percentage = percentage
            info.charging = charging != 0
            info.source = "short:0x1004"

            if info.charging:
                info.status_text = "充电中"
            elif level & 0x08:
                info.status_text = "已充满"
            elif level & 0x04:
                info.status_text = "电量良好"
            elif level & 0x02:
                info.status_text = "电量低"
            elif level & 0x01:
                info.status_text = "电量极低"
            else:
                info.status_text = "放电中"

            logger.info(f"UNIFIED_BATTERY: {percentage}%, 充电={info.charging}")
            return info

        return None

    def get_battery_status(self, device_index: int) -> Optional[BatteryInfo]:
        """
        通过 BATTERY_STATUS (0x1000) 获取电池状态
        Function 0: get_battery_level_status
        """
        idx = self.get_feature_index(device_index, FEATURE_BATTERY_STATUS)
        if idx is None:
            return None

        response = self._send_short(device_index, idx, 0x00)
        if response and len(response) >= 7:
            percentage = response[4]
            next_percentage = response[5]
            status = response[6]

            if not self._is_valid_percentage(percentage):
                logger.debug(f"忽略异常 BATTERY_STATUS 百分比: {percentage}, raw={response.hex()}")
                return None
            if status not in (0, 1, 2, 3, 4, 5, 6):
                logger.debug(f"忽略异常 BATTERY_STATUS 状态: {status}, raw={response.hex()}")
                return None

            info = BatteryInfo()
            info.percentage = percentage
            info.source = "short:0x1000"

            # status: 0=discharging, 1=recharging, 2=almost_full,
            #         3=charged, 4=slow_recharge, 5=invalid_battery,
            #         6=thermal_error
            if status in (1, 2, 4):
                info.charging = True
                info.status_text = "充电中"
            elif status == 3:
                # 收紧判定：已充满(3)不再视为“正在充电”
                info.charging = False
                info.status_text = "已充满"
            elif status == 0:
                info.status_text = "放电中"
            else:
                info.status_text = "未知"

            logger.info(f"BATTERY_STATUS: {percentage}%, status={status}")
            return info

        return None

    def get_battery_voltage(self, device_index: int) -> Optional[BatteryInfo]:
        """
        通过 BATTERY_VOLTAGE (0x1001) 获取电池电压
        """
        idx = self.get_feature_index(device_index, FEATURE_BATTERY_VOLTAGE)
        if idx is None:
            return None

        response = self._send_short(device_index, idx, 0x00)
        if response and len(response) >= 7:
            voltage = (response[4] << 8) | response[5]
            flags = response[6]

            # Li-ion 鼠标电池电压通常在 3000~5000mV，超出判定为噪声帧
            if voltage < 3000 or voltage > 5000:
                logger.debug(f"忽略异常 BATTERY_VOLTAGE 电压: {voltage}mV, raw={response.hex()}")
                return None

            info = BatteryInfo()
            info.charging = (flags & 0x80) != 0
            info.source = "short:0x1001"

            # 电压 -> 百分比转换
            info.percentage = self._voltage_to_percent(voltage)
            info.status_text = "充电中" if info.charging else "放电中"

            logger.info(f"BATTERY_VOLTAGE: {voltage}mV -> {info.percentage}%, 充电={info.charging}")
            return info

        return None

    @staticmethod
    def _voltage_to_percent(voltage: int) -> int:
        """将电压值转换为百分比"""
        if voltage >= VOLTAGE_TO_PERCENT[0][0]:
            return 100
        if voltage <= VOLTAGE_TO_PERCENT[-1][0]:
            return 0

        for i in range(len(VOLTAGE_TO_PERCENT) - 1):
            v_high, p_high = VOLTAGE_TO_PERCENT[i]
            v_low, p_low = VOLTAGE_TO_PERCENT[i + 1]
            if voltage >= v_low:
                # 线性插值
                ratio = (voltage - v_low) / (v_high - v_low)
                return int(p_low + ratio * (p_high - p_low))

        return 0

    def get_battery(self, device_index: int = DEVICE_INDEX_FIRST) -> Optional[BatteryInfo]:
        """
        获取电池状态（依次尝试多种 Feature）
        """
        # 优先尝试 UNIFIED_BATTERY（更新的接口）
        result = self.get_battery_unified(device_index)
        if result:
            return result

        # 尝试 BATTERY_STATUS
        result = self.get_battery_status(device_index)
        if result:
            return result

        # 最后尝试 BATTERY_VOLTAGE
        result = self.get_battery_voltage(device_index)
        if result:
            return result

        logger.warning(f"无法从设备 {device_index} 获取电池信息")
        return None

    def get_device_name(self, device_index: int) -> Optional[str]:
        """通过 GET_DEVICE_NAME (0x0005) 获取真实鼠标名称"""
        idx = self.get_feature_index(device_index, 0x0005)
        if idx is None:
            return None

        # Function 0: GetCount
        response = self._send_short(device_index, idx, 0x00)
        if not response or len(response) < 5:
            return None

        name_len = response[4]
        if name_len == 0:
            return None

        name_bytes = bytearray()
        # Function 1: GetDeviceName, 参数: char_index
        for offset in range(0, name_len, 16):
            resp = self._send_long(device_index, idx, 0x01, offset)
            if resp and len(resp) >= 20:
                chunk = resp[4:20]
                name_bytes.extend(chunk)

        name_bytes = name_bytes[:name_len]
        try:
            end = name_bytes.find(0)
            if end != -1:
                name_bytes = name_bytes[:end]
            return name_bytes.decode('utf-8', errors='ignore')
        except Exception:
            return None

    def ping_device(self, device_index: int) -> bool:
        """
        快速探测设备是否在线（300ms 超时）
        只要收到任何响应（包括错误响应），就说明该槽位有设备
        """
        data = [HIDPP_SHORT_MSG, device_index, 0x00, 0x00, 0x00, 0x00, 0x00]
        
        if not self._device:
            return False
            
        try:
            self._device.write(bytes(data))
            start = time.monotonic()
            
            while (time.monotonic() - start) * 1000 < 300:
                response = self._device.read(64)
                if response:
                    resp = bytes(response)
                    if len(resp) >= 4 and resp[1] == device_index:
                        return True
                time.sleep(0.01)
        except Exception:
            pass
        return False

    def get_battery_legacy_long(self, device_index: int = DEVICE_INDEX_FIRST) -> Optional[BatteryInfo]:
        """
        长报文专用电池读取（如 G903、G502X 的部分端点）
        直接打开 usage=0x0002 的长消息通道，用长报文读取 0x1000, 0x1001, 0x1004 电压
        """
        try:
            logger.debug(
                f"{self.product_string} 开始 legacy_long 电量读取: pid=0x{self.product_id:04X} "
                f"device_index=0x{device_index:02X}"
            )
            # 枚举该接收器的所有端点，找到 usage=2 (长消息通道)
            all_devs = hid.enumerate(LOGITECH_VID, self.product_id)
            long_path = None
            for d in all_devs:
                if d.get('usage_page', 0) == 0xFF00 and d.get('usage', 0) == 0x0002:
                    long_path = d['path']
                    break
            
            if not long_path:
                logger.debug(f"{self.product_string} 未找到 usage=2 的长消息通道")
                return None
            
            # 打开独立句柄（不影响主 receiver）
            dev = hid.device()
            dev.open_path(long_path)
            dev.set_nonblocking(True)
            
            try:
                # 首先尝试发 ping 获取活动的 device_index？
                # 不需要，因为我们要试的 feature 比这个明确。
                
                # 尝试长报文获取 0x1004 (统一电池) 
                query = [HIDPP_LONG_MSG, device_index, 0x00, 0x0A, 0x10, 0x04] + [0] * 14
                logger.debug(
                    f"{self.product_string} legacy_long 尝试特性发现: feature=0x1004 query={bytes(query).hex()}"
                )
                dev.write(bytes(query))
                
                start = time.monotonic()
                feat_idx = None
                while (time.monotonic() - start) * 1000 < 500:
                    resp = dev.read(64)
                    if resp:
                        r = bytes(resp)
                        if len(r) >= 5 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG) and r[1] == device_index and r[2] == 0x00 and (r[3] & 0x0F) == 0x0A:
                            if r[4] != 0:
                                feat_idx = r[4]
                                break
                    time.sleep(0.01)
                
                if feat_idx:
                    logger.debug(f"{self.product_string} legacy_long 命中 UNIFIED_BATTERY(0x1004), feat_idx={feat_idx}")
                    # 直接读取 Function 1 (GetBatteryLevelStatus，获取精确电量百分比和充放电状态)
                    read_cmd_f1 = [HIDPP_LONG_MSG, device_index, feat_idx, 0x1A] + [0] * 16
                    logger.debug(
                        f"{self.product_string} legacy_long 读取 UNIFIED_BATTERY(F1): cmd={bytes(read_cmd_f1).hex()}"
                    )
                    dev.write(bytes(read_cmd_f1))
                    
                    start = time.monotonic()
                    while (time.monotonic() - start) * 1000 < 500:
                        resp = dev.read(64)
                        if resp:
                            r = bytes(resp)
                            if (len(r) >= 7 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG)
                                    and r[1] == device_index and r[2] == feat_idx
                                    and (r[3] & 0xF0) == 0x10 and (r[3] & 0x0F) == 0x0A):
                                info = BatteryInfo()
                                info.percentage = r[4]
                                status = r[6]
                                if not self._is_valid_percentage(info.percentage) or status not in (0, 1, 2, 3, 4, 5, 6):
                                    logger.debug(f"忽略异常 UNIFIED_BATTERY(F1) 响应: {r.hex()}")
                                    continue
                                info.charging = status in (1, 2, 3)
                                info.status_text = "充电中" if info.charging else "放电中"
                                info.source = "legacy_long:0x1004/F1"
                                logger.info(
                                    f"{self.product_string} UNIFIED_BATTERY(F1): {info.percentage}% 状态={status} "
                                    f"source=legacy_long:0x1004/F1"
                                )
                                return info
                        time.sleep(0.01)
                    logger.debug(f"{self.product_string} legacy_long 0x1004 已命中特性，但读取 F1 超时/无有效响应")
                else:
                    logger.warning(f"{self.product_string} legacy_long 未命中 0x1004，回退尝试 0x1001/0x1000")

                # 如果没拿到 0x1004，尝试长报文查询 feature 0x1001 (电压)
                query = [HIDPP_LONG_MSG, device_index, 0x00, 0x0A, 0x10, 0x01] + [0] * 14
                logger.debug(
                    f"{self.product_string} legacy_long 尝试特性发现: feature=0x1001 query={bytes(query).hex()}"
                )
                dev.write(bytes(query))
                
                start = time.monotonic()
                feat_idx = None
                while (time.monotonic() - start) * 1000 < 500:
                    resp = dev.read(64)
                    if resp:
                        r = bytes(resp)
                        if len(r) >= 5 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG) and r[1] == device_index and r[2] == 0x00 and (r[3] & 0x0F) == 0x0A:
                            if r[4] != 0:
                                feat_idx = r[4]
                                break
                    time.sleep(0.01)
                
                if feat_idx:
                    logger.debug(f"{self.product_string} legacy_long 命中 BATTERY_VOLTAGE(0x1001), feat_idx={feat_idx}")
                    # 用长报文读取电压
                    read_cmd = [HIDPP_LONG_MSG, device_index, feat_idx, 0x0A] + [0] * 16
                    logger.debug(
                        f"{self.product_string} legacy_long 读取 BATTERY_VOLTAGE: cmd={bytes(read_cmd).hex()}"
                    )
                    dev.write(bytes(read_cmd))
                    
                    start = time.monotonic()
                    while (time.monotonic() - start) * 1000 < 500:
                        resp = dev.read(64)
                        if resp:
                            r = bytes(resp)
                            if (len(r) >= 7 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG)
                                    and r[1] == device_index and r[2] == feat_idx
                                    and (r[3] & 0x0F) == 0x0A):
                                voltage = (r[4] << 8) | r[5]
                                flags = r[6]
                                if voltage < 3000 or voltage > 5000:
                                    logger.debug(f"忽略异常 BATTERY_VOLTAGE 响应: {r.hex()}")
                                    continue
                                info = BatteryInfo()
                                info.charging = (flags & 0x80) != 0
                                info.percentage = self._voltage_to_percent(voltage)
                                info.status_text = "充电中" if info.charging else "放电中"
                                info.source = "legacy_long:0x1001"
                                logger.info(
                                    f"{self.product_string} BATTERY_VOLTAGE: {voltage}mV -> {info.percentage}% "
                                    f"charging={info.charging} source=legacy_long:0x1001"
                                )
                                return info
                        time.sleep(0.01)
                    logger.debug(f"{self.product_string} legacy_long 0x1001 已命中特性，但读取电压超时/无有效响应")
                else:
                    logger.warning(f"{self.product_string} legacy_long 未命中 0x1001，继续回退尝试 0x1000")
                
                # 最后尝试 0x1000 (状态)
                query = [HIDPP_LONG_MSG, device_index, 0x00, 0x0A, 0x10, 0x00] + [0] * 14
                logger.debug(
                    f"{self.product_string} legacy_long 尝试特性发现: feature=0x1000 query={bytes(query).hex()}"
                )
                dev.write(bytes(query))
                
                start = time.monotonic()
                feat_idx = None
                while (time.monotonic() - start) * 1000 < 500:
                    resp = dev.read(64)
                    if resp:
                        r = bytes(resp)
                        if len(r) >= 5 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG) and r[1] == device_index and r[2] == 0x00 and (r[3] & 0x0F) == 0x0A:
                            if r[4] != 0:
                                feat_idx = r[4]
                                break
                    time.sleep(0.01)
                
                if feat_idx:
                    logger.debug(f"{self.product_string} legacy_long 命中 BATTERY_STATUS(0x1000), feat_idx={feat_idx}")
                    # 读取 0x1000
                    read_cmd = [HIDPP_LONG_MSG, device_index, feat_idx, 0x0A] + [0] * 16
                    logger.debug(
                        f"{self.product_string} legacy_long 读取 BATTERY_STATUS: cmd={bytes(read_cmd).hex()}"
                    )
                    dev.write(bytes(read_cmd))
                    
                    start = time.monotonic()
                    while (time.monotonic() - start) * 1000 < 500:
                        resp = dev.read(64)
                        if resp:
                            r = bytes(resp)
                            if (len(r) >= 7 and r[0] in (HIDPP_SHORT_MSG, HIDPP_LONG_MSG)
                                    and r[1] == device_index and r[2] == feat_idx
                                    and (r[3] & 0x0F) == 0x0A):
                                info = BatteryInfo()
                                info.percentage = r[4]
                                status = r[6]
                                if not self._is_valid_percentage(info.percentage) or status not in (0, 1, 2, 3, 4, 5, 6):
                                    logger.debug(f"忽略异常 BATTERY_STATUS 响应: {r.hex()}")
                                    continue
                                # 收紧判定：status=3(已充满)不再视为“正在充电”
                                info.charging = status in (1, 2, 4)
                                if status == 3:
                                    info.status_text = "已充满"
                                else:
                                    info.status_text = "充电中" if info.charging else "放电中"
                                info.source = "legacy_long:0x1000"
                                logger.info(
                                    f"{self.product_string} BATTERY_STATUS: {info.percentage}% status={status} "
                                    f"charging={info.charging} source=legacy_long:0x1000"
                                )
                                return info
                        time.sleep(0.01)
                    logger.debug(f"{self.product_string} legacy_long 0x1000 已命中特性，但读取状态超时/无有效响应")

                logger.debug(f"{self.product_string}: 所有已知电量读取尝试均失败或超时")
                return None
            finally:
                dev.close()
        except Exception as e:
            logger.warning(f"{self.product_string} 电池读取发生异常: {e}")
            return None


def find_logitech_receivers() -> list[dict]:
    """
    扫描所有已连接的罗技 Lightspeed/Unifying 接收器
    返回适合打开的 HID 设备信息列表
    """
    receivers = []
    seen_paths = set()

    try:
        all_devices = hid.enumerate(LOGITECH_VID, 0)
    except Exception as e:
        logger.error(f"枚举 HID 设备失败: {e}")
        return []

    for dev in all_devices:
        pid = dev['product_id']
        path = dev['path']

        if pid not in LIGHTSPEED_RECEIVER_PIDS:
            continue
        if path in seen_paths:
            continue

        # 选择 usage_page=1 (Generic Desktop) 或 usage_page=0xFF00 (Vendor Defined)
        # HID++ 通常在 usage_page=0xFF00 上，或者直接使用第一个接口
        usage_page = dev.get('usage_page', 0)
        usage = dev.get('usage', 0)

        # 我们需要能收发 HID++ 报文的接口
        # 优先选择 usage_page=0xFF00 (vendor-defined), usage=1
        # 或 usage_page=1, usage=2
        if usage_page in (0xFF00, 0x0001) or usage_page == 0:
            seen_paths.add(path)
            receivers.append(dev)
            logger.debug(
                f"发现罗技接收器: PID=0x{pid:04X}, "
                f"usage_page=0x{usage_page:04X}, usage=0x{usage:02X}, "
                f"interface={dev.get('interface_number', -1)}"
            )

    # 按 PID + interface_number 去重，优先选择 usage_page=0xFF00 的
    filtered = {}
    for dev in receivers:
        key = dev['product_id']
        existing = filtered.get(key)
        if existing is None:
            filtered[key] = dev
        else:
            # 优先选 usage_page=0xFF00 且 usage=1 的短消息通道
            # 因为 get_battery() 发的是短报文 (0x10)
            dev_page = dev.get('usage_page', 0)
            dev_usage = dev.get('usage', 0)
            exist_page = existing.get('usage_page', 0)
            exist_usage = existing.get('usage', 0)
            
            if dev_page == 0xFF00:
                if exist_page != 0xFF00:
                    # 新的是 0xFF00，旧的不是，替换
                    filtered[key] = dev
                elif dev_usage == 0x0001 and exist_usage != 0x0001:
                    # 新的是 usage=1 (短消息)，旧的不是，替换
                    filtered[key] = dev

    return list(filtered.values())
