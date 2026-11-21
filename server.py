#!/usr/bin/env python3
"""
muse-remote-mcp — remote MCP server exposing THIS Muse's image/video
generation to another Muse.

HONEST DESIGN — read this before using it:
  There is NO official Muse image/video API (checked Nov 2026), and
  this Muse's internal image/video tools are agent tools, not an HTTP
  API — no Python server can call them directly. So this server does
  NOT pretend to generate media itself. It is the transport + queue:
    1. Another Muse calls generate_image / generate_video here.
       The call returns a job_id IMMEDIATELY (video is async by design;
       image is async too, because fulfilment is via the agent queue).
    2. The job is stored in jobs/<job_id>.json (status: queued).
    3. THIS Muse (the agent that owns this workspace) fulfils queued
       jobs with its own image/video tools, saves the file to
       outputs/<job_id>.<png|mp4>, and marks the job done.
       A fulfiller cron polls jobs/ every 5 minutes for this.
    4. The caller polls job_status(job_id), then download_output(job_id)
       returns a direct downloadable file link.

  If you run this server on a machine where muse2api/Meta API exists,
  you could add that as a backend — but that would be a bridge, NOT
  this Muse's own generation, and must be labelled as such.

Transport: Streamable HTTP MCP, POST /mcp
Auth: MCP_TOKEN — via ?token=, Authorization: Bearer, or x-mcp-token.
  / and /health are public. /mcp, /files/*, /queue and
  /jobs/<job_id>/upload require the token.
  Never put the token in a repo. Set it as an env var.
Download: GET /files/<job_id>?token=... — Content-Disposition: attachment.
  FAST DOWNLOADS (v2.1.0, GitHub CDN): the localhost.run tunnel is too
  slow for video files (~4 KB/s), so finished outputs are also pushed
  to the public GitHub repo Chirag862/muse-remote-queue under
  outputs/<job_id>.<ext>. When a job has "github_url" set (by
  upload_to_github.py, run by the fulfiller after fulfilment),
  download_output / job_status return that raw.githubusercontent.com
  link instead of the tunnel /files/ link — same file, fast CDN.
  The tunnel then only carries small MCP JSON, which it handles fine.
  /files/ stays as the fallback when no github_url is recorded yet.

Render / remote-fulfiller endpoints (added for the Render-free deploy):
  GET  /queue — list of jobs with status "queued" (oldest first), for
       the fulfiller (this Muse agent) to poll.
  POST /jobs/<job_id>/upload — multipart file field "file"; saves the
       file to outputs/<job_id>.<ext> and marks the job done, so the
       other Muse can then download it from /files/<job_id>.
  This is what makes a Render deploy work: the server lives on Render
  (stable URL, fast downloads), the queue and finished files live
  there, and the agent fulfils jobs remotely via these two endpoints.
  Render's disk is ephemeral — files are lost on restart/redeploy;
  the fulfiller keeps local copies and can re-upload.

Tools (exactly 4):
  generate_image(prompt, aspect_ratio="9:16") -> job_id
  generate_video(prompt, duration=10, aspect_ratio="9:16") -> job_id (async)
  job_status(job_id) -> status / output / download_url
  download_output(job_id) -> direct downloadable file link

Env:
  MCP_TOKEN (required in production), PORT (default 8000),
  PUBLIC_BASE_URL (e.g. https://xxx.trycloudflare.com — set this when
  you put a tunnel in front, so download links are public),
  JOBS_DIR / OUTPUTS_DIR (defaults to ./jobs, ./outputs next to this file)
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import httpx  # noqa: F401  (kept for future HTTP backends; not used for generation)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # mcp 2.x
    from mcp.server import MCPServer as FastMCP  # type: ignore

BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = Path(os.environ.get("JOBS_DIR", "") or BASE_DIR / "jobs")
OUTPUTS_DIR = Path(os.environ.get("OUTPUTS_DIR", "") or BASE_DIR / "outputs")
JOBS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

MCP_TOKEN = os.environ.get("MCP_TOKEN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
SERVER_NAME = "muse-remote"
SERVER_VERSION = "2.1.0"

mcp = FastMCP(SERVER_NAME)


def _now() -> str:
    return datetime.now().isoformat()


def _job_path(job_id: str) -> Path:
    # job ids are server-generated uuids; reject path traversal
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        raise ValueError(f"bad job_id: {job_id}")
    return JOBS_DIR / f"{job_id}.json"


def _new_job(kind: str, prompt: str, aspect_ratio: str, duration: int | None) -> dict:
    job_id = f"{kind}-{uuid.uuid4().hex[:12]}"
    job = {
        "job_id": job_id,
        "type": kind,  # "image" | "video"
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "duration": duration,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "output_file": None,
        "error": None,
    }
    _job_path(job_id).write_text(json.dumps(job, indent=2))
    return job


def _load_job(job_id: str) -> dict:
    p = _job_path(job_id)
    if not p.exists():
        raise FileNotFoundError(f"no such job: {job_id}")
    return json.loads(p.read_text())


def _save_job(job: dict) -> None:
    job["updated_at"] = _now()
    _job_path(job["job_id"]).write_text(json.dumps(job, indent=2))


def _download_url(job_id: str, job: dict | None = None) -> str:
    # Fast path: finished file already on the public GitHub repo
    # (raw.githubusercontent.com CDN) — prefer it over the slow tunnel.
    if job is None:
        try:
            job = _load_job(job_id)
        except Exception:
            job = {}
    gh = (job or {}).get("github_url")
    if gh:
        return gh
    base = PUBLIC_BASE_URL
    if not base:
        port = os.environ.get("PORT", "8000")
        base = f"http://127.0.0.1:{port}"
    return f"{base}/files/{job_id}?token={MCP_TOKEN}" if MCP_TOKEN else f"{base}/files/{job_id}"


def _status_text(job: dict) -> str:
    lines = [
        f"job_id: {job['job_id']}",
        f"type: {job['type']}",
        f"status: {job['status']}",
        f"prompt: {job['prompt']}",
        f"aspect_ratio: {job['aspect_ratio']}",
    ]
    if job.get("duration") is not None:
        lines.append(f"duration: {job['duration']}s")
    lines.append(f"created_at: {job['created_at']}")
    if job["status"] == "done" and job.get("output_file"):
        lines.append(f"output_file: {job['output_file']}")
        lines.append(f"download_url: {_download_url(job['job_id'], job)}")
    if job["status"] == "failed" and job.get("error"):
        lines.append(f"error: {job['error']}")
    if job["status"] in ("queued", "running"):
        lines.append("Call job_status(job_id) again to check — video jobs take minutes.")
    return "\n".join(lines)


@mcp.tool()
def generate_image(prompt: str, aspect_ratio: str = "9:16") -> str:
    """Generate an image with this Muse. Async — returns a job_id immediately.

    The job is queued for this Muse to fulfil with its own image tools
    (there is no direct Muse image API). Poll job_status(job_id), then
    download_output(job_id) for the file link.
    aspect_ratio: 9:16 (vertical), 16:9, 1:1, 3:2, 2:3, 4:3, 3:4.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")
    job = _new_job("image", prompt.strip(), aspect_ratio or "9:16", None)
    return (
        f"Image job queued.\n"
        f"job_id: {job['job_id']}\n"
        f"status: queued\n"
        "This Muse fulfils queued jobs with its own image tools — "
        "poll job_status(job_id), then download_output(job_id)."
    )


@mcp.tool()
def generate_video(prompt: str, duration: int = 10, aspect_ratio: str = "9:16") -> str:
    """Generate a video with this Muse. ASYNC — returns a job_id immediately.

    The job is queued for this Muse to fulfil with its own video tools
    (there is no direct Muse video API). Do NOT wait on this call.
    Poll job_status(job_id), then download_output(job_id) for the file.
    duration: requested length in seconds (fulfiller makes a ~10s clip
    per generation; longer videos are made as one clip per job).
    aspect_ratio: 9:16 (vertical reels), 16:9, 1:1.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")
    if duration is None or int(duration) <= 0:
        duration = 10
    job = _new_job("video", prompt.strip(), aspect_ratio or "9:16", int(duration))
    return (
        f"Video job queued (async).\n"
        f"job_id: {job['job_id']}\n"
        f"status: queued\n"
        f"requested duration: {int(duration)}s, aspect_ratio: {aspect_ratio}\n"
        "This Muse fulfils queued jobs with its own video tools — "
        "poll job_status(job_id), then download_output(job_id)."
    )


@mcp.tool()
def job_status(job_id: str) -> str:
    """Check a job's status. Returns queued / running / done / failed,
    plus the download URL when done."""
    job = _load_job(job_id)
    return _status_text(job)


@mcp.tool()
def download_output(job_id: str) -> str:
    """Return a direct downloadable file link for a finished job.

    Returns the link only when status is done. If the job is still
    queued/running, it says so — poll job_status(job_id) instead.
    When the file was pushed to the public GitHub repo, the link is
    the fast raw.githubusercontent.com URL; otherwise it is
    GET /files/<job_id>?token=... (tunnel, slow for big files).
    """
    job = _load_job(job_id)
    if job["status"] != "done" or not job.get("output_file"):
        return (
            f"Not ready yet.\n"
            f"job_id: {job_id}\n"
            f"status: {job['status']}\n"
            "Poll job_status(job_id) until status is done, then call "
            "download_output(job_id) again."
        )
    out = OUTPUTS_DIR / job["output_file"]
    if not out.exists():
        return f"Job is marked done but the file is missing: {job['output_file']}"
    return (
        f"Download ready.\n"
        f"job_id: {job_id}\n"
        f"file: {job['output_file']}\n"
        f"download_url: {_download_url(job_id, job)}"
    )


# ---- HTTP app: FastMCP streamable HTTP + /files download + health ----

from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import FileResponse, JSONResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402

try:
    mcp.settings.host = "0.0.0.0"
    mcp.settings.port = int(os.environ.get("PORT", "8000"))
    ts = mcp.settings.transport_security
    ts.allowed_hosts = ["*"]
    ts.allowed_origins = ["*"]
    ts.enable_dns_rebinding_protection = False
except Exception:
    pass


class TokenAuthMiddleware:
    """Pure ASGI auth — no body buffering, so MCP streaming still works."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in ("/", "/health"):
            await self.app(scope, receive, send)
            return
        if not MCP_TOKEN:
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        query = parse_qs(scope.get("query_string", b"").decode())
        ok = (
            headers.get("authorization") == f"Bearer {MCP_TOKEN}"
            or headers.get("x-mcp-token") == MCP_TOKEN
            or query.get("token", [None])[0] == MCP_TOKEN
        )
        if not ok:
            body = b"Unauthorized - bad or missing MCP token"
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


async def health(request: Request):
    jobs = sorted(p.name for p in JOBS_DIR.glob("*.json"))
    return JSONResponse({
        "ok": True,
        "server": SERVER_NAME,
        "version": SERVER_VERSION,
        "tools": ["generate_image", "generate_video", "job_status", "download_output"],
        "jobs": len(jobs),
        "endpoint": "POST /mcp  (add ?token=YOUR_MCP_TOKEN)",
        "note": "Jobs are fulfilled by this Muse agent from the queue; "
                "there is no direct Muse image/video API.",
    })


async def download_file(request: Request):
    job_id = request.path_params["job_id"]
    try:
        job = _load_job(job_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    if job["status"] != "done" or not job.get("output_file"):
        return JSONResponse({"error": f"job {job_id} is {job['status']}, not done yet"},
                            status_code=409)
    out = OUTPUTS_DIR / job["output_file"]
    if not out.exists():
        return JSONResponse({"error": "output file missing"}, status_code=404)
    media = "video/mp4" if out.suffix == ".mp4" else "image/png"
    if out.suffix in (".jpg", ".jpeg"):
        media = "image/jpeg"
    return FileResponse(str(out), media_type=media,
                        filename=job["output_file"],
                        headers={"Content-Disposition": f'attachment; filename="{job["output_file"]}"'})


# ---- Render fulfiller endpoints: GET /queue, POST /jobs/<id>/upload ----
# Same token auth as /mcp (TokenAuthMiddleware above: only / and /health
# are public). Lets the agent fulfil jobs remotely when the server runs
# on Render: poll /queue, generate locally, upload the finished file.

def _safe_ext(filename: str) -> str:
    """Extension from an uploaded filename, restricted to known media types."""
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return suffix
    return ".bin"


async def queue_list(request: Request):
    queued = []
    for p in JOBS_DIR.glob("*.json"):
        try:
            job = json.loads(p.read_text())
        except Exception:
            continue
        if job.get("status") != "queued":
            continue
        queued.append({
            "job_id": job["job_id"],
            "type": job["type"],
            "prompt": job["prompt"],
            "duration": job.get("duration"),
            "aspect_ratio": job.get("aspect_ratio"),
            "created_at": job.get("created_at"),
        })
    queued.sort(key=lambda j: j.get("created_at") or "")
    return JSONResponse({"ok": True, "jobs": queued})


async def upload_job_output(request: Request):
    job_id = request.path_params["job_id"]
    try:
        job = _load_job(job_id)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    if job["status"] == "done":
        return JSONResponse({"ok": False, "error": f"job {job_id} is already done"},
                            status_code=409)
    try:
        form = await request.form()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"bad multipart form: {e}"},
                            status_code=400)
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        return JSONResponse({"ok": False, "error": 'missing multipart field "file"'},
                            status_code=400)
    ext = _safe_ext(getattr(upload, "filename", "") or "")
    dest_name = f"{job_id}{ext}"
    dest = OUTPUTS_DIR / dest_name
    try:
        with dest.open("wb") as f:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"failed to save upload: {e}"},
                            status_code=500)
    # Same done-fields fulfill_job.py writes: status + output_file.
    job["status"] = "done"
    job["output_file"] = dest_name
    _save_job(job)
    return JSONResponse({"ok": True, "job_id": job_id,
                         "download_url": _download_url(job_id, job)})


mcp_app = mcp.streamable_http_app()
mcp_app.routes.insert(0, Route("/jobs/{job_id}/upload", upload_job_output, methods=["POST"]))
mcp_app.routes.insert(0, Route("/queue", queue_list, methods=["GET"]))
mcp_app.routes.insert(0, Route("/files/{job_id}", download_file, methods=["GET"]))
mcp_app.routes.insert(0, Route("/health", health))
mcp_app.routes.insert(0, Route("/", health))

app = TokenAuthMiddleware(mcp_app)


def main() -> None:
    import uvicorn

    if not MCP_TOKEN:
        print("WARNING: MCP_TOKEN is not set — server is open to anyone who finds the URL.")
    port = int(os.environ.get("PORT", "8000"))
    print(f"{SERVER_NAME} v{SERVER_VERSION} listening on 0.0.0.0:{port}  endpoint: POST /mcp?token=...")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
