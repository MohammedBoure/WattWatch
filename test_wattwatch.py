#!/usr/bin/env python3
"""
Unit and Integration Tests for WattWatch (power_logger & powertool)
"""

import os
import sys
import unittest
import tempfile
from datetime import datetime, timedelta

# Import modules to test
import power_logger
import powertool


class TestPowerCalculation(unittest.TestCase):
    """Test power and energy calculations including counter overflow handling."""

    def test_normal_power_calculation(self):
        last_uj = 10_000_000
        curr_uj = 25_000_000  # delta = 15,000,000 uJ = 15 J
        delta_sec = 2.0
        max_range = 65_532_610_987

        watts = power_logger.compute_watts(curr_uj, last_uj, delta_sec, max_range)
        self.assertAlmostEqual(watts, 7.5, places=3)

    def test_counter_wraparound(self):
        max_range = 65_532_610_987
        last_uj = 65_530_000_000  # close to max
        curr_uj = 5_000_000       # wrapped past 0
        delta_sec = 2.5

        # delta_uj = (65532610987 - 65530000000) + 5000000 = 2610987 + 5000000 = 7610987 uJ
        # watts = 7.610987 / 2.5 = 3.0443948 W
        expected_delta_uj = (max_range - last_uj) + curr_uj
        expected_watts = (expected_delta_uj / 1_000_000.0) / delta_sec

        watts_logger = power_logger.compute_watts(curr_uj, last_uj, delta_sec, max_range)
        watts_tool = powertool.compute_watts(curr_uj, last_uj, delta_sec, max_range)

        self.assertAlmostEqual(watts_logger, expected_watts, places=4)
        self.assertAlmostEqual(watts_tool, expected_watts, places=4)

    def test_zero_delta_time(self):
        watts = power_logger.compute_watts(20_000, 10_000, 0.0, 65_000_000_000)
        self.assertEqual(watts, 0.0)


class TestHistoricalMetrics(unittest.TestCase):
    """Test CSV log parsing and historical range metric calculations."""

    def setUp(self):
        self.base_time = datetime(2026, 10, 10, 12, 0, 0)
        self.sample_records = [
            (self.base_time + timedelta(seconds=0), 10.0),
            (self.base_time + timedelta(seconds=2), 15.0),
            (self.base_time + timedelta(seconds=4), 20.0),
            (self.base_time + timedelta(seconds=6), 25.0),
            (self.base_time + timedelta(seconds=8), 30.0),
        ]

    def test_calculate_range_metrics_exact_formulas(self):
        start_dt = self.base_time
        end_dt = self.base_time + timedelta(seconds=8)

        metrics = powertool.calculate_range_metrics(self.sample_records, start_dt, end_dt)
        self.assertIsNotNone(metrics)

        # Average watts = (10 + 15 + 20 + 25 + 30) / 5 = 20.0 W
        self.assertEqual(metrics["num_samples"], 5)
        self.assertAlmostEqual(metrics["avg_watts"], 20.0, places=3)

        # Total duration = 8.0 seconds
        self.assertEqual(metrics["total_seconds"], 8.0)
        self.assertEqual(metrics["duration_minutes"], 0)
        self.assertEqual(metrics["duration_seconds"], 8)

        # Total Joules = Watts * TotalSeconds = 20.0 * 8.0 = 160.0 J
        self.assertAlmostEqual(metrics["total_joules"], 160.0, places=3)

        # Total kWh = Joules / 3,600,000 = 160 / 3,600,000 = 0.0000444... kWh
        expected_kwh = 160.0 / 3_600_000.0
        self.assertAlmostEqual(metrics["total_kwh"], expected_kwh, places=8)

    def test_range_filtering(self):
        # Query only middle range: seconds 2 to 6
        start_dt = self.base_time + timedelta(seconds=2)
        end_dt = self.base_time + timedelta(seconds=6)

        metrics = powertool.calculate_range_metrics(self.sample_records, start_dt, end_dt)
        self.assertIsNotNone(metrics)
        self.assertEqual(metrics["num_samples"], 3)
        # avg of 15, 20, 25 = 20.0 W
        self.assertAlmostEqual(metrics["avg_watts"], 20.0)
        # duration = 6 - 2 = 4 seconds
        self.assertEqual(metrics["total_seconds"], 4.0)
        # Joules = 20.0 * 4.0 = 80.0 J
        self.assertAlmostEqual(metrics["total_joules"], 80.0)

    def test_empty_range_returns_none(self):
        start_dt = self.base_time + timedelta(seconds=100)
        end_dt = self.base_time + timedelta(seconds=200)

        metrics = powertool.calculate_range_metrics(self.sample_records, start_dt, end_dt)
        self.assertIsNone(metrics)

    def test_csv_parser_with_temporary_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("header,timestamp,power\n")  # Header to ignore
            tf.write("2026-10-10 12:00:00,12.50\n")
            tf.write("\n")  # Empty line
            tf.write("invalid line without comma\n")
            tf.write("2026-10-10 12:00:02,18.75\n")
            tf.flush()
            temp_path = tf.name

        try:
            records = powertool.parse_log_file(temp_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0][0], datetime(2026, 10, 10, 12, 0, 0))
            self.assertEqual(records[0][1], 12.50)
            self.assertEqual(records[1][0], datetime(2026, 10, 10, 12, 0, 2))
            self.assertEqual(records[1][1], 18.75)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


class TestFullDevicePower(unittest.TestCase):
    """Test full-device power discovery, calculation, and formatting."""

    def _write_file(self, directory: str, name: str, content: str):
        with open(os.path.join(directory, name), "w", encoding="utf-8") as f:
            f.write(content)

    def test_battery_discharging_via_current_and_voltage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bat_dir = os.path.join(tmpdir, "BAT1")
            os.makedirs(bat_dir)
            self._write_file(bat_dir, "type", "Battery\n")
            self._write_file(bat_dir, "present", "1\n")
            self._write_file(bat_dir, "status", "Discharging\n")
            self._write_file(bat_dir, "current_now", "2000000\n")  # 2.0 A
            self._write_file(bat_dir, "voltage_now", "12000000\n")  # 12.0 V
            self._write_file(bat_dir, "capacity", "75\n")

            info = powertool.read_power_supply_info(tmpdir)
            self.assertEqual(info["battery_status"], "Discharging")
            self.assertAlmostEqual(info["battery_watts"], 24.0, places=3)

            total_watts, label = powertool.resolve_device_power(8.5, info)
            self.assertAlmostEqual(total_watts, 24.0, places=3)
            self.assertEqual(label, "BAT1 75%")

            line = powertool.format_live_status_line(8.5, total_watts, label, "10:00:00", 1)
            self.assertIn("Device Total:  24.00 W", line)
            self.assertIn("CPU:  8.50 W", line)
            self.assertIn("Other: 15.50 W", line)

    def test_battery_discharging_via_power_now(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bat_dir = os.path.join(tmpdir, "BAT0")
            os.makedirs(bat_dir)
            self._write_file(bat_dir, "type", "Battery\n")
            self._write_file(bat_dir, "present", "1\n")
            self._write_file(bat_dir, "status", "Discharging\n")
            self._write_file(bat_dir, "power_now", "18500000\n")  # 18.5 W

            info = powertool.read_power_supply_info(tmpdir)
            self.assertAlmostEqual(info["battery_watts"], 18.5, places=3)
            total_watts, _ = powertool.resolve_device_power(5.0, info)
            self.assertAlmostEqual(total_watts, 18.5, places=3)

    def test_ac_charging_does_not_report_charge_rate_as_device_draw(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ac_dir = os.path.join(tmpdir, "ACAD")
            bat_dir = os.path.join(tmpdir, "BAT1")
            os.makedirs(ac_dir)
            os.makedirs(bat_dir)
            self._write_file(ac_dir, "type", "Mains\n")
            self._write_file(ac_dir, "online", "1\n")
            self._write_file(bat_dir, "type", "Battery\n")
            self._write_file(bat_dir, "present", "1\n")
            self._write_file(bat_dir, "status", "Charging\n")
            self._write_file(bat_dir, "current_now", "2000000\n")
            self._write_file(bat_dir, "voltage_now", "12000000\n")
            self._write_file(bat_dir, "capacity", "80\n")

            info = powertool.read_power_supply_info(tmpdir)
            total_watts, label = powertool.resolve_device_power(6.0, info, psys_watts=None)
            self.assertIsNone(total_watts)
            self.assertIn("On AC", label)
            self.assertIn("Charging 24.00W", label)

            # If psys is available on AC, it should be used as total device power
            total_psys, psys_label = powertool.resolve_device_power(6.0, info, psys_watts=21.25)
            self.assertAlmostEqual(total_psys, 21.25, places=3)
            self.assertEqual(psys_label, "psys")


class TestHardwareInterface(unittest.TestCase):
    """Verify live hardware reading on the current host."""

    def test_sysfs_energy_read(self):
        energy = powertool.read_energy_uj()
        self.assertIsInstance(energy, int)
        self.assertGreater(energy, 0)

    def test_sysfs_max_range_read(self):
        max_range = powertool.read_max_energy_range_uj()
        self.assertIsInstance(max_range, int)
        self.assertGreater(max_range, 1_000_000)


if __name__ == "__main__":
    unittest.main()
