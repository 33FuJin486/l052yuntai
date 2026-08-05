"""配置读取、校验与原子保存。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from .resource_path import portable_path, resource_path


DEFAULT_SETTINGS: dict[str, Any] = {
    "serial_port": "",
    "baudrate": 115200,
    "camera_index": 0,
    "frame_width": 640,
    "frame_height": 480,
    "model_path": "model/best.pt",
    "confidence": 0.5,
    "inference_size": 640,
    "target_classes": [0],
    "send_frequency": 30,
}


class ConfigManager:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else resource_path("config/settings.json")
        self.last_warning = ""

    def load(self) -> dict[str, Any]:
        settings = deepcopy(DEFAULT_SETTINGS)
        self.last_warning = ""
        if not self.path.exists():
            return settings

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("配置根节点必须是 JSON 对象")
            settings.update(raw)
            return self.validate(settings)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self.last_warning = f"配置文件损坏或不可读，已使用默认值：{exc}"
            return deepcopy(DEFAULT_SETTINGS)

    def validate(self, settings: dict[str, Any]) -> dict[str, Any]:
        clean = deepcopy(DEFAULT_SETTINGS)
        clean["serial_port"] = str(settings.get("serial_port") or "")
        clean["baudrate"] = _bounded_int(settings.get("baudrate"), 1200, 4_000_000, 115200)
        clean["camera_index"] = _bounded_int(settings.get("camera_index"), 0, 99, 0)
        clean["frame_width"] = _bounded_int(settings.get("frame_width"), 160, 7680, 640)
        clean["frame_height"] = _bounded_int(settings.get("frame_height"), 120, 4320, 480)
        clean["model_path"] = str(settings.get("model_path") or "model/best.pt")
        clean["confidence"] = _bounded_float(settings.get("confidence"), 0.01, 1.0, 0.5)
        clean["inference_size"] = _bounded_int(settings.get("inference_size"), 32, 4096, 640)
        clean["target_classes"] = _target_classes(settings.get("target_classes"))
        clean["send_frequency"] = _bounded_int(settings.get("send_frequency"), 1, 100, 30)
        return clean

    def save(self, settings: dict[str, Any]) -> None:
        clean = self.validate(settings)
        clean["model_path"] = portable_path(resource_path(clean["model_path"]))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def _bounded_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def _bounded_float(value: Any, low: float, high: float, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if low <= parsed <= high else default


def _target_classes(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return deepcopy(DEFAULT_SETTINGS["target_classes"])
    try:
        parsed = [int(item) for item in value]
    except (TypeError, ValueError):
        return deepcopy(DEFAULT_SETTINGS["target_classes"])
    if any(item < 0 for item in parsed):
        return deepcopy(DEFAULT_SETTINGS["target_classes"])
    return parsed
