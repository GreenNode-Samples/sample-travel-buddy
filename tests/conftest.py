"""Test setup: dummy environment BEFORE the backend modules are imported (agent.py validates it)."""
import os
import sys
from pathlib import Path

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("AGENTBASE_MEMORY_ID", "memory-test")
os.environ.setdefault("MEMORY_STRATEGY_PREF_ID", "ltms-pref-test")
os.environ.setdefault("MEMORY_STRATEGY_FACTS_ID", "ltms-facts-test")
os.environ.setdefault("MCP_TAVILY_URL", "https://gw.example/tavily")
for name in ("AGENT_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
    os.environ.pop(name, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
