from __future__ import annotations

import math

MAX_HOST_LENGTH = 253
MAX_PACKET_SIZE = 65500
MAX_INTERVAL_SECONDS = 86400.0
MAX_TIMEOUT_SECONDS = 300.0
MAX_COUNT = 10_000_000
MAX_DURATION_SECONDS = 31 * 24 * 60 * 60
MIN_INTERVAL_SECONDS = 0.1
MIN_TIMEOUT_SECONDS = 0.05


class ValidationError(ValueError):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


def parse_host(value: str) -> str:
    host = value.strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1].strip()
    if not host:
        raise ValidationError("error_host_msg")
    if len(host) > MAX_HOST_LENGTH:
        raise ValidationError("error_host_too_long")
    if any(ch.isspace() for ch in host):
        raise ValidationError("error_host_whitespace")
    return host


def parse_integer(value: str, empty_key: str, invalid_key: str) -> int:
    text = value.strip()
    if not text:
        raise ValidationError(empty_key)
    if not text.isdecimal():
        raise ValidationError(invalid_key)
    return int(text, 10)


def parse_packet_size(value: str) -> int:
    size = parse_integer(value, "error_size", "error_size_int")
    if size <= 0:
        raise ValidationError("error_size_positive")
    if size > MAX_PACKET_SIZE:
        raise ValidationError("error_size_too_large")
    return size


def parse_finite_float(
    value: str,
    *,
    empty_key: str,
    invalid_key: str,
    positive_key: str,
    minimum: float,
    maximum: float,
    too_small_key: str,
    too_large_key: str,
) -> float:
    text = value.strip()
    if not text:
        raise ValidationError(empty_key)
    try:
        result = float(text)
    except ValueError as exc:
        raise ValidationError(invalid_key) from exc
    if not math.isfinite(result):
        raise ValidationError(invalid_key)
    if result <= 0:
        raise ValidationError(positive_key)
    if result < minimum:
        raise ValidationError(too_small_key)
    if result > maximum:
        raise ValidationError(too_large_key)
    return result


def parse_interval(value: str) -> float:
    return parse_finite_float(
        value,
        empty_key="error_interval",
        invalid_key="error_interval",
        positive_key="error_interval_positive",
        minimum=MIN_INTERVAL_SECONDS,
        maximum=MAX_INTERVAL_SECONDS,
        too_small_key="error_interval_too_small",
        too_large_key="error_interval_too_large",
    )


def parse_timeout(value: str) -> float:
    return parse_finite_float(
        value,
        empty_key="error_timeout",
        invalid_key="error_timeout",
        positive_key="error_timeout_positive",
        minimum=MIN_TIMEOUT_SECONDS,
        maximum=MAX_TIMEOUT_SECONDS,
        too_small_key="error_timeout_too_small",
        too_large_key="error_timeout_too_large",
    )


def parse_count(value: str) -> int | None:
    text = value.strip()
    if text == "":
        return None
    if not text.isdecimal():
        raise ValidationError("error_count")
    count = int(text, 10)
    if count == 0:
        return None
    if count > MAX_COUNT:
        raise ValidationError("error_count_too_large")
    return count


def parse_duration(value: str) -> float:
    return parse_finite_float(
        value,
        empty_key="error_duration",
        invalid_key="error_duration_number",
        positive_key="error_duration_positive",
        minimum=MIN_TIMEOUT_SECONDS,
        maximum=MAX_DURATION_SECONDS,
        too_small_key="error_duration_positive",
        too_large_key="error_duration_too_large",
    )
