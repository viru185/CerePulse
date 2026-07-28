from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx

from cerepulse.core.config import AppConfig
from cerepulse.core.errors import ServerUnavailableError, TransportError
from cerepulse.transport.client import HttpClient

BASE = "https://cerebulb.spinehr.in"


@pytest.fixture
def config() -> AppConfig:
    # No real backoff — these tests exercise retry logic, not the clock.
    return AppConfig.from_dict({"network": {"max_retries": 2, "backoff_factor": 0.0}})


@pytest.fixture
def client(config: AppConfig) -> Iterator[HttpClient]:
    with HttpClient(config) as http:
        yield http


def test_resolves_relative_paths(client: HttpClient) -> None:
    assert client.url_for("/login.aspx") == f"{BASE}/login.aspx"
    assert client.url_for("Atten/MyAttendanceReport.aspx") == (
        f"{BASE}/Atten/MyAttendanceReport.aspx"
    )


def test_absolute_urls_pass_through(client: HttpClient) -> None:
    assert client.url_for("https://example.com/x") == "https://example.com/x"


@respx.mock
def test_redirects_are_not_followed(client: HttpClient) -> None:
    """The 302 to start_new.aspx *is* the login success signal — it must stay visible."""
    respx.post(f"{BASE}/login.aspx").mock(
        return_value=httpx.Response(302, headers={"location": "/start_new.aspx"})
    )
    response = client.post("/login.aspx", data={})
    assert response.status_code == 302
    assert response.headers["location"] == "/start_new.aspx"


@respx.mock
def test_retries_then_succeeds_on_transient_5xx(client: HttpClient) -> None:
    route = respx.get(f"{BASE}/start_new.aspx").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, text="ok"),
        ]
    )
    assert client.get("/start_new.aspx").text == "ok"
    assert route.call_count == 2


@respx.mock
def test_gives_up_after_max_retries(client: HttpClient) -> None:
    route = respx.get(f"{BASE}/start_new.aspx").mock(return_value=httpx.Response(502))
    with pytest.raises(ServerUnavailableError):
        client.get("/start_new.aspx")
    assert route.call_count == 3  # 1 initial + 2 retries


@respx.mock
def test_retries_timeouts(client: HttpClient) -> None:
    route = respx.get(f"{BASE}/start_new.aspx").mock(
        side_effect=[httpx.ConnectTimeout("slow"), httpx.Response(200, text="ok")]
    )
    assert client.get("/start_new.aspx").status_code == 200
    assert route.call_count == 2


@respx.mock
def test_transport_failure_becomes_a_domain_error(client: HttpClient) -> None:
    respx.get(f"{BASE}/start_new.aspx").mock(side_effect=httpx.ConnectError("dns"))
    with pytest.raises(TransportError):
        client.get("/start_new.aspx")


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404, 500])
def test_non_retryable_statuses_are_returned_immediately(client: HttpClient, status: int) -> None:
    """Auth and client errors must not be retried (Chapter 03 section 8)."""
    route = respx.get(f"{BASE}/x.aspx").mock(return_value=httpx.Response(status))
    assert client.get("/x.aspx").status_code == status
    assert route.call_count == 1


@respx.mock
def test_cookies_persist_across_requests(client: HttpClient) -> None:
    respx.get(f"{BASE}/a.aspx").mock(
        return_value=httpx.Response(200, headers={"set-cookie": ".ASPXFORMSAUTH=ticket; path=/"})
    )
    echo = respx.get(f"{BASE}/b.aspx").mock(return_value=httpx.Response(200))

    client.get("/a.aspx")
    assert client.has_cookie(".ASPXFORMSAUTH") is True

    client.get("/b.aspx")
    assert "ASPXFORMSAUTH" in echo.calls.last.request.headers.get("cookie", "")


@respx.mock
def test_clear_cookies(client: HttpClient) -> None:
    respx.get(f"{BASE}/a.aspx").mock(
        return_value=httpx.Response(200, headers={"set-cookie": ".ASPXFORMSAUTH=t; path=/"})
    )
    client.get("/a.aspx")
    client.clear_cookies()
    assert client.has_cookie(".ASPXFORMSAUTH") is False


@respx.mock
def test_sends_a_browser_user_agent(client: HttpClient) -> None:
    route = respx.get(f"{BASE}/x.aspx").mock(return_value=httpx.Response(200))
    client.get("/x.aspx")
    assert "Mozilla/5.0" in route.calls.last.request.headers["user-agent"]
