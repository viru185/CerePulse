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
#: The leave application form. Reached only through the list's own postbacks; its page
#: methods (the comp-off lookups) are called on this path.
LEAVE_APPLICATION = "/Leave/EmpLeaveApp.aspx"

HOLIDAY_LIST = "/HolidayList.aspx"
