import json
import os
import sys
import tempfile
import threading
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

    def test_bind_publishes_binding_state_before_hardware_probe(self):
        manager = devices.DeviceManager.__new__(devices.DeviceManager)
        manager._lock = threading.Lock()
        manager._io_lock = threading.Lock()
        manager._bluetooth_candidates = [BluetoothCandidate('device-1', 'BLE Mouse', connected=True)]
        manager._bluetooth_devices = []
        manager._bluetooth_scan_state = 'ready'
        manager._bluetooth_scan_message = ''
        manager.config_manager = mock.Mock()
        manager.config_manager.bluetooth_bindings = []
        manager._notify_update = mock.Mock()

        def probe(candidate):
            self.assertEqual(manager.bluetooth_scan_state, 'binding')
            self.assertIn(candidate.name, manager.bluetooth_scan_message)
            return BluetoothInfo(candidate.device_id, candidate.name, percentage=55, online=True)

        with mock.patch.object(devices, 'probe_bluetooth_candidate', side_effect=probe):
            manager._bind_bluetooth('device-1', request_id=123)

        self.assertEqual(manager.bluetooth_scan_state, 'bound')
        self.assertEqual(manager._bluetooth_request_id, 123)
        self.assertEqual(manager._notify_update.call_count, 2)

    def test_gui_keeps_loading_until_matching_tray_response(self):
        with mock.patch.dict(sys.modules, {'flet': mock.Mock()}):
            import gui

        app = gui.MouseBatteryApp.__new__(gui.MouseBatteryApp)
        app._bluetooth_dialog = mock.Mock(open=True)
        app._bluetooth_bind_action = mock.Mock()
        app._bluetooth_dialog_loading = True
        app._bluetooth_pending_request_id = 200
        app._bluetooth_selected_device_id = 'device-1'
        app.device_manager = mock.Mock(bluetooth_request_id=100)
        app._bluetooth_scan_state = mock.Mock(return_value=('ready', '旧扫描结果'))
        app._bluetooth_candidates_snapshot = mock.Mock(return_value=[
            BluetoothCandidate('device-1', 'BLE Mouse', connected=True),
        ])
        app._bluetooth_devices_snapshot = mock.Mock(return_value=[])
        app._build_bluetooth_dialog_content = mock.Mock(return_value=mock.Mock())

        app._refresh_bluetooth_dialog()

        self.assertTrue(app._bluetooth_dialog_loading)
        self.assertEqual(app._bluetooth_pending_request_id, 200)
        self.assertTrue(app._bluetooth_bind_action.disabled)


class BluetoothTrayTests(unittest.TestCase):
    def test_lowest_battery_priority_includes_bluetooth_devices(self):
        tray_icon = tray.TrayApp.__new__(tray.TrayApp)
        tray_icon.config_manager = mock.Mock(tray_icon_priority=TRAY_ICON_PRIORITY_LOWEST_BATTERY)
        bluetooth = [BluetoothInfo('ble-1', 'BLE Mouse', percentage=12, online=True)]
        selected = tray_icon._select_icon_target([], None, bluetooth)
        self.assertEqual(selected['percentage'], 12)


if __name__ == '__main__':
    unittest.main()
