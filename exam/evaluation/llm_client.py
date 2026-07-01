"""LLM client helpers for evaluation entrypoints."""

from __future__ import annotations

import os
from typing import Any

from exam.agents.utils.agent_utils import create_llm_client
from exam.config import DEFAULT_CONFIG


def create_eval_llm_client(
    *,
    enabled: bool,
    model_env_var: str | None = None,
) -> Any | None:
    """Create a real LLM client for evals when explicitly enabled."""
    if not enabled:
        return None

    provider = str(DEFAULT_CONFIG.get("llm_provider", "openai"))
    required_env = {
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }.get(provider)

    if required_env and not os.environ.get(required_env):
        raise RuntimeError(
            f"启用真实大模型评测需要先设置环境变量 {required_env}。"
        )

    config = dict(DEFAULT_CONFIG)
    if model_env_var:
        model_override = os.environ.get(model_env_var, "").strip()
        if model_override:
            config["deep_think_llm"] = model_override

    return create_llm_client(config)


def eval_llm_summary(
    *,
    enabled: bool,
    model_env_var: str | None = None,
) -> dict[str, Any]:
    """Return non-secret LLM config metadata for reports and logs."""
    provider = str(DEFAULT_CONFIG.get("llm_provider", "openai"))
    if provider == "openai":
        api_key_env = "OPENAI_API_KEY"
        base_url = os.environ.get("OPENAI_BASE_URL", "")
    elif provider == "deepseek":
        api_key_env = "DEEPSEEK_API_KEY"
        base_url = "https://api.deepseek.com"
    else:
        api_key_env = ""
        base_url = ""

    model = DEFAULT_CONFIG.get("deep_think_llm", "")
    model_source = "default"
    if model_env_var:
        model_override = os.environ.get(model_env_var, "").strip()
        if model_override:
            model = model_override
            model_source = model_env_var

    return {
        "enabled": enabled,
        "provider": provider,
        "model": model,
        "model_source": model_source,
        "api_key_env": api_key_env,
        "api_key_present": bool(api_key_env and os.environ.get(api_key_env)),
        "base_url": base_url,
    }
