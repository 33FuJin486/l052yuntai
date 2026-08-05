"""STM32 串口协议。

协议来源：原始 Python 程序 ``main(1).py`` 与 STM32 ``app(3).c``。
任何协议变更都应同时修改两端并更新测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


LOST_TARGET_VALUE = 9999
PACKET_PATTERN = re.compile(r"^\[(-?\d+),(-?\d+)\]\n$")


@dataclass(frozen=True)
class TrackingPacket:
    error_x: int
    error_y: int

    @property
    def is_stop(self) -> bool:
        return (
            self.error_x == LOST_TARGET_VALUE
            and self.error_y == LOST_TARGET_VALUE
        )


def format_tracking_packet(error_x: int, error_y: int) -> bytes:
    """生成无空格、LF 结尾的 ASCII 跟踪帧。"""
    return f"[{int(error_x)},{int(error_y)}]\n".encode("ascii")


def stop_packet() -> bytes:
    """生成目标丢失/安全停车帧。

    STM32 收到该帧后会把两轴速度置零并复位两个 PID。
    """
    return format_tracking_packet(LOST_TARGET_VALUE, LOST_TARGET_VALUE)


def parse_tracking_packet(packet: bytes | str) -> TrackingPacket:
    """解析协议帧，主要用于回归测试和诊断。"""
    text = packet.decode("ascii") if isinstance(packet, bytes) else packet
    match = PACKET_PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"无效串口帧：{text!r}")
    return TrackingPacket(int(match.group(1)), int(match.group(2)))
