from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable

from azure.identity import AzureCliCredential

from .config import DYNAMIC_SESSIONS_SCOPE, FOUNDRY_SCOPE, SANDBOX_SCOPE, SampleConfig


def create_credential(config: SampleConfig) -> AzureCliCredential:
    if not config.subscription_id or not config.tenant_id:
        raise ValueError("Subscription and expected tenant are required")
    return AzureCliCredential(subscription=config.subscription_id, process_timeout=30)


def verify_token_tenant(token: str, expected_tenant_id: str) -> None:
    """Check the unsigned JWT payload for local account selection; not signature validation."""
    try:
        segments = token.split(".")
        if len(segments) != 3 or not expected_tenant_id:
            raise ValueError
        payload = segments[1]
        decoded = base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
        claims = json.loads(decoded)
        tid = claims.get("tid") if isinstance(claims, dict) else None
        if not isinstance(tid, str) or tid.casefold() != expected_tenant_id.casefold():
            raise ValueError
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError):
        raise ValueError("Token tenant could not be verified against configured tenant") from None


def _provider(credential: AzureCliCredential, tenant_id: str, scope: str) -> Callable[[], str]:
    import time
    cached_token: str | None = None
    cached_expires_on: float = 0.0

    def get_token() -> str:
        nonlocal cached_token, cached_expires_on
        now = time.time()
        if cached_token is not None and cached_expires_on > now + 120:
            return cached_token
        access_token = credential.get_token(scope)
        token = access_token.token
        verify_token_tenant(token, tenant_id)
        expires_on = getattr(access_token, "expires_on", None)
        if isinstance(expires_on, (int, float)) and expires_on > now + 120:
            cached_token = token
            cached_expires_on = float(expires_on)
        return token
    return get_token


def foundry_token_provider(credential: AzureCliCredential, tenant_id: str) -> Callable[[], str]:
    return _provider(credential, tenant_id, FOUNDRY_SCOPE)


def dynamic_sessions_token_provider(credential: AzureCliCredential, tenant_id: str) -> Callable[[], str]:
    return _provider(credential, tenant_id, DYNAMIC_SESSIONS_SCOPE)


def sandbox_token_provider(credential: AzureCliCredential, tenant_id: str) -> Callable[[], str]:
    return _provider(credential, tenant_id, SANDBOX_SCOPE)
