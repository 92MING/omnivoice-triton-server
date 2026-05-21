from __future__ import annotations

import json
import mmap
import os
import struct
import time
from pathlib import Path
from typing import Any


_HEADER = struct.Struct("<QI")
_HEADER_SIZE = 16


class SharedMetricsWriter:
    def __init__(self, path: str, size: int) -> None:
        self.path = path
        self.size = max(size, 4096)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.ftruncate(self.fd, self.size)
        self.mm = mmap.mmap(self.fd, self.size, access=mmap.ACCESS_WRITE)
        self.seq = 0

    def write(self, payload: dict[str, Any]) -> None:
        snapshot = dict(payload)
        snapshot["_metrics_snapshot_written_at"] = time.time()
        snapshot["metrics_transport"] = "shared_memory"
        data = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        max_payload = self.size - _HEADER_SIZE
        if len(data) > max_payload:
            data = json.dumps(
                {
                    "type": "health",
                    "status": "metrics_too_large",
                    "ready": False,
                    "metrics_transport": "shared_memory",
                    "_metrics_snapshot_written_at": time.time(),
                    "payload_bytes": len(data),
                    "max_payload_bytes": max_payload,
                },
                separators=(",", ":"),
            ).encode("utf-8")

        odd_seq = self.seq + 1
        even_seq = self.seq + 2
        self.mm.seek(0)
        self.mm.write(_HEADER.pack(odd_seq, 0))
        self.mm.write(b"\x00" * (_HEADER_SIZE - _HEADER.size))
        self.mm.seek(_HEADER_SIZE)
        self.mm.write(data)
        self.mm.seek(8)
        self.mm.write(struct.pack("<I", len(data)))
        self.mm.seek(0)
        self.mm.write(struct.pack("<Q", even_seq))
        self.seq = even_seq

    def close(self) -> None:
        self.mm.close()
        os.close(self.fd)


class SharedMetricsReader:
    def __init__(self, path: str, size: int) -> None:
        self.path = path
        self.size = max(size, 4096)
        self.fd: int | None = None
        self.mm: mmap.mmap | None = None

    def _open(self) -> bool:
        if self.mm is not None:
            return True
        if not self.path:
            return False
        try:
            self.fd = os.open(self.path, os.O_RDONLY)
            self.mm = mmap.mmap(self.fd, self.size, access=mmap.ACCESS_READ)
            return True
        except OSError:
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None
            self.mm = None
            return False

    def read(self) -> dict[str, Any] | None:
        if not self._open() or self.mm is None:
            return None

        for _ in range(5):
            self.mm.seek(0)
            seq1, length = _HEADER.unpack(self.mm.read(_HEADER.size))
            if seq1 == 0 or seq1 % 2 == 1 or length <= 0 or length > self.size - _HEADER_SIZE:
                time.sleep(0)
                continue
            self.mm.seek(_HEADER_SIZE)
            raw = self.mm.read(length)
            self.mm.seek(0)
            seq2 = struct.unpack("<Q", self.mm.read(8))[0]
            if seq1 == seq2 and seq2 % 2 == 0:
                payload = json.loads(raw)
                written_at = payload.get("_metrics_snapshot_written_at")
                if isinstance(written_at, (int, float)):
                    age = max(0.0, time.time() - float(written_at))
                    payload["_metrics_snapshot_age_s"] = round(age, 3)
                    payload["_metrics_snapshot_stale"] = age > 5.0
                return payload
        return None

    def close(self) -> None:
        if self.mm is not None:
            self.mm.close()
        if self.fd is not None:
            os.close(self.fd)
        self.mm = None
        self.fd = None


class SharedMetricsGroupReader:
    def __init__(self, entries: list[dict[str, Any]], size: int) -> None:
        self.entries = entries
        self.readers = [
            SharedMetricsReader(str(entry.get("metrics_shm_path") or ""), size)
            for entry in entries
        ]

    def read(self) -> dict[str, Any] | None:
        snapshots: list[dict[str, Any]] = []
        for entry, reader in zip(self.entries, self.readers):
            snapshot = reader.read()
            if snapshot is None:
                snapshots.append(
                    {
                        "status": "metrics_unavailable",
                        "ready": False,
                        "inferer_name": entry.get("name"),
                        "inferer_kind": entry.get("kind"),
                        "host": entry.get("host"),
                        "port": entry.get("port"),
                        "metrics_shm_path": entry.get("metrics_shm_path"),
                    }
                )
                continue
            merged = dict(snapshot)
            merged.setdefault("inferer_name", entry.get("name"))
            merged.setdefault("inferer_kind", entry.get("kind"))
            merged["host"] = entry.get("host")
            merged["port"] = entry.get("port")
            snapshots.append(merged)

        if not snapshots:
            return None

        numeric_keys = (
            "pending_tasks",
            "pending_chunks",
            "queued_batches",
            "queued_tasks",
            "running_batches",
            "total_batches",
            "total_tasks",
            "total_errors",
            "total_pcm_bytes",
            "total_empty_audio_fallbacks",
        )
        totals = {
            key: sum(int(snapshot.get(key) or 0) for snapshot in snapshots)
            for key in numeric_keys
        }
        ready_count = sum(
            1
            for snapshot in snapshots
            if snapshot.get("ready") and not snapshot.get("_metrics_snapshot_stale")
        )
        by_kind: dict[str, dict[str, int]] = {}
        for snapshot in snapshots:
            kind = str(snapshot.get("inferer_kind") or "unknown")
            bucket = by_kind.setdefault(kind, {key: 0 for key in numeric_keys})
            for key in numeric_keys:
                bucket[key] += int(snapshot.get(key) or 0)
        profile = self._merge_profile(snapshots)
        return {
            "type": "multi_inferer_metrics",
            "status": "healthy" if ready_count else "metrics_unavailable",
            "ready": ready_count > 0,
            "ready_inferers": ready_count,
            "inferer_count": len(snapshots),
            **totals,
            "by_kind": by_kind,
            "avg_batch_size": round(totals["total_tasks"] / totals["total_batches"], 3)
            if totals["total_batches"]
            else 0.0,
            "profile": profile,
            "inferers": snapshots,
            "_metrics_snapshot_written_at": time.time(),
            "metrics_transport": "shared_memory_group",
        }

    def close(self) -> None:
        for reader in self.readers:
            reader.close()

    def _merge_profile(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        merged: dict[str, dict[str, float]] = {}
        for snapshot in snapshots:
            profile = snapshot.get("profile")
            if not isinstance(profile, dict):
                continue
            for key, value in profile.items():
                if not isinstance(value, dict):
                    continue
                bucket = merged.setdefault(
                    str(key),
                    {
                        "count": 0.0,
                        "total_s": 0.0,
                        "max_ms": 0.0,
                    },
                )
                bucket["count"] += float(value.get("count") or 0)
                bucket["total_s"] += float(value.get("total_s") or 0.0)
                bucket["max_ms"] = max(bucket["max_ms"], float(value.get("max_ms") or 0.0))

        result: dict[str, Any] = {}
        for key, value in sorted(merged.items()):
            count = int(value["count"])
            total_s = float(value["total_s"])
            result[key] = {
                "count": count,
                "total_s": round(total_s, 3),
                "avg_ms": round((total_s / count) * 1000.0, 3) if count else 0.0,
                "max_ms": round(float(value["max_ms"]), 3),
            }
        return result
