"""Ticker spelling shared by Wikipedia, Nasdaq listings, and Yahoo."""

from __future__ import annotations

import re

_YAHOO = re.compile(r"^[A-Z]{1,5}(?:-[A-Z])?$")


def yahoo_symbol(value) -> str | None:
    """BRK.B and BRK/B become BRK-B. Preferreds and warrants do not match."""
    if value is None:
        return None
    text = str(value).strip().upper().replace(".", "-").replace("/", "-")
    if text in {"", "NAN", "NONE", "—", "-"}:
        return None
    if not _YAHOO.match(text):
        return None
    return text
