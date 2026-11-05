import base64
import json
from types import SimpleNamespace

import pytest

from aca_agent_sample import auth
from aca_agent_sample.config import DYNAMIC_SESSIONS_SCOPE, FOUNDRY_SCOPE, SANDBOX_SCOPE, SampleConfig


def jwt(tid):
    body = base64.urlsafe_b64encode(json.dumps({"tid": tid}).encode()).decode().rstrip("=")
    return "header." + body + ".signature"


def test_credential_uses_subscription_only(monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "AzureCliCredential", lambda **kwargs: calls.append(kwargs) or object())
    auth.create_credential(SampleConfig(subscription_id="sub", tenant_id="tenant"))
    assert calls == [{"subscription": "sub", "process_timeout": 30}]


def test_tenant_mismatch_and_malformed_are_rejected():
    auth.verify_token_tenant(jwt("TENANT"), "tenant")
    for token in [jwt("another"), "not-a-jwt", "a.@@@.b", jwt(None)]:
        with pytest.raises(ValueError, match="tenant"):
            auth.verify_token_tenant(token, "tenant")


def test_separate_providers_request_expected_scopes_and_check_tenant():
    scopes = []
    class Credential:
        def get_token(self, scope):
            scopes.append(scope)
            return SimpleNamespace(token=jwt("tenant"))
    credential = Credential()
    for factory in (auth.foundry_token_provider, auth.dynamic_sessions_token_provider, auth.sandbox_token_provider):
        assert factory(credential, "tenant")() == jwt("tenant")
    assert scopes == [FOUNDRY_SCOPE, DYNAMIC_SESSIONS_SCOPE, SANDBOX_SCOPE]
    with pytest.raises(ValueError):
        auth.foundry_token_provider(credential, "different")()


@pytest.mark.parametrize("factory", [auth.foundry_token_provider, auth.dynamic_sessions_token_provider,
                                      auth.sandbox_token_provider])
def test_every_service_rejects_mismatched_tenant_before_returning_token(factory):
    class Credential:
        def get_token(self, scope):
            return SimpleNamespace(token=jwt("another"))

    with pytest.raises(ValueError, match="tenant"):
        factory(Credential(), "expected")()


@pytest.mark.parametrize("claims", [{}, [], {"tid": 1}, {"tid": ""}])
def test_missing_or_invalid_tenant_claim_is_rejected(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    with pytest.raises(ValueError, match="tenant"):
        auth.verify_token_tenant("header." + body + ".signature", "tenant")
