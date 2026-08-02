"""Background work for the UI.

The one hard rule in this layer: **no network or SQLite call happens on the GUI thread.**
Every service call goes through :class:`TaskRunner`, which runs it on a ``QThreadPool`` and
delivers the outcome back via signals — Qt marshals queued signal delivery onto the
receiving object's thread, so handlers touch widgets safely.

A single-slot pool is used deliberately. The portal is a stateful WebForms session where
every request carries page state, so two concurrent postbacks would interleave and invalidate
each other's ``__VIEWSTATE``. Serialising sync work is a correctness requirement, not a
politeness one.
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from loguru import logger
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from cerepulse.core.errors import CerePulseError
from cerepulse.core.logging_setup import workflow


class TaskSignals(QObject):
    """Signals emitted by a running task. Separate because QRunnable is not a QObject."""

    succeeded = Signal(object)
    failed = Signal(object)  # the exception instance
    finished = Signal()


class Task(QRunnable):
    """Runs one callable off the GUI thread and reports the outcome."""

    def __init__(self, name: str, operation: Callable[[], Any]) -> None:
        super().__init__()
        self.name = name
        self._operation = operation
        self.signals = TaskSignals()
        # Auto-delete would destroy this runnable — and the QObject carrying its signals —
        # as soon as run() returns, which can happen before the queued callbacks are
        # delivered to the GUI thread. TaskRunner holds the reference instead and drops it
        # once `finished` has actually arrived.
        self.setAutoDelete(False)

    @Slot()
    def run(self) -> None:
        try:
            with workflow(self.name):
                result = self._operation()
        except CerePulseError as exc:
            logger.warning("Task {!r} failed: {}", self.name, exc)
            self.signals.failed.emit(exc)
        except Exception as exc:  # noqa: BLE001 — a crashed worker must not kill the app
            logger.error("Task {!r} crashed:\n{}", self.name, traceback.format_exc())
            self.signals.failed.emit(exc)
        else:
            self.signals.succeeded.emit(result)
        finally:
            self.signals.finished.emit()


#: What each task name means to a person. A status line saying "analyze-day" is a status
#: line written for the developer; the queue is single-slot, so whatever is running is
#: genuinely what the user is waiting for and deserves saying properly.
TASK_LABELS = {
    "load-cache": "Reading cached data",
    "sync": "Syncing",
    "reload": "Reloading the month",
    "analyze-day": "Working out your day",
    "leave": "Fetching leave",
    "swipes": "Fetching swipe requests",
    "ledger": "Reading the leave ledger",
    "trends": "Building insights",
    "periods": "Asking which months are available",
    "history": "Fetching history",
    "scopes": "Checking what is up to date",
    "sync-day": "Fetching that day",
    "sign-in": "Signing in",
    "sign-out": "Signing out",
    "clear-cache": "Clearing cached data",
    "update-check": "Checking for updates",
    "update-check-manual": "Checking for updates",
}


def describe(name: str) -> str:
    """A human label for a task name, falling back to something harmless."""
    if name.startswith("scope-"):
        return "Fetching " + name.removeprefix("scope-").replace("_", " ")
    return TASK_LABELS.get(name, "Working")


class TaskRunner(QObject):
    """Submits tasks to a serialised background pool.

    ``busy`` reports whether anything is in flight; ``activity_changed`` says *what*, which
    is what the status line needs. Ten different task names used to collapse into the single
    word "Syncing…", so a two-minute history backfill and a cache read looked identical.
    """

    busy_changed = Signal(bool)
    #: The running task's label and how many are queued behind it. Empty label when idle.
    activity_changed = Signal(str, int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        # See the module docstring: concurrent postbacks would corrupt page state.
        self._pool.setMaxThreadCount(1)
        self._in_flight = 0
        #: Keeps each task alive until its signals have been delivered.
        self._active: set[Task] = set()
        #: Submission order, so the label names the task actually running rather than an
        #: arbitrary member of a set.
        self._queue: list[str] = []

    @property
    def busy(self) -> bool:
        return self._in_flight > 0

    @property
    def activity(self) -> str:
        return describe(self._queue[0]) if self._queue else ""

    def submit(
        self,
        name: str,
        operation: Callable[[], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> Task:
        """Queue an operation. Callbacks run on the GUI thread."""
        task = Task(name, operation)
        if on_success is not None:
            task.signals.succeeded.connect(on_success)
        if on_error is not None:
            task.signals.failed.connect(on_error)
        task.signals.finished.connect(lambda: self._task_finished(task))

        self._in_flight += 1
        self._active.add(task)
        self._queue.append(name)
        if self._in_flight == 1:
            self.busy_changed.emit(True)
        self._announce()

        self._pool.start(task)
        return task

    @Slot()
    def _task_finished(self, task: Task) -> None:
        self._active.discard(task)
        self._in_flight = max(0, self._in_flight - 1)
        if task.name in self._queue:
            self._queue.remove(task.name)
        if self._in_flight == 0:
            self.busy_changed.emit(False)
        self._announce()

    def _announce(self) -> None:
        # The queue depth matters: the pool is single-slot, so work submitted behind a
        # history backfill genuinely waits, and saying so is the difference between "slow"
        # and "broken".
        self.activity_changed.emit(self.activity, max(0, len(self._queue) - 1))

    def wait(self, timeout_ms: int = 10_000) -> bool:
        """Block until the queue drains. Used on shutdown and in tests, never in handlers."""
        return self._pool.waitForDone(timeout_ms)
