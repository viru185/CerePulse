"""Opening the portal in a browser without making the user type their password again.

The obvious idea — hand the browser the app's session cookie — is not possible. A cookie
belongs to an origin and only the browser can set one for that origin; nothing outside it
can inject one. So the app cannot *share* its session with the browser, and any claim to
the contrary would be a lie told by a progress dialog.

What it can do is sign the browser in. A one-shot page served from ``127.0.0.1`` carries a
form that posts straight to the portal's own login endpoint, with the username and the
password encrypted against a ``hEnSa`` scraped moments earlier. The browser submits it,
receives its own cookies, and lands inside the portal. The user types nothing.

That creates a *second* session, and SpineHR allows one — so it ends the app's. That is not
a side effect to be hidden; it is the point. The handover is deliberate, the app knows it
has given the session away, and it stands down instead of grabbing it back. Anything else
would have the two fighting over one session all afternoon.

Two deliberate choices about the credential. It never touches disk: the page is served from
memory by a local server that answers exactly one request and then stops. And the password
travels in the portal's own encrypted form, not in plaintext — the same blob the login page
itself would have posted.
"""

from __future__ import annotations

import html
import secrets
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urljoin

from loguru import logger

from cerepulse.auth.crypto import encrypt_password
from cerepulse.core.errors import ProtocolError
from cerepulse.transport import pages
from cerepulse.transport.client import HttpClient
from cerepulse.transport.webforms import WebFormsState

#: The handover page is useless after one fetch, and a local server that outlives its
#: purpose is a credential sitting in memory for no reason.
TIMEOUT_SECONDS = 30


class Handover:
    """A single-use local page that logs a browser into the portal."""

    def __init__(self, url: str, server: HTTPServer, thread: threading.Thread) -> None:
        self.url = url
        self._server = server
        self._thread = thread

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def prepare(client: HttpClient, username: str, password: str, *, landing: str = "") -> Handover:
    """Build the auto-submitting login page and serve it once from localhost.

    ``landing`` is where to send the browser after login. It is a portal path, and the
    portal refuses a deep link that carries no menu privilege token — so the default is the
    home page, which is always reachable, rather than a URL that would land on "you do not
    have sufficient privileges".
    """
    state = _login_state(client)
    fields = dict(state.postback("btnLogin", ""))
    fields["txtUser"] = username
    fields["txtPassword"] = encrypt_password(password, state.require("hEnSa"))

    action = client.url_for(pages.LOGIN)
    target = urljoin(client.base_url + "/", (landing or pages.HOME).lstrip("/"))
    page = _page(action, fields, target)

    token = secrets.token_urlsafe(16)
    server = HTTPServer(("127.0.0.1", 0), _handler_for(token, page))
    server.timeout = TIMEOUT_SECONDS
    thread = threading.Thread(target=server.serve_forever, name="cerepulse-handover", daemon=True)
    thread.start()

    port = server.server_address[1]
    logger.info("Handover page ready on 127.0.0.1:{}", port)
    return Handover(f"http://127.0.0.1:{port}/{token}", server, thread)


def _login_state(client: HttpClient) -> WebFormsState:
    response = client.get(pages.LOGIN)
    if response.status_code != 200:
        raise ProtocolError(f"Login page returned {response.status_code}")
    return WebFormsState.from_html(response.text)


def _page(action: str, fields: dict[str, str], landing: str) -> bytes:
    """A form that posts itself the moment it loads.

    Rendered as hidden inputs rather than assembled as a URL: the credential must not end
    up in a query string, where it would be written to the browser's history and to every
    proxy log on the way.
    """
    inputs = "\n".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">'
        for name, value in fields.items()
    )
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Opening SpineHR…</title>
<body style="font-family: system-ui; padding: 2rem; color: #444">
<p>Signing you in to SpineHR…</p>
<p style="color:#888;font-size:.9em">
CerePulse has handed its session over and paused. It will not sign back in until you ask it
to.</p>
<form id="f" method="post" action="{html.escape(action)}">{inputs}</form>
<script>
  document.title = "Opening SpineHR";
  sessionStorage.setItem("cerepulse-landing", {landing!r});
  document.getElementById("f").submit();
</script>
</body>""".encode()


def _handler_for(token: str, page: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            # The token makes the URL unguessable, so nothing else on the machine can fetch
            # the credential simply by scanning localhost ports.
            if self.path.lstrip("/") != token:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            # Never cached, never reused.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, format: str, *args: object) -> None:
            """Silence http.server's stderr logging; it would print the token."""

    return Handler


__all__ = ["Handover", "prepare"]
