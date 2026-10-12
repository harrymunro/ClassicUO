"""Client for the agent RPC server inside ClassicUO (src/ClassicUO.Client/Agent/AgentHost.cs).

One JSON object per line in each direction; replies carry the request id. If the client goes
away (it crashed or was restarted), the next call waits for one on the same port and goes on there,
so a long-running brain outlives its client (cuo-m12).
"""

import asyncio
import itertools
import json
from typing import Any


LINE_LIMIT = 16 * 1024 * 1024  # a snapshot with the pack can pass asyncio's 64 KiB default


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"{message} ({code})")
        self.code = code


class AgentRpc:
    RECONNECT_ATTEMPTS = 120  # seconds to wait for a client to come back on the port

    def __init__(self, host: str = "127.0.0.1", port: int = 5577, reconnect: bool = True):
        self.host = host
        self.port = port
        self.reconnect = reconnect
        self._closed = False      # the connection dropped: reconnect before the next call
        self._lock: asyncio.Lock | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None

    async def connect(self, attempts: int = 60, delay: float = 1.0) -> None:
        """Connect, retrying while the client starts up."""
        for attempt in range(attempts):
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port, limit=LINE_LIMIT)
                break
            except OSError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(delay)
        self._closed = False
        self._read_task = asyncio.create_task(self._read_loop())

    async def _reconnect(self) -> None:
        self._lock = self._lock or asyncio.Lock()
        async with self._lock:
            if not self._closed and self._writer is not None and not self._writer.is_closing():
                return  # another call reconnected already
            await self.close()
            await self.connect(attempts=self.RECONNECT_ATTEMPTS)

    async def close(self) -> None:
        if self._read_task:
            self._read_task.cancel()
        if self._writer:
            self._writer.close()

    async def call(self, method: str, timeout: float = 10.0, **params: Any) -> Any:
        if self._writer is None:
            raise RuntimeError("not connected")
        if self.reconnect and (self._closed or self._writer.is_closing()):
            await self._reconnect()
        try:
            return await self._call(method, timeout, params)
        except (ConnectionError, OSError) as e:
            if not self.reconnect or isinstance(e, TimeoutError):  # a slow reply isn't a lost client
                raise
            self._closed = True
            await self._reconnect()
            return await self._call(method, timeout, params)

    async def _call(self, method: str, timeout: float, params: dict[str, Any]) -> Any:
        assert self._writer is not None
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
        try:
            await self._read_lines()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a reset or a broken line: the connection is gone either way
            pass
        self._closed = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("agent connection closed"))

    async def _read_lines(self) -> None:
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
