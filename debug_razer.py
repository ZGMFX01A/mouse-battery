"""
雷蛇设备诊断脚本 - 枚举所有接口并尝试通信
"""
import hid
import time
import struct

RAZER_VID = 0x1532
TARGET_PID = 0x00CD  # 巴塞利斯蛇 V3 Pro 无线 Dongle
REPORT_LEN = 90

def calculate_crc(data: bytes) -> int:
    crc = 0
    for i in range(2, 88):
        crc ^= data[i]
    return crc

def build_battery_report(transaction_id=0x1F):
    report = bytearray(REPORT_LEN)
    report[0] = 0x00  # status: new
    report[1] = transaction_id
    report[2] = 0x00  # remaining_packets high
    report[3] = 0x00  # remaining_packets low
    report[4] = 0x00  # protocol_type
    report[5] = 0x02  # data_size
    report[6] = 0x07  # command_class
    report[7] = 0x80  # command_id (get battery level)
    report[88] = calculate_crc(bytes(report))
    report[89] = 0x00
    return bytes(report)

def build_charging_report(transaction_id=0x1F):
    report = bytearray(REPORT_LEN)
    report[0] = 0x00
    report[1] = transaction_id
    report[2] = 0x00
    report[3] = 0x00
    report[4] = 0x00
    report[5] = 0x02
    report[6] = 0x07
    report[7] = 0x84  # get charging status
    report[88] = calculate_crc(bytes(report))
    report[89] = 0x00
    return bytes(report)

# 枚举所有 Razer 设备接口
print("=" * 70)
print("枚举所有 Razer 0x00CD 设备接口")
print("=" * 70)

all_devs = hid.enumerate(RAZER_VID, TARGET_PID)
for i, dev in enumerate(all_devs):
    print(f"\n--- 接口 #{i} ---")
    print(f"  path:             {dev['path']}")
    print(f"  interface_number: {dev['interface_number']}")
    print(f"  usage_page:       0x{dev.get('usage_page', 0):04X}")
    print(f"  usage:            0x{dev.get('usage', 0):04X}")
    print(f"  product_string:   {dev.get('product_string', '')}")
    print(f"  manufacturer:     {dev.get('manufacturer_string', '')}")

# 尝试每个接口
print("\n" + "=" * 70)
print("尝试通过每个接口发送电池查询")
print("=" * 70)

battery_report = build_battery_report(0x1F)

for i, dev_info in enumerate(all_devs):
    iface = dev_info['interface_number']
    usage_page = dev_info.get('usage_page', 0)
    print(f"\n--- 尝试接口 #{i} (interface={iface}, usage_page=0x{usage_page:04X}) ---")

    d = hid.device()
    try:
        d.open_path(dev_info['path'])
        d.set_nonblocking(True)

        # 方式1: send_feature_report
        print("  [方式1] send_feature_report + get_feature_report")
        try:
            sent = d.send_feature_report(b'\x00' + battery_report)
            print(f"    发送: {sent} bytes")
            time.sleep(0.08)

            resp = d.get_feature_report(0x00, REPORT_LEN + 1)
            if resp:
                resp_bytes = bytes(resp)
                print(f"    响应长度: {len(resp_bytes)} bytes")
                print(f"    响应数据(前16字节): {resp_bytes[:16].hex(' ')}")
                if len(resp_bytes) > 1:
                    # 去掉 report_id
                    data = resp_bytes[1:] if len(resp_bytes) == REPORT_LEN + 1 else resp_bytes
                    status = data[0]
                    tid = data[1]
                    data_size = data[5]
                    cmd_class = data[6]
                    cmd_id = data[7]
                    print(f"    status: 0x{status:02X}, tid: 0x{tid:02X}, data_size: {data_size}")
                    print(f"    cmd_class: 0x{cmd_class:02X}, cmd_id: 0x{cmd_id:02X}")
                    print(f"    args[0-3]: {data[8:12].hex(' ')}")
                    if status == 0x02 and cmd_id == 0x80:
                        level = data[9]
                        if level > 100:
                            level = int(level / 255 * 100)
                        print(f"    *** 电量: {level}% ***")
            else:
                print("    响应: None")
        except Exception as e:
            print(f"    feature_report 失败: {e}")

        # 方式2: write + read
        print("  [方式2] write + read")
        try:
            sent = d.write(b'\x00' + battery_report)
            print(f"    发送: {sent} bytes")
            time.sleep(0.08)

            resp = d.read(REPORT_LEN + 1, timeout_ms=1000)
            if resp:
                resp_bytes = bytes(resp)
                print(f"    响应长度: {len(resp_bytes)} bytes")
                print(f"    响应(前16字节): {resp_bytes[:16].hex(' ')}")
            else:
                print("    响应: 无数据")
        except Exception as e:
            print(f"    write/read 失败: {e}")

        d.close()
    except Exception as e:
        print(f"  打开失败: {e}")
        try:
            d.close()
        except:
            pass

# 尝试不同 transaction_id
print("\n" + "=" * 70)
print("尝试不同 transaction_id (使用第一个可用接口)")
print("=" * 70)

if all_devs:
    # 选择 interface 0 或第一个
    chosen = all_devs[0]
    for dev_info in all_devs:
        if dev_info['interface_number'] == 0:
            chosen = dev_info
            break

    d = hid.device()
    try:
        d.open_path(chosen['path'])
        d.set_nonblocking(True)

        for tid in [0x1F, 0xFF, 0x3F, 0x00, 0x20]:
            report = build_battery_report(tid)
            print(f"\n  transaction_id=0x{tid:02X}:")
            try:
                d.send_feature_report(b'\x00' + report)
                time.sleep(0.1)
                resp = d.get_feature_report(0x00, REPORT_LEN + 1)
                if resp:
                    resp_bytes = bytes(resp)
                    data = resp_bytes[1:] if len(resp_bytes) == REPORT_LEN + 1 else resp_bytes
                    if len(data) >= 10:
                        print(f"    status=0x{data[0]:02X} tid=0x{data[1]:02X} cmd=0x{data[6]:02X}:0x{data[7]:02X} args={data[8:12].hex(' ')}")
                        if data[0] == 0x02:
                            level = data[9]
                            if level > 100:
                                level = int(level / 255 * 100)
                            print(f"    *** 成功! 电量: {level}% ***")
                    else:
                        print(f"    响应太短: {len(data)} bytes")
                else:
                    print("    无响应")
            except Exception as e:
                print(f"    失败: {e}")

        d.close()
    except Exception as e:
        print(f"  打开设备失败: {e}")

print("\n诊断完成。")
