"""Startup validation: missing values and the .env.example placeholders are rejected."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent / "src" / "backend"
GOOD_ENV = {
    "LLM_API_KEY": "lap-real", "AGENTBASE_MEMORY_ID": "memory-1", "MEMORY_STRATEGY_PREF_ID": "ltms-1",
    "MEMORY_STRATEGY_FACTS_ID": "ltms-2", "MCP_TAVILY_URL": "https://gw.example/tavily",
}


def start(module: str, **overrides) -> subprocess.CompletedProcess:
    """Import a backend module in a clean interpreter with the given environment."""
    env = {k: v for k, v in os.environ.items() if k not in GOOD_ENV and k != "AGENT_API_KEY"}
    env.update({**GOOD_ENV, **overrides, "PYTHONPATH": str(BACKEND)})
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"], env=env, cwd=BACKEND.parent.parent / "tests",
        capture_output=True, text=True, timeout=60,
    )


def test_a_complete_configuration_starts():
    assert start("agent").returncode == 0


@pytest.mark.parametrize(
    ("name", "value"),
    [(name, "change-me") for name in GOOD_ENV] + [("LLM_API_KEY", ""), ("MCP_TAVILY_URL", "change-me-url")],
)
def test_each_required_value_is_checked(name, value):
    result = start("agent", **{name: value})
    assert result.returncode != 0 and f"{name} is not configured" in result.stderr


def test_the_example_api_key_placeholder_is_rejected():
    result = start("main", AGENT_API_KEY="change-me")
    assert result.returncode != 0 and "AGENT_API_KEY still has" in result.stderr
    assert start("main", AGENT_API_KEY="a-real-secret").returncode == 0
