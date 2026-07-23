from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from typing import Iterator


_SPREADSHEET_FORMULA_PREFIXES = ("=", "+", "-", "@")


def spreadsheet_safe_text(value: object) -> str:
    """Return text that spreadsheet applications will not evaluate as a formula.

    CSV is a data exchange format, but common spreadsheet applications may execute
    cells beginning with formula metacharacters. Prefixing an apostrophe preserves
    the visible value while forcing text interpretation.
    """
    text = "" if value is None else str(value)
    candidate = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or candidate.startswith(_SPREADSHEET_FORMULA_PREFIXES):
        return "'" + text
    return text


def normalize_output_path(path: str | os.PathLike[str]) -> Path:
    target = Path(path).expanduser()
    if not target.name:
        raise ValueError("Output path must include a file name")
    parent = target.parent.resolve(strict=True)
    return parent / target.name


@contextmanager
def atomic_output_path(
    destination: str | os.PathLike[str],
    *,
    suffix: str = ".tmp",
) -> Iterator[Path]:
    """Yield a same-directory temporary path and atomically replace destination.

    The destination is left untouched if the caller raises. Using the same directory
    keeps os.replace on one filesystem, which preserves its atomic replacement
    guarantee on supported local filesystems.
    """
    target = normalize_output_path(destination)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=suffix,
        dir=str(target.parent),
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        yield temporary
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(
    destination: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    with atomic_output_path(destination, suffix=".txt.tmp") as temporary:
        with temporary.open("w", encoding=encoding, newline=newline) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
