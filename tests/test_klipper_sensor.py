# Klipper-backed generic sensor unit tests
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moonraker.components.sensor import KlipperSensor, Sensors


class KlippyAPI:
    def __init__(self):
        self.callbacks = []
        self.status = {"temperature_sensor _voltage": {"temperature": 24.27}}

    async def subscribe_objects(self, objects, callback=None, default=None):
        self.objects = objects
        if self.status is not None and callback is not None:
            self.callbacks.append(callback)
        return self.status


class TestKlipperSensor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = KlippyAPI()
        self.server = Mock()
        self.server.lookup_component.side_effect = lambda name: (
            self.api if name == "klippy_apis" else Mock()
        )
        self.options = {"type": "klipper", "object": "temperature_sensor _voltage",
                        "parameter_voltage": {"units": "V"}}
        config = Mock()
        config.get_server.return_value = self.server
        config.get_name.return_value = "sensor voltage"
        config.get.side_effect = lambda name, default=None: self.options.get(name, default)
        config.getint.side_effect = lambda name, default: default
        config.get_options.return_value = self.options
        config.getdict.side_effect = lambda name: dict(self.options[name])
        self.sensor = KlipperSensor(config)

    async def test_initial_subscription_and_units(self):
        await self.sensor._on_klippy_ready()
        self.assertEqual(self.sensor.last_measurements, {"voltage": 24.27})
        self.assertEqual(self.api.objects, {"temperature_sensor _voltage": ["temperature"]})
        self.assertEqual(self.sensor.param_info, [{"name": "voltage", "units": "V"}])
        self.sensor._update_sensor_value(0.)
        self.assertEqual(self.sensor.get_sensor_measurements(), {"voltage": [24.27]})

    async def test_reconnect_restores_callback_without_duplicates(self):
        await self.sensor._on_klippy_ready()
        await self.sensor._on_klippy_ready()
        self.assertEqual(len(self.api.callbacks), 1)
        self.api.callbacks.clear()  # Real KlippyAPI disconnect behavior.
        self.sensor._on_klippy_disconnect()
        self.assertEqual(self.sensor.last_measurements, {})
        await self.sensor._on_klippy_ready()
        self.assertEqual(len(self.api.callbacks), 1)
        self.api.callbacks[0]({self.sensor.object: {"temperature": 23.5}}, 1.)
        self.assertEqual(self.sensor.last_measurements, {"voltage": 23.5})

    async def test_subscription_failure_can_retry(self):
        self.api.status = None
        await self.sensor._on_klippy_ready()
        self.assertFalse(self.sensor._subscribed)
        self.api.status = {self.sensor.object: {"temperature": 24}}
        await self.sensor._on_klippy_ready()
        self.assertEqual(len(self.api.callbacks), 1)
        self.assertIsNone(self.sensor.error_state)

    async def test_missing_object(self):
        self.api.status = {}
        await self.sensor._on_klippy_ready()
        self.assertEqual(self.sensor.last_measurements, {})
        self.assertIn("unavailable", self.sensor.error_state)

    async def test_partial_updates(self):
        await self.sensor._on_klippy_ready()
        self.sensor._on_status({"toolhead": {"position": [0, 0, 0]}}, 1.)
        self.sensor._on_status({self.sensor.object: {"other": 0}}, 1.)
        self.assertEqual(self.sensor.last_measurements, {"voltage": 24.27})

    async def test_invalid_values(self):
        for value in (True, False, None, "24", float("nan"), float("inf"), []):
            self.sensor._on_status({self.sensor.object: {"temperature": value}}, 1.)
            self.assertEqual(self.sensor.last_measurements, {})
            self.assertIsNotNone(self.sensor.error_state)

    async def test_custom_parameter_and_history_tracker(self):
        tracker = Mock()
        self.sensor.parameter = "current"
        self.sensor.field_info = {"current": [Mock(tracker=tracker)]}
        self.sensor._on_status({self.sensor.object: {"temperature": 0.125}}, 1.)
        self.assertEqual(self.sensor.last_measurements, {"current": 0.125})
        tracker.update.assert_called_once_with(0.125)

    def test_registered_native_type(self):
        self.assertIs(Sensors._Sensors__sensor_types["KLIPPER"], KlipperSensor)


if __name__ == "__main__":
    unittest.main()
