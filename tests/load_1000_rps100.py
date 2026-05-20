from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:9194"
OUT_DIR = Path(__file__).resolve().parents[1] / "tmp" / "test-artifacts"


SHORT_TEXTS = [
    "Hi.",
    "Okay.",
    "Thanks.",
    "One moment.",
    "Done.",
    "Yes.",
    "No problem.",
    "Try again.",
    "Good morning.",
    "See you soon.",
]

VOICES = [
    "auto",
    "alloy",
    "nova",
    "onyx",
    "sage",
    "design:female, young adult, moderate pitch",
    "design:male, middle-aged, low pitch",
]

LANGUAGES = [None, None, None, "en"]
DURATIONS = [0.22, 0.28, 0.35, 0.45]
SPEEDS = [0.8, 1.0, 1.15, 1.3]
FORMATS = ["pcm", "pcm", "pcm", "wav"]


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def make_payload(
    index: int,
    *,
    include_duration: bool = True,
    include_cfg: bool = True,
    speaker_field: str = "voice",
    response_format: str | None = None,
) -> dict[str, Any]:
    fmt = response_format or FORMATS[index % len(FORMATS)]
    payload: dict[str, Any] = {
        "model": "tts-1",
        "input": f"{SHORT_TEXTS[index % len(SHORT_TEXTS)]} #{index}",
        speaker_field: VOICES[index % len(VOICES)],
        "response_format": fmt,
        "speed": SPEEDS[index % len(SPEEDS)],
    }
    # Most requests set explicit duration to keep generated audio short. A
    # minority omit it so speed still exercises the model path.
    if include_duration and index % 5 != 0:
        payload["duration"] = DURATIONS[index % len(DURATIONS)]
    language = LANGUAGES[index % len(LANGUAGES)]
    if language:
        payload["language"] = language
    if include_cfg and index % 17 == 0:
        payload["CFG"] = 2.5
    return payload


def is_audio_body_valid(fmt: str, body: bytes) -> bool:
    if fmt == "wav":
        return len(body) > 44 and body[:4] == b"RIFF" and body[8:12] == b"WAVE"
    return len(body) > 0


async def sample_metrics(client: httpx.AsyncClient, stop: asyncio.Event, samples: list[dict[str, Any]]) -> None:
    while not stop.is_set():
        try:
            response = await client.get("/metrics", timeout=5.0)
            if response.status_code == 200:
                item = response.json()
                item["_ts"] = time.time()
                samples.append(item)
        except Exception as exc:
            samples.append({"_ts": time.time(), "error": repr(exc)})
        await asyncio.sleep(1.0)


async def send_one(
    client: httpx.AsyncClient,
    index: int,
    scheduled_at: float,
    result: dict[str, Any],
    *,
    include_duration: bool,
    include_cfg: bool,
    speaker_field: str,
    response_format: str | None,
) -> None:
    await asyncio.sleep(max(0.0, scheduled_at - time.perf_counter()))
    payload = make_payload(
        index,
        include_duration=include_duration,
        include_cfg=include_cfg,
        speaker_field=speaker_field,
        response_format=response_format,
    )
    start = time.perf_counter()
    try:
        response = await client.post("/v1/audio/speech", json=payload, timeout=300.0)
        body = await response.aread()
        elapsed = time.perf_counter() - start
        result.update(
            {
                "index": index,
                "status": response.status_code,
                "elapsed_s": elapsed,
                "bytes": len(body),
                "valid_audio": response.status_code == 200
                and is_audio_body_valid(payload["response_format"], body),
                "format": payload["response_format"],
                "speaker": payload[speaker_field],
                "speaker_field": speaker_field,
                "has_duration": "duration" in payload,
                "has_cfg": "CFG" in payload,
                "language": payload.get("language"),
                "error": None if response.status_code == 200 else body[:300].decode("utf-8", "replace"),
            }
        )
    except Exception as exc:
        result.update(
            {
                "index": index,
                "status": None,
                "elapsed_s": time.perf_counter() - start,
                "bytes": 0,
                "valid_audio": False,
                "format": payload["response_format"],
                "speaker": payload[speaker_field],
                "speaker_field": speaker_field,
                "has_duration": "duration" in payload,
                "has_cfg": "CFG" in payload,
                "language": payload.get("language"),
                "error": repr(exc),
            }
        )


async def run_load(
    total: int,
    rate: float,
    concurrency_limit: int,
    *,
    include_duration: bool = True,
    include_cfg: bool = True,
    speaker_field: str = "voice",
    response_format: str | None = None,
) -> dict[str, Any]:
    limits = httpx.Limits(max_connections=concurrency_limit, max_keepalive_connections=concurrency_limit)
    timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)
    async with httpx.AsyncClient(base_url=BASE_URL, limits=limits, timeout=timeout) as client:
        before = (await client.get("/metrics")).json()
        stop = asyncio.Event()
        metric_samples: list[dict[str, Any]] = []
        sampler = asyncio.create_task(sample_metrics(client, stop, metric_samples))

        results: list[dict[str, Any]] = [{} for _ in range(total)]
        started_at = time.perf_counter()
        tasks = [
            asyncio.create_task(
                send_one(
                    client,
                    i,
                    started_at + (i / rate),
                    results[i],
                    include_duration=include_duration,
                    include_cfg=include_cfg,
                    speaker_field=speaker_field,
                    response_format=response_format,
                )
            )
            for i in range(total)
        ]
        await asyncio.gather(*tasks)
        completed_at = time.perf_counter()

        stop.set()
        await sampler
        after = (await client.get("/metrics")).json()

    latencies = [item["elapsed_s"] for item in results if item.get("status") == 200]
    statuses: dict[str, int] = {}
    for item in results:
        key = str(item.get("status"))
        statuses[key] = statuses.get(key, 0) + 1
    errors = [item for item in results if item.get("status") != 200]
    bad_outputs = [item for item in results if item.get("status") == 200 and not item.get("valid_audio")]
    duration = completed_at - started_at
    delta_batches = after.get("total_batches", 0) - before.get("total_batches", 0)
    delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    delta_errors = after.get("total_errors", 0) - before.get("total_errors", 0)
    pending_peaks = [sample.get("pending_tasks", 0) for sample in metric_samples if "pending_tasks" in sample]
    queued_batch_peaks = [
        sample.get("queued_batches", 0) for sample in metric_samples if "queued_batches" in sample
    ]
    queued_task_peaks = [
        sample.get("queued_tasks", 0) for sample in metric_samples if "queued_tasks" in sample
    ]
    running_peaks = [sample.get("running_batches", 0) for sample in metric_samples if "running_batches" in sample]
    backlog_age_peaks = [
        sample.get("oldest_backlog_task_age_ms", 0.0)
        for sample in metric_samples
        if "oldest_backlog_task_age_ms" in sample
    ]

    return {
        "total": total,
        "target_rate_rps": rate,
        "traffic": {
            "include_duration": include_duration,
            "include_cfg": include_cfg,
            "speaker_field": speaker_field,
            "response_format": response_format or "mixed",
            "varied_fields": ["speed", speaker_field, "language"],
        },
        "actual_wall_s": round(duration, 3),
        "actual_completion_rps": round(total / duration, 3) if duration else 0,
        "success": len(latencies),
        "statuses": statuses,
        "latency_s": {
            "min": round(min(latencies), 4) if latencies else 0,
            "mean": round(statistics.fmean(latencies), 4) if latencies else 0,
            "p50": round(percentile(latencies, 50), 4),
            "p90": round(percentile(latencies, 90), 4),
            "p95": round(percentile(latencies, 95), 4),
            "p99": round(percentile(latencies, 99), 4),
            "max": round(max(latencies), 4) if latencies else 0,
        },
        "bytes": {
            "total": sum(item.get("bytes", 0) for item in results),
            "mean": round(statistics.fmean([item.get("bytes", 0) for item in results]), 1),
        },
        "backend_delta": {
            "batches": delta_batches,
            "tasks": delta_tasks,
            "errors": delta_errors,
            "avg_batch_size": round(delta_tasks / delta_batches, 3) if delta_batches else 0,
        },
        "observed_peaks": {
            "pending_tasks": max(pending_peaks) if pending_peaks else 0,
            "queued_batches": max(queued_batch_peaks) if queued_batch_peaks else 0,
            "queued_tasks": max(queued_task_peaks) if queued_task_peaks else 0,
            "running_batches": max(running_peaks) if running_peaks else 0,
            "oldest_backlog_task_age_ms": round(max(backlog_age_peaks), 1)
            if backlog_age_peaks
            else 0.0,
        },
        "before_metrics": before,
        "after_metrics": after,
        "metric_samples": metric_samples,
        "errors": errors[:20],
        "bad_outputs": bad_outputs[:20],
        "passed": len(errors) == 0
        and len(bad_outputs) == 0
        and delta_tasks == total
        and delta_errors == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--total", type=int, default=1000)
    parser.add_argument("--rate", type=float, default=100.0)
    parser.add_argument("--concurrency-limit", type=int, default=512)
    parser.add_argument("--no-duration", action="store_true")
    parser.add_argument("--no-cfg", action="store_true")
    parser.add_argument("--speaker-field", choices=["voice", "speaker"], default="voice")
    parser.add_argument("--response-format", choices=["wav", "pcm"], default=None)
    parser.add_argument("--out", default=str(OUT_DIR / "load_1000_rps100_results.json"))
    args = parser.parse_args()

    result = asyncio.run(
        run_load(
            args.total,
            args.rate,
            args.concurrency_limit,
            include_duration=not args.no_duration,
            include_cfg=not args.no_cfg,
            speaker_field=args.speaker_field,
            response_format=args.response_format,
        )
    )
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result[k] for k in result if k not in {"metric_samples"}}, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
