"""Agent core against a scripted fake LLM, a fake Memory API and fake MCP tools."""
import asyncio
import base64
import json
from datetime import datetime

import httpx
import openai
import pytest
from greennode_agent_bridge import AgentBaseMemoryEvents
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import agent
import mcp_client
import memory_tools
from fakes import FakeCheckpointApi, FakeSdk, ScriptedChatModel, tool_call

SEARCH_TOOL = {
    "name": "tavily_search",
    "description": "search the web",
    "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}},
}


@pytest.fixture()
def rig(monkeypatch):
    model = ScriptedChatModel()
    api = FakeCheckpointApi()
    sdk = FakeSdk()
    rig = type("Rig", (), {})()
    rig.model, rig.api, rig.sdk = model, api, sdk
    rig.tool_defs = [SEARCH_TOOL]
    rig.tool_calls = []

    monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: model)
    monkeypatch.setattr(agent, "_checkpointer", AgentBaseMemoryEvents(memory_id="memory-test", memory_client=api))
    monkeypatch.setattr(memory_tools, "_client", sdk)
    monkeypatch.setattr(memory_tools, "_RETRY_BASE_DELAY", 0)
    for name, value in (("_agent", None), ("_agent_tools", None), ("_tools", []), ("_tool_defs", []), ("_tools_loaded_at", 0.0)):
        monkeypatch.setattr(agent, name, value)
    monkeypatch.setattr(mcp_client, "list_tools", lambda url: rig.tool_defs)

    def call_tool(url, name, arguments):
        rig.tool_calls.append((name, arguments))
        return f"result for {arguments}"

    monkeypatch.setattr(mcp_client, "call_tool", call_tool)

    def turn(text, user="alice", session="s1"):
        return asyncio.run(memory_tools.arun_coro(agent.run_turn(text, user, session)))

    rig.turn = turn
    return rig


def valid_for_openai(messages):
    """Every ToolMessage answers an earlier AI tool call; the first message is not mid-pair."""
    first = next(m for m in messages if not isinstance(m, SystemMessage))
    assert not isinstance(first, ToolMessage)
    assert not (isinstance(first, AIMessage) and first.tool_calls)
    announced: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            announced.update(c["id"] for c in m.tool_calls)
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in announced, "orphan ToolMessage"


# --- loop caps -------------------------------------------------------------------------------

def test_a_model_that_always_calls_tools_stops_at_the_model_call_limit(rig):
    rig.model.script = [AIMessage("", tool_calls=[tool_call(f"c{i}")]) for i in range(40)]
    result = rig.turn("loop forever")
    assert len(rig.model.seen) == agent.MODEL_CALL_LIMIT
    assert result.reply  # the turn returns a reply instead of raising GraphRecursionError


def test_tool_calls_over_the_limit_are_refused_and_the_model_still_answers(rig):
    calls = [tool_call(f"c{i}") for i in range(agent.TOOL_CALL_LIMIT + 3)]
    rig.model.script = [AIMessage("", tool_calls=calls), AIMessage("done anyway")]
    result = rig.turn("many tools at once")
    assert result.reply == "done anyway"
    assert len(rig.tool_calls) == agent.TOOL_CALL_LIMIT
    refused = [m for m in rig.model.seen[-1] if isinstance(m, ToolMessage) and m.status == "error"]
    assert len(refused) == 3


# --- tool errors -----------------------------------------------------------------------------

def test_a_failing_tool_becomes_an_error_message_for_the_model(rig):
    rig.sdk.insert_errors = [RuntimeError("memory 503 secret-internal-host")]
    rig.model.script = [
        AIMessage("", tool_calls=[tool_call("c1", "remember", fact="I am vegetarian")]),
        AIMessage("I could not save that."),
    ]
    result = rig.turn("I am vegetarian")
    assert result.reply == "I could not save that."
    tool_message = next(m for m in rig.model.seen[-1] if isinstance(m, ToolMessage))
    assert tool_message.status == "error"
    assert "remember" in tool_message.content and "RuntimeError" in tool_message.content
    assert "secret-internal-host" not in tool_message.content  # no raw exception text for the model
    assert result.memories_used == []


def test_an_mcp_tool_failure_does_not_kill_the_turn(rig, monkeypatch):
    def broken(url, name, arguments):
        raise httpx.ConnectError("gateway unreachable")

    monkeypatch.setattr(mcp_client, "call_tool", broken)
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c1")]), AIMessage("search is down")]
    assert rig.turn("search please").reply == "search is down"


def test_tools_reject_bad_input_with_a_readable_message(rig):
    rig.model.script = [
        AIMessage("", tool_calls=[tool_call("c1", "remember", fact="  ")]),
        AIMessage("ok"),
    ]
    rig.turn("remember nothing")
    tool_message = next(m for m in rig.model.seen[-1] if isinstance(m, ToolMessage))
    assert "must not be empty" in tool_message.content


# --- model retries ---------------------------------------------------------------------------

def test_transient_llm_errors_are_retried(rig):
    request = httpx.Request("POST", "https://llm.example/v1/chat/completions")
    rig.model.fail_with = [openai.APIConnectionError(request=request)]
    rig.model.script = [AIMessage("recovered")]
    assert rig.turn("hello").reply == "recovered"


def test_permanent_llm_errors_propagate_without_retry(rig):
    rig.model.fail_with = [ValueError("bad request")]
    with pytest.raises(ValueError, match="bad request"):
        rig.turn("hello")
    assert len(rig.model.seen) == 1


# --- context bounding ------------------------------------------------------------------------

def test_summarization_bounds_the_history_and_never_leaves_an_orphan_tool_message(rig, monkeypatch):
    monkeypatch.setattr(agent, "SUMMARIZE_AT_TOKENS", 400)
    monkeypatch.setattr(agent, "KEEP_MESSAGES", 3)  # small, so the cut lands between AI and tool messages
    for i in range(8):
        parallel = [tool_call(f"t{i}-{j}", query=f"q{i}{j}") for j in range(1 + i % 3)]
        rig.model.script = [AIMessage("", tool_calls=parallel), AIMessage("answer " + "word " * 60)]
        rig.turn(f"question {i} " + "blah " * 60)
    assert rig.model.summaries, "summarization never triggered"
    for messages in rig.model.seen:
        valid_for_openai(messages)
        assert len(messages) <= 14  # bounded: summary + kept messages + the current turn
    sent = rig.model.seen[-1]
    assert any("Here is a summary of the conversation" in str(m.content) for m in sent)


# --- MCP tool cache --------------------------------------------------------------------------

def test_a_failed_tools_list_is_retried_on_the_next_turn_and_the_agent_rebuilt(rig, monkeypatch):
    attempts = []

    def flaky_list(url):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("gateway 503 at boot")
        return [SEARCH_TOOL]

    monkeypatch.setattr(mcp_client, "list_tools", flaky_list)
    first = asyncio.run(memory_tools.arun_coro(agent.aget_agent()))
    assert len(attempts) == 1 and agent._tools == []
    rig.model.script = [AIMessage("no tools yet")]
    assert rig.turn("hi").reply == "no tools yet"
    second = asyncio.run(memory_tools.arun_coro(agent.aget_agent()))
    assert second is not first  # rebuilt with the recovered tool
    assert [t.name for t in agent.get_mcp_tools()] == ["tavily_search"]
    assert len(attempts) == 2  # retried while failing, cached once it worked
    agent.get_mcp_tools()
    assert len(attempts) == 2


def test_an_empty_tools_list_is_not_cached(rig):
    rig.tool_defs = []
    assert agent.get_mcp_tools() == []
    rig.tool_defs = [SEARCH_TOOL]
    assert [t.name for t in agent.get_mcp_tools()] == ["tavily_search"]


def test_tools_are_refreshed_after_the_ttl_and_a_failed_refresh_keeps_the_old_list(rig, monkeypatch):
    assert len(agent.get_mcp_tools()) == 1
    monkeypatch.setattr(agent, "TOOLS_TTL_SECONDS", 0)

    def broken(url):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(mcp_client, "list_tools", broken)
    assert [t.name for t in agent.get_mcp_tools()] == ["tavily_search"]
    rig.tool_defs = [SEARCH_TOOL, {**SEARCH_TOOL, "name": "tavily_extract"}]
    monkeypatch.setattr(mcp_client, "list_tools", lambda url: rig.tool_defs)
    assert [t.name for t in agent.get_mcp_tools()] == ["tavily_search", "tavily_extract"]


def test_unchanged_tools_keep_the_same_agent(rig):
    first = agent.get_agent()
    assert agent.get_agent() is first


def test_one_bad_tool_schema_skips_only_that_tool(rig, caplog):
    rig.tool_defs = [
        {"name": "broken", "inputSchema": {"properties": {"_private": {"type": "string"}}}},
        {"inputSchema": {}},  # no name at all
        SEARCH_TOOL,
    ]
    with caplog.at_level("WARNING", logger="travel-buddy"):
        tools = agent.get_mcp_tools()
    assert [t.name for t in tools] == ["tavily_search"]
    assert "skipping MCP tool" in caplog.text


# --- JSON schema -> pydantic -----------------------------------------------------------------

def model_for(properties, required=()):
    return agent._schema_to_model(
        {"name": "t", "inputSchema": {"type": "object", "required": list(required), "properties": properties}}
    )


def test_schema_required_and_optional_fields():
    model = model_for({"query": {"type": "string", "description": "q"}, "n": {"type": "integer"}}, ["query"])
    assert model(query="x").n is None
    with pytest.raises(ValueError):
        model()


def test_schema_type_given_as_a_list():
    model = model_for({"days": {"type": ["integer", "null"]}}, ["days"])
    assert model(days=3).days == 3
    assert model(days=None).days is None
    with pytest.raises(ValueError):
        model(days="three")


def test_schema_enum():
    model = model_for({"depth": {"type": "string", "enum": ["basic", "advanced"]}})
    assert model(depth="basic").depth == "basic"
    with pytest.raises(ValueError):
        model(depth="deep")


def test_schema_arrays_with_items():
    model = model_for({"domains": {"type": "array", "items": {"type": "string"}}, "any": {"type": "array"}})
    assert model(domains=["a.com"], any=[1, "x"]).domains == ["a.com"]
    with pytest.raises(ValueError):
        model(domains=[1])


def test_schema_nested_objects_fall_back_to_dict():
    model = model_for({"filters": {"type": "object", "properties": {"a": {"type": "string"}}}})
    assert model(filters={"a": "x", "b": 1}).filters == {"a": "x", "b": 1}


def test_schema_anyof_and_missing_type():
    model = model_for({"x": {"anyOf": [{"type": "integer"}, {"type": "string"}]}, "y": {"description": "free"}})
    assert model(x="s", y=object()).x == "s"


def test_schema_without_properties_has_no_fake_argument():
    model = agent._schema_to_model({"name": "no-args", "inputSchema": {}})
    assert model.model_fields == {}


def test_mcp_tool_drops_unset_optional_arguments(rig):
    tool = agent._build_tools([SEARCH_TOOL])[0]
    assert tool.invoke({"query": "da lat"}) == "result for {'query': 'da lat'}"
    assert rig.tool_calls == [("tavily_search", {"query": "da lat"})]


# --- prompt ----------------------------------------------------------------------------------

def test_the_system_prompt_has_the_current_vietnam_date_on_every_call(rig, monkeypatch):
    clock = {"now": datetime(2030, 3, 4, 8, 15, tzinfo=agent.TZ_VN)}
    monkeypatch.setattr(agent, "now_vn", lambda: clock["now"])
    rig.turn("hello")
    assert "Monday, 2030-03-04 08:15" in rig.model.seen[-1][0].content
    clock["now"] = datetime(2030, 3, 5, 9, 0, tzinfo=agent.TZ_VN)  # the next day, same cached agent
    rig.turn("hello again")
    assert "Tuesday, 2030-03-05 09:00" in rig.model.seen[-1][0].content
    assert "Asia/Ho_Chi_Minh" in rig.model.seen[-1][0].content


def test_the_system_prompt_covers_the_required_behaviours():
    prompt = agent.build_system_prompt().lower()
    for needle in ("vietnamese", "`recall`", "`remember`", "untrusted data", "denied by policy", "source url"):
        assert needle in prompt


# --- memories_used ---------------------------------------------------------------------------

def test_memories_used_covers_the_current_turn_only(rig):
    rig.sdk.search_results = {"/strategies/ltms-pref-test/actors/alice": [{"memory": "vegetarian", "score": 0.9}]}
    rig.model.script = [
        AIMessage("", tool_calls=[tool_call("c1", "remember", fact="I am vegetarian")]),
        AIMessage("noted"),
    ]
    first = rig.turn("I am vegetarian")
    assert first.memories_used == ["I am vegetarian"]
    rig.model.script = [AIMessage("plain answer")]
    second = rig.turn("thanks")  # same thread: the first turn's tool message is still in the history
    assert second.memories_used == []
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c2", "recall", query="prefs")]), AIMessage("you are vegetarian")]
    third = rig.turn("what do you know about me?")
    assert third.memories_used == ["vegetarian"]


def test_memories_used_ignores_the_summary_message_and_failed_tools():
    summary = HumanMessage("Here is a summary", additional_kwargs={"lc_source": "summarization"})
    old = ToolMessage("x", tool_call_id="1", name="remember", artifact=["old fact"])
    failed = ToolMessage("boom", tool_call_id="2", name="recall", status="error")
    fresh = ToolMessage("y", tool_call_id="3", name="recall", artifact=["new fact", "new fact"])
    messages = [HumanMessage("turn 1"), old, HumanMessage("turn 2"), failed, fresh]
    assert agent.memories_used(messages) == ["new fact"]
    # the summarization message does not start a new turn
    assert agent.memories_used([HumanMessage("turn"), fresh, summary, ToolMessage("z", tool_call_id="4", name="recall", artifact=["x"])]) == ["new fact", "x"]


# --- checkpoint traffic ----------------------------------------------------------------------

def test_one_checkpoint_is_written_per_turn_and_state_survives_between_turns(rig):
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c1")]), AIMessage("first answer")]
    rig.turn("first question")
    writes_exit = rig.api.creates
    rig.model.script = [AIMessage("second answer")]
    before = rig.api.creates
    rig.turn("second question")
    assert rig.api.creates - before <= 12  # one put (channel blobs + 1 checkpoint), not one per graph step
    sent = rig.model.seen[-1]
    assert [m.content for m in sent if isinstance(m, HumanMessage)] == ["first question", "second question"]
    assert writes_exit > 0


def test_async_durability_would_write_far_more(rig, monkeypatch):
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c1")]), AIMessage("answer")]
    rig.turn("question", session="exit")
    exit_writes = rig.api.creates
    monkeypatch.setattr(agent, "_DURABILITY", "async")
    rig.api.creates = 0
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c2")]), AIMessage("answer")]
    rig.turn("question", session="async")
    assert rig.api.creates > exit_writes * 2


# --- streaming -------------------------------------------------------------------------------

def collect_stream(text, user="alice", session="s1"):
    async def main():
        return [item async for item in agent.stream_turn(text, user, session)]

    return asyncio.run(memory_tools.arun_coro(main()))


def test_stream_yields_tokens_then_the_result(rig):
    rig.model.script = [AIMessage("", tool_calls=[tool_call("c1")]), AIMessage("the final answer")]
    items = collect_stream("go")
    assert items[:-1] == ["the ", "final ", "answer "]
    assert items[-1] == agent.TurnResult("the final answer", [])


def test_stream_does_not_leak_summarization_tokens(rig, monkeypatch):
    monkeypatch.setattr(agent, "SUMMARIZE_AT_TOKENS", 100)
    monkeypatch.setattr(agent, "KEEP_MESSAGES", 2)
    for i in range(3):
        rig.model.script = [AIMessage("answer " * 40)]
        rig.turn(f"question {i} " + "blah " * 40)
    rig.model.script = [AIMessage("fresh reply")]
    items = collect_stream("one more " + "blah " * 40)
    assert rig.model.summaries
    assert "".join(items[:-1]) == "fresh reply "
    assert "SUMMARY" not in "".join(items[:-1])


def test_stream_reply_comes_from_the_final_message_when_the_cap_ends_the_turn(rig):
    rig.model.script = [AIMessage("", tool_calls=[tool_call(f"c{i}")]) for i in range(40)]
    result = collect_stream("loop")[-1]
    assert result.reply and len(rig.model.seen) == agent.MODEL_CALL_LIMIT


def test_an_empty_model_answer_falls_back_to_a_message(rig):
    rig.model.script = [AIMessage("")]
    assert rig.turn("hi").reply == agent.FALLBACK_REPLY


# --- misc ------------------------------------------------------------------------------------

def test_run_config_sets_identity_and_a_recursion_limit_above_langgraphs_default():
    config = agent._run_config("alice", "s1", None)
    assert config["configurable"] == {"thread_id": "s1", "actor_id": "alice"}
    assert config["recursion_limit"] == agent.RECURSION_LIMIT > 25
    assert config["callbacks"] == []


def test_whoami_decodes_the_runtime_token(monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "svc-1", "azp": "client"}).encode()).decode().rstrip("=")
    monkeypatch.setattr(mcp_client, "get_token", lambda force=False: f"h.{payload}.s")
    monkeypatch.setenv("GREENNODE_CLIENT_ID", "cid")
    assert agent.whoami() == {"client_id": "cid", "token_sub": "svc-1", "azp": "client", "authAccountId": ""}
