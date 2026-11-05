from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from dotenv import dotenv_values

FOUNDRY_SCOPE = "https://ai.azure.com/.default"
DYNAMIC_SESSIONS_SCOPE = "https://dynamicsessions.io/.default"
SANDBOX_SCOPE = "https://dynamicsessions.io/.default"
class ConfigError(ValueError):
    pass


_PLACEHOLDER = re.compile(r"<[^<>]+>|\$\{[^}]+\}")


def parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", ""}:
        return False
    raise ConfigError("Invalid boolean; use true, 1, yes, false, 0, or empty")


def mask_sensitive(value: str | None) -> str:
    return "[REDACTED]" if value else "[NOT SET]"


def validate_https_url(value: str, name: str = "URL") -> None:
    if _PLACEHOLDER.search(value):
        raise ConfigError(f"{name} contains a placeholder")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ConfigError(f"{name} must be a valid HTTPS URL") from None
    if (parsed.scheme.lower() != "https" or not hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() for c in value) or "\\" in value):
        raise ConfigError(f"{name} must be a valid HTTPS URL without credentials, query, or fragment")


def validate_foundry_base_url(value: str) -> None:
    validate_https_url(value, "foundry_openai_base_url")
    if urlsplit(value).path not in {"/openai/v1", "/openai/v1/"}:
        raise ConfigError("foundry_openai_base_url must use /openai/v1/, not a project endpoint")


@dataclass(frozen=True)
class SampleConfig:
    subscription_id: str = field(default="", repr=False)
    tenant_id: str = field(default="", repr=False)
    location: str = "eastus2"
    foundry_resource_group: str = field(default="", repr=False)
    foundry_account_name: str = field(default="", repr=False)
    foundry_project_name: str = field(default="", repr=False)
    foundry_project_endpoint: str = field(default="", repr=False)
    foundry_openai_base_url: str = field(default="", repr=False)
    foundry_model_deployment: str = "gpt-5.4-mini"
    foundry_api_mode: str = "chat_completions"
    foundry_token_scope: str = FOUNDRY_SCOPE
    dynamic_sessions_resource_group: str = field(default="", repr=False)
    dynamic_sessions_pool_endpoint: str = field(default="", repr=False)
    dynamic_sessions_wire_profile: str = "containerapp-1.3.0b5"
    dynamic_sessions_api_version: str = "2025-10-02-preview"
    dynamic_sessions_token_scope: str = DYNAMIC_SESSIONS_SCOPE
    sandbox_resource_group: str = field(default="", repr=False)
    sandbox_group_name: str = field(default="", repr=False)
    sandbox_endpoint: str = field(default="", repr=False)
    sandbox_api_version: str = "2026-02-01-preview"
    sandbox_arm_api_version: str = "2026-07-01"
    sandbox_token_scope: str = SANDBOX_SCOPE
    enable_live_azure: bool = False
    enable_provisioning: bool = False

    def validate(self, mode: str = "common") -> None:
        if mode not in {"common", "foundry", "dynamic-session", "sandbox"}:
            raise ConfigError("Unknown configuration mode")
        for name, value in vars(self).items():
            if isinstance(value, str) and _PLACEHOLDER.search(value):
                raise ConfigError(f"{name} contains a placeholder")
        for name in ("foundry_project_endpoint", "foundry_openai_base_url", "dynamic_sessions_pool_endpoint", "sandbox_endpoint"):
            value = getattr(self, name)
            if value:
                validate_https_url(value, name)
        if self.foundry_openai_base_url:
            validate_foundry_base_url(self.foundry_openai_base_url)
        if self.foundry_api_mode != "chat_completions":
            raise ConfigError("Only chat_completions mode is supported")
        if (self.foundry_token_scope != FOUNDRY_SCOPE
                or self.dynamic_sessions_token_scope != DYNAMIC_SESSIONS_SCOPE
                or self.sandbox_token_scope != SANDBOX_SCOPE):
            raise ConfigError("Unsupported service token scope")
        if self.dynamic_sessions_wire_profile != "containerapp-1.3.0b5":
            raise ConfigError("Unsupported Dynamic Sessions wire profile")
        required = {
            "common": (),
            "foundry": ("subscription_id", "tenant_id", "foundry_openai_base_url", "foundry_model_deployment"),
            "dynamic-session": ("subscription_id", "tenant_id", "dynamic_sessions_resource_group", "dynamic_sessions_pool_endpoint"),
            "sandbox": ("subscription_id", "tenant_id", "location", "sandbox_resource_group", "sandbox_group_name"),
        }[mode]
        for name in required:
            if not getattr(self, name):
                raise ConfigError(f"Missing required setting: {name}")


def load_config(env_file: str | Path = ".env", environ: Mapping[str, str] | None = None) -> SampleConfig:
    values = {k: v for k, v in dotenv_values(env_file, interpolate=False).items() if v is not None}
    values.update(dict(os.environ if environ is None else environ))
    defaults = SampleConfig()
    mapping = {
        "subscription_id": "AZURE_SUBSCRIPTION_ID", "tenant_id": "AZURE_TENANT_ID",
        "location": "AZURE_LOCATION", "foundry_resource_group": "FOUNDRY_RESOURCE_GROUP",
        "foundry_account_name": "FOUNDRY_ACCOUNT_NAME", "foundry_project_name": "FOUNDRY_PROJECT_NAME",
        "foundry_project_endpoint": "FOUNDRY_PROJECT_ENDPOINT", "foundry_openai_base_url": "FOUNDRY_OPENAI_BASE_URL",
        "foundry_model_deployment": "FOUNDRY_MODEL_DEPLOYMENT", "foundry_api_mode": "FOUNDRY_API_MODE",
        "foundry_token_scope": "FOUNDRY_TOKEN_SCOPE", "dynamic_sessions_resource_group": "DYNAMIC_SESSIONS_RESOURCE_GROUP",
        "dynamic_sessions_pool_endpoint": "DYNAMIC_SESSIONS_POOL_ENDPOINT", "dynamic_sessions_wire_profile": "DYNAMIC_SESSIONS_WIRE_PROFILE",
        "dynamic_sessions_api_version": "DYNAMIC_SESSIONS_API_VERSION", "dynamic_sessions_token_scope": "DYNAMIC_SESSIONS_TOKEN_SCOPE",
        "sandbox_resource_group": "SANDBOX_RESOURCE_GROUP", "sandbox_group_name": "SANDBOX_GROUP_NAME",
        "sandbox_endpoint": "SANDBOX_ENDPOINT", "sandbox_api_version": "SANDBOX_API_VERSION",
        "sandbox_arm_api_version": "SANDBOX_ARM_API_VERSION", "sandbox_token_scope": "SANDBOX_TOKEN_SCOPE",
    }
    fields = {name: values.get(key, getattr(defaults, name)).strip() for name, key in mapping.items()}
    fields["enable_live_azure"] = parse_bool(values.get("ENABLE_LIVE_AZURE", "false"))
    fields["enable_provisioning"] = parse_bool(values.get("ENABLE_PROVISIONING", "false"))
    config = SampleConfig(**fields)
    config.validate()
    return config

