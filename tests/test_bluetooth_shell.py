import json
import os
import tempfile
import unittest
from unittest import mock

import config
import devices
import tray
from core_bridge import BluetoothCandidate, BluetoothInfo
from config import TRAY_ICON_PRIORITY_LOWEST_BATTERY


class BluetoothConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = os.path.join(self.temp_dir.name, 'config.json')
        self.path_patch = mock.patch.object(config, 'CONFIG_FILE', self.config_path)
        self.path_patch.start()
        self.update_patch = mock.patch.object(config.updater, 'clean_old_version')
        self.update_patch.start()

    def tearDown(self):
        self.update_patch.stop()
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_old_config_defaults_to_empty_bluetooth_bindings(self):
        with open(self.config_path, 'w', encoding='utf-8') as file:
            json.dump({'low_battery_notify': 10}, file)
        manager = config.ConfigManager()
        self.assertEqual(manager.bluetooth_bindings, [])

    def test_bindings_are_deduplicated_and_removable(self):
        manager = config.ConfigManager()
        self.assertTrue(manager.add_bluetooth_binding({'device_id': 'id-1', 'name': 'Mouse'}))
        self.assertFalse(manager.add_bluetooth_binding({'device_id': 'id-1', 'name': 'Mouse Again'}))
        self.assertEqual(manager.bluetooth_bindings, [{'device_id': 'id-1', 'name': 'Mouse'}])
        self.assertTrue(manager.remove_bluetooth_binding('id-1'))
        self.assertEqual(manager.bluetooth_bindings, [])


class BluetoothSharedStateTests(unittest.TestCase):
    def test_state_round_trip(self):
        state = BluetoothInfo('device-1', 'BLE Mouse', percentage=67, online=True, status_text='已连接')
        restored = devices._deserialize_bluetooth_state(devices._serialize_bluetooth_state(state))
        self.assertEqual(restored.device_id, state.device_id)
        self.assertEqual(restored.percentage, 67)
        self.assertTrue(restored.online)

    def test_candidate_round_trip_preserves_connection_status(self):
        candidate = BluetoothCandidate('device-1', 'BLE Mouse', connected=True)
        restored = devices._deserialize_bluetooth_candidate(devices._serialize_bluetooth_candidate(candidate))
        self.assertTrue(restored.connected)


class BluetoothTrayTests(unittest.TestCase):
    def test_lowest_battery_priority_includes_bluetooth_devices(self):
        tray_icon = tray.TrayApp.__new__(tray.TrayApp)
        tray_icon.config_manager = mock.Mock(tray_icon_priority=TRAY_ICON_PRIORITY_LOWEST_BATTERY)
        bluetooth = [BluetoothInfo('ble-1', 'BLE Mouse', percentage=12, online=True)]
        selected = tray_icon._select_icon_target([], None, bluetooth)
        self.assertEqual(selected['percentage'], 12)


if __name__ == '__main__':
    unittest.main()
