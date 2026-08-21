from __future__ import annotations

import re


_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_LEI_RE = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def normalize_cik(value: object) -> str | None:
    """Return a ten-digit SEC CIK or reject an ambiguous identifier."""

    text = str(value or "").strip()
    if not text:
        return None
    if not text.isdigit() or len(text) > 10 or int(text) <= 0:
        raise ValueError(f"Invalid SEC CIK: {value!r}")
    return text.zfill(10)


def _mod97(value: str) -> int:
    remainder = 0
    for character in value:
        expanded = str(ord(character) - 55) if character.isalpha() else character
        for digit in expanded:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def normalize_lei(value: object) -> str | None:
    """Normalize and checksum-validate an ISO 17442 LEI."""

    text = str(value or "").strip().upper()
    if not text:
        return None
    if not _LEI_RE.fullmatch(text) or _mod97(text) != 1:
        raise ValueError(f"Invalid LEI: {value!r}")
    return text


def _valid_luhn(value: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(value)):
        digit = int(character)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def normalize_isin(value: object) -> str | None:
    """Normalize and checksum-validate an ISO 6166 ISIN."""

    text = str(value or "").strip().upper()
    if not text:
        return None
    if not _ISIN_RE.fullmatch(text):
        raise ValueError(f"Invalid ISIN: {value!r}")
    expanded = "".join(
        str(ord(character) - 55) if character.isalpha() else character
        for character in text
    )
    if not _valid_luhn(expanded):
        raise ValueError(f"Invalid ISIN checksum: {value!r}")
    return text


def normalize_country_code(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if len(text) != 2 or not text.isalpha():
        raise ValueError(f"Invalid ISO country code: {value!r}")
    return text


def normalize_mic(value: object) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if len(text) != 4 or not text.isalnum():
        raise ValueError(f"Invalid market identifier code: {value!r}")
    return text
