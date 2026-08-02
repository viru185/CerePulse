"""Filed leave, outdoor-duty and comp-off applications.

Distinct from a swipe request, which corrects a punch on a day already worked. These ask for
a day *not* to be worked, or record one that was worked outside the office — and unlike the
swipe list they carry the portal's own ``App. Id``, so identity does not have to be rebuilt
from the fields.

The portal serves all three through one page (``OutdoorDutyList.aspx`` for two of them, under
different ``odtype`` tokens) with the same grid and the same status filter, which is why one
model covers all three.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from cerepulse.models.swipe import SwipeStatus

#: The portal uses one status vocabulary across every request list it serves — the same
#: Approved / Rejected / In Process / Lapsed on swipes, leave, outdoor duty and comp-off.
#: Aliased rather than duplicated so a status parsed on one screen means the same on all.
RequestStatus = SwipeStatus


class ApplicationKind(Enum):
    """Which of the portal's request lists an application came from."""

    LEAVE = "leave"
    OUTDOOR_DUTY = "outdoor_duty"
    COMP_OFF = "comp_off"

    @property
    def label(self) -> str:
        return {
            ApplicationKind.LEAVE: "Leave",
            ApplicationKind.OUTDOOR_DUTY: "Outdoor duty",
            ApplicationKind.COMP_OFF: "Comp-off",
        }[self]


@dataclass(frozen=True, slots=True)
class Application:
    """One filed application and where it stands."""

    app_id: str
    kind: ApplicationKind
    start: date
    end: date
    #: Days applied for. Halves are real — the portal writes "0.50 CO+" and marks the date
    #: cell "1st Half" or "2nd Half".
    days: float
    remark: str
    status: RequestStatus
    #: The leave code ("CO-", "PL"). Only the leave list has this column; the outdoor-duty
    #: and comp-off grids do not, and an empty string is the honest answer there.
    leave_type: str = ""

    @property
    def is_open(self) -> bool:
        return self.status is RequestStatus.IN_PROCESS

    @property
    def is_single_day(self) -> bool:
        return self.start == self.end

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end
