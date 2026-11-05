import pytest

from aca_agent_sample.config import SampleConfig, load_config, mask_sensitive, parse_bool, validate_https_url


@pytest.mark.parametrize(("value", "expected"), [("false", False), ("0", False), ("", False), ("TRUE", True), ("1", True), ("yes", True)])
def test_parse_bool(value, expected):
    assert parse_bool(value) is expected


@pytest.mark.parametrize("value", ["no", "off", "2", "maybe"])
def test_parse_bool_rejects_other_values(value):
    with pytest.raises(ValueError):
        parse_bool(value)


def test_env_precedence_and_false(monkeypatch):
    monkeypatch.setattr("aca_agent_sample.config.dotenv_values", lambda *a, **kw: {"ENABLE_LIVE_AZURE": "true", "AZURE_SUBSCRIPTION_ID": "file"})
    config = load_config("unused.env", {"ENABLE_LIVE_AZURE": "false", "AZURE_SUBSCRIPTION_ID": "env"})
    assert config.enable_live_azure is False
    assert config.subscription_id == "env"


@pytest.mark.parametrize("value", ["<YOUR_SUBSCRIPTION_ID>", "prefix<YOUR_VALUE>suffix", "${UNSET}"])
def test_reject_placeholders(value):
    with pytest.raises(ValueError, match="placeholder"):
        SampleConfig(subscription_id=value).validate()


@pytest.mark.parametrize("url", ["http://example.test/", "https:///missing", "https://user:pass@example.test/", "https://example.test/?x=1", "https://<YOUR_RESOURCE>.example.test/", "https://example.test:bad/"])
def test_https_validation_rejects_invalid(url):
    with pytest.raises(ValueError):
        validate_https_url(url)


def test_https_validation_accepts_valid_url():
    validate_https_url("https://example.test/openai/v1/")


def test_masking_and_repr():
    secret = "an-example-sensitive-value"
    assert secret not in mask_sensitive(secret)
    assert mask_sensitive("") == "[NOT SET]"
    assert secret not in repr(SampleConfig(subscription_id=secret, tenant_id=secret, foundry_openai_base_url=secret))


def test_required_resources_checked_independently():
    config = SampleConfig(subscription_id="sample", tenant_id="tenant", foundry_openai_base_url="https://example.test/openai/v1/")
    config.validate("foundry")
    with pytest.raises(ValueError, match="dynamic_sessions_resource_group"):
        config.validate("dynamic-session")
    with pytest.raises(ValueError, match="sandbox_resource_group"):
        config.validate("sandbox")


def test_scopes_are_not_configurable_to_untrusted_audience():
    with pytest.raises(ValueError, match="scope"):
        SampleConfig(foundry_token_scope="https://example.test/.default").validate()



@pytest.mark.parametrize("path", ["/api/projects/example", "/v1/", "/", "/openai/v1/chat/completions"])
def test_foundry_configuration_rejects_non_inference_base_path(path):
    with pytest.raises(ValueError, match="openai/v1"):
        SampleConfig(foundry_openai_base_url="https://example.test" + path).validate()
