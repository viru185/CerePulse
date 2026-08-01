"""Controllers — the parts of the window that coordinate rather than render.

``MainWindow`` had grown to own navigation, sync orchestration, update checks, export and
error handling all at once, which made every new feature a change to the same 700-line
class. The split follows one rule: **anything that submits work to the task runner lives in
a controller; anything that touches a widget lives in the window.** Results cross the seam
as signals.
"""

from cerepulse.ui.controllers.navigation import NavigationController
from cerepulse.ui.controllers.sync import SyncController
from cerepulse.ui.controllers.updates import UpdateController

__all__ = ["NavigationController", "SyncController", "UpdateController"]
