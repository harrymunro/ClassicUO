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
