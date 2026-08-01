"""One CerePulse per data directory.

A second copy is not merely untidy, it is harmful. Two processes mean two SQLite
connections to one file, two tray icons, two auto-refresh timers, and — worst — two
clients driving the same stateful WebForms session, where each postback carries a
``__VIEWSTATE`` the other has already invalidated. That is precisely the hazard
``TaskRunner``'s single-slot pool exists to prevent inside one process.

The key is derived from the data directory rather than the app name, so a portable copy on
a USB stick and an installed copy are genuinely separate applications and do not block each
other — they have separate caches and separate sessions, which is the whole point of the
portable build.

Qt's local sockets are named pipes on Windows. A pipe left behind by a crash cannot be
connected to, so a failed connection followed by a successful listen is taken as "no live
instance" rather than as an error.
"""

from __future__ import annotations

from hashlib import blake2s

from loguru import logger
from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from cerepulse.core import paths

#: Long enough that a second launch never waits noticeably, short enough that a wedged
#: first instance does not hold the second hostage.
CONNECT_TIMEOUT_MS = 500

#: What a second launch sends. The content does not matter; arrival is the message.
WAKE = b"show\n"


def instance_key(name: str = "cerepulse") -> str:
    """A pipe name unique to this installation's data directory.

    Hashed rather than embedded: the path can contain characters a named pipe cannot, and
    Windows caps the name length.
    """
    digest = blake2s(str(paths.data_root()).casefold().encode("utf-8"), digest_size=8).hexdigest()
    return f"{name}-{digest}"


class SingleInstance(QObject):
    """Claims the instance lock, or reports that another copy already holds it."""

    #: Another launch asked for the window. Connect this to raising and focusing it.
    wake_requested = Signal()

    def __init__(self, key: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._key = key or instance_key()
        self._server: QLocalServer | None = None

    def try_claim(self) -> bool:
        """True when this process is the only one. False means another copy was woken."""
        if self._notify_existing():
            return False

        server = QLocalServer(self)
        # Clear a pipe orphaned by a crash; we only reach here having failed to connect,
        # so nothing live is listening on it.
        QLocalServer.removeServer(self._key)
        if not server.listen(self._key):
            # Cannot listen and cannot connect. Rather than refuse to start, run anyway —
            # a missing guard is a lesser fault than an app that will not open.
            logger.warning("Could not claim the instance lock: {}", server.errorString())
            return True

        server.newConnection.connect(self._on_connection)
        self._server = server
        return True

    def _notify_existing(self) -> bool:
        """Ask a running copy to show itself. True when one answered."""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(CONNECT_TIMEOUT_MS):
            return False

        socket.write(WAKE)
        socket.waitForBytesWritten(CONNECT_TIMEOUT_MS)
        socket.disconnectFromServer()
        logger.info("Another CerePulse is already running; asked it to come forward")
        return True

    def _on_connection(self) -> None:
        if self._server is None:
            return
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.readyRead.connect(lambda: self.wake_requested.emit())
            connection.disconnected.connect(connection.deleteLater)

    def release(self) -> None:
        """Give up the lock. Called on shutdown so a restart is never blocked."""
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._key)
            self._server = None


__all__ = ["CONNECT_TIMEOUT_MS", "WAKE", "SingleInstance", "instance_key"]
