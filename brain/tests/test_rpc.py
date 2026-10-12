import asyncio
import json

import pytest

from uo_brain.rpc import AgentRpc, RpcError


async def fake_client(reader, writer):
    while line := await reader.readline():
        req = json.loads(line)
        if req["method"] == "boom":
            reply = {"id": req["id"], "error": {"code": -32601, "message": "unknown method 'boom'"}}
        else:
            reply = {"id": req["id"], "result": {"method": req["method"], "params": req["params"]}}
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()


def test_round_trip_and_errors():
    async def go():
        server = await asyncio.start_server(fake_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        rpc = AgentRpc(port=port)
        await rpc.connect()
        a, b = await asyncio.gather(rpc.call("snapshot", since=3), rpc.call("status"))
        assert a == {"method": "snapshot", "params": {"since": 3}}
        assert b["method"] == "status"
        with pytest.raises(RpcError) as e:
            await rpc.call("boom")
        assert e.value.code == -32601
        await rpc.close()
        server.close()

    asyncio.run(go())



def test_a_restarted_client_is_found_again_on_the_same_port():
    async def go():
        server = await asyncio.start_server(fake_client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        rpc = AgentRpc(port=port)
        await rpc.connect()
        assert (await rpc.call("status"))["method"] == "status"
        # The client goes away (its connections close) and a new one starts on the port.
        server.close()
        rpc._writer.transport.abort()
        await asyncio.sleep(0.05)
        server = await asyncio.start_server(fake_client, "127.0.0.1", port)
        assert (await rpc.call("snapshot", since=0))["method"] == "snapshot"
        await rpc.close()
        server.close()

    asyncio.run(go())


def test_a_reset_connection_and_a_long_line_are_survived():
    """Found in review: a reset (RST) killed the read loop quietly, so calls waited out their timeout
    and nothing reconnected; and a reply over 64 KiB broke the reader for good."""
    big = "x" * 200_000
    resets = []

    async def client(reader, writer):
        line = await reader.readline()
        req = json.loads(line)
        if req["method"] == "reset" and not resets:
            resets.append(1)
            writer.transport.abort()  # RST, once
            return
        writer.write((json.dumps({"id": req["id"], "result": big}) + "\n").encode())
        await writer.drain()
        await fake_client(reader, writer)

    async def go():
        server = await asyncio.start_server(client, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        rpc = AgentRpc(port=port)
        await rpc.connect()
        assert len(await rpc.call("snapshot")) == len(big)
        # The next connection is reset at the first request: the call reconnects and is answered.
        rpc2 = AgentRpc(port=port)
        await rpc2.connect()
        res = await asyncio.wait_for(rpc2.call("reset", timeout=3), 8)
        assert res == big and resets == [1]  # reset once, then answered on the new connection
        await rpc.close()
        await rpc2.close()
        server.close()

    asyncio.run(go())
