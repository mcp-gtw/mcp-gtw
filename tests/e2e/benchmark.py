import asyncio
import json
import time
import tracemalloc

from starlette.requests import Request

from mcpgtw.config import GatewaySettings
from mcpgtw.errors import ChannelCapacityError
from mcpgtw.gateway import Gateway
from mcpgtw.oauth.channel_access import DenyUnlessGranted
from mcpgtw.oauth.channel_grants import MemoryChannelGrantStore
from mcpgtw.oauth.token_verifier import AccessTokenVerifier
from mcpgtw.oauth.verified_principal import VerifiedPrincipal


class TestVerifier(AccessTokenVerifier):
    def __init__(self, principals):
        self.principals = principals

    async def verify(self, raw_token, expected_resource):
        return self.principals.get(raw_token)


async def main():
    count = 1000
    principals = {
        str(i): VerifiedPrincipal(
            "https://test.invalid",
            str(i),
            "host",
            frozenset({"mcp:access"}),
            int(time.time()) + 300,
        )
        for i in range(count)
    }
    tracemalloc.start()
    start = time.perf_counter()
    grants = MemoryChannelGrantStore(count)
    gateway = Gateway(
        GatewaySettings(
            oauth_mode="resource_server",
            oauth_resource_url="https://game.test/mcp",
            oauth_authorization_servers=["https://test.invalid"],
            oauth_token_verifier="custom",
            maximum_channels=count,
            oauth_maximum_sessions=count,
        ),
        access_token_verifier=TestVerifier(principals),
        channel_access=DenyUnlessGranted(grants),
    )
    for i in range(count):
        channel = await gateway.create_channel(channel_id=str(i))
        await grants.grant(principals[str(i)], channel.channel_id)
    setup = time.perf_counter() - start
    start = time.perf_counter()

    async def resolve(i):
        result = await gateway.mcp_access_controller.authorize(
            Request(
                {
                    "type": "http",
                    "path": "/mcp/" + str(i),
                    "root_path": "/mcp",
                    "method": "POST",
                    "query_string": b"",
                    "headers": [(b"authorization", ("Bearer " + str(i)).encode())],
                }
            )
        )
        assert result.channel.channel_id == str(i)
        assert gateway.session_bindings.bind(str(i), result.context)
        return result.context

    contexts = await asyncio.gather(*(resolve(i) for i in range(count)))
    elapsed = time.perf_counter() - start
    assert not gateway.session_bindings.bind("overflow", contexts[0])
    assert not gateway.session_bindings.accepts("0", contexts[1])
    assert gateway.session_bindings.accepts("0", contexts[0])
    try:
        await gateway.create_channel()
    except ChannelCapacityError:
        pass
    else:
        raise AssertionError("Channel cap not enforced")
    try:
        await grants.grant(principals["0"], "overflow")
    except ValueError:
        pass
    else:
        raise AssertionError("Grant cap not enforced")
    current, peak = tracemalloc.get_traced_memory()
    await gateway.registry.close_all()
    gateway.session_bindings.clear()
    assert not await grants.channels(principals["0"])
    assert not gateway.session_bindings._bindings
    print(
        json.dumps(
            {
                "channels": count,
                "principals": count,
                "setupSeconds": round(setup, 4),
                "concurrentAuthorizationSeconds": round(elapsed, 4),
                "tracedCurrentBytes": current,
                "tracedPeakBytes": peak,
                "channelCap": True,
                "grantCap": True,
                "sessionCap": True,
                "wrongOwnerDenied": True,
                "cleanup": True,
                "verifier": "test-only",
                "grants": "memory",
                "scope": "controller/session capacity; not JWT/SQLite/network throughput",
            }
        )
    )


asyncio.run(main())
