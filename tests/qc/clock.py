"""Freeze ``date.today()`` and ``datetime.now()`` across the app, for repeatable screenshots.

The intelligence layer takes its clock as an argument, but the controllers and views read the
real one — which is right for the app and wrong for a screenshot that should look the same on
every run. Every ``cerepulse`` module that imported ``date`` or ``datetime`` by name gets a
subclass whose clock is pinned. Subclasses, so isinstance checks and arithmetic still work.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, tzinfo
from types import ModuleType


@contextmanager
def frozen(moment: datetime) -> Iterator[None]:
    """Pin the clock for every loaded ``cerepulse`` module, and put it back afterwards.

    Restoring matters under pytest: the patch is process-wide, and the tests that run after
    this one must see the real clock.
    """

    class FrozenDate(date):
        @classmethod
        def today(cls) -> FrozenDate:
            pinned = moment.date()
            return cls(pinned.year, pinned.month, pinned.day)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FrozenDateTime:
            pinned = moment if tz is None else moment.astimezone(tz)
            return cls(
                pinned.year,
                pinned.month,
                pinned.day,
                pinned.hour,
                pinned.minute,
                pinned.second,
                pinned.microsecond,
                pinned.tzinfo,
            )

        @classmethod
        def today(cls) -> FrozenDateTime:
            return cls.now()

    import_the_app()
    patched: list[tuple[ModuleType, str, object]] = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("cerepulse") or module is None:
            continue
        for attribute, real, fake in (
            ("date", date, FrozenDate),
            ("datetime", datetime, FrozenDateTime),
        ):
            if getattr(module, attribute, None) is real:
                patched.append((module, attribute, real))
                setattr(module, attribute, fake)
    try:
        yield
    finally:
        for module, attribute, real in patched:
            setattr(module, attribute, real)


def import_the_app() -> None:
    """Load every module the window touches, so :func:`frozen` can reach them all."""
    import importlib
    import pkgutil

    import cerepulse

    for info in pkgutil.walk_packages(cerepulse.__path__, prefix="cerepulse."):
        if info.name.endswith("__main__"):
            continue
        importlib.import_module(info.name)
