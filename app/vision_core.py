"""不依赖 OpenCV/Qt/YOLO 的视觉核心算法。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DetectionCandidate:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    class_id: int


@dataclass(frozen=True)
class SelectedTarget:
    center_x: int
    center_y: int
    error_x: int
    error_y: int
    confidence: float
    class_id: int
    distance: float


def select_closest_target(
    candidates: Iterable[DetectionCandidate],
    frame_width: int,
    frame_height: int,
) -> SelectedTarget | None:
    """复现原程序：选择中心点距画面中心最近的检测框。

    检测框中心先转换为整数；X 向右为正，Y 向上为正。
    距离相同时保留结果中的第一个检测框。
    """
    frame_center_x = frame_width // 2
    frame_center_y = frame_height // 2
    selected: SelectedTarget | None = None

    for item in candidates:
        center_x = int((item.x1 + item.x2) / 2)
        center_y = int((item.y1 + item.y2) / 2)
        distance = math.hypot(center_x - frame_center_x, center_y - frame_center_y)
        if selected is None or distance < selected.distance:
            selected = SelectedTarget(
                center_x=center_x,
                center_y=center_y,
                error_x=center_x - frame_center_x,
                error_y=frame_center_y - center_y,
                confidence=float(item.confidence),
                class_id=int(item.class_id),
                distance=distance,
            )

    return selected
