"""Working-time policy.

Defaults match what the attendance grid implies for the GS shift (8:00 AM to 7:00 PM span,
with a nine-hour expectation): eight hours of work, one hour of break, a nine-hour shift
span. All three are configurable because they are company policy, not protocol facts.
"""

from __future__ import annotations

from dataclasses import dataclass

from cerepulse.models.values import Duration


@dataclass(frozen=True, slots=True)
class ShiftPolicy:
    """Targets a working day is measured against."""

    work_target: Duration = Duration(8 * 60)
    break_target: Duration = Duration(60)
    shift_span: Duration = Duration(9 * 60)

    @classmethod
    def default(cls) -> ShiftPolicy:
        return cls()

    def owed_for(self, portion: float) -> Duration:
        """The target a day of this size is measured against.

        A half day owes half. The portal carries the portion on every row and the app
        parsed and stored it from the first release without ever reading it back — so a
        half day off booked a four-hour deficit into the month bank, the week delta, the
        short-day count and the attention highlight, each one an invented debt.
        """
        if 0 < portion < 1:
            return Duration(round(self.work_target.minutes * portion))
        return self.work_target

    def __post_init__(self) -> None:
        if self.work_target.minutes <= 0:
            raise ValueError("work_target must be positive")
        if self.break_target.minutes < 0:
            raise ValueError("break_target cannot be negative")
        if self.shift_span.minutes <= 0:
            raise ValueError("shift_span must be positive")
