"""Working-time policy.

Defaults match what the attendance grid implies for the GS shift (8:00 AM to 7:00 PM span,
with a nine-hour expectation): eight hours of work, one hour of break, a nine-hour shift
span. All three are configurable because they are company policy, not protocol facts.

**A day is measured by its span** — first In to last Out, breaks inside it — against
:attr:`ShiftPolicy.day_target`, the shift span. Nine hours between arriving and leaving is a
complete shift, however the time inside was spent; that is the rule as the company states it,
and it is what the portal's own ``Tot. Hrs.`` measures. Until 0.17 the app measured *net*
work against eight hours instead, which disagreed both ways: a short lunch "completed" the
day at 17:15 while the screen counted down to 18:00, and a long one left a full nine-hour day
short. Net work is still shown, as detail; it decides nothing.
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

    @property
    def day_target(self) -> Duration:
        """What a full working day owes: the span from first In to last Out."""
        return self.shift_span

    @property
    def stretch_day(self) -> Duration:
        """The longest day still worth calling ordinary when planning to catch up.

        A day plus its break allowance again — the same hour of headroom the old net-work
        rule allowed between an eight-hour target and a nine-hour span.
        """
        return self.day_target + self.break_target

    def owed_for(self, portion: float) -> Duration:
        """The span a day of this size is measured against.

        A half day owes half. The portal carries the portion on every row and the app
        parsed and stored it from the first release without ever reading it back — so a
        half day off booked a four-hour deficit into the month bank, the week delta, the
        short-day count and the attention highlight, each one an invented debt.
        """
        if 0 < portion < 1:
            return Duration(round(self.day_target.minutes * portion))
        return self.day_target

    def __post_init__(self) -> None:
        if self.work_target.minutes <= 0:
            raise ValueError("work_target must be positive")
        if self.break_target.minutes < 0:
            raise ValueError("break_target cannot be negative")
        if self.shift_span.minutes <= 0:
            raise ValueError("shift_span must be positive")
        if self.shift_span < self.work_target + self.break_target:
            raise ValueError("shift_span cannot be shorter than work_target plus break_target")
