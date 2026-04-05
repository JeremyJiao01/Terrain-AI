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
            result = raw.decode(enc)
            if enc != "utf-8":
                logger.debug(
                    "Decoded {} bytes with fallback encoding: {}", len(raw), enc
                )
            return result
        except (UnicodeDecodeError, LookupError):
            continue
    # latin-1 never fails, but just in case:
    logger.debug(
        "All encodings failed for {} bytes, using utf-8 with replacement", len(raw)
    )
    return raw.decode("utf-8", errors="replace")


def normalize_to_utf8_bytes(raw: bytes) -> bytes:
    """Normalize raw file bytes to clean UTF-8 with LF line endings.

    1. Detect encoding via :func:`smart_decode` (GBK/GB2312/GB18030/Latin-1).
    2. Strip ``\\r`` characters (CRLF → LF).
    3. Re-encode to UTF-8 bytes.

    This is intended for feeding source files to tree-sitter, which
    expects UTF-8.  The original file on disk is **not** modified.
    """
    text = smart_decode(raw)
    if "\r" in text:
        logger.debug(
            "Stripped CR characters from {} bytes (CRLF → LF)", len(raw)
        )
        text = text.replace("\r", "")
    return text.encode("utf-8")


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
