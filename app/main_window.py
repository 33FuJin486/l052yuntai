"""Qt 主窗口、信号槽连接与应用生命周期。"""

from __future__ import annotations

import html
import sys
from datetime import datetime
from typing import Any

import cv2
from PySide6.QtCore import QObject, QMetaObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .config_manager import ConfigManager
from .driver_support import CH340_OFFICIAL_DRIVER_URL
from .resource_path import portable_path, resource_path
from .serial_manager import SerialManager
from .vision_worker import VisionWorker


class CameraScanWorker(QObject):
    finished = Signal(object)

    @Slot()
    def scan(self) -> None:
        found: list[int] = []
        thread = QThread.currentThread()
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        for index in range(10):
            if thread.isInterruptionRequested():
                break
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                found.append(index)
            cap.release()
        self.finished.emit(found)


class MainWindow(QMainWindow):
    load_model_requested = Signal(str, float, int, object)
    open_camera_requested = Signal(int, int, int)
    close_camera_requested = Signal()
    start_recognition_requested = Signal()
    stop_recognition_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("云台视觉跟踪系统")
        self.resize(1440, 900)
        self.setMinimumSize(1120, 720)

        self.config_manager = ConfigManager()
        self.settings = self.config_manager.load()
        self.serial_manager = SerialManager(self)
        self._last_image: QImage | None = None
        self._last_target_found: bool | None = None
        self._model_ready = False
        self._camera_ready = False
        self._recognizing = False
        self._closing = False
        self._camera_scan_thread: QThread | None = None
        self._camera_scan_worker: CameraScanWorker | None = None

        self._build_ui()
        self._apply_style()
        self._create_vision_thread()
        self._connect_signals()
        self.vision_thread.start()
        self._restore_settings()

        self.refresh_serial_ports()
        self.refresh_cameras()
        if self.config_manager.last_warning:
            self.log(self.config_manager.last_warning, error=True)
        self.log("软件已启动；识别与串口发送可独立控制")
        QTimer.singleShot(250, self.load_model)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("云台视觉跟踪系统")
        title.setObjectName("title")
        subtitle = QLabel("YOLO 实时识别 · STM32 两轴控制 · CPU/CUDA 自动选择")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()

        self.device_chip = self._new_chip("设备：检测中", "neutral")
        self.model_chip = self._new_chip("模型：未加载", "neutral")
        self.camera_chip = self._new_chip("摄像头：关闭", "neutral")
        self.serial_chip = self._new_chip("串口：断开", "neutral")
        for chip in (self.device_chip, self.model_chip, self.camera_chip, self.serial_chip):
            header.addWidget(chip)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_video_panel())
        splitter.addWidget(self._build_control_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([980, 410])
        root.addWidget(splitter, 1)

        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(150)
        self.log_edit.document().setMaximumBlockCount(800)
        log_layout.addWidget(self.log_edit)
        root.addWidget(log_group)

        self.statusBar().showMessage("就绪")

    def _build_video_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("videoPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)

        self.video_label = QLabel("请加载模型并打开摄像头")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(640, 420)
        self.video_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.video_label.setObjectName("videoSurface")
        layout.addWidget(self.video_label, 1)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(8)
        self.target_value = self._metric_card("目标状态", "等待识别")
        self.class_value = self._metric_card("类别 / 置信度", "—")
        self.error_value = self._metric_card("中心误差 X / Y", "— / —")
        self.fps_value = self._metric_card("FPS / 检测数", "0.0 / 0")
        metrics.addWidget(self.target_value.parentWidget(), 0, 0)
        metrics.addWidget(self.class_value.parentWidget(), 0, 1)
        metrics.addWidget(self.error_value.parentWidget(), 0, 2)
        metrics.addWidget(self.fps_value.parentWidget(), 0, 3)
        layout.addLayout(metrics)
        return panel

    def _build_control_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(390)
        scroll.setMaximumWidth(470)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(10)

        layout.addWidget(self._camera_group())
        layout.addWidget(self._serial_group())
        layout.addWidget(self._model_group())
        layout.addWidget(self._tracking_group())
        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _camera_group(self) -> QGroupBox:
        group = QGroupBox("摄像头控制")
        form = QFormLayout(group)
        self.camera_combo = QComboBox()
        self.refresh_camera_button = QPushButton("刷新摄像头")
        camera_row = QHBoxLayout()
        camera_row.addWidget(self.camera_combo, 1)
        camera_row.addWidget(self.refresh_camera_button)
        form.addRow("设备", camera_row)

        resolution_row = QHBoxLayout()
        self.width_spin = QSpinBox()
        self.width_spin.setRange(160, 7680)
        self.width_spin.setSingleStep(80)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(120, 4320)
        self.height_spin.setSingleStep(60)
        resolution_row.addWidget(self.width_spin)
        resolution_row.addWidget(QLabel("×"))
        resolution_row.addWidget(self.height_spin)
        form.addRow("分辨率", resolution_row)

        buttons = QHBoxLayout()
        self.open_camera_button = QPushButton("打开摄像头")
        self.close_camera_button = QPushButton("关闭摄像头")
        buttons.addWidget(self.open_camera_button)
        buttons.addWidget(self.close_camera_button)
        form.addRow(buttons)
        return group

    def _serial_group(self) -> QGroupBox:
        group = QGroupBox("串口控制")
        form = QFormLayout(group)
        self.serial_combo = QComboBox()
        self.refresh_serial_button = QPushButton("刷新串口")
        serial_row = QHBoxLayout()
        serial_row.addWidget(self.serial_combo, 1)
        serial_row.addWidget(self.refresh_serial_button)
        form.addRow("端口", serial_row)

        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["9600", "57600", "115200", "230400", "460800", "921600"])
        form.addRow("波特率", self.baud_combo)

        self.serial_help_label = QLabel(
            "未发现可用串口。请检查设备和 USB 数据线；若使用 CH340/CH341，可打开沁恒官网下载驱动。"
        )
        self.serial_help_label.setWordWrap(True)
        self.serial_help_label.setObjectName("serialHelp")
        self.serial_help_label.setVisible(False)
        form.addRow(self.serial_help_label)

        buttons = QHBoxLayout()
        self.connect_serial_button = QPushButton("连接串口")
        self.disconnect_serial_button = QPushButton("断开串口")
        buttons.addWidget(self.connect_serial_button)
        buttons.addWidget(self.disconnect_serial_button)
        form.addRow(buttons)

        self.ch340_help_button = QPushButton("CH340/CH341 驱动帮助")
        form.addRow(self.ch340_help_button)
        return group

    def _model_group(self) -> QGroupBox:
        group = QGroupBox("模型设置")
        form = QFormLayout(group)
        self.model_path_edit = QLineEdit()
        self.browse_model_button = QPushButton("选择…")
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_path_edit, 1)
        model_row.addWidget(self.browse_model_button)
        form.addRow("模型路径", model_row)

        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.01, 1.0)
        self.confidence_spin.setSingleStep(0.05)
        self.confidence_spin.setDecimals(2)
        form.addRow("置信度", self.confidence_spin)

        self.inference_spin = QSpinBox()
        self.inference_spin.setRange(32, 4096)
        self.inference_spin.setSingleStep(32)
        form.addRow("推理尺寸", self.inference_spin)

        self.classes_edit = QLineEdit()
        self.classes_edit.setPlaceholderText("例如 0,1；全部类别填 None")
        form.addRow("检测类别", self.classes_edit)

        self.load_model_button = QPushButton("加载模型")
        self.load_model_button.setObjectName("primaryButton")
        form.addRow(self.load_model_button)
        return group

    def _tracking_group(self) -> QGroupBox:
        group = QGroupBox("跟踪控制")
        layout = QVBoxLayout(group)
        frequency_row = QFormLayout()
        self.frequency_spin = QSpinBox()
        self.frequency_spin.setRange(1, 100)
        self.frequency_spin.setSuffix(" Hz")
        frequency_row.addRow("发送频率", self.frequency_spin)
        layout.addLayout(frequency_row)

        recognition_buttons = QHBoxLayout()
        self.start_recognition_button = QPushButton("启动识别")
        self.start_recognition_button.setObjectName("primaryButton")
        self.stop_recognition_button = QPushButton("停止识别")
        recognition_buttons.addWidget(self.start_recognition_button)
        recognition_buttons.addWidget(self.stop_recognition_button)
        layout.addLayout(recognition_buttons)

        sending_buttons = QHBoxLayout()
        self.start_sending_button = QPushButton("开始发送")
        self.pause_sending_button = QPushButton("暂停发送")
        sending_buttons.addWidget(self.start_sending_button)
        sending_buttons.addWidget(self.pause_sending_button)
        layout.addLayout(sending_buttons)

        self.emergency_button = QPushButton("紧急停止")
        self.emergency_button.setObjectName("dangerButton")
        self.emergency_button.setMinimumHeight(46)
        layout.addWidget(self.emergency_button)
        return group

    def _create_vision_thread(self) -> None:
        self.vision_thread = QThread(self)
        self.vision_thread.setObjectName("VisionThread")
        self.vision_worker = VisionWorker()
        self.vision_worker.moveToThread(self.vision_thread)
        self.vision_thread.started.connect(self.vision_worker.initialize)

    def _connect_signals(self) -> None:
        self.load_model_requested.connect(self.vision_worker.load_model)
        self.open_camera_requested.connect(self.vision_worker.open_camera)
        self.close_camera_requested.connect(self.vision_worker.close_camera)
        self.start_recognition_requested.connect(self.vision_worker.start_recognition)
        self.stop_recognition_requested.connect(self.vision_worker.stop_recognition)

        self.vision_worker.frame_ready.connect(self._on_frame)
        self.vision_worker.metrics_ready.connect(self._on_metrics)
        self.vision_worker.model_state.connect(self._on_model_state)
        self.vision_worker.camera_state.connect(self._on_camera_state)
        self.vision_worker.recognition_state.connect(self._on_recognition_state)
        self.vision_worker.device_ready.connect(self._on_device_ready)
        self.vision_worker.error_occurred.connect(self._show_error)
        self.vision_worker.log_message.connect(self.log)

        self.serial_manager.connection_changed.connect(self._on_serial_state)
        self.serial_manager.sending_changed.connect(self._on_sending_state)
        self.serial_manager.error_occurred.connect(self._show_error)
        self.serial_manager.log_message.connect(self.log)

        self.refresh_camera_button.clicked.connect(self.refresh_cameras)
        self.open_camera_button.clicked.connect(self.open_camera)
        self.close_camera_button.clicked.connect(self.close_camera_requested.emit)
        self.refresh_serial_button.clicked.connect(self.refresh_serial_ports)
        self.connect_serial_button.clicked.connect(self.connect_serial)
        self.disconnect_serial_button.clicked.connect(self.serial_manager.disconnect_port)
        self.ch340_help_button.clicked.connect(self.open_ch340_driver_help)
        self.browse_model_button.clicked.connect(self.browse_model)
        self.load_model_button.clicked.connect(self.load_model)
        self.start_recognition_button.clicked.connect(self.start_recognition_requested.emit)
        self.stop_recognition_button.clicked.connect(self.stop_recognition)
        self.start_sending_button.clicked.connect(self.start_sending)
        self.pause_sending_button.clicked.connect(self.serial_manager.pause_sending)
        self.emergency_button.clicked.connect(self.emergency_stop)

    def _restore_settings(self) -> None:
        self.width_spin.setValue(self.settings["frame_width"])
        self.height_spin.setValue(self.settings["frame_height"])
        self.baud_combo.setCurrentText(str(self.settings["baudrate"]))
        self.model_path_edit.setText(self.settings["model_path"])
        self.confidence_spin.setValue(self.settings["confidence"])
        self.inference_spin.setValue(self.settings["inference_size"])
        target_classes = self.settings["target_classes"]
        self.classes_edit.setText(
            "None" if target_classes is None else ",".join(map(str, target_classes))
        )
        self.frequency_spin.setValue(self.settings["send_frequency"])

    @Slot()
    def refresh_serial_ports(self) -> None:
        saved = self.serial_combo.currentData() or self.settings.get("serial_port", "")
        ports = self.serial_manager.available_ports()
        self.serial_combo.clear()
        for item in ports:
            self.serial_combo.addItem(item.display_name, item.device)
        if not ports:
            self.serial_combo.addItem("未发现串口", "")
        self.serial_help_label.setVisible(not ports)
        index = self.serial_combo.findData(saved)
        self.serial_combo.setCurrentIndex(index if index >= 0 else 0)
        ch34x_count = sum(item.is_ch34x for item in ports)
        if ports:
            suffix = f"，其中 {ch34x_count} 个 CH340/CH341" if ch34x_count else ""
            self.log(f"串口扫描完成：发现 {len(ports)} 个设备{suffix}")
        else:
            self.log(
                "未发现可用串口：请检查设备、USB 数据线和驱动；可使用 CH340/CH341 驱动帮助",
                error=True,
            )

    @Slot()
    def open_ch340_driver_help(self) -> None:
        answer = QMessageBox.question(
            self,
            "CH340/CH341 驱动帮助",
            "软件没有发现串口时，原因也可能是设备未连接、USB 线不支持数据或端口被禁用。\n\n"
            "若你的 USB 转串口芯片是 CH340/CH341，可前往沁恒（WCH）官方下载 Windows 驱动。"
            "软件不会自动下载或安装驱动。\n\n是否打开官方网站？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if QDesktopServices.openUrl(QUrl(CH340_OFFICIAL_DRIVER_URL)):
            self.log("已打开沁恒 CH340/CH341 官方驱动页面")
        else:
            self._show_error(f"无法打开浏览器，请手动访问：{CH340_OFFICIAL_DRIVER_URL}")

    @Slot()
    def refresh_cameras(self) -> None:
        if self._camera_scan_thread is not None and self._camera_scan_thread.isRunning():
            return
        self.refresh_camera_button.setEnabled(False)
        self.log("正在后台扫描摄像头…")
        thread = QThread(self)
        worker = CameraScanWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.scan)
        worker.finished.connect(self._on_cameras_scanned)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_camera_scan)
        self._camera_scan_thread = thread
        self._camera_scan_worker = worker
        thread.start()

    @Slot(object)
    def _on_cameras_scanned(self, indices: list[int]) -> None:
        saved = int(self.settings.get("camera_index", 0))
        self.camera_combo.clear()
        for index in indices:
            self.camera_combo.addItem(f"摄像头 {index}", index)
        if not indices:
            self.camera_combo.addItem("未检测到摄像头（可手动使用 0）", 0)
        selected = self.camera_combo.findData(saved)
        self.camera_combo.setCurrentIndex(selected if selected >= 0 else 0)
        self.refresh_camera_button.setEnabled(True)
        self.log(f"摄像头扫描完成：发现 {len(indices)} 个设备")

    @Slot()
    def _clear_camera_scan(self) -> None:
        self._camera_scan_thread = None
        self._camera_scan_worker = None
        self.refresh_camera_button.setEnabled(True)

    @Slot()
    def open_camera(self) -> None:
        index = int(self.camera_combo.currentData() or 0)
        self.open_camera_requested.emit(index, self.width_spin.value(), self.height_spin.value())

    @Slot()
    def connect_serial(self) -> None:
        port = str(self.serial_combo.currentData() or "")
        try:
            baudrate = int(self.baud_combo.currentText())
        except ValueError:
            self._show_error("波特率必须是整数")
            return
        self.serial_manager.connect_port(port, baudrate)

    @Slot()
    def browse_model(self) -> None:
        current = resource_path(self.model_path_edit.text())
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "选择 YOLO 模型",
            str(current.parent),
            "PyTorch 模型 (*.pt);;所有文件 (*)",
        )
        if filename:
            self.model_path_edit.setText(portable_path(filename))

    @Slot()
    def load_model(self) -> None:
        try:
            target_classes = self._parse_target_classes(self.classes_edit.text())
        except ValueError as exc:
            self._show_error(str(exc))
            return
        model_path = resource_path(self.model_path_edit.text())
        self._set_chip(self.model_chip, "模型：加载中", "warning")
        self.load_model_button.setEnabled(False)
        self.load_model_requested.emit(
            str(model_path),
            self.confidence_spin.value(),
            self.inference_spin.value(),
            target_classes,
        )

    @Slot()
    def stop_recognition(self) -> None:
        self.stop_recognition_requested.emit()
        self.serial_manager.pause_sending()
        self.serial_manager.update_latest(0, 0, False)

    @Slot()
    def start_sending(self) -> None:
        if not self._recognizing:
            self._show_error("请先启动视觉识别，再开始发送")
            return
        self.serial_manager.start_sending(self.frequency_spin.value())

    @Slot()
    def emergency_stop(self) -> None:
        self.serial_manager.emergency_stop()
        self.stop_recognition_requested.emit()
        self.log("紧急停止已执行；摄像头保持打开，便于现场观察", error=True)
        self.statusBar().showMessage("紧急停止已执行")

    @Slot(QImage)
    def _on_frame(self, image: QImage) -> None:
        self._last_image = image
        self._render_image()

    @Slot(object)
    def _on_metrics(self, metrics: dict[str, Any]) -> None:
        found = bool(metrics["target_found"])
        self.target_value.setText("已锁定" if found else "目标丢失")
        self.target_value.setProperty("state", "ok" if found else "danger")
        self.target_value.style().unpolish(self.target_value)
        self.target_value.style().polish(self.target_value)
        self.class_value.setText(
            f"{metrics['class_name']} / {metrics['confidence']:.2f}" if found else "—"
        )
        self.error_value.setText(
            f"{metrics['error_x']:+d} / {metrics['error_y']:+d}" if found else "9999 / 9999"
        )
        self.fps_value.setText(f"{metrics['fps']:.1f} / {metrics['detection_count']}")
        self.serial_manager.update_latest(
            metrics["error_x"], metrics["error_y"], found
        )
        if self._last_target_found is not None and found != self._last_target_found:
            self.log("重新发现目标" if found else "目标丢失")
        self._last_target_found = found

    @Slot(bool, str, object)
    def _on_model_state(self, ready: bool, message: str, names: Any) -> None:
        self._model_ready = ready
        self.load_model_button.setEnabled(True)
        self._set_chip(self.model_chip, "模型：已加载" if ready else "模型：失败", "ok" if ready else "danger")
        if ready:
            self.log(f"{message}；类别表：{names}")
        else:
            self._show_error(message)
        self._update_controls()

    @Slot(bool, str)
    def _on_camera_state(self, ready: bool, message: str) -> None:
        self._camera_ready = ready
        self._set_chip(self.camera_chip, "摄像头：已打开" if ready else "摄像头：关闭", "ok" if ready else "neutral")
        if not ready and "已关闭" not in message:
            self.log(message, error=True)
        self._update_controls()

    @Slot(bool)
    def _on_recognition_state(self, running: bool) -> None:
        self._recognizing = running
        if not running:
            self.serial_manager.update_latest(0, 0, False)
        self._update_controls()
        self.statusBar().showMessage("正在识别" if running else "识别已停止")

    @Slot(str)
    def _on_device_ready(self, device: str) -> None:
        self._set_chip(self.device_chip, f"设备：{device}", "ok")

    @Slot(bool, str)
    def _on_serial_state(self, connected: bool, message: str) -> None:
        self._set_chip(self.serial_chip, "串口：已连接" if connected else "串口：断开", "ok" if connected else "neutral")
        self._update_controls()
        self.statusBar().showMessage(message)

    @Slot(bool)
    def _on_sending_state(self, sending: bool) -> None:
        self.start_sending_button.setEnabled(not sending and self.serial_manager.is_connected)
        self.pause_sending_button.setEnabled(sending)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.log(message, error=True)
        self.statusBar().showMessage(message, 8000)

    def resizeEvent(self, event: Any) -> None:
        super().resizeEvent(event)
        self._render_image()

    def _render_image(self) -> None:
        if self._last_image is None:
            return
        pixmap = QPixmap.fromImage(self._last_image)
        self.video_label.setPixmap(
            pixmap.scaled(
                self.video_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _update_controls(self) -> None:
        connected = self.serial_manager.is_connected
        self.connect_serial_button.setEnabled(not connected)
        self.disconnect_serial_button.setEnabled(connected)
        self.start_recognition_button.setEnabled(
            self._model_ready and self._camera_ready and not self._recognizing
        )
        self.stop_recognition_button.setEnabled(self._recognizing)
        self.start_sending_button.setEnabled(
            connected and self._recognizing and not self.serial_manager.is_sending
        )
        self.pause_sending_button.setEnabled(self.serial_manager.is_sending)
        self.open_camera_button.setEnabled(not self._recognizing)
        self.close_camera_button.setEnabled(self._camera_ready)

    def _collect_settings(self) -> dict[str, Any]:
        try:
            classes = self._parse_target_classes(self.classes_edit.text())
        except ValueError:
            classes = [0]
        try:
            baudrate = int(self.baud_combo.currentText())
        except ValueError:
            baudrate = 115200
        return {
            "serial_port": str(self.serial_combo.currentData() or ""),
            "baudrate": baudrate,
            "camera_index": int(self.camera_combo.currentData() or 0),
            "frame_width": self.width_spin.value(),
            "frame_height": self.height_spin.value(),
            "model_path": self.model_path_edit.text() or "model/best.pt",
            "confidence": self.confidence_spin.value(),
            "inference_size": self.inference_spin.value(),
            "target_classes": classes,
            "send_frequency": self.frequency_spin.value(),
        }

    @staticmethod
    def _parse_target_classes(text: str) -> list[int] | None:
        clean = text.strip()
        if clean.lower() in {"none", "all", "全部"}:
            return None
        if not clean:
            raise ValueError("检测类别不能为空；追踪全部类别请输入 None")
        try:
            values = [int(item.strip()) for item in clean.split(",")]
        except ValueError as exc:
            raise ValueError("检测类别格式错误，请输入 0,1 或 None") from exc
        if any(item < 0 for item in values):
            raise ValueError("检测类别编号不能为负数")
        return values

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing:
            event.accept()
            return
        self._closing = True
        self.log("正在安全停止线程并释放资源…")
        try:
            self.config_manager.save(self._collect_settings())
        except OSError as exc:
            QMessageBox.warning(self, "配置保存失败", str(exc))

        self.serial_manager.emergency_stop()
        self.serial_manager.disconnect_port()
        if self._camera_scan_thread is not None and self._camera_scan_thread.isRunning():
            self._camera_scan_thread.requestInterruption()
            self._camera_scan_thread.quit()
            if not self._camera_scan_thread.wait(5000):
                self.log("摄像头扫描线程未及时退出，执行最终终止", error=True)
                self._camera_scan_thread.terminate()
                self._camera_scan_thread.wait(1000)

        if self.vision_thread.isRunning():
            QMetaObject.invokeMethod(
                self.vision_worker,
                "shutdown",
                Qt.ConnectionType.BlockingQueuedConnection,
            )
            self.vision_thread.quit()
            if not self.vision_thread.wait(5000):
                self.log("视觉线程未在 5 秒内退出", error=True)
        event.accept()

    @staticmethod
    def _new_chip(text: str, state: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("statusChip")
        label.setProperty("state", state)
        return label

    @staticmethod
    def _set_chip(label: QLabel, text: str, state: str) -> None:
        label.setText(text)
        label.setProperty("state", state)
        label.style().unpolish(label)
        label.style().polish(label)

    @staticmethod
    def _metric_card(title: str, initial: str) -> QLabel:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 9, 12, 9)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        value = QLabel(initial)
        value.setObjectName("metricValue")
        value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(title_label)
        layout.addWidget(value)
        return value

    @Slot(str)
    def log(self, message: str, error: bool = False) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = "#ef4444" if error else "#cbd5e1"
        safe = html.escape(str(message))
        self.log_edit.append(
            f'<span style="color:#64748b">[{timestamp}]</span> '
            f'<span style="color:{color}">{safe}</span>'
        )

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #0b1220; color: #e5e7eb; font-family: "Microsoft YaHei UI"; font-size: 13px; }
            QLabel#title { font-size: 24px; font-weight: 700; color: #f8fafc; }
            QLabel#subtitle { color: #94a3b8; }
            QGroupBox { border: 1px solid #263348; border-radius: 10px; margin-top: 12px; padding-top: 12px; font-weight: 600; background: #111b2e; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #cbd5e1; }
            QFrame#videoPanel { background: #111b2e; border: 1px solid #263348; border-radius: 12px; }
            QLabel#videoSurface { background: #050a12; color: #64748b; border: 1px solid #263348; border-radius: 8px; }
            QFrame#metricCard { background: #0f172a; border: 1px solid #263348; border-radius: 8px; }
            QLabel#metricTitle { color: #64748b; font-size: 11px; }
            QLabel#metricValue { color: #f8fafc; font-size: 17px; font-weight: 700; }
            QLabel#metricValue[state="ok"] { color: #34d399; }
            QLabel#metricValue[state="danger"] { color: #fb7185; }
            QLabel#statusChip { padding: 6px 10px; border-radius: 10px; background: #1e293b; color: #cbd5e1; }
            QLabel#statusChip[state="ok"] { background: #12372c; color: #6ee7b7; }
            QLabel#statusChip[state="warning"] { background: #3b2a10; color: #fbbf24; }
            QLabel#statusChip[state="danger"] { background: #451a24; color: #fda4af; }
            QPushButton { background: #1e293b; border: 1px solid #334155; border-radius: 7px; padding: 7px 10px; color: #e2e8f0; }
            QPushButton:hover { background: #334155; }
            QPushButton:disabled { color: #64748b; background: #111827; border-color: #1f2937; }
            QPushButton#primaryButton { background: #2563eb; border-color: #3b82f6; color: white; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #1d4ed8; }
            QPushButton#dangerButton { background: #b91c1c; border-color: #ef4444; color: white; font-size: 15px; font-weight: 700; }
            QPushButton#dangerButton:hover { background: #991b1b; }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit { background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 6px; color: #e5e7eb; selection-background-color: #2563eb; }
            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: #0f172a; width: 10px; }
            QScrollBar::handle:vertical { background: #334155; border-radius: 5px; min-height: 24px; }
            QStatusBar { background: #0f172a; color: #94a3b8; }
            """
        )
