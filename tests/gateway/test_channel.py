from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as types
import pytest
from support import FakeWebSocket

from mcp_gtw.channel import Channel, PendingRequest
from mcp_gtw.config import GatewaySettings
from mcp_gtw.errors import (
    ChannelOfflineError,
    ProviderMessageError,
    ProviderRequestError,
)


def make_channel(settings: GatewaySettings) -> Channel:
    return Channel(
        channel_id="chan",
        mcp_token="mcp",
        provider_token="prov",
        settings=settings,
    )


async def register_and_attach(
    settings: GatewaySettings,
    tool: dict,
) -> tuple[Channel, FakeWebSocket]:
    channel = make_channel(settings)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p1", provider_name="demo")
    await channel.register("tools", [tool])
    return channel, websocket


async def test_registers_and_executes_tool(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    assert channel.tool_count == 1
    assert channel.provider_connected is True

    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "right"}))
    await asyncio.sleep(0)
    call = websocket.last("request")
    assert call["params"]["name"] == "move"

    channel.handle_result(
        {"type": "result", "requestId": call["requestId"], "result": {"ok": True}}
    )
    result = await task
    assert result.isError is False
    assert result.structuredContent == {"ok": True}


async def test_unknown_tool(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    result = await channel.execute_tool(name="missing", arguments={})
    assert result.isError is True
    assert "Unknown tool" in result.content[0].text


async def test_input_validation_error(settings: GatewaySettings, move_tool: dict) -> None:
    channel, _ = await register_and_attach(settings, move_tool)
    result = await channel.execute_tool(name="move", arguments={})
    assert result.isError is True
    assert "Input validation error" in result.content[0].text


async def test_execute_without_provider(settings: GatewaySettings, move_tool: dict) -> None:
    channel = make_channel(settings)
    await channel.register("tools", [move_tool])
    result = await channel.execute_tool(name="move", arguments={"direction": "left"})
    assert result.isError is True
    assert "offline" in result.content[0].text


async def test_pending_call_limit(settings: GatewaySettings, move_tool: dict) -> None:
    channel, _ = await register_and_attach(settings, move_tool)
    tasks = [
        asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
        for _ in range(settings.maximum_pending_calls_per_channel)
    ]
    await asyncio.sleep(0)

    overflow = await channel.execute_tool(name="move", arguments={"direction": "left"})
    assert overflow.isError is True
    assert "Too many pending calls" in overflow.content[0].text

    for task in tasks:
        assert (await task).isError is True


async def test_timeout_sends_cancel(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    result = await channel.execute_tool(name="move", arguments={"direction": "right"})
    assert result.isError is True
    assert "timed out" in result.content[0].text
    assert websocket.last("cancel")["reason"] == "timeout"


async def test_detach_fails_pending_call(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)
    assert await channel.detach(websocket) is True

    result = await task
    assert result.isError is True
    assert "disconnected" in result.content[0].text


async def test_detach_ignores_foreign_socket(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)
    assert await channel.detach(FakeWebSocket()) is False


async def test_reattach_replaces_previous(settings: GatewaySettings, move_tool: dict) -> None:
    channel, first = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)

    second = FakeWebSocket()
    await channel.attach(second, provider_id="p2", provider_name="demo")

    assert first.closed is True
    assert channel.tool_count == 0
    result = await task
    assert result.isError is True
    assert "replaced" in result.content[0].text


async def test_reattach_same_socket_is_noop(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)
    await channel.attach(websocket, provider_id="p", provider_name=None)
    assert websocket.closed is False


async def test_cancelled_call_sends_cancel(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert websocket.last("cancel")["reason"] == "mcp_client_cancelled"


async def test_send_to_provider_requires_socket(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(Exception, match="No channel provider"):
        await channel.send_to_provider({"type": "ping"})


async def test_send_to_provider_detects_swapped_socket(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    first = FakeWebSocket()
    await channel.attach(first, provider_id="p", provider_name=None)

    # hold the send lock so the send captures the current socket then blocks on the lock
    await channel._send_lock.acquire()
    task = asyncio.create_task(channel.send_to_provider({"type": "ping"}))
    await asyncio.sleep(0)
    channel._websocket = FakeWebSocket()
    channel._send_lock.release()

    with pytest.raises(ChannelOfflineError, match="connection changed"):
        await task


async def test_handle_provider_message_variants(settings: GatewaySettings, move_tool: dict) -> None:
    channel = make_channel(settings)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)

    await channel.handle_provider_message(
        {"type": "register", "registry": "tools", "items": [move_tool]}
    )
    assert websocket.last("ack")["count"] == 1

    await channel.handle_provider_message({"type": "ping"})
    assert websocket.last("pong")["type"] == "pong"

    await channel.handle_provider_message({"type": "result", "requestId": "unknown", "result": {}})

    with pytest.raises(ProviderMessageError, match="array of objects"):
        await channel.handle_provider_message(
            {"type": "register", "registry": "tools", "items": "nope"}
        )

    with pytest.raises(ProviderMessageError, match="Unsupported provider message"):
        await channel.handle_provider_message({"type": "nonsense"})


async def test_register_unknown_registry_is_rejected(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="Unknown registry"):
        await channel.register("widgets", [])


async def test_register_tools_limits(settings: GatewaySettings, move_tool: dict) -> None:
    channel = make_channel(settings.model_copy(update={"maximum_tools": 1}))

    with pytest.raises(ProviderMessageError, match="Too many tools"):
        await channel.register("tools", [move_tool, {**move_tool, "name": "b"}])


async def test_register_tools_oversized_definition(
    settings: GatewaySettings, move_tool: dict
) -> None:
    channel = make_channel(settings.model_copy(update={"maximum_tool_definition_bytes": 10}))

    with pytest.raises(ProviderMessageError, match="maximum size"):
        await channel.register("tools", [move_tool])


async def test_register_tools_invalid_definition(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="Invalid MCP tool definition"):
        await channel.register("tools", [{"description": "missing name"}])


async def test_register_tools_duplicate_name(settings: GatewaySettings, move_tool: dict) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="Duplicate tool name"):
        await channel.register("tools", [move_tool, move_tool])


async def test_register_tools_invalid_input_schema(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="Invalid JSON Schema"):
        await channel.register(
            "tools", [{"name": "bad", "description": "d", "inputSchema": {"type": 123}}]
        )


async def test_register_tools_invalid_output_schema(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="Invalid JSON Schema"):
        await channel.register(
            "tools",
            [
                {
                    "name": "bad",
                    "description": "d",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": 123},
                }
            ],
        )


async def test_handle_result_requires_request_id(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderMessageError, match="string requestId"):
        channel.handle_result({"type": "result", "requestId": 5})


async def test_handle_result_error_payload(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)
    call = websocket.last("request")
    channel.handle_result({"type": "result", "requestId": call["requestId"], "error": "too far"})
    result = await task
    assert result.isError is True
    assert result.content[0].text == "too far"


async def test_handle_result_invalid_payload(settings: GatewaySettings, move_tool: dict) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)
    call = websocket.last("request")
    channel.handle_result(
        {"type": "result", "requestId": call["requestId"], "result": {"content": "bad"}}
    )
    result = await task
    assert result.isError is True
    assert "Invalid provider result" in result.content[0].text


async def test_handle_result_ignores_done_future(
    settings: GatewaySettings, move_tool: dict
) -> None:
    channel, websocket = await register_and_attach(settings, move_tool)
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={"direction": "left"}))
    await asyncio.sleep(0)
    call = websocket.last("request")
    channel.handle_result(
        {"type": "result", "requestId": call["requestId"], "result": {"ok": True}}
    )
    await task
    channel.handle_result(
        {"type": "result", "requestId": call["requestId"], "result": {"ok": False}}
    )


def test_normalize_result_shapes() -> None:
    assert Channel.normalize_result("hello").content[0].text == "hello"
    assert Channel.normalize_result({"a": 1}).structuredContent == {"a": 1}
    assert Channel.normalize_result([1, 2]).structuredContent is None
    wrapped = Channel.normalize_result(
        {"content": [{"type": "text", "text": "x"}], "isError": False}
    )
    assert wrapped.content[0].text == "x"


async def test_validate_output_paths(settings: GatewaySettings, move_tool: dict) -> None:
    channel = make_channel(settings)
    await channel.register("tools", [move_tool])
    tool = channel._tools["move"]
    error = Channel.error_result("boom")
    assert channel.validate_output(tool, error) is error
    assert channel.validate_output(tool, Channel.normalize_result("x")).isError is False


async def test_validate_output_with_schema(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    await channel.register(
        "tools",
        [
            {
                "name": "score",
                "description": "d",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "outputSchema": {
                    "type": "object",
                    "properties": {"score": {"type": "integer"}},
                    "required": ["score"],
                },
            }
        ],
    )
    tool = channel._tools["score"]
    missing = channel.validate_output(tool, Channel.normalize_result("no-structured"))
    assert "structuredContent is missing" in missing.content[0].text

    invalid = channel.validate_output(tool, Channel.normalize_result({"score": "x"}))
    assert "Output validation error" in invalid.content[0].text

    ok = channel.validate_output(tool, Channel.normalize_result({"score": 3}))
    assert ok.isError is False


async def test_snapshot_reflects_provider_and_tools(
    settings: GatewaySettings, move_tool: dict
) -> None:
    channel = make_channel(settings)
    assert channel.snapshot()["providerConnected"] is False

    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p1", provider_name="service-a")
    await channel.register("tools", [move_tool])

    snapshot = channel.snapshot()
    assert snapshot["providerConnected"] is True
    assert snapshot["providerId"] == "p1"
    assert snapshot["providerName"] == "service-a"
    assert snapshot["tools"] == ["move"]
    assert snapshot["pendingCalls"] == 0


def test_remember_mcp_session_dedupes_and_caps(settings: GatewaySettings) -> None:
    channel = make_channel(settings.model_copy(update={"maximum_mcp_sessions_per_channel": 2}))
    first, second, third = object(), object(), object()

    channel.remember_mcp_session(first)
    channel.remember_mcp_session(first)
    channel.remember_mcp_session(second)
    channel.remember_mcp_session(third)

    assert list(channel._mcp_sessions.values()) == [second, third]


async def test_notify_list_changed_drops_dead_sessions(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    class GoodSession:
        def __init__(self) -> None:
            self.calls = 0

        async def send_tool_list_changed(self) -> None:
            self.calls += 1

    class DeadSession:
        async def send_tool_list_changed(self) -> None:
            raise RuntimeError("gone")

    good = GoodSession()
    channel.remember_mcp_session(good)
    channel.remember_mcp_session(good)
    channel.remember_mcp_session(DeadSession())
    await channel._notify(("send_tool_list_changed",))

    assert good.calls == 1
    assert list(channel._mcp_sessions.values()) == [good]


async def test_send_to_provider_wraps_transport_errors(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    websocket = FakeWebSocket(fail_send=True)
    await channel.attach(websocket, provider_id="p", provider_name=None)

    with pytest.raises(ChannelOfflineError):
        await channel.send_to_provider({"type": "ping"})


async def test_execute_tool_survives_a_transport_send_failure(
    settings: GatewaySettings, move_tool: dict
) -> None:
    channel = make_channel(settings)
    websocket = FakeWebSocket(fail_send=True)
    await channel.attach(websocket, provider_id="p", provider_name=None)
    await channel.register("tools", [move_tool])

    result = await channel.execute_tool(name="move", arguments={"direction": "right"})

    assert result.isError
    assert result.content[0].text == "Failed to send to the channel provider"
    assert channel._pending == {}


async def test_concurrent_notify_prunes_dead_sessions_without_clobbering(
    settings: GatewaySettings,
) -> None:
    channel = make_channel(settings)
    gate = asyncio.Event()

    class Live:
        def __init__(self) -> None:
            self.calls = 0

        async def send_tool_list_changed(self) -> None:
            self.calls += 1

    class Dead:
        def __init__(self) -> None:
            self.calls = 0

        async def send_tool_list_changed(self) -> None:
            self.calls += 1
            await gate.wait()
            raise RuntimeError("gone")

    live = Live()
    dead = Dead()
    channel.remember_mcp_session(live)
    channel.remember_mcp_session(dead)

    first = asyncio.create_task(channel._notify(("send_tool_list_changed",)))
    second = asyncio.create_task(channel._notify(("send_tool_list_changed",)))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)

    assert dead.calls == 2
    assert list(channel._mcp_sessions.values()) == [live]


async def test_notify_keeps_sessions_added_during_dispatch(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    added = object()

    class Adder:
        async def send_tool_list_changed(self) -> None:
            channel.remember_mcp_session(added)

    channel.remember_mcp_session(Adder())
    await channel._notify(("send_tool_list_changed",))

    assert added in channel._mcp_sessions.values()


async def test_fail_pending_skips_completed_futures(settings: GatewaySettings) -> None:
    channel = make_channel(settings)
    done: asyncio.Future = asyncio.get_running_loop().create_future()
    done.set_result(Channel.error_result("already done"))
    channel._pending["done"] = PendingRequest(future=done)

    channel._fail_pending(RuntimeError("boom"))
    assert channel._pending == {}
    assert done.exception() is None


async def test_close_with_and_without_socket(settings: GatewaySettings) -> None:
    connected = make_channel(settings)
    websocket = FakeWebSocket()
    await connected.attach(websocket, provider_id="p", provider_name=None)
    await connected.close()
    assert websocket.closed is True

    idle = make_channel(settings)
    await idle.close()
    assert idle.provider_connected is False


RESOURCE = {"uri": "mem://a", "name": "a"}


async def attach_only(settings: GatewaySettings) -> tuple[Channel, FakeWebSocket]:
    channel = make_channel(settings)
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)
    return channel, websocket


class RecordingSession:
    def __init__(self) -> None:
        self.updated: list[str] = []
        self.logs: list[tuple[str, Any, Any]] = []
        self.progress: list[tuple] = []
        self.changed: list[str] = []

    async def send_tool_list_changed(self) -> None:
        self.changed.append("tools")

    async def send_resource_list_changed(self) -> None:
        self.changed.append("resources")

    async def send_prompt_list_changed(self) -> None:
        self.changed.append("prompts")

    async def send_resource_updated(self, uri) -> None:
        self.updated.append(str(uri))

    async def send_log_message(self, level, data, logger) -> None:
        self.logs.append((level, data, logger))

    async def send_progress_notification(self, progress_token, progress, total, message) -> None:
        self.progress.append((progress_token, progress, total, message))


async def _drive(channel: Channel, websocket: FakeWebSocket, task, result) -> Any:
    await asyncio.sleep(0)
    call = websocket.last("request")
    channel.handle_result({"type": "result", "requestId": call["requestId"], **result})
    return await task


async def test_register_and_list_resources(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    await channel.register("resources", [RESOURCE])
    assert [str(r.uri) for r in channel.list_resources()] == ["mem://a"]
    await channel.register("resourceTemplates", [{"uriTemplate": "mem://{id}", "name": "t"}])
    assert [t.uriTemplate for t in channel.list_resource_templates()] == ["mem://{id}"]


async def test_register_duplicate_resource(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)

    with pytest.raises(ProviderMessageError, match="Duplicate resource"):
        await channel.register("resources", [RESOURCE, RESOURCE])


async def test_register_invalid_resource(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)

    with pytest.raises(ProviderMessageError, match="Invalid MCP resource"):
        await channel.register("resources", [{"name": "missing uri"}])


async def test_read_resource_success(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))
    await asyncio.sleep(0)
    assert websocket.last("request")["method"] == "resources/read"
    contents = await _drive(
        channel,
        websocket,
        task,
        {"result": {"contents": [{"uri": "mem://a", "text": "hi", "mimeType": "text/plain"}]}},
    )
    assert contents[0].content == "hi"
    assert contents[0].mime_type == "text/plain"


async def test_read_resource_blob(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    import base64

    blob = base64.b64encode(b"bytes").decode()
    task = asyncio.create_task(channel.read_resource("mem://a"))
    contents = await _drive(
        channel, websocket, task, {"result": {"contents": [{"uri": "mem://a", "blob": blob}]}}
    )
    assert contents[0].content == b"bytes"


async def test_read_resource_provider_error(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))

    with pytest.raises(ProviderRequestError, match="gone"):
        await _drive(channel, websocket, task, {"error": "gone"})


async def test_read_resource_invalid_result(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))

    with pytest.raises(ProviderRequestError, match="invalid resource result"):
        await _drive(channel, websocket, task, {"result": {}})


async def test_read_resource_content_requires_text_or_blob(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))

    with pytest.raises(ProviderRequestError, match="text or blob"):
        await _drive(channel, websocket, task, {"result": {"contents": [{"uri": "mem://a"}]}})


async def test_read_resource_content_must_be_object(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))

    with pytest.raises(ProviderRequestError, match="must be an object"):
        await _drive(channel, websocket, task, {"result": {"contents": ["oops"]}})


async def test_read_resource_rejects_invalid_blob(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.read_resource("mem://a"))

    with pytest.raises(ProviderRequestError, match="not valid base64"):
        await _drive(channel, websocket, task, {"result": {"contents": [{"blob": "not base64!!"}]}})


async def test_read_resource_offline(settings: GatewaySettings) -> None:
    channel = make_channel(settings)

    with pytest.raises(ProviderRequestError, match="offline"):
        await channel.read_resource("mem://a")


async def test_subscribe_updated_unsubscribe(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = RecordingSession()
    channel.remember_mcp_session(session)

    task = asyncio.create_task(channel.subscribe("mem://a", session))
    await asyncio.sleep(0)
    assert websocket.last("request")["method"] == "resources/subscribe"
    await _drive(channel, websocket, task, {"result": {}})

    await channel.handle_provider_notification(
        {
            "type": "notify",
            "method": "notifications/resources/updated",
            "params": {"uri": "mem://a"},
        }
    )
    assert session.updated == ["mem://a"]

    task = asyncio.create_task(channel.unsubscribe("mem://a", session))
    await _drive(channel, websocket, task, {"result": {}})
    assert channel._subscriptions == {}


async def test_resource_updated_without_subscribers_is_noop(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    await channel.handle_provider_notification(
        {"type": "notify", "method": "notifications/resources/updated", "params": {"uri": "x://y"}}
    )


async def test_resource_updated_drops_gone_subscriber(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = RecordingSession()
    channel.remember_mcp_session(session)
    task = asyncio.create_task(channel.subscribe("mem://a", session))
    await _drive(channel, websocket, task, {"result": {}})

    channel._mcp_sessions.clear()
    await channel.handle_provider_notification(
        {
            "type": "notify",
            "method": "notifications/resources/updated",
            "params": {"uri": "mem://a"},
        }
    )
    assert channel._subscriptions == {}


async def test_resource_updated_keeps_a_concurrent_resubscribe(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    fresh: set[int] = {999}

    class ReplacingSession(RecordingSession):
        async def send_resource_updated(self, uri) -> None:
            channel._subscriptions["mem://a"].discard(id(self))
            channel._subscriptions["mem://a"] = fresh

    session = ReplacingSession()
    channel.remember_mcp_session(session)
    channel._subscriptions["mem://a"] = {id(session)}

    await channel._notify_resource_updated("mem://a")
    assert channel._subscriptions["mem://a"] is fresh


async def test_notification_rejects_bad_params_and_unknown_method(
    settings: GatewaySettings,
) -> None:
    channel, _ = await attach_only(settings)

    with pytest.raises(ProviderMessageError, match="object params"):
        await channel.handle_provider_notification({"type": "notify", "method": "x", "params": 5})

    with pytest.raises(ProviderMessageError, match="Unsupported notification"):
        await channel.handle_provider_notification(
            {"type": "notify", "method": "bogus", "params": {}}
        )

    with pytest.raises(ProviderMessageError, match="string uri"):
        await channel.handle_provider_notification(
            {"type": "notify", "method": "notifications/resources/updated", "params": {}}
        )

    with pytest.raises(ProviderMessageError, match="Invalid log level"):
        await channel.handle_provider_notification(
            {"type": "notify", "method": "notifications/message", "params": {"level": "loud"}}
        )

    with pytest.raises(ProviderMessageError, match="numeric progress"):
        await channel.handle_provider_notification(
            {"type": "notify", "method": "notifications/progress", "params": {"progress": 0.5}}
        )

    with pytest.raises(ProviderMessageError, match="numeric progress"):
        await channel.handle_provider_notification(
            {
                "type": "notify",
                "method": "notifications/progress",
                "params": {"requestId": "r", "progress": "half"},
            }
        )


async def test_unsubscribe_branches(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    s1, s2 = RecordingSession(), RecordingSession()
    channel.remember_mcp_session(s1)
    channel.remember_mcp_session(s2)

    for session in (s1, s2):
        task = asyncio.create_task(channel.subscribe("mem://a", session))
        await _drive(channel, websocket, task, {"result": {}})

    task = asyncio.create_task(channel.unsubscribe("mem://a", s1))
    await _drive(channel, websocket, task, {"result": {}})
    assert channel._subscriptions == {"mem://a": {id(s2)}}

    task = asyncio.create_task(channel.unsubscribe("mem://none", s1))
    await _drive(channel, websocket, task, {"result": {}})


async def test_handle_provider_message_dispatches_notify(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = RecordingSession()
    channel.remember_mcp_session(session)
    task = asyncio.create_task(channel.subscribe("mem://a", session))
    await _drive(channel, websocket, task, {"result": {}})

    await channel.handle_provider_message(
        {
            "type": "notify",
            "method": "notifications/resources/updated",
            "params": {"uri": "mem://a"},
        }
    )
    assert session.updated == ["mem://a"]


async def test_forget_session_clears_subscriptions_on_overflow(settings: GatewaySettings) -> None:
    channel = make_channel(settings.model_copy(update={"maximum_mcp_sessions_per_channel": 1}))
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)

    s1 = RecordingSession()
    channel.remember_mcp_session(s1)
    task = asyncio.create_task(channel.subscribe("mem://a", s1))
    await _drive(channel, websocket, task, {"result": {}})
    assert channel._subscriptions == {"mem://a": {id(s1)}}

    channel.remember_mcp_session(RecordingSession())
    assert channel._subscriptions == {}


async def test_forget_session_keeps_shared_subscription(settings: GatewaySettings) -> None:
    channel = make_channel(settings.model_copy(update={"maximum_mcp_sessions_per_channel": 2}))
    websocket = FakeWebSocket()
    await channel.attach(websocket, provider_id="p", provider_name=None)

    s1, s2 = RecordingSession(), RecordingSession()

    for session in (s1, s2):
        channel.remember_mcp_session(session)
        task = asyncio.create_task(channel.subscribe("mem://a", session))
        await _drive(channel, websocket, task, {"result": {}})

    channel.remember_mcp_session(RecordingSession())
    assert channel._subscriptions == {"mem://a": {id(s2)}}


PROMPT = {"name": "greet", "description": "Say hi"}


async def test_register_and_list_prompts(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    await channel.register("prompts", [PROMPT])
    assert [p.name for p in channel.list_prompts()] == ["greet"]


async def test_register_duplicate_prompt(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)

    with pytest.raises(ProviderMessageError, match="Duplicate prompt"):
        await channel.register("prompts", [PROMPT, PROMPT])


async def test_get_prompt_success(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.get_prompt("greet", {"name": "x"}))
    await asyncio.sleep(0)
    assert websocket.last("request")["method"] == "prompts/get"
    result = await _drive(
        channel,
        websocket,
        task,
        {"result": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]}},
    )
    assert result.messages[0].content.text == "hi"


async def test_get_prompt_error(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(channel.get_prompt("greet", None))

    with pytest.raises(ProviderRequestError, match="unknown prompt"):
        await _drive(channel, websocket, task, {"error": "unknown prompt"})


async def test_complete_success(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    task = asyncio.create_task(
        channel.complete({"type": "ref/prompt", "name": "greet"}, {"name": "a", "value": "h"}, None)
    )
    await asyncio.sleep(0)
    assert websocket.last("request")["method"] == "completion/complete"
    result = await _drive(
        channel, websocket, task, {"result": {"values": ["hi", "ho"], "total": 2}}
    )
    assert result.values == ["hi", "ho"]


async def _log(channel: Channel, level: str, data: str = "x") -> None:
    await channel.handle_provider_message(
        {
            "type": "notify",
            "method": "notifications/message",
            "params": {"level": level, "data": data, "logger": "app"},
        }
    )


async def test_logging_respects_per_session_level(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    quiet, verbose = RecordingSession(), RecordingSession()
    channel.remember_mcp_session(quiet)
    channel.remember_mcp_session(verbose)
    channel.set_log_level(quiet, "warning")

    await _log(channel, "info")
    assert quiet.logs == []
    assert verbose.logs == [("info", "x", "app")]

    await _log(channel, "error")
    assert quiet.logs == [("error", "x", "app")]


async def test_logging_drops_dead_session(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)

    class Dead(RecordingSession):
        async def send_log_message(self, level, data, logger) -> None:
            raise RuntimeError("gone")

    dead = Dead()
    channel.remember_mcp_session(dead)
    await _log(channel, "info")
    assert channel._mcp_sessions == {}


async def test_progress_relayed_to_originating_session(settings: GatewaySettings) -> None:
    channel, websocket = await register_and_attach(
        settings,
        {
            "name": "move",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
    )
    session = RecordingSession()
    task = asyncio.create_task(
        channel.execute_tool(name="move", arguments={}, session=session, progress_token="tok")
    )
    await asyncio.sleep(0)
    request_id = websocket.last("request")["requestId"]

    await channel.handle_provider_message(
        {
            "type": "notify",
            "method": "notifications/progress",
            "params": {"requestId": request_id, "progress": 0.5, "total": 1, "message": "half"},
        }
    )
    assert session.progress == [("tok", 0.5, 1, "half")]

    channel.handle_result({"type": "result", "requestId": request_id, "result": {"ok": True}})
    await task


async def test_progress_without_token_is_ignored(settings: GatewaySettings) -> None:
    channel, websocket = await register_and_attach(
        settings,
        {
            "name": "move",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
    )
    task = asyncio.create_task(channel.execute_tool(name="move", arguments={}))
    await asyncio.sleep(0)
    request_id = websocket.last("request")["requestId"]

    await channel.handle_provider_message(
        {
            "type": "notify",
            "method": "notifications/progress",
            "params": {"requestId": request_id, "progress": 1},
        }
    )
    await channel.handle_provider_message(
        {
            "type": "notify",
            "method": "notifications/progress",
            "params": {"requestId": "unknown", "progress": 1},
        }
    )
    channel.handle_result({"type": "result", "requestId": request_id, "result": {"ok": True}})
    await task


class ClientCallSession(RecordingSession):
    async def create_message(self, **kwargs) -> types.CreateMessageResult:
        self.create_kwargs = kwargs
        return types.CreateMessageResult(
            role="assistant", content=types.TextContent(type="text", text="hi"), model="m"
        )

    async def elicit(self, message, requestedSchema) -> types.ElicitResult:
        self.elicit_args = (message, requestedSchema)
        return types.ElicitResult(action="accept", content={"ok": True})


async def test_sampling_relayed_to_client(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = ClientCallSession()
    channel.remember_mcp_session(session)

    messages = [{"role": "user", "content": {"type": "text", "text": "q"}}]
    await channel.handle_provider_message(
        {
            "type": "call",
            "requestId": "r1",
            "method": "sampling/createMessage",
            "params": {"messages": messages, "maxTokens": 100, "modelPreferences": {"hints": []}},
        }
    )
    reply = websocket.last("response")
    assert reply["requestId"] == "r1"
    assert reply["result"]["content"]["text"] == "hi"
    assert session.create_kwargs["model_preferences"] is not None

    await channel.handle_provider_message(
        {
            "type": "call",
            "requestId": "r2",
            "method": "sampling/createMessage",
            "params": {"messages": messages, "maxTokens": 50},
        }
    )
    assert session.create_kwargs["model_preferences"] is None


async def test_elicitation_relayed_to_client(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = ClientCallSession()
    channel.remember_mcp_session(session)

    await channel.handle_provider_message(
        {
            "type": "call",
            "requestId": "e1",
            "method": "elicitation/create",
            "params": {"message": "Your name?", "requestedSchema": {"type": "object"}},
        }
    )
    reply = websocket.last("response")
    assert reply["result"]["action"] == "accept"
    assert session.elicit_args[0] == "Your name?"


async def test_busy_session_survives_eviction(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)
    busy = RecordingSession()

    for _ in range(settings.maximum_mcp_sessions_per_channel * 2):
        channel.remember_mcp_session(busy)
        channel.remember_mcp_session(RecordingSession())

    assert id(busy) in channel._mcp_sessions
    assert len(channel._mcp_sessions) == settings.maximum_mcp_sessions_per_channel


async def test_subscription_survives_provider_reconnect(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    session = RecordingSession()
    channel.remember_mcp_session(session)
    task = asyncio.create_task(channel.subscribe("mem://a", session))
    await _drive(channel, websocket, task, {"result": {}})

    await channel.detach(websocket)
    assert channel._subscriptions == {"mem://a": {id(session)}}

    reconnected = FakeWebSocket()
    await channel.attach(reconnected, provider_id="p2", provider_name=None)
    await channel.handle_provider_notification(
        {
            "type": "notify",
            "method": "notifications/resources/updated",
            "params": {"uri": "mem://a"},
        }
    )
    assert session.updated == ["mem://a"]


async def test_replace_notifies_clients_of_cleared_registries(
    settings: GatewaySettings, move_tool: dict
) -> None:
    channel, _first = await register_and_attach(settings, move_tool)
    session = RecordingSession()
    channel.remember_mcp_session(session)

    second = FakeWebSocket()
    await channel.attach(second, provider_id="p2", provider_name=None)

    assert set(session.changed) == {"tools", "resources", "prompts"}
    assert channel.list_tools() == []


async def test_reverse_call_targets_the_initiating_client(settings: GatewaySettings) -> None:
    channel, _websocket = await attach_only(settings)
    loop = asyncio.get_running_loop()
    initiator = ClientCallSession()
    newest = ClientCallSession()
    channel.remember_mcp_session(initiator)
    channel.remember_mcp_session(newest)
    channel._pending["with-session"] = PendingRequest(
        future=loop.create_future(), session=initiator
    )
    channel._pending["no-session"] = PendingRequest(future=loop.create_future(), session=None)

    async def sample(originating: str) -> None:
        await channel.handle_provider_call(
            {
                "requestId": "c",
                "method": "sampling/createMessage",
                "params": {
                    "messages": [{"role": "user", "content": {"type": "text", "text": "q"}}],
                    "maxTokens": 5,
                },
                "originatingRequestId": originating,
            }
        )

    initiator.create_kwargs = None
    newest.create_kwargs = None
    await sample("with-session")
    assert initiator.create_kwargs is not None
    assert newest.create_kwargs is None

    newest.create_kwargs = None
    await sample("no-session")
    assert newest.create_kwargs is not None

    newest.create_kwargs = None
    await sample("ghost")
    assert newest.create_kwargs is not None

    for pending in channel._pending.values():
        pending.future.cancel()


async def test_client_call_without_session(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    await channel.handle_provider_call(
        {"requestId": "r", "method": "sampling/createMessage", "params": {}}
    )
    assert "No MCP client" in websocket.last("response")["error"]


async def test_client_call_unsupported_method(settings: GatewaySettings) -> None:
    channel, websocket = await attach_only(settings)
    channel.remember_mcp_session(ClientCallSession())
    await channel.handle_provider_call({"requestId": "r", "method": "bogus", "params": {}})
    assert "Unsupported client call" in websocket.last("response")["error"]


async def test_client_call_rejects_bad_shape(settings: GatewaySettings) -> None:
    channel, _ = await attach_only(settings)

    with pytest.raises(ProviderMessageError, match="string requestId"):
        await channel.handle_provider_call({"method": "x", "params": {}})

    with pytest.raises(ProviderMessageError, match="object params"):
        await channel.handle_provider_call({"requestId": "r", "method": "x", "params": 5})
