from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config_manager import ConfigManager, DEFAULT_SETTINGS


class ConfigManagerTests(unittest.TestCase):
    def test_missing_file_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manager = ConfigManager(Path(directory) / "settings.json")
            self.assertEqual(manager.load(), DEFAULT_SETTINGS)

    def test_corrupt_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{broken", encoding="utf-8")
            manager = ConfigManager(path)
            self.assertEqual(manager.load(), DEFAULT_SETTINGS)
            self.assertIn("配置文件损坏", manager.last_warning)

    def test_invalid_values_fall_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(
                json.dumps(
                    {
                        "confidence": 9,
                        "send_frequency": 0,
                        "camera_index": -2,
                        "target_classes": [-1],
                    }
                ),
                encoding="utf-8",
            )
            loaded = ConfigManager(path).load()
            self.assertEqual(loaded["confidence"], 0.5)
            self.assertEqual(loaded["send_frequency"], 30)
            self.assertEqual(loaded["camera_index"], 0)
            self.assertEqual(loaded["target_classes"], [0])

    def test_save_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            manager = ConfigManager(path)
            settings = dict(DEFAULT_SETTINGS)
            settings["serial_port"] = "COM8"
            settings["target_classes"] = None
            manager.save(settings)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["serial_port"], "COM8")
            self.assertIsNone(saved["target_classes"])


if __name__ == "__main__":
    unittest.main()
