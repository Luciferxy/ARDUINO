import unittest

from serial_protocol import frame_command, parse_ack


class SerialProtocolTests(unittest.TestCase):
    def test_frames_command_with_id(self):
        self.assertEqual(frame_command(7, "BEEP:2000|250"), "CMD:7:BEEP:2000|250\n")

    def test_rejects_multiline_payload(self):
        with self.assertRaises(ValueError):
            frame_command(1, "MSG:hello\nworld")

    def test_parses_ack(self):
        self.assertEqual(parse_ack(" ACK:42\n"), "42")
        self.assertIsNone(parse_ack("NACK:42"))


if __name__ == "__main__":
    unittest.main()
