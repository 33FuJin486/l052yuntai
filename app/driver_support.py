"""USB 转串口芯片识别与官方驱动入口。"""

from __future__ import annotations


CH340_OFFICIAL_DRIVER_URL = "https://www.wch.cn/downloads/ch341ser_exe.html"

# WCH/沁恒常见 USB VID。文本匹配用于兼容未提供 VID 的枚举结果。
WCH_VENDOR_IDS = frozenset({0x1A86, 0x4348})
CH34X_MARKERS = ("CH340", "CH341", "CH34X", "USB-SERIAL CH34")


def is_ch34x_port(
    *,
    vid: int | None,
    description: str = "",
    manufacturer: str = "",
    hwid: str = "",
) -> bool:
    """判断串口枚举信息是否像 CH340/CH341 设备。"""

    if vid in WCH_VENDOR_IDS:
        return True
    searchable = " ".join((description, manufacturer, hwid)).upper()
    return any(marker in searchable for marker in CH34X_MARKERS)
