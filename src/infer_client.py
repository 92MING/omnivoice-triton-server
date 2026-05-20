from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from config import Settings
from metrics_shm import SharedMetricsReader
from protocol import InferRequest


@dataclass
class InfererEndpoint:
    name: str
    kind: str
    host: str
    port: int
    metrics_shm_path: str = ""
    local_inflight: int = 0
    local_inflight_weight: int = 0
    last_health: dict[str, Any] | None = None
    last_health_at: float = 0.0
    metrics_reader: SharedMetricsReader | None = field(default=None, repr=False)


class InfererClient:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.endpoints = self._load_endpoints(cfg)
        self._lock = asyncio.Lock()

    def _load_endpoints(self, cfg: Settings) -> list[InfererEndpoint]:
        entries: list[dict[str, Any]] = []
        if cfg.inferers:
            try:
                payload = json.loads(cfg.inferers)
                if isinstance(payload, list):
                    entries = [entry for entry in payload if isinstance(entry, dict)]
            except json.JSONDecodeError:
                entries = []

        if not entries and cfg.infer_port:
            entries = [
                {
                    "name": "inferer-0",
                    "kind": cfg.inferer_kind or "gpu",
                    "host": cfg.infer_host,
                    "port": cfg.infer_port,
                    "metrics_shm_path": cfg.metrics_shm_path,
                }
            ]

        endpoints: list[InfererEndpoint] = []
        for idx, entry in enumerate(entries):
            host = str(entry.get("host") or cfg.infer_host)
            port = int(entry.get("port") or 0)
            if port <= 0:
                continue
            metrics_shm_path = str(entry.get("metrics_shm_path") or "")
            endpoints.append(
                InfererEndpoint(
                    name=str(entry.get("name") or f"inferer-{idx}"),
                    kind=str(entry.get("kind") or "gpu"),
                    host=host,
                    port=port,
                    metrics_shm_path=metrics_shm_path,
                    metrics_reader=SharedMetricsReader(metrics_shm_path, cfg.metrics_shm_size)
                    if metrics_shm_path
                    else None,
                )
            )
        return endpoints

    async def health(self) -> dict[str, Any]:
        snapshots = await asyncio.gather(
            *(self._endpoint_health(endpoint, max_age_s=0.0) for endpoint in self.endpoints),
            return_exceptions=True,
        )
        inferers: list[dict[str, Any]] = []
        for endpoint, result in zip(self.endpoints, snapshots):
            if isinstance(result, Exception):
                inferers.append(
                    {
                        "status": "unreachable",
                        "ready": False,
                        "inferer_name": endpoint.name,
                        "inferer_kind": endpoint.kind,
                        "host": endpoint.host,
                        "port": endpoint.port,
                        "error": str(result),
                    }
                )
            else:
                inferers.append(result)

        if len(inferers) == 1:
            return inferers[0]
        ready = [item for item in inferers if item.get("ready")]
        return {
            "type": "multi_inferer_health",
            "status": "healthy" if ready else "unreachable",
            "ready": bool(ready),
            "ready_inferers": len(ready),
            "inferer_count": len(inferers),
            "inferers": inferers,
        }

    async def infer(self, req: InferRequest, timeout_s: float) -> dict[str, Any]:
        endpoint, weight = await self._select_endpoint(req)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(
                endpoint.host, endpoint.port, limit=256 * 1024 * 1024
            )
            await self._write(writer, {"type": "infer", "request": req.model_dump()})
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
                if not line:
                    raise RuntimeError("inferer closed connection")
                msg = json.loads(line)
                if msg.get("type") == "accepted":
                    continue
                if msg.get("type") == "error":
                    raise RuntimeError(msg.get("error", "inferer error"))
                if msg.get("type") == "done":
                    return msg
        finally:
            await self._release_endpoint(endpoint, weight)
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    async def stream(self, req: InferRequest, timeout_s: float) -> AsyncIterator[dict[str, Any]]:
        endpoint, weight = await self._select_endpoint(req)
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.open_connection(
                endpoint.host, endpoint.port, limit=256 * 1024 * 1024
            )
            await self._write(writer, {"type": "infer", "request": req.model_dump()})
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout_s)
                if not line:
                    break
                msg = json.loads(line)
                typ = msg.get("type")
                if typ == "accepted":
                    yield msg
                    continue
                if typ == "error":
                    raise RuntimeError(msg.get("error", "inferer error"))
                yield msg
                if typ == "done":
                    break
        finally:
            await self._release_endpoint(endpoint, weight)
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    async def _select_endpoint(self, req: InferRequest) -> tuple[InfererEndpoint, int]:
        if not self.endpoints:
            raise RuntimeError("no inferer endpoints configured")

        request_weight = self._request_weight(req)
        health = await asyncio.gather(
            *(self._endpoint_health(endpoint) for endpoint in self.endpoints),
            return_exceptions=True,
        )

        async with self._lock:
            available: list[tuple[InfererEndpoint, dict[str, Any], int]] = []
            for endpoint, result in zip(self.endpoints, health):
                if isinstance(result, Exception):
                    continue
                if not result.get("ready"):
                    continue
                available.append((endpoint, result, self._score(endpoint, result)))
            if not available:
                raise RuntimeError("no inferer endpoints are ready")

            endpoint = min(available, key=lambda item: item[2])[0]
            endpoint.local_inflight += 1
            endpoint.local_inflight_weight += request_weight
            return endpoint, request_weight

    async def _release_endpoint(self, endpoint: InfererEndpoint, weight: int) -> None:
        async with self._lock:
            endpoint.local_inflight = max(0, endpoint.local_inflight - 1)
            endpoint.local_inflight_weight = max(0, endpoint.local_inflight_weight - weight)

    async def _endpoint_health(
        self,
        endpoint: InfererEndpoint,
        max_age_s: float = 0.1,
    ) -> dict[str, Any]:
        now = time.monotonic()
        if endpoint.last_health is not None and now - endpoint.last_health_at <= max_age_s:
            snapshot = dict(endpoint.last_health)
        else:
            snapshot = self._read_endpoint_shared_metrics(endpoint) or {}
            if not snapshot or snapshot.get("_metrics_snapshot_stale"):
                reader, writer = await asyncio.open_connection(
                    endpoint.host, endpoint.port, limit=256 * 1024 * 1024
                )
                try:
                    await self._write(writer, {"type": "health"})
                    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    snapshot = json.loads(line)
                finally:
                    writer.close()
                    await writer.wait_closed()
            endpoint.last_health = dict(snapshot)
            endpoint.last_health_at = now

        snapshot.setdefault("inferer_name", endpoint.name)
        snapshot.setdefault("inferer_kind", endpoint.kind)
        snapshot["host"] = endpoint.host
        snapshot["port"] = endpoint.port
        snapshot["local_inflight"] = endpoint.local_inflight
        snapshot["local_inflight_weight"] = endpoint.local_inflight_weight
        snapshot["route_score"] = self._score(endpoint, snapshot)
        return snapshot

    def _score(self, endpoint: InfererEndpoint, health: dict[str, Any]) -> int:
        return (
            int(health.get("pending_chunks") or health.get("pending_tasks") or 0)
            + int(health.get("queued_tasks") or 0)
            + int(health.get("running_batches") or 0) * int(health.get("batch_size") or self.cfg.batch_size)
            + endpoint.local_inflight_weight
        )

    def _request_weight(self, req: InferRequest) -> int:
        chunks = [chunk for chunk in req.chunks if chunk.strip()]
        chunk_count = len(chunks) or 1
        char_count = sum(len(chunk) for chunk in chunks) or len(req.input)
        text_weight = max(1, (char_count + 79) // 80)
        return max(chunk_count, text_weight)

    def _read_endpoint_shared_metrics(self, endpoint: InfererEndpoint) -> dict[str, Any] | None:
        if endpoint.metrics_reader is None:
            return None
        try:
            return endpoint.metrics_reader.read()
        except Exception:
            return None

    async def _write(self, writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        writer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")
        await writer.drain()
