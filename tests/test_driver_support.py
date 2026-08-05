import unittest

from app.driver_support import CH340_OFFICIAL_DRIVER_URL, is_ch34x_port


class DriverSupportTests(unittest.TestCase):
    def test_wch_vid_is_detected(self) -> None:
        self.assertTrue(is_ch34x_port(vid=0x1A86, description="USB Serial"))

    def test_ch340_text_is_detected_without_vid(self) -> None:
        self.assertTrue(is_ch34x_port(vid=None, description="USB-SERIAL CH340"))

    def test_unrelated_serial_chip_is_not_detected(self) -> None:
        self.assertFalse(
            is_ch34x_port(vid=0x10C4, description="CP210x USB to UART Bridge")
        )

    def test_driver_url_uses_wch_official_domain(self) -> None:
        self.assertTrue(CH340_OFFICIAL_DRIVER_URL.startswith("https://www.wch.cn/"))


if __name__ == "__main__":
    unittest.main()
