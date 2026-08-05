from __future__ import annotations

import unittest

from app.protocol import (
    LOST_TARGET_VALUE,
    format_tracking_packet,
    parse_tracking_packet,
    stop_packet,
)


class ProtocolTests(unittest.TestCase):
    def test_normal_packet_matches_original_protocol(self) -> None:
        self.assertEqual(format_tracking_packet(35, -18), b"[35,-18]\n")

    def test_values_are_converted_to_int(self) -> None:
        self.assertEqual(format_tracking_packet(3.9, -2.7), b"[3,-2]\n")

    def test_stop_packet_matches_stm32_contract(self) -> None:
        self.assertEqual(stop_packet(), b"[9999,9999]\n")
        parsed = parse_tracking_packet(stop_packet())
        self.assertEqual(parsed.error_x, LOST_TARGET_VALUE)
        self.assertTrue(parsed.is_stop)

    def test_parser_rejects_protocol_drift(self) -> None:
        for invalid in (b"[1, 2]\n", b"[1,2]\r\n", b"1,2\n", b"[x,y]\n"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    parse_tracking_packet(invalid)


if __name__ == "__main__":
    unittest.main()
