"""Encoding utilities for reading source files with automatic fallback.

Source code files in user repositories may use non-UTF-8 encodings
(GB2312, GBK, GB18030, Latin-1, etc.).  The functions here try UTF-8
first, then fall back through common encodings so that Chinese comments
and string literals are preserved correctly.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

#: Encoding fallback chain.  UTF-8 is tried first; ``latin-1`` is the
#: last resort because it never raises ``UnicodeDecodeError`` (it maps
#: every byte 0x00–0xFF to a codepoint).
_FALLBACK_ENCODINGS = ("utf-8", "gb2312", "gbk", "gb18030", "latin-1")


def smart_decode(raw: bytes) -> str:
    """Decode *raw* bytes by trying common encodings in order.

    Returns the decoded string.  Never raises ``UnicodeDecodeError``.
    """
    for enc in _FALLBACK_ENCODINGS:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 never fails, but just in case:
    return raw.decode("utf-8", errors="replace")


def read_source_file(path: Path) -> str:
    """Read a source file with automatic encoding detection.

    Tries UTF-8 first, then GB2312/GBK/GB18030, then Latin-1.
    Returns the file content as a string.

    Raises:
        OSError: if the file cannot be read.
    """
    raw = path.read_bytes()
    return smart_decode(raw)


def read_source_lines(path: Path) -> list[str]:
    """Read a source file and return its lines (without line endings).

    Uses :func:`read_source_file` for encoding detection.
    """
    return read_source_file(path).splitlines()
