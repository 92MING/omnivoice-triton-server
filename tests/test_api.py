from __future__ import annotations

import base64
import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "http://127.0.0.1:9194"
OUT_DIR = Path(__file__).resolve().parents[1] / "tmp" / "test-artifacts"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def post_json(path: str, data: dict[str, Any], timeout: float = 300.0):
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def get_json(path: str) -> dict[str, Any]:
    with urllib.request.urlopen(BASE_URL + path, timeout=10.0) as response:
        assert_true(response.status == 200, f"GET {path} returned {response.status}")
        return json.loads(response.read().decode("utf-8"))


def multipart_request(path: str, fields: dict[str, str], files: dict[str, tuple[str, bytes, str]] | None = None):
    boundary = b"omnivoice-test-boundary"
    body_parts: list[bytes] = []
    for name, value in fields.items():
        body_parts.extend(
            [
                b"--" + boundary + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, (filename, data, content_type) in (files or {}).items():
        body_parts.extend(
            [
                b"--" + boundary + b"\r\n",
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
                data,
                b"\r\n",
            ]
        )
    body_parts.append(b"--" + boundary + b"--\r\n")
    request = urllib.request.Request(
        BASE_URL + path,
        data=b"".join(body_parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary.decode()}"},
        method="POST",
    )
    try:
        return urllib.request.urlopen(request, timeout=300.0)
    except urllib.error.HTTPError as exc:
        return exc


def assert_wav(name: str, data: bytes, min_bytes: int = 1000) -> None:
    assert_true(len(data) > min_bytes, f"{name} too small: {len(data)} bytes")
    assert_true(data[:4] == b"RIFF" and data[8:12] == b"WAVE", f"{name} is not a WAV")


def read_body(response) -> bytes:
    try:
        return response.read()
    finally:
        response.close()


def parse_sse(response) -> tuple[list[tuple[str | None, dict[str, Any] | str]], list[str]]:
    events: list[tuple[str | None, dict[str, Any] | str]] = []
    raw_lines: list[str] = []
    current_event: str | None = None
    for line in response:
        decoded = line.decode("utf-8", "replace").rstrip("\n")
        raw_lines.append(decoded)
        if decoded.startswith("event: "):
            current_event = decoded[7:]
        elif decoded.startswith("data: "):
            payload = decoded[6:]
            if payload == "[DONE]":
                events.append((current_event, "[DONE]"))
                break
            events.append((current_event, json.loads(payload)))
            current_event = None
    response.close()
    return events, raw_lines


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    health = get_json("/health")
    assert_true(health["ready"] is True, "health not ready")
    assert_true(health["inferer"]["batch_size"] == 16, "batch_size is not 16")
    for key in (
        "total_errors",
        "avg_batch_size",
        "avg_batch_elapsed_s",
        "avg_queue_wait_ms",
        "max_batch_size_seen",
        "last_batch",
    ):
        assert_true(key in health["inferer"], f"health missing metric {key}")
    results["health"] = health["inferer"]

    models = get_json("/v1/models")
    model_ids = {item["id"] for item in models["data"]}
    assert_true({"omnivoice", "tts-1", "tts-1-hd"} <= model_ids, "model list incomplete")
    model = get_json("/v1/models/tts-1")
    assert_true(model["id"] == "tts-1", "model detail mismatch")

    bad_model = urllib.request.urlopen if False else None
    try:
        urllib.request.urlopen(BASE_URL + "/v1/models/not-a-model", timeout=10.0)
        raise AssertionError("invalid model should 404")
    except urllib.error.HTTPError as exc:
        assert_true(exc.code == 404, f"invalid model returned {exc.code}")
        bad_model = exc.code
    results["invalid_model_status"] = bad_model

    response = post_json(
        "/v1/audio/speech",
        {"model": "tts-1", "input": "param test", "voice": "auto", "num_step": 8},
    )
    forbidden_body = read_body(response).decode("utf-8")
    assert_true(response.status == 422, f"forbidden num_step returned {response.status}")
    assert_true("num_step" in forbidden_body, "forbidden response missing num_step")
    results["forbidden_status"] = response.status

    response = post_json(
        "/v1/audio/speech",
        {
            "model": "tts-1",
            "input": "Unsupported response format test.",
            "voice": "auto",
            "response_format": "mp3",
            "duration": 0.35,
        },
    )
    unsupported_body = read_body(response).decode("utf-8")
    assert_true(response.status == 400, f"unsupported format returned {response.status}")
    assert_true("not implemented" in unsupported_body, "unsupported format detail mismatch")
    results["unsupported_format_status"] = response.status

    response = post_json(
        "/v1/audio/speech",
        {
            "model": "tts-1",
            "input": "Short pcm smoke test.",
            "voice": "auto",
            "response_format": "pcm",
            "duration": 0.35,
        },
    )
    pcm = read_body(response)
    assert_true(response.status == 200, f"pcm returned {response.status}")
    assert_true(response.headers.get("content-type") == "audio/pcm", "pcm content-type mismatch")
    assert_true(len(pcm) > 1000, "pcm response too small")
    (OUT_DIR / "pcm_smoke.pcm").write_bytes(pcm)
    results["pcm_bytes"] = len(pcm)

    response = post_json(
        "/v1/audio/speech",
        {
            "model": "tts-1",
            "input": "Reference audio for clone smoke test.",
            "voice": "auto",
            "response_format": "wav",
            "duration": 0.35,
            "CFG": 2.5,
        },
    )
    ref_wav = read_body(response)
    assert_true(response.status == 200, f"reference wav returned {response.status}")
    assert_wav("reference wav", ref_wav)
    extra_header = response.headers.get("X-OmniVoice-Extra-Fields")
    assert_true(extra_header == '{"CFG": 2.5}', f"extra header mismatch: {extra_header}")
    (OUT_DIR / "reference_for_clone.wav").write_bytes(ref_wav)
    results["extra_field_header"] = extra_header
    results["reference_wav_bytes"] = len(ref_wav)

    response = multipart_request(
        "/v1/audio/design",
        {
            "text": "Design endpoint smoke test.",
            "instruct": "female, young adult, moderate pitch",
            "duration": "0.35",
            "response_format": "wav",
        },
    )
    design_wav = read_body(response)
    assert_true(response.status == 200, f"design returned {response.status}")
    assert_wav("design wav", design_wav)
    (OUT_DIR / "design_smoke.wav").write_bytes(design_wav)
    results["design_bytes"] = len(design_wav)

    response = multipart_request(
        "/v1/audio/clone",
        {
            "text": "Clone endpoint smoke test.",
            "ref_text": "Reference audio for clone smoke test.",
            "duration": "0.35",
            "response_format": "wav",
        },
        {"ref_audio": ("ref.wav", ref_wav, "audio/wav")},
    )
    clone_wav = read_body(response)
    assert_true(response.status == 200, f"clone upload returned {response.status}")
    assert_wav("clone wav", clone_wav)
    (OUT_DIR / "clone_smoke.wav").write_bytes(clone_wav)
    results["clone_bytes"] = len(clone_wav)

    response = multipart_request(
        "/v1/audio/clone",
        {
            "text": "Clone endpoint base64 smoke test.",
            "ref_text": "Reference audio for clone smoke test.",
            "ref_audio_base64": "data:audio/wav;base64,"
            + base64.b64encode(ref_wav).decode("ascii"),
            "duration": "0.35",
            "response_format": "wav",
        },
    )
    clone_b64_wav = read_body(response)
    assert_true(response.status == 200, f"clone base64 returned {response.status}")
    assert_wav("clone base64 wav", clone_b64_wav)
    (OUT_DIR / "clone_base64_smoke.wav").write_bytes(clone_b64_wav)
    results["clone_base64_bytes"] = len(clone_b64_wav)

    before = get_json("/metrics")

    def one_design(index: int) -> tuple[int, int]:
        item = multipart_request(
            "/v1/audio/design",
            {
                "text": f"Design batch regression {index}.",
                "instruct": "male, young adult, moderate pitch",
                "response_format": "wav",
                "duration": "0.35",
            },
        )
        body = read_body(item)
        return item.status, len(body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        design_batch_results = list(executor.map(one_design, range(3)))
    after = get_json("/metrics")
    design_delta_batches = after.get("total_batches", 0) - before.get("total_batches", 0)
    design_delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    assert_true(
        all(status == 200 and size > 1000 for status, size in design_batch_results),
        "design batch request failed",
    )
    assert_true(design_delta_tasks == 3, f"design delta tasks should be 3, got {design_delta_tasks}")
    assert_true(
        design_delta_batches == 1,
        f"design batch should dispatch as one backend batch, got {design_delta_batches}",
    )
    results["design_batch_delta_batches"] = design_delta_batches
    results["design_batch_delta_tasks"] = design_delta_tasks

    before = get_json("/metrics")
    varied_instructs = [
        "male, young adult, moderate pitch",
        "female, young adult, high pitch",
        "male, middle-aged, low pitch",
    ]

    def one_varied_design(item: tuple[int, str]) -> tuple[int, int]:
        index, instruct = item
        request = multipart_request(
            "/v1/audio/design",
            {
                "text": f"Varied design batch {index}.",
                "instruct": instruct,
                "response_format": "wav",
                "duration": "0.35",
            },
        )
        body = read_body(request)
        return request.status, len(body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        varied_design_results = list(executor.map(one_varied_design, enumerate(varied_instructs)))
    after = get_json("/metrics")
    varied_design_delta_batches = after.get("total_batches", 0) - before.get(
        "total_batches", 0
    )
    varied_design_delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    assert_true(
        all(status == 200 and size > 1000 for status, size in varied_design_results),
        "varied design batch request failed",
    )
    assert_true(
        varied_design_delta_tasks == 3,
        f"varied design delta tasks should be 3, got {varied_design_delta_tasks}",
    )
    assert_true(
        varied_design_delta_batches == 1,
        f"varied design should dispatch as one backend batch, got {varied_design_delta_batches}",
    )
    results["varied_design_delta_batches"] = varied_design_delta_batches
    results["varied_design_delta_tasks"] = varied_design_delta_tasks

    response = post_json(
        "/v1/audio/speech",
        {
            "model": "tts-1",
            "input": "Single task streaming split test.",
            "voice": "auto",
            "stream": True,
            "duration": 0.8,
        },
    )
    events, raw_lines = parse_sse(response)
    (OUT_DIR / "stream_single_split.sse").write_text("\n".join(raw_lines), encoding="utf-8")
    accepted = [payload for event, payload in events if event == "speech.accepted"]
    deltas = [
        payload
        for event, payload in events
        if event == "speech.audio.delta" and isinstance(payload, dict)
    ]
    assert_true(accepted and accepted[0]["tasks"] == 1, "single stream should accept one task")
    assert_true(len(deltas) >= 2, f"single stream should split audio into parts, got {len(deltas)}")
    assert_true({d["seq"] for d in deltas} == {0}, "single stream seq mismatch")
    assert_true(events[-1][1] == "[DONE]", "single stream missing DONE")
    results["single_stream_delta_count"] = len(deltas)

    text = (
        ("Chunk order test sentence number one. " * 12)
        + ("Chunk order test sentence number two. " * 12)
        + ("Chunk order test sentence number three. " * 12)
    )
    response = post_json(
        "/v1/audio/speech",
        {
            "model": "tts-1",
            "input": text,
            "voice": "auto",
            "stream": True,
            "duration": 0.35,
        },
    )
    events, raw_lines = parse_sse(response)
    (OUT_DIR / "stream_long.sse").write_text("\n".join(raw_lines), encoding="utf-8")
    accepted = [payload for event, payload in events if event == "speech.accepted"]
    deltas = [
        payload
        for event, payload in events
        if event == "speech.audio.delta" and isinstance(payload, dict)
    ]
    seqs = [payload["seq"] for payload in deltas]
    assert_true(accepted and accepted[0]["tasks"] >= 2, "long stream did not chunk")
    assert_true(seqs == sorted(seqs), f"long stream not ordered: {seqs}")
    assert_true(events[-1][1] == "[DONE]", "long stream missing DONE")
    results["stream_accepted_tasks"] = accepted[0]["tasks"]
    results["stream_delta_count"] = len(deltas)
    results["stream_unique_seqs"] = sorted(set(seqs))

    before = get_json("/metrics")

    def one(index: int) -> tuple[int, int]:
        item = post_json(
            "/v1/audio/speech",
            {
                "model": "tts-1",
                "input": f"Concurrent batch smoke request {index}.",
                "voice": "auto",
                "response_format": "pcm",
                "duration": 0.35,
            },
        )
        body = read_body(item)
        return item.status, len(body)

    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        concurrent_results = list(executor.map(one, range(8)))
    elapsed = time.perf_counter() - start
    after = get_json("/metrics")

    assert_true(all(status == 200 for status, _ in concurrent_results), "concurrent status failed")
    assert_true(all(size > 1000 for _, size in concurrent_results), "concurrent output too small")
    delta_batches = after.get("total_batches", 0) - before.get("total_batches", 0)
    delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    assert_true(delta_tasks == 8, f"concurrent delta tasks should be 8, got {delta_tasks}")
    assert_true(delta_batches <= 2, f"8 concurrent tasks were not batched enough: {delta_batches}")
    results["concurrent_elapsed_s"] = round(elapsed, 3)
    results["metrics_delta_batches"] = delta_batches
    results["metrics_delta_tasks"] = delta_tasks

    before = get_json("/metrics")

    def one_speed(item: tuple[int, float]) -> tuple[int, int]:
        index, speed = item
        request = post_json(
            "/v1/audio/speech",
            {
                "model": "tts-1",
                "input": f"Varied speed batch {index}.",
                "voice": "auto",
                "response_format": "pcm",
                "duration": 0.35,
                "speed": speed,
            },
        )
        body = read_body(request)
        return request.status, len(body)

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        varied_speed_results = list(executor.map(one_speed, enumerate([0.8, 1.0, 1.2])))
    after = get_json("/metrics")
    varied_speed_delta_batches = after.get("total_batches", 0) - before.get("total_batches", 0)
    varied_speed_delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    assert_true(
        all(status == 200 and size > 1000 for status, size in varied_speed_results),
        "varied speed batch request failed",
    )
    assert_true(
        varied_speed_delta_tasks == 3,
        f"varied speed delta tasks should be 3, got {varied_speed_delta_tasks}",
    )
    assert_true(
        varied_speed_delta_batches == 1,
        f"varied speed should dispatch as one backend batch, got {varied_speed_delta_batches}",
    )
    results["varied_speed_delta_batches"] = varied_speed_delta_batches
    results["varied_speed_delta_tasks"] = varied_speed_delta_tasks

    before = get_json("/metrics")

    def one_auto(index: int) -> tuple[int, int]:
        item = post_json(
            "/v1/audio/speech",
            {
                "model": "tts-1",
                "input": f"Mixed auto {index}.",
                "voice": "auto",
                "response_format": "pcm",
                "duration": 0.35,
            },
        )
        body = read_body(item)
        return item.status, len(body)

    def one_clone(index: int) -> tuple[int, int]:
        item = multipart_request(
            "/v1/audio/clone",
            {
                "text": f"Mixed clone {index}.",
                "ref_text": "Reference audio for clone smoke test.",
                "ref_audio_base64": "data:audio/wav;base64,"
                + base64.b64encode(ref_wav).decode("ascii"),
                "response_format": "wav",
                "duration": "0.35",
            },
        )
        body = read_body(item)
        return item.status, len(body)

    mixed_tasks = []
    for index in range(3):
        mixed_tasks.extend([(one_auto, index), (one_design, index), (one_clone, index)])
    mixed_start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=9) as executor:
        mixed_results = list(executor.map(lambda item: item[0](item[1]), mixed_tasks))
    mixed_elapsed = time.perf_counter() - mixed_start
    after = get_json("/metrics")
    mixed_delta_batches = after.get("total_batches", 0) - before.get("total_batches", 0)
    mixed_delta_tasks = after.get("total_tasks", 0) - before.get("total_tasks", 0)
    assert_true(
        all(status == 200 and size > 1000 for status, size in mixed_results),
        "mixed concurrency request failed",
    )
    assert_true(mixed_delta_tasks == 9, f"mixed delta tasks should be 9, got {mixed_delta_tasks}")
    assert_true(
        3 <= mixed_delta_batches <= 6,
        f"mixed concurrency should split into compatible batches, got {mixed_delta_batches}",
    )
    results["mixed_concurrent_elapsed_s"] = round(mixed_elapsed, 3)
    results["mixed_delta_batches"] = mixed_delta_batches
    results["mixed_delta_tasks"] = mixed_delta_tasks

    tmp_refs = list(Path("/tmp").glob("omnivoice_ref_*"))
    assert_true(not tmp_refs, f"clone temp files leaked: {tmp_refs}")

    final_metrics = get_json("/metrics")
    assert_true(final_metrics["total_errors"] == 0, f"backend errors seen: {final_metrics}")
    assert_true(final_metrics["last_batch"] is not None, "last_batch metric missing after tests")
    assert_true(final_metrics["max_batch_size_seen"] >= 3, "max batch metric did not update")
    results["final_metrics"] = {
        key: final_metrics[key]
        for key in [
            "total_batches",
            "total_tasks",
            "total_errors",
            "avg_batch_size",
            "avg_batch_elapsed_s",
            "avg_queue_wait_ms",
            "max_batch_size_seen",
            "last_batch",
        ]
    }

    results["passed"] = True
    (OUT_DIR / "api_test_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
