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
        self._stopped = threading.Event()

    def stop(self) -> None:
        """Take the page down. Safe to call twice, and called from two directions.

        Once by the request handler the moment the page has been served, and once by the
        timer for a page nobody ever fetched. Whichever gets there first wins; the other
        returns immediately rather than blocking on a server that is already closing.
        """
        if self._stopped.is_set():
            return
        self._stopped.set()
        # Logged here rather than after the join: this runs on the caller's thread, where
        # the sink is known to still exist. The teardown below is a detached daemon and can
        # outlive whatever configured logging, which is a good way to end up writing to a
        # closed file to announce that a server closed.
        logger.info("Taking the handover page down")
        # Never from the serving thread itself: `shutdown` waits for the serve loop to
        # exit, and the serve loop is what would be calling it.
        threading.Thread(target=self._shutdown, name="cerepulse-handover-stop", daemon=True).start()

    def _shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def prepare(client: HttpClient, username: str, password: str) -> Handover:
    """Build the auto-submitting login page and serve it once from localhost.

    Where the browser ends up is the portal's decision, not ours: the form posts top-level
    to the login endpoint and the portal redirects to its own home page. A ``landing``
    argument used to be threaded through here to send the browser somewhere specific; it
    never worked, and the attempt to make it work is what broke signing in. See :func:`_page`.

    The login form is scraped through a **cookie-less** client. ``hEnSa`` is the AES key the
    password is encrypted against and the portal mints it per page load; scraping it through
    the app's authenticated client bound it to a session the browser does not have, which is
    the wrong session to be logging in from twice over.
    """
    state = _login_state(client)
    fields = dict(state.postback("btnLogin", ""))
    fields["txtUser"] = username
    fields["txtPassword"] = encrypt_password(password, state.require("hEnSa"))

    page = _page(client.url_for(pages.LOGIN), fields)

    token = secrets.token_urlsafe(16)
    # The handler needs the Handover to shut itself down, and the Handover needs the server
    # the handler is part of. A one-element list breaks the cycle without a global.
    handle: list[Handover] = []
    server = HTTPServer(("127.0.0.1", 0), _handler_for(token, page, handle))
    thread = threading.Thread(target=server.serve_forever, name="cerepulse-handover", daemon=True)
    thread.start()

    port = server.server_address[1]
    handover = Handover(f"http://127.0.0.1:{port}/{token}", server, thread)
    handle.append(handover)

    # A page nobody fetches must not sit there holding a credential until the app exits.
    # `server.timeout` does not do this: it only applies to `handle_request`, and the thread
    # runs `serve_forever`, which ignores it — so this promise was previously unkept.
    threading.Timer(TIMEOUT_SECONDS, handover.stop).start()

    logger.info("Handover page ready on 127.0.0.1:{}", port)
    return handover


def _login_state(client: HttpClient) -> WebFormsState:
    """Scrape the login form through a throwaway client with an empty cookie jar.

    Two reasons not to use the app's own client. It is signed in, so the GET would carry
    ``.ASPXFORMSAUTH`` to a login page that has no business seeing it. And every request the
    app makes stamps the clock ``looks_evicted`` reads — so scraping through it made the app
    look freshly active at the exact moment it is about to hand the session away, which is
    the state that makes the next expiry read as a theft rather than an expected loss.
    """
    with HttpClient(client.config) as anonymous:
        response = anonymous.get(pages.LOGIN)
        if response.status_code != 200:
            raise ProtocolError(f"Login page returned {response.status_code}")
        return WebFormsState.from_html(response.text)


def _page(action: str, fields: dict[str, str]) -> bytes:
    """A form that posts itself, at the top level, the moment it loads.

    Rendered as hidden inputs rather than assembled as a URL: the credential must not end
    up in a query string, where it would be written to the browser's history and to every
    proxy log on the way.

    **The post must be top-level.** 0.11 submitted it into a hidden iframe so this page
    would survive its own submission and could then navigate on to a deep link. It does
    survive — and the sign-in silently stops working, because the iframe is cross-origin.
    The portal's cookies are third-party in that context, which Chrome and Edge now block or
    partition by default: the login genuinely succeeds inside the frame and the
    ``.ASPXFORMSAUTH`` it issues never reaches the top-level browsing context. The user
    lands on the portal looking signed out.

    Posting at the top level makes those cookies first-party, which is the whole mechanism.
    The cost is that control is handed to the portal the instant the response arrives, so
    the browser goes wherever the portal's own login sends it — its home page. That is the
    trade, taken deliberately this time: signing in is the point, and the deep link was
    never worth breaking it for.
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
<script>document.getElementById("f").submit();</script>
</body>""".encode()


def _handler_for(token: str, page: bytes, handle: list[Handover]) -> type[BaseHTTPRequestHandler]:
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
            self.wfile.flush()

            # Served once, and that is the whole life of it. Without this the page — and the
            # encrypted credential in it — stayed available on localhost until the app quit,
            # which the module docstring already claimed was not the case.
            if handle:
                handle[0].stop()

        def log_message(self, format: str, *args: object) -> None:
            """Silence http.server's stderr logging; it would print the token."""

    return Handler


__all__ = ["Handover", "prepare"]
