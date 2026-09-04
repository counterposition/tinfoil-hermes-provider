"""Forward 127.0.0.1:<port> inside the container to the host's tinfoil-proxy.

The proxy rejects any Host header other than 127.0.0.1/localhost, so the
plugin's default base_url must be reachable at that literal address.

    python forward.py <listen-port> <target-host> <target-port>
"""

from __future__ import annotations

import asyncio
import sys


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def _serve(listen_port: int, host: str, port: int) -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(host, port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pump(reader, upstream_writer), _pump(upstream_reader, writer))

    server = await asyncio.start_server(handle, "127.0.0.1", listen_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_serve(int(sys.argv[1]), sys.argv[2], int(sys.argv[3])))
