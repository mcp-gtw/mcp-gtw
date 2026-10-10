import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

fixture = Path(__file__).resolve().parent
issuer = "https://localhost:19502"

for port in (19502, 19503):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", port))

with tempfile.TemporaryDirectory(prefix="oauth-consent-") as temporary:
    directory = Path(temporary)
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(directory / "tls.key"),
            "-out",
            str(directory / "tls.pem"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    environment = os.environ | {"TEST_ISSUER": issuer, "TEST_DIRECTORY": str(directory)}

    with (directory / "server.log").open("w") as log:
        servers = []

        for module, port in (("consent_server", 19502), ("consent_callback", 19503)):
            servers.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        module + ":app",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(port),
                        "--ssl-keyfile",
                        str(directory / "tls.key"),
                        "--ssl-certfile",
                        str(directory / "tls.pem"),
                        "--no-access-log",
                    ],
                    cwd=fixture,
                    env=environment,
                    stdout=log,
                    stderr=log,
                )
            )

        try:
            with httpx.Client(verify=False, timeout=1) as client:
                for _ in range(100):
                    if any(server.poll() is not None for server in servers):
                        raise RuntimeError((directory / "server.log").read_text())

                    try:
                        ready = [
                            client.get(issuer + "/.well-known/oauth-authorization-server"),
                            client.get("https://127.0.0.1:19503/stats"),
                        ]
                    except httpx.HTTPError:
                        time.sleep(0.1)
                        continue

                    if all(response.status_code == 200 for response in ready):
                        break

                    time.sleep(0.1)
                else:
                    raise RuntimeError("The local authorization server did not become ready")

            subprocess.run(
                ["node", str(fixture / "browser/consent.mjs")], env=environment, check=True
            )
        finally:
            for server in servers:
                server.terminate()

            for server in servers:
                server.wait(timeout=15)
