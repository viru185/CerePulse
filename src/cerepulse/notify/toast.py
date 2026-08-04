"""Windows toasts that survive being missed.

Qt's ``QSystemTrayIcon.showMessage`` is a ``Shell_NotifyIcon`` balloon. Windows 10 and 11
re-skin balloons to look like modern toasts, which is why nothing appeared to be wrong — but
a balloon is not a ``ToastNotification`` and the notification platform never sees it, so it
cannot be written to the store. Miss it and it is gone: nothing in the Action Center, nothing
in the notification history, no way to read it thirty seconds later.

A real toast needs three things, and two were already in place. The process declares an
AppUserModelID (``ui/app.py``), the Start Menu shortcut carries the same one
(``packaging/installer.iss``), and this supplies the third: handing the notification to
``ToastNotificationManager`` under that identity rather than to the shell's tray icon.

Everything here degrades rather than raises. A machine without the WinRT bindings, a build
where the AUMID never registered, a Windows version that refuses — all of them come back as
"not delivered" so the caller can fall back to the tray, which is exactly what the app did
before this module existed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from loguru import logger

#: Matches the identity the process claims and the installer stamps on both shortcuts. A
#: toast sent under an AUMID that no shortcut declares is shown and then discarded, which is
#: the failure mode this whole module exists to avoid.
APP_USER_MODEL_ID = "VirenHirpara.CerePulse"


@dataclass(slots=True)
class ToastSender:
    """Sends real Windows toasts, or reports honestly that it cannot.

    Constructed once and reused: building the notifier resolves COM interfaces, which is not
    something to do per notification.
    """

    #: Why toasts are unavailable, or empty when they are. Surfaced in Settings rather than
    #: only logged — "notifications are on and nothing arrives" needs an explanation on
    #: screen, and the last one lived in a DEBUG line nobody had switched on.
    unavailable: str = ""
    _toaster: object | None = None

    def __post_init__(self) -> None:
        if self.unavailable:
            # Constructed with a reason already in hand — a caller that knows toasts are not
            # wanted, or a test that needs the fallback path. Do not go looking for COM.
            return
        if sys.platform != "win32":
            self.unavailable = "Windows toasts are only available on Windows"
            return
        try:
            from windows_toasts import WindowsToaster

            self._toaster = WindowsToaster(APP_USER_MODEL_ID)
        except Exception as exc:  # noqa: BLE001 — any failure here means fall back, not crash
            self.unavailable = f"{type(exc).__name__}: {exc}"
            logger.warning("Windows toasts unavailable, falling back to the tray: {}", exc)

    @property
    def available(self) -> bool:
        return self._toaster is not None

    def send(self, title: str, body: str) -> bool:
        """Show one toast. Returns whether it was actually handed to Windows.

        A False here is not an error the user needs to see — it means the tray should have
        the notification instead — so it is logged and returned rather than raised.
        """
        if self._toaster is None:
            return False
        try:
            from windows_toasts import Toast

            toast = Toast()
            # Two lines, because that is what the platform renders before truncating and the
            # insight already comes as a headline and a detail.
            toast.text_fields = [title, body]
            self._toaster.show_toast(toast)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — same reasoning as construction
            logger.warning("Could not show a Windows toast: {}", exc)
            return False
        return True


__all__ = ["APP_USER_MODEL_ID", "ToastSender"]
