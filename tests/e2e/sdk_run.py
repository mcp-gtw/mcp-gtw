import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(config):
    async with httpx.AsyncClient() as client:
        for _ in range(100):
            response = await client.get("http://127.0.0.1:19480/ready")

            if response.json()["ready"]:
                break

            await asyncio.sleep(0.1)
        else:
            raise AssertionError("Providers did not register")

        for name in config["channels"]:
            token = config[name]["mcp"] if name == "static" else config["access"]

            async with (
                httpx2.AsyncClient(headers={"Authorization": "Bearer " + token}) as transport,
                streamable_http_client(
                    "http://127.0.0.1:19480/mcp/" + name, http_client=transport
                ) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                assert len((await session.list_tools()).tools) == 1
                result = await session.call_tool("identity", {})
                assert result.structured_content == {"channel": name}

        response = await client.post(
            "http://127.0.0.1:19480/mcp/static",
            headers={"Authorization": "Bearer " + config["access"]},
        )
        assert response.status_code == 404
        response = await client.post(
            "http://127.0.0.1:19480/mcp/oauth",
            headers={"Authorization": "Bearer " + config["oauth"]["mcp"]},
        )
        assert response.status_code == 401
        print(
            json.dumps(
                {
                    "staticProvider": True,
                    "oauthProvider": True,
                    "crossChannelDenied": True,
                    "internalTokenDenied": True,
                    "sourceUnchanged": True,
                    "installedArtifact": config["installed_artifact"],
                }
            )
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-module", type=Path)
    args = parser.parse_args()

    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 19480))

    root = Path(__file__).resolve().parents[2]
    fixture = Path(__file__).parent

    with tempfile.TemporaryDirectory() as temp:
        module = args.provider_module or root.parent / "mcp-gtw-provider/src/index.js"
        config = {
            "access": secrets.token_urlsafe(32),
            "channels": ["static", "oauth"],
            "provider_module": module.resolve().as_uri(),
            "installed_artifact": args.provider_module is not None,
        }

        for name in config["channels"]:
            config[name] = {"mcp": secrets.token_urlsafe(32), "provider": secrets.token_urlsafe(32)}

        path = Path(temp) / "credentials.json"
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        env = os.environ | {"TEST_CONFIG": str(path)}

        with (Path(temp) / "server.log").open("w") as log:
            server = subprocess.Popen(
                [
                    str(root / ".venv/bin/python"),
                    "-m",
                    "uvicorn",
                    "sdk_server:app",
                    "--app-dir",
                    str(fixture),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "19480",
                    "--no-access-log",
                ],
                env=env,
                stdout=log,
                stderr=log,
            )
            provider = None

            try:
                time.sleep(1)
                provider = subprocess.Popen(
                    ["node", str(fixture / "sdk_provider.mjs")], env=env, stdout=log, stderr=log
                )
                asyncio.run(verify(config))
            finally:
                if provider:
                    provider.terminate()
                    provider.wait(timeout=10)

                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()
