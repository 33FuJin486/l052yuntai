"""摄像头读取、YOLO 推理、目标选择与画面标注工作线程。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import torch
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage
from ultralytics import YOLO

from .vision_core import DetectionCandidate, select_closest_target


class VisionWorker(QObject):
    frame_ready = Signal(QImage)
    metrics_ready = Signal(object)
    model_state = Signal(bool, str, object)
    camera_state = Signal(bool, str)
    recognition_state = Signal(bool)
    device_ready = Signal(str)
    error_occurred = Signal(str)
    log_message = Signal(str)

    CAMERA_FAIL_LIMIT = 10

    def __init__(self) -> None:
        super().__init__()
        self._timer: QTimer | None = None
        self._model: YOLO | None = None
        self._model_path = ""
        self._cap: cv2.VideoCapture | None = None
        self._recognizing = False
        self._confidence = 0.5
        self._inference_size = 640
        self._target_classes: list[int] | None = [0]
        self._camera_fail_count = 0
        self._fps_start = time.perf_counter()
        self._fps_count = 0
        self._current_fps = 0.0
        self._device_arg: int | str = "cpu"

    @Slot()
    def initialize(self) -> None:
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._process_frame)

        if torch.cuda.is_available():
            self._device_arg = 0
            device_text = f"CUDA: {torch.cuda.get_device_name(0)}"
        else:
            self._device_arg = "cpu"
            device_text = "CPU"
        self.device_ready.emit(device_text)
        self.log_message.emit(f"推理设备：{device_text}")

    @Slot(str, float, int, object)
    def load_model(
        self,
        model_path: str,
        confidence: float,
        inference_size: int,
        target_classes: list[int] | None,
    ) -> None:
        if self._recognizing:
            self.error_occurred.emit("请先停止识别，再切换模型")
            return

        path = Path(model_path)
        if not path.exists():
            message = f"模型文件不存在：{path}"
            self.model_state.emit(False, message, {})
            self.error_occurred.emit(message)
            return

        try:
            model = YOLO(str(path))
            names = model.names
            self._validate_target_classes(names, target_classes)
        except Exception as exc:
            message = f"模型加载失败：{exc}"
            self.model_state.emit(False, message, {})
            self.error_occurred.emit(message)
            return

        self._model = model
        self._model_path = str(path)
        self._confidence = float(confidence)
        self._inference_size = int(inference_size)
        self._target_classes = target_classes
        message = f"模型加载成功：{path.name}"
        self.model_state.emit(True, message, names)
        self.log_message.emit(message)

    @Slot(int, int, int)
    def open_camera(self, camera_index: int, frame_width: int, frame_height: int) -> None:
        if self._recognizing:
            self.stop_recognition()
        self._release_camera()

        cap = cv2.VideoCapture(int(camera_index))
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(frame_width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(frame_height))
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            cap.release()
            message = f"无法打开编号为 {camera_index} 的摄像头，可能不存在或被占用"
            self.camera_state.emit(False, message)
            self.error_occurred.emit(message)
            return

        self._cap = cap
        self._camera_fail_count = 0
        actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        message = f"摄像头 {camera_index} 已打开：{actual_width}×{actual_height}"
        self.camera_state.emit(True, message)
        self.log_message.emit(message)

    @Slot()
    def close_camera(self) -> None:
        self.stop_recognition()
        self._release_camera()
        self.camera_state.emit(False, "摄像头已关闭")
        self.log_message.emit("摄像头已关闭")

    @Slot()
    def start_recognition(self) -> None:
        if self._recognizing:
            self.log_message.emit("视觉识别已经处于运行状态")
            return
        if self._model is None:
            self.error_occurred.emit("请先加载模型")
            return
        if self._cap is None or not self._cap.isOpened():
            self.error_occurred.emit("请先打开摄像头")
            return
        if self._timer is None:
            self.error_occurred.emit("视觉线程尚未初始化")
            return

        self._camera_fail_count = 0
        self._fps_start = time.perf_counter()
        self._fps_count = 0
        self._current_fps = 0.0
        self._recognizing = True
        self._timer.start()
        self.recognition_state.emit(True)
        self.log_message.emit("视觉识别已启动")

    @Slot()
    def stop_recognition(self) -> None:
        was_running = self._recognizing
        self._recognizing = False
        if self._timer is not None:
            self._timer.stop()
        self.recognition_state.emit(False)
        if was_running:
            self.log_message.emit("视觉识别已停止")

    @Slot()
    def shutdown(self) -> None:
        self.stop_recognition()
        self._release_camera()
        self._model = None

    @Slot()
    def _process_frame(self) -> None:
        if not self._recognizing or self._cap is None or self._model is None:
            return

        ok, frame = self._cap.read()
        if not ok:
            self._camera_fail_count += 1
            if self._camera_fail_count >= self.CAMERA_FAIL_LIMIT:
                self.stop_recognition()
                self._release_camera()
                message = "摄像头连续读取失败，已停止识别并释放设备"
                self.camera_state.emit(False, message)
                self.error_occurred.emit(message)
            return

        self._camera_fail_count = 0
        now = time.perf_counter()
        height, width = frame.shape[:2]
        center_x = width // 2
        center_y = height // 2

        try:
            results = self._model.predict(
                frame,
                conf=self._confidence,
                imgsz=self._inference_size,
                verbose=False,
                classes=self._target_classes,
                device=self._device_arg,
            )
        except Exception as exc:
            self.stop_recognition()
            self.error_occurred.emit(f"YOLO 推理失败，识别已停止：{exc}")
            return

        result = results[0] if results else None
        candidates: list[DetectionCandidate] = []
        if result is not None and result.boxes is not None:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                confidence = float(box.conf[0]) if box.conf is not None else 0.0
                class_id = int(box.cls[0]) if box.cls is not None else -1
                candidates.append(
                    DetectionCandidate(x1, y1, x2, y2, confidence, class_id)
                )

        target = select_closest_target(candidates, width, height)
        annotated = result.plot() if result is not None else frame.copy()

        cv2.drawMarker(
            annotated,
            (center_x, center_y),
            (0, 255, 0),
            cv2.MARKER_CROSS,
            30,
            2,
        )

        target_found = target is not None
        class_name = "—"
        confidence = 0.0
        error_x = 0
        error_y = 0
        if target is not None:
            error_x = target.error_x
            error_y = target.error_y
            confidence = target.confidence
            class_name = self._class_name(target.class_id)
            cv2.circle(annotated, (target.center_x, target.center_y), 8, (0, 0, 255), -1)
            cv2.line(
                annotated,
                (center_x, center_y),
                (target.center_x, target.center_y),
                (0, 0, 255),
                2,
            )

        self._fps_count += 1
        elapsed = now - self._fps_start
        if elapsed >= 1.0:
            self._current_fps = self._fps_count / elapsed
            self._fps_count = 0
            self._fps_start = now

        cv2.putText(
            annotated,
            f"FPS: {self._current_fps:.1f}",
            (15, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 255),
            2,
        )
        cv2.putText(
            annotated,
            "TARGET" if target_found else "LOST",
            (15, 78),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0) if target_found else (0, 0, 255),
            2,
        )
        cv2.putText(
            annotated,
            f"error: ({error_x}, {error_y})" if target_found else "error: (9999, 9999)",
            (15, 112),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
        )

        rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
        image = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()

        self.frame_ready.emit(image)
        self.metrics_ready.emit(
            {
                "target_found": target_found,
                "error_x": error_x,
                "error_y": error_y,
                "class_name": class_name,
                "confidence": confidence,
                "fps": self._current_fps,
                "detection_count": len(candidates),
            }
        )

    def _release_camera(self) -> None:
        if self._cap is not None:
            self._cap.release()
        self._cap = None

    def _class_name(self, class_id: int) -> str:
        if self._model is None:
            return str(class_id)
        names: Any = self._model.names
        if isinstance(names, dict):
            return str(names.get(class_id, class_id))
        if 0 <= class_id < len(names):
            return str(names[class_id])
        return str(class_id)

    @staticmethod
    def _validate_target_classes(names: Any, target_classes: list[int] | None) -> None:
        if target_classes is None:
            return
        valid_ids = set(names.keys()) if isinstance(names, dict) else set(range(len(names)))
        invalid = [item for item in target_classes if item not in valid_ids]
        if invalid:
            raise ValueError(f"检测类别编号无效：{invalid}；模型类别表：{names}")
