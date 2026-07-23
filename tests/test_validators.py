import unittest

from pyping_app.validators import (
    ValidationError,
    parse_count,
    parse_duration,
    parse_host,
    parse_interval,
    parse_packet_size,
)


class ValidatorTests(unittest.TestCase):
    def assert_invalid(self, func, value):
        with self.assertRaises(ValidationError):
            func(value)

    def test_non_finite_values_rejected(self):
        for value in ("nan", "inf", "-inf", "NaN"):
            self.assert_invalid(parse_interval, value)
            self.assert_invalid(parse_duration, value)

    def test_packet_size_limits(self):
        self.assertEqual(parse_packet_size("56"), 56)
        self.assert_invalid(parse_packet_size, "0")
        self.assert_invalid(parse_packet_size, "65501")
        self.assert_invalid(parse_packet_size, "1e3")

    def test_count_zero_and_blank_mean_infinite(self):
        self.assertIsNone(parse_count(""))
        self.assertIsNone(parse_count("0"))
        self.assertEqual(parse_count("30"), 30)
        self.assert_invalid(parse_count, "-1")

    def test_host_normalization(self):
        self.assertEqual(parse_host(" [::1] "), "::1")
        self.assert_invalid(parse_host, "bad host")
        for value in ("example.com\x00", "example.com\x1f", "example.com\x7f"):
            self.assert_invalid(parse_host, value)


if __name__ == "__main__":
    unittest.main()
