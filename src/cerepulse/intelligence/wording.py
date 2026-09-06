"""Turning what the portal wrote into what a person would say.

Three sources describe one event in three dialects. The leave grid ends every remark with the
approver's empty label (``… 🏡✈️ <b>Manager :</b>``); the leave register and the muster carry
the same remark with the emoji mangled to ``????``; and every grid counts in ``day(s)``.
Everything here is pure string work, shared by the timeline and the leave cards so the two
never disagree about how a reason reads.
"""

from __future__ import annotations

import re
from datetime import date

#: What the portal writes in Remarks on an ordinary day; it says nothing.
ROUTINE_REMARK = "Attendance Muster"

#: Labels the portal appends to a remark with nothing after them. Only the ones seen in
#: captures: an unseen label would survive, which is the safe failure.
_EMPTY_LABELS = ("Manager", "Approver", "HOD", "HR")
_EMPTY_LABEL = re.compile(r"\s*\b(?:" + "|".join(_EMPTY_LABELS) + r")\s*:\s*$", re.IGNORECASE)
#: A run of question marks standing on its own is an emoji the portal could not encode.
#: ``Why??`` keeps its text; ``home ????`` loses the tail.
_MANGLED = re.compile(r"(?<![\w?])\?{2,}(?![\w?])")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")
_ESSENCE = re.compile(r"[^a-z0-9]+")


def clean_remark(text: str) -> str:
    """The remark as the person wrote it, without the portal's additions."""
    text = " ".join((text or "").split())
    if text.casefold() == ROUTINE_REMARK.casefold():
        return ""
    while True:
        stripped = _EMPTY_LABEL.sub("", text)
        if stripped == text:
            break
        text = stripped
    text = _MANGLED.sub("", text)
    text = _SPACE_BEFORE_PUNCTUATION.sub(r"\1", text)
    return " ".join(text.split()).strip(" -—|·")


def same_reason(a: str, b: str) -> bool:
    """Whether two remarks are one reason written twice.

    Compared on letters and digits alone, so the application's ``🏡✈️`` and the muster's
    ``????`` are the same sentence. A blank on either side agrees: it has nothing to disagree
    with, and the muster's blank is the usual case.
    """
    first, second = _essence(a), _essence(b)
    return not first or not second or first == second


def _essence(text: str) -> str:
    return _ESSENCE.sub("", clean_remark(text).casefold())


def days_phrase(days: float) -> str:
    """``Half day`` / ``1 day`` / ``2 days`` / ``5.5 days``. Never ``day(s)``."""
    if days == 0.5:
        return "Half day"
    return f"{days:g} day{'s' if days != 1 else ''}"


def day_label(value: date) -> str:
    """``Thu 27 Aug`` — the timeline's date, without a leading zero."""
    return value.strftime("%a %d %b").replace(" 0", " ")


__all__ = ["ROUTINE_REMARK", "clean_remark", "day_label", "days_phrase", "same_reason"]
