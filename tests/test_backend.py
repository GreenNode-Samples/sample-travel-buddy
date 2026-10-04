"""Unit tests — pure functions, không cần mạng/services thật."""
import json

import pytest


# ── memory_tools ──
def test_field_reads_dict_and_object():
    from memory_tools import _field

    assert _field({"memory": "abc"}, "memory") == "abc"
    assert _field({"memory": None}, "memory", "x") == "x"

    class Obj:
        memory = "xyz"

    assert _field(Obj(), "memory") == "xyz"
    assert _field(Obj(), "missing", "d") == "d"


def test_build_namespace_format():
    from memory_tools import build_namespace

    ns = build_namespace("alice", "ltms-1")
    assert ns == "/strategies/ltms-1/actors/alice"
    ns2 = build_namespace("bob")  # mặc định strategy PREF
    assert ns2 == "/strategies/ltms-pref-test/actors/bob"


# ── agent._schema_to_model ──
def test_schema_to_model_required_and_optional():
    from agent import _schema_to_model

    model = _schema_to_model(
        {
            "name": "tavily_search",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "search query"},
                    "max_results": {"type": "integer"},
                },
            },
        }
    )
    fields = model.model_fields
    assert set(fields) == {"query", "max_results"}
    assert model(query="x").query == "x"
    assert model(query="x").max_results is None


def test_schema_to_model_empty_schema():
    from agent import _schema_to_model

    model = _schema_to_model({"name": "no-args", "inputSchema": {}})
    assert model is not None  # thêm field noop


# ── agent._trim_history (dùng bởi checkpointer _TrimmingEvents) ──
def test_trim_history_keeps_short_history():
    from agent import _trim_history
    from langchain_core.messages import AIMessage, HumanMessage

    msgs = [HumanMessage("a"), AIMessage("b")]
    assert _trim_history(msgs) == msgs


def test_trim_history_cuts_at_human_boundary_and_keeps_system():
    from agent import _trim_history
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    sysmsg = SystemMessage("system")
    # [sys, H, A, H, A, ..., H, A] — 50 message user/assistant
    msgs = [sysmsg]
    for i in range(25):
        msgs.append(HumanMessage(f"h{i}"))
        msgs.append(AIMessage(f"a{i}"))
    assert len(msgs) == 51

    trimmed = _trim_history(msgs)
    assert len(trimmed) <= 41  # system + 40
    assert trimmed[0] == sysmsg
    # phải bắt đầu bằng HumanMessage (không cắt giữa cặp AI→tool)
    assert isinstance(trimmed[1], HumanMessage)
    # giữ các message MỚI NHẤT
    assert trimmed[-1].content == "a24"


# ── agent._jwt_claims ──
def test_jwt_claims_decodes_payload():
    import base64

    from agent import _jwt_claims

    payload = base64.urlsafe_b64encode(json.dumps({"sub": "user-123"}).encode()).decode().rstrip("=")
    token = f"header.{payload}.signature"
    assert _jwt_claims(token)["sub"] == "user-123"
    assert _jwt_claims("not-a-jwt") == {}
