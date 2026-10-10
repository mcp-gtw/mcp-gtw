from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpcore


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    """Pin DNS to a public address while preserving the original TLS server name."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self.backend = backend or httpcore.AnyIOBackend()

    @staticmethod
    def public_address(address: str) -> str:
        ip = ipaddress.ip_address(address)

        if (
            not ip.is_global
            or ip.is_multicast
            or ip.is_reserved
            or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None)
        ):
            raise ValueError("Client metadata requires a public network address")

        return str(ip)

    async def connect_tcp(
        self,
        host,
        port,
        timeout=None,  # noqa: ASYNC109 - HTTPCore's network backend contract
        local_address=None,
        socket_options=None,
    ) -> httpcore.AsyncNetworkStream:
        async with asyncio.timeout(timeout):
            answers = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
            addresses = [self.public_address(answer[4][0]) for answer in answers]

            if not addresses:
                raise ValueError("No public client metadata address")

            selected = addresses[0]
            stream = await self.backend.connect_tcp(
                selected, port, timeout, local_address, socket_options
            )
            peer = stream.get_extra_info("server_addr")

            if peer is None or peer[0] != selected:
                await stream.aclose()
                raise ValueError("Client metadata peer differs from validated DNS")

            return stream
