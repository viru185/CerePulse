"""Transport layer: HTTP session, WebForms page state, AJAX delta handling."""

from __future__ import annotations

from cerepulse.transport.client import RETRYABLE_STATUS, HttpClient
from cerepulse.transport.webforms import (
    HIDDEN_FIELD,
    DeltaRecord,
    WebFormsState,
    async_postback_headers,
    async_postback_payload,
    find_postback_targets,
    find_script_manager,
    is_delta_response,
    parse_delta,
)

__all__ = [
    "HIDDEN_FIELD",
    "RETRYABLE_STATUS",
    "DeltaRecord",
    "HttpClient",
    "WebFormsState",
    "async_postback_headers",
    "async_postback_payload",
    "find_postback_targets",
    "find_script_manager",
    "is_delta_response",
    "parse_delta",
]
