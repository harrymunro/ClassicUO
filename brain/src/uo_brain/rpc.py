"""Client for the agent RPC server inside ClassicUO (src/ClassicUO.Client/Agent/AgentHost.cs).

One JSON object per line in each direction; replies carry the request id.
"""

import asyncio
import itertools
import json
from typing import Any


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"{message} ({code})")
        self.code = code


class AgentRpc:
    def __init__(self, host: str = "127.0.0.1", port: int = 5577):
        self.host = host
        self.port = port
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None

    async def connect(self, attempts: int = 60, delay: float = 1.0) -> None:
        """Connect, retrying while the client starts up."""
        for attempt in range(attempts):
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
                break
            except OSError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(delay)
        self._read_task = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            self._writer.close()

    async def call(self, method: str, timeout: float = 10.0, **params: Any) -> Any:
        if self._writer is None:
            raise RuntimeError("not connected")
        rid = next(self._ids)
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        line = json.dumps({"id": rid, "method": method, "params": params}) + "\n"
        self._writer.write(line.encode())
        await self._writer.drain()
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while line := await self._reader.readline():
            msg = json.loads(line)
            fut = self._pending.get(msg.get("id"))
            if fut is None or fut.done():
                continue
            if "error" in msg:
                err = msg["error"]
                fut.set_exception(RpcError(err.get("code", -1), err.get("message", "error")))
            else:
                fut.set_result(msg.get("result"))
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("agent connection closed"))
