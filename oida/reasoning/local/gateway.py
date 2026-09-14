"""Authenticated loopback OpenAI-compatible endpoint for admitted local planners."""

from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from fastapi import FastAPI, HTTPException, Request
from akousma.resource_admission import heavy_lease


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def create_app(config_path=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    path = Path(config_path or os.environ["OIDA_LOCAL_REASONING_CONFIG"])
    config = json.loads(path.read_text())
    token = Path(config["token_file"]).read_text().strip()
    if len(token) < 32:
        raise ValueError("Invalid local gateway credential")
    slot = threading.BoundedSemaphore(1)

    def validate(entry):
        if (
            entry.get("status") != "admitted"
            or not entry.get("revision")
            or not entry.get("files")
        ):
            raise ValueError("Planning deployment not admitted")
        if any(sha(p) != digest for p, digest in entry["files"].items()):
            raise ValueError("Planning artifacts changed since evaluation")
        if str(Path(__file__).with_name("worker.py")) not in entry["files"]:
            raise ValueError("Worker missing from admission")

    for entry in config["models"]:
        validate(entry)

    async def auth(request):
        if request.headers.get("origin") or not secrets.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            raise HTTPException(401, "Local owner authentication required")

    @app.get("/v1/models")
    async def models(request: Request):
        await auth(request)
        return dict(
            object="list",
            data=[
                dict(
                    id=e["id"],
                    name=e.get("name", e["id"]),
                    object="model",
                    owned_by="local",
                    metadata=dict(
                        revision=e["revision"],
                        recommended=e["id"] == config["recommended_model"],
                    ),
                )
                for e in config["models"]
            ],
        )

    def execute(entry, body, stop):
        def check():
            if stop.is_set():
                raise InterruptedError("Planning cancelled")

        if not slot.acquire(blocking=False):
            raise ValueError("Local planning worker busy")
        try:
            validate(entry)
            with (
                heavy_lease("oida", "situated-planning", timeout=10, checkpoint=check),
                tempfile.TemporaryDirectory(prefix="centaur-plan-") as directory,
            ):
                check()
                root = Path(directory)
                req = root / "input.json"
                out = root / "output.json"
                req.write_text(
                    json.dumps(
                        dict(
                            path=entry["path"],
                            messages=body["messages"],
                            schema=body["response_format"]["json_schema"]["schema"],
                            max_tokens=body.get("max_tokens", 1024),
                        )
                    )
                )
                with (root / "error.log").open("w") as log:
                    process = subprocess.Popen(
                        [
                            config["python"],
                            str(Path(__file__).with_name("worker.py")),
                            str(req),
                            str(out),
                        ],
                        env={
                            **{
                                k: os.environ[k]
                                for k in ("PATH", "HOME", "TMPDIR")
                                if k in os.environ
                            },
                            "HF_HUB_OFFLINE": "1",
                            "PYTHONDONTWRITEBYTECODE": "1",
                        },
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                    try:
                        deadline = time.monotonic() + 75
                        while process.poll() is None:
                            check()
                            if time.monotonic() > deadline:
                                raise TimeoutError("Local planning deadline exceeded")
                            time.sleep(0.05)
                        if process.returncode:
                            raise ValueError(
                                "Local model failed to produce a valid bounded plan"
                            )
                        if out.stat().st_size > 128 * 1024:
                            raise ValueError("Planning output too large")
                        value = json.loads(out.read_text())
                        check()
                        return value
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
        finally:
            slot.release()

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        await auth(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 192 * 1024:
                raise HTTPException(413, "Planning request too large")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Invalid planning body")
            entry = next(e for e in config["models"] if e["id"] == body.get("model"))
            messages = body["messages"]
            schema = body["response_format"]["json_schema"]["schema"]
            if (
                not isinstance(schema, dict)
                or len(messages) != 2
                or [m["role"] for m in messages] != ["system", "user"]
                or any(not isinstance(m["content"], str) for m in messages)
            ):
                raise ValueError("Invalid planning messages")
            if body.get("stream") or not 1 <= body.get("max_tokens", 1024) <= 4096:
                raise ValueError("Invalid planning bound")
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
            raise HTTPException(422, "Invalid bounded planning request")
        stop = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(execute, entry, body, stop))
        try:
            while not task.done():
                if await request.is_disconnected():
                    stop.set()
                await asyncio.sleep(0.1)
            value = await task
            return dict(
                id="local-" + secrets.token_hex(8),
                object="chat.completion",
                model=entry["id"],
                centaur_deployment=dict(
                    revision=entry["revision"],
                    worker_sha256=entry["files"][
                        str(Path(__file__).with_name("worker.py"))
                    ],
                    evaluation_sha256=entry["evaluation_sha256"],
                ),
                choices=[
                    dict(
                        index=0,
                        message=dict(role="assistant", content=value["content"]),
                        finish_reason="stop",
                    )
                ],
                usage=value["usage"],
            )
        except (ValueError, TimeoutError, InterruptedError) as exc:
            raise HTTPException(503, str(exc))
        finally:
            stop.set()

    return app
