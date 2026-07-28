"""Known portal page paths, discovered from the navigation shell of ``start_new.aspx``.

Kept in one place so a vendor-side URL change is a one-line fix rather than a hunt.
"""

from __future__ import annotations

LOGIN = "/login.aspx"
HOME = "/start_new.aspx"
LOGOFF = "/LogOff.aspx"

ATTENDANCE_REPORT = "/Atten/MyAttendanceReport.aspx"
ATTENDANCE_CALENDAR = "/Atten/EmployeeWiseCalender.aspx"
SWIPE_REQUESTS = "/Atten/SwipeRequestList.aspx"
SHIFT_CHANGE_REQUEST = "/Atten/ShiftChangeRequest.aspx"

LEAVE_BALANCE = "/BalanceLeave.aspx"
LEAVE_LIST = "/Leave/LeaveList.aspx"
LEAVE_BALANCE_DETAIL = "/Leave/LeaveBalanceDetail.aspx"

HOLIDAY_LIST = "/HolidayList.aspx"
