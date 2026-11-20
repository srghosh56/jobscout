"""Mask e-mail addresses and phone numbers in posting text before it leaves Postgres.

Postings often end with a recruiter's e-mail address or phone number. The columnar
snapshot is meant for analytics, so these are replaced by ``[email]`` and ``[phone]``.
``find_contacts`` uses the same patterns and backs the snapshot privacy check.

Phone numbers are matched conservatively for German and international formats: a number
must start with ``+`` or ``0`` (or follow a "Tel"/"Phone" label) and contain 7-15 digits.
Dates, salaries, years and postcodes therefore stay readable.
"""

from __future__ import annotations

import re

EMAIL_MASK = "[email]"
PHONE_MASK = "[phone]"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# +49 30 1234567 · 030/1234567 · (030) 123 456-78 · 0171-1234567 · +49 (0)30 12345678
_PHONE_BODY = r"\(?\d[\d \t/\-()]{5,22}\d"
PHONE_RE = re.compile(
    rf"(?<![\w+])(?:\+\d{{1,3}}[ \t]?{_PHONE_BODY}|\(?0{_PHONE_BODY})"
    rf"|(?i:(?<=tel\. )|(?<=tel: )|(?<=telefon: )|(?<=phone: )|(?<=telefon ))\d[\d \t/\-()]{{5,22}}\d"
)
_DATE_RE = re.compile(r"^\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}$")


def _digits(s: str) -> int:
    return sum(c.isdigit() for c in s)


def _is_phone(candidate: str) -> bool:
    return 7 <= _digits(candidate) <= 15 and not _DATE_RE.match(candidate.strip())


def find_contacts(text: str) -> list[str]:
    """E-mail addresses and phone numbers found in ``text`` (empty when clean)."""
    found = EMAIL_RE.findall(text)
    found += [m.group(0) for m in PHONE_RE.finditer(text) if _is_phone(m.group(0))]
    return found


def mask_contacts(text: str) -> str:
    """Replace e-mail addresses and phone numbers with placeholders."""
    text = EMAIL_RE.sub(EMAIL_MASK, text)

    def phone(m: re.Match[str]) -> str:
        return PHONE_MASK if _is_phone(m.group(0)) else m.group(0)

    return PHONE_RE.sub(phone, text)
