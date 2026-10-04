"""HTTP layer: authentication, validation, error handling, streaming, A2A, async routes."""
import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

import agent
import main
import mcp_client
import memory_tools

USER = "X-GreenNode-AgentBase-User-Id"
SESSION = "X-GreenNode-AgentBase-Session-Id"
IDENTITY = {USER: "alice", SESSION: "s-1"}
KEY = "k-" + "x" * 30


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Anything a test forgets to fake fails fast instead of calling GreenNode."""
    monkeypatch.setattr(memory_tools, "_client", SimpleNamespace())

    def offline(*args, **kwargs):
        raise RuntimeError("network disabled in tests")

    monkeypatch.setattr(mcp_client, "list_tools", offline)
    monkeypatch.setattr(agent, "_tools", [])


@pytest.fixture()
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture()
def turns(monkeypatch):
    """Replace the agent and the history writer; `turns.calls` records (text, user, session)."""
    state = SimpleNamespace(calls=[], saved=[], reply="a reply", memories=["a fact"], fail=None, tokens=["a ", "reply"])

    async def run_turn(text, user_id, session_id, *, callbacks=None):
        state.calls.append((text, user_id, session_id))
        if state.fail:
            raise state.fail
        return agent.TurnResult(state.reply, state.memories)

    async def stream_turn(text, user_id, session_id, *, callbacks=None):
        state.calls.append((text, user_id, session_id))
        if state.fail:
            raise state.fail
        for token in state.tokens:
            yield token
        yield agent.TurnResult(state.reply, state.memories)

    async def add_chat_events(user_id, session_id, user_text, bot_text):
        state.saved.append((user_id, session_id, user_text, bot_text))

    monkeypatch.setattr(agent, "run_turn", run_turn)
    monkeypatch.setattr(agent, "stream_turn", stream_turn)
    monkeypatch.setattr(memory_tools, "add_chat_events", add_chat_events)
    return state


def sse_events(response) -> list[dict]:
    return [json.loads(chunk[5:]) for chunk in response.text.split("\n\n") if chunk.startswith("data:")]


# --- authentication ----------------------------------------------------------------------------

PROTECTED = [
    ("POST", "/invocations"), ("POST", "/a2a"), ("GET", "/ready"), ("POST", "/api/chat/stream"),
    ("GET", "/api/memory?actor=alice"), ("GET", "/api/history?actor=alice&session=s"), ("GET", "/api/actors"),
]
PUBLIC = ["/", "/health", "/api/info", "/.well-known/agent-card.json"]


@pytest.fixture()
def keyed(monkeypatch):
    monkeypatch.setattr(main, "AGENT_API_KEY", KEY)


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_protected_paths_need_the_api_key(client, keyed, method, path):
    assert client.request(method, path, json={}).status_code == 401
    assert client.request(method, path, json={}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.request(method, path, json={}, headers={"X-API-Key": KEY}).status_code != 401


@pytest.mark.parametrize("path", PUBLIC)
def test_ui_health_info_and_agent_card_stay_public(client, keyed, path):
    assert client.get(path).status_code == 200


def test_every_registered_route_is_either_protected_or_deliberately_public():
    paths = {r.path for r in main.app.routes if isinstance(r, Route)}
    assert {"/invocations", "/a2a", "/ready", "/api/chat/stream", "/api/memory"} <= paths
    for path in paths:
        assert main._requires_key(path) or path in PUBLIC, f"{path} is neither protected nor listed as public"


def test_without_a_key_everything_is_open(client, monkeypatch):
    monkeypatch.setattr(main, "AGENT_API_KEY", "")
    assert client.get("/api/actors").status_code != 401


def test_the_key_is_compared_in_constant_time_and_non_ascii_does_not_crash(client, keyed, monkeypatch):
    seen = []
    real = main.hmac.compare_digest
    monkeypatch.setattr(main.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    client.get("/api/memory", headers={"X-API-Key": KEY})
    assert seen == [(KEY.encode(), KEY.encode())]
    response = client.get("/api/memory", headers={"X-API-Key": "café".encode("latin-1")})
    assert response.status_code == 401


def test_api_info_hides_resource_ids_from_unauthorized_callers(client, keyed):
    public = client.get("/api/info").json()
    assert public == {"agent": "travel-buddy", "auth_required": True}
    full = client.get("/api/info", headers={"X-API-Key": KEY}).json()
    assert full["memory_id"] == "memory-test" and full["gateway"] == "https://gw.example"
    assert full["llm_model"] == "test-model" and "streaming" not in full and "mcp_url" not in full


def test_api_info_without_a_key_shows_everything(client, monkeypatch):
    monkeypatch.setattr(main, "AGENT_API_KEY", "")
    info = client.get("/api/info").json()
    assert info["auth_required"] is False and info["memory_id"] == "memory-test"


# --- /invocations ------------------------------------------------------------------------------

def test_invocation_success(client, turns):
    r = client.post("/invocations", json={"message": "  hi  "}, headers=IDENTITY)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success" and body["response"] == "a reply" and body["memories_used"] == ["a fact"]
    assert turns.calls == [("hi", "alice", "s-1")]
    assert turns.saved == [("alice", "s-1", "hi", "a reply")]


def test_invocation_accepts_input_as_an_alias(client, turns):
    assert client.post("/invocations", json={"input": "hello"}, headers=IDENTITY).status_code == 200


@pytest.mark.parametrize("headers", [{}, {USER: "alice"}, {SESSION: "s-1"}], ids=["none", "only-user", "only-session"])
def test_missing_identity_headers_are_a_400_and_never_reach_the_agent(client, turns, headers):
    for path in ("/invocations", "/api/chat/stream"):
        r = client.post(path, json={"message": "hi"}, headers=headers)
        assert r.status_code == 400 and USER in r.text
    assert turns.calls == []


@pytest.mark.parametrize("bad", ["a/b", "x y", "../etc", "a" * 129, "café"])
def test_malformed_user_or_session_ids_are_rejected(client, turns, bad):
    bad = bad.encode("latin-1")  # HTTP header values are bytes
    for headers in ({USER: bad, SESSION: "s-1"}, {USER: "alice", SESSION: bad}):
        assert client.post("/invocations", json={"message": "hi"}, headers=headers).status_code == 400
    assert turns.calls == []


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "   \n"}, {"message": 5}, {"message": None}, [1, 2], "text"])
def test_empty_or_malformed_messages_are_a_400(client, turns, body):
    assert client.post("/invocations", json=body, headers=IDENTITY).status_code == 400
    assert client.post("/api/chat/stream", json=body, headers=IDENTITY).status_code == 400
    assert turns.calls == []


def test_oversized_messages_are_a_400(client, turns):
    too_long = {"message": "x" * (main.MAX_MESSAGE_CHARS + 1)}
    assert client.post("/invocations", json=too_long, headers=IDENTITY).status_code == 400
    assert client.post("/api/chat/stream", json=too_long, headers=IDENTITY).status_code == 400
    assert client.post("/invocations", json={"message": "x" * main.MAX_MESSAGE_CHARS}, headers=IDENTITY).status_code == 200


def test_invalid_json_is_a_400(client):
    r = client.post("/api/chat/stream", content=b"{not json", headers={**IDENTITY, "Content-Type": "application/json"})
    assert r.status_code == 400


def test_agent_errors_return_a_generic_message_and_a_request_id(client, turns, caplog):
    turns.fail = RuntimeError("secret-db-password leaked in a trace")
    r = client.post("/invocations", json={"message": "hi"}, headers=IDENTITY)
    assert r.status_code == 500
    assert "secret-db-password" not in r.text and "leaked in a trace" not in r.text
    request_id = r.json()["details"]["request_id"]
    assert r.json()["error"] == main.GENERIC_ERROR
    assert request_id in caplog.text and "secret-db-password" in caplog.text  # the detail is logged, not returned


def test_whoami_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(main, "DEBUG_OPS", False)
    assert client.post("/invocations", json={"op": "whoami"}).status_code == 403


def test_whoami_when_enabled(client, monkeypatch):
    monkeypatch.setattr(main, "DEBUG_OPS", True)
    monkeypatch.setattr(agent, "whoami", lambda: {"token_sub": "svc"})
    r = client.post("/invocations", json={"op": "whoami"})
    assert r.status_code == 200 and r.json()["token_sub"] == "svc"


# --- /api/chat/stream --------------------------------------------------------------------------

def test_stream_sends_tokens_then_done(client, turns):
    r = client.post("/api/chat/stream", json={"message": "hi"}, headers=IDENTITY)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert sse_events(r) == [
        {"type": "token", "text": "a "}, {"type": "token", "text": "reply"},
        {"type": "done", "response": "a reply", "memories_used": ["a fact"]},
    ]
    assert turns.saved == [("alice", "s-1", "hi", "a reply")]


def test_stream_setup_errors_end_the_stream_with_an_error_event(client, turns, caplog):
    turns.fail = RuntimeError("could not build the agent: secret")
    r = client.post("/api/chat/stream", json={"message": "hi"}, headers=IDENTITY)
    assert r.status_code == 200
    (event,) = sse_events(r)
    assert event["type"] == "error" and event["error"] == main.GENERIC_ERROR
    assert "secret" not in r.text and event["request_id"] in caplog.text


# --- A2A ---------------------------------------------------------------------------------------

def rpc(text="hello", **message):
    return {"jsonrpc": "2.0", "id": 7, "method": "message/send",
            "params": {"message": {"parts": [{"kind": "text", "text": text}], **message}}}


def test_a2a_card_shape():
    card = main._a2a_card()
    assert card["name"] == "travel-buddy" and card["protocolVersion"] == "0.3.0"
    assert card["preferredTransport"] == "JSONRPC" and card["url"].endswith("/a2a")
    assert card["capabilities"]["streaming"] is True
    assert [s["id"] for s in card["skills"]] == ["travel-planning", "personalization"]
    for skill in card["skills"]:
        assert skill["name"] and skill["description"] and skill["tags"]


def test_a2a_card_uses_the_public_url(monkeypatch):
    monkeypatch.setattr(main, "A2A_PUBLIC_URL", "https://agent.example")
    assert main._a2a_card()["url"] == "https://agent.example/a2a"


def test_a2a_send(client, turns):
    r = client.post("/a2a", json=rpc(contextId="c-1"), headers={USER: "alice"})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["kind"] == "message" and result["role"] == "agent" and result["contextId"] == "c-1"
    assert result["parts"] == [{"kind": "text", "text": "a reply"}] and r.json()["id"] == 7
    assert turns.calls == [("hello", "alice", "c-1")]


def test_a2a_needs_the_user_header_and_has_no_shared_actor(client, turns):
    r = client.post("/a2a", json=rpc())
    assert r.status_code == 400 and r.json()["error"]["code"] == -32602
    assert turns.calls == []


@pytest.mark.parametrize(
    "body",
    [
        [1, 2],
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": []},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"parts": ["text"]}}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"parts": "text"}}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": "text"}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"parts": []}}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"contextId": 5, "parts": [{"text": "x"}]}}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send", "params": {"message": {"contextId": "a/b", "parts": [{"text": "x"}]}}},
        {"jsonrpc": "2.0", "id": 1, "method": "message/send"},
        {"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {}},
    ],
    ids=["body-list", "params-list", "part-not-object", "parts-not-list", "message-not-object",
         "no-text", "context-id-int", "context-id-path", "no-params", "unknown-method"],
)
def test_malformed_a2a_requests_are_a_400_not_a_500(client, turns, body):
    r = client.post("/a2a", json=body, headers={USER: "alice"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] in (-32600, -32601, -32602)
    assert turns.calls == []


def test_a2a_unparseable_body_is_a_parse_error(client):
    r = client.post("/a2a", content=b"nope", headers={USER: "alice", "Content-Type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700


def test_a2a_oversized_text_is_rejected(client, turns):
    r = client.post("/a2a", json=rpc("x" * (main.MAX_MESSAGE_CHARS + 1)), headers={USER: "alice"})
    assert r.status_code == 400 and turns.calls == []


def test_a2a_context_falls_back_to_the_session_header_then_a_fresh_id(client, turns):
    client.post("/a2a", json=rpc(), headers={USER: "alice", SESSION: "from-header"})
    client.post("/a2a", json=rpc(), headers={USER: "alice"})
    assert turns.calls[0][2] == "from-header" and turns.calls[1][2].startswith("a2a-")


def test_a2a_send_errors_are_generic(client, turns):
    turns.fail = RuntimeError("secret")
    r = client.post("/a2a", json=rpc(), headers={USER: "alice"})
    assert r.status_code == 500 and r.json()["error"]["code"] == -32603 and "secret" not in r.text


def test_a2a_stream_events_follow_the_spec_shape(client, turns):
    r = client.post("/a2a", json={**rpc(contextId="c-2"), "method": "message/stream"}, headers={USER: "alice"})
    events = [e["result"] for e in sse_events(r)]
    assert [(e["kind"], e.get("status", {}).get("state")) for e in events] == [
        ("status-update", "working"), ("artifact-update", None), ("artifact-update", None),
        ("artifact-update", None), ("status-update", "completed"),
    ]
    for event in events:
        assert event["contextId"] == "c-2" and event["taskId"].startswith("task-") and "task" not in event
    assert events[0]["final"] is False and events[-1]["final"] is True
    tokens = events[1:3]
    assert [(e["append"], e["lastChunk"]) for e in tokens] == [(False, False), (True, False)]
    assert tokens[0]["artifact"]["parts"] == [{"kind": "text", "text": "a "}]
    last = events[3]
    assert last["lastChunk"] is True and last["append"] is False
    assert last["artifact"] == {"artifactId": "reply", "parts": [{"kind": "text", "text": "a reply"}]}
    assert turns.saved == [("alice", "c-2", "hello", "a reply")]


def test_a2a_stream_failure_is_a_failed_task_with_a_message_object(client, turns):
    turns.fail = RuntimeError("secret")
    r = client.post("/a2a", json={**rpc(), "method": "message/stream"}, headers={USER: "alice"})
    final = [e["result"] for e in sse_events(r)][-1]
    assert final["final"] is True and final["status"]["state"] == "failed"
    message = final["status"]["message"]
    assert message["kind"] == "message" and message["role"] == "agent" and "secret" not in r.text


def test_a2a_msg_result_envelope():
    out = main._a2a_msg_result("req-1", "ctx-9", "Da Lat is lovely")
    assert out["jsonrpc"] == "2.0" and out["id"] == "req-1"
    assert out["result"]["parts"] == [{"kind": "text", "text": "Da Lat is lovely"}] and out["result"]["messageId"]


def test_a2a_text_helper():
    parts = {"message": {"parts": [{"kind": "text", "text": "xin "}, {"text": "chao"}, {"kind": "file", "file": {}}]}}
    assert main._a2a_text(parts) == "xin chao"
    assert main._a2a_text(None) == main._a2a_text({}) == main._a2a_text({"message": {}}) == ""


def test_a2a_ctx_helper():
    assert main._a2a_ctx({"params": {"message": {"contextId": "ctx-x"}}}, "hdr") == "ctx-x"
    assert main._a2a_ctx({}, "hdr") == "hdr"
    assert main._a2a_ctx({}, "").startswith("a2a-")


# --- REST helpers for the UI ---------------------------------------------------------------------

def test_memory_panel_groups_records_and_hides_failures(client, monkeypatch, caplog):
    async def browse_group(actor, strategy_id, limit=100):
        if strategy_id == "ltms-facts-test":
            raise RuntimeError("secret backend detail")
        return [{"id": "1", "memory": "vegetarian", "createdAt": "now"}]

    monkeypatch.setattr(memory_tools, "browse_group", browse_group)
    body = client.get("/api/memory?actor=alice").json()
    prefs, facts = body["groups"]
    assert prefs["strategy"] == "user-preferences" and prefs["records"][0]["memory"] == "vegetarian"
    assert facts["records"] == [] and "secret" not in facts["error"]
    assert "secret backend detail" in caplog.text


def test_memory_and_history_validate_their_query(client):
    assert client.get("/api/memory").status_code == 400
    assert client.get("/api/memory?actor=a/b").status_code == 400
    assert client.get("/api/history?actor=alice").status_code == 400
    assert client.get("/api/history?actor=alice&session=a b").status_code == 400
    assert client.get("/api/history?actor=alice&session=s&limit=many").status_code == 400


def test_history_and_actors_pass_through_the_listings(client, monkeypatch):
    async def list_conversation(actor, session, limit):
        return [{"role": "user", "message": f"{actor}/{session}/{limit}", "createdAt": "t"}]

    async def list_actors():
        return [{"actorId": "alice", "sessions": ["s-1"]}]

    monkeypatch.setattr(memory_tools, "list_conversation", list_conversation)
    monkeypatch.setattr(memory_tools, "list_actors", list_actors)
    assert client.get("/api/history?actor=alice&session=s-1&limit=500").json()["events"][0]["message"] == "alice/s-1/200"
    assert client.get("/api/actors").json() == {"actors": [{"actorId": "alice", "sessions": ["s-1"]}]}


def test_listing_failures_are_generic(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("secret")

    monkeypatch.setattr(memory_tools, "list_actors", boom)
    monkeypatch.setattr(memory_tools, "list_conversation", boom)
    for path in ("/api/actors", "/api/history?actor=alice&session=s"):
        r = client.get(path)
        assert r.status_code == 500 and "secret" not in r.text and r.json()["request_id"]


def test_ready_reflects_recovery(client, monkeypatch):
    async def ping():
        return None

    tools = []
    monkeypatch.setattr(memory_tools, "ping", ping)
    monkeypatch.setattr(agent, "get_mcp_tools", lambda: tools)
    degraded = client.get("/ready")
    assert degraded.status_code == 503 and degraded.json()["checks"]["gateway"] == {"ok": False, "tools": 0}
    tools.append(object())
    healthy = client.get("/ready")
    assert healthy.status_code == 200 and healthy.json()["status"] == "ok"


def test_ready_reports_a_memory_outage_without_details(client, monkeypatch):
    async def ping():
        raise RuntimeError("secret")

    monkeypatch.setattr(memory_tools, "ping", ping)
    monkeypatch.setattr(agent, "get_mcp_tools", lambda: [object()])
    r = client.get("/ready")
    assert r.status_code == 503 and r.json()["checks"]["memory"] == {"ok": False, "error": "RuntimeError"}


# --- no route blocks the event loop ----------------------------------------------------------------

@pytest.mark.parametrize("slow_path", ["/api/actors", "/api/memory?actor=alice", "/api/history?actor=alice&session=s", "/ready"])
def test_slow_backends_do_not_stall_other_requests(monkeypatch, slow_path):
    async def slow_async(*args, **kwargs):
        await asyncio.sleep(0.4)
        return []

    monkeypatch.setattr(memory_tools, "list_actors", slow_async)
    monkeypatch.setattr(memory_tools, "browse_group", slow_async)
    monkeypatch.setattr(memory_tools, "list_conversation", slow_async)
    monkeypatch.setattr(memory_tools, "ping", slow_async)
    monkeypatch.setattr(agent, "get_mcp_tools", lambda: (time.sleep(0.4), [object()])[1])  # a blocking call

    async def probe():
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            started = time.monotonic()

            async def info():
                await asyncio.sleep(0.05)
                await http.get("/api/info")
                return time.monotonic() - started

            _, info_latency = await asyncio.gather(http.get(slow_path), info())
            return info_latency

    assert asyncio.run(probe()) < 0.3


# --- Langfuse helpers: tracing off by default -------------------------------------------------------

def test_tracing_is_off_without_langfuse_env():
    assert main._lf_tracing() is False and main._lf_callback() is None
    with main._traced("t", "u", "s", ["x"]) as callbacks:
        assert callbacks == []
