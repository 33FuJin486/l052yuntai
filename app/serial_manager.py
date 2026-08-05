"""串口扫描、连接、限频发送与安全停车。"""

from __future__ import annotations

from dataclasses import dataclass

import serial
from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from serial import SerialException
from serial.tools import list_ports

from .driver_support import is_ch34x_port
from .protocol import format_tracking_packet, stop_packet


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    description: str
    vid: int | None = None
    pid: int | None = None
    manufacturer: str = ""
    hwid: str = ""

    @property
    def is_ch34x(self) -> bool:
        return is_ch34x_port(
            vid=self.vid,
            description=self.description,
            manufacturer=self.manufacturer,
            hwid=self.hwid,
        )

    @property
    def display_name(self) -> str:
        chip = " [CH340/CH341]" if self.is_ch34x else ""
        return f"{self.device} — {self.description}{chip}"


class SerialManager(QObject):
    connection_changed = Signal(bool, str)
    sending_changed = Signal(bool)
    packet_sent = Signal(str)
    error_occurred = Signal(str)
    log_message = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._port: serial.Serial | None = None
        self._sending = False
        self._latest_error_x = 0
        self._latest_error_y = 0
        self._target_found = False
        self._last_target_found: bool | None = None
        self._frequency_hz = 30

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._send_latest)

    @staticmethod
    def available_ports() -> list[SerialPortInfo]:
        return [
            SerialPortInfo(
                device=item.device,
                description=item.description or "未知设备",
                vid=item.vid,
                pid=item.pid,
                manufacturer=item.manufacturer or "",
                hwid=item.hwid or "",
            )
            for item in list_ports.comports()
        ]

    @property
    def is_connected(self) -> bool:
        return bool(self._port is not None and self._port.is_open)

    @property
    def is_sending(self) -> bool:
        return self._sending

    @Slot(str, int)
    def connect_port(self, port_name: str, baudrate: int) -> None:
        if self.is_connected:
            self.disconnect_port()
        if not port_name:
            self.error_occurred.emit("请选择串口")
            return

        try:
            self._port = serial.Serial(
                port=port_name,
                baudrate=int(baudrate),
                timeout=0.01,
                write_timeout=0.05,
            )
        except (SerialException, OSError, ValueError) as exc:
            self._port = None
            message = f"串口 {port_name} 连接失败：{exc}"
            self.connection_changed.emit(False, message)
            self.error_occurred.emit(message)
            return

        message = f"串口 {port_name} 已连接，波特率 {baudrate}"
        self.connection_changed.emit(True, message)
        self.log_message.emit(message)

    @Slot()
    def disconnect_port(self) -> None:
        self.pause_sending()
        port_name = self._port.port if self._port is not None else ""
        if self._port is not None:
            try:
                self._port.close()
            except (SerialException, OSError):
                pass
        self._port = None
        message = f"串口 {port_name} 已断开" if port_name else "串口已断开"
        self.connection_changed.emit(False, message)
        self.log_message.emit(message)

    @Slot(int, int, bool)
    def update_latest(self, error_x: int, error_y: int, target_found: bool) -> None:
        state_changed = self._last_target_found is None or target_found != self._last_target_found
        self._latest_error_x = int(error_x)
        self._latest_error_y = int(error_y)
        self._target_found = bool(target_found)
        self._last_target_found = self._target_found
        if self._sending and state_changed:
            self._send_latest()
            if self._sending:
                self._timer.start()

    @Slot(int)
    def start_sending(self, frequency_hz: int) -> None:
        if not self.is_connected:
            self.error_occurred.emit("串口未连接，无法开始发送")
            return
        if self._sending:
            self.log_message.emit("串口发送已经处于运行状态")
            return

        self._frequency_hz = max(1, min(100, int(frequency_hz)))
        self._sending = True
        self._timer.start(max(1, round(1000 / self._frequency_hz)))
        self._send_latest()
        self.sending_changed.emit(True)
        self.log_message.emit(f"开始发送跟踪数据：{self._frequency_hz} Hz")

    @Slot()
    def pause_sending(self) -> None:
        was_sending = self._sending
        self._sending = False
        self._timer.stop()
        if was_sending and self.is_connected:
            self._write(stop_packet())
        self.sending_changed.emit(False)
        if was_sending:
            self.log_message.emit("已暂停发送，并发送安全停车帧")

    @Slot()
    def emergency_stop(self) -> None:
        self._sending = False
        self._timer.stop()
        self._target_found = False
        self._latest_error_x = 0
        self._latest_error_y = 0
        if self.is_connected:
            self._write(stop_packet())
        self.sending_changed.emit(False)
        self.log_message.emit("紧急停止：已停止正常数据并发送 [9999,9999]")

    @Slot()
    def _send_latest(self) -> None:
        if not self._sending or not self.is_connected:
            return
        packet = (
            format_tracking_packet(self._latest_error_x, self._latest_error_y)
            if self._target_found
            else stop_packet()
        )
        self._write(packet)

    def _write(self, packet: bytes) -> bool:
        if not self.is_connected:
            return False
        try:
            assert self._port is not None
            self._port.write(packet)
            self.packet_sent.emit(packet.decode("ascii").rstrip())
            return True
        except (SerialException, OSError) as exc:
            message = f"串口发送失败，已停止发送：{exc}"
            self._sending = False
            self._timer.stop()
            try:
                assert self._port is not None
                self._port.close()
            except (SerialException, OSError):
                pass
            self._port = None
            self.sending_changed.emit(False)
            self.connection_changed.emit(False, "串口连接已断开")
            self.error_occurred.emit(message)
            return False
