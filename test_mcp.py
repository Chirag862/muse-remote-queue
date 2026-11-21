#!/usr/bin/env python3
"""Test muse-remote-mcp v2 over real HTTP (MCP client + file download).

Phase 1 (this script): health, 401 without token, initialize, list tools
(must be exactly the 4 requested), generate_image + generate_video return
job_ids immediately, job_status says queued, download_output says not ready.
Phase 2 (after the agent fulfils the jobs with real media via
fulfill_job.py): re-run with --check JOB_ID to verify job_status done +
download returns the actual file bytes.
"""
import json
import os
import subprocess
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("TEST_PORT", "8001"))
TOKEN = "test-token-123"
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    # This VM sets proxy env vars whose NO_PROXY list contains bracketed
    # IPv6 entries that httpx's proxy parsing chokes on (Invalid port ':1]').
    # Localhost traffic needs no proxy — drop them for this test process.
    for k in list(os.environ):
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            del os.environ[k]
    check_job = sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--check" else None
    env = dict(os.environ, MCP_TOKEN=TOKEN, PORT=str(PORT),
               PUBLIC_BASE_URL=BASE, PYTHONUNBUFFERED="1")
    env.pop("MUSE_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "server.py")],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        client = httpx.Client(trust_env=False, timeout=10)
        for _ in range(30):
            try:
                r = client.get(f"{BASE}/")
                if r.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            print("FAIL: server did not start")
            print(proc.stdout.read() if proc.stdout else "")
            return 1
        print("health:", client.get(f"{BASE}/").json())

        # 401 without token
        r = client.post(f"{BASE}/mcp", json={})
        print("no-token status (expect 401):", r.status_code)
        assert r.status_code == 401, "expected 401 without token"

        async def run_mcp():
            async with streamablehttp_client(f"{BASE}/mcp?token={TOKEN}") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = [t.name for t in tools.tools]
                    print("tools:", names)
                    assert names == ["generate_image", "generate_video", "job_status", "download_output"], names
                    if check_job:
                        st = await session.call_tool("job_status", {"job_id": check_job})
                        print("job_status:", st.content[0].text)
                        assert "status: done" in st.content[0].text
                        dl = await session.call_tool("download_output", {"job_id": check_job})
                        print("download_output:", dl.content[0].text)
                        assert "download_url:" in dl.content[0].text
                        url = [l.split("download_url: ", 1)[1] for l in dl.content[0].text.splitlines()
                               if l.startswith("download_url: ")][0]
                        fr = client.get(url)
                        print("file download:", fr.status_code, fr.headers.get("content-type"),
                              len(fr.content), "bytes",
                              fr.headers.get("content-disposition"))
                        assert fr.status_code == 200 and len(fr.content) > 1000
                        return None
                    t0 = time.time()
                    img = await session.call_tool("generate_image", {
                        "prompt": "A golden temple at dawn, cinematic, vertical",
                        "aspect_ratio": "9:16"})
                    print("generate_image:", img.content[0].text)
                    assert "job_id:" in img.content[0].text
                    img_id = [l.split("job_id: ", 1)[1] for l in img.content[0].text.splitlines()
                              if l.startswith("job_id: ")][0].strip()
                    vid = await session.call_tool("generate_video", {
                        "prompt": "A golden temple at dawn, slow cinematic camera move, vertical",
                        "duration": 10, "aspect_ratio": "9:16"})
                    print(f"generate_video ({time.time() - t0:.1f}s total):", vid.content[0].text)
                    assert "job_id:" in vid.content[0].text
                    vid_id = [l.split("job_id: ", 1)[1] for l in vid.content[0].text.splitlines()
                              if l.startswith("job_id: ")][0].strip()
                    st = await session.call_tool("job_status", {"job_id": img_id})
                    print("job_status image:", st.content[0].text)
                    assert "status: queued" in st.content[0].text
                    dl = await session.call_tool("download_output", {"job_id": img_id})
                    print("download_output (not ready):", dl.content[0].text)
                    assert "Not ready yet" in dl.content[0].text
                    return img_id, vid_id

        import asyncio
        result = asyncio.run(run_mcp())
        if result:
            print(json.dumps({"image_job_id": result[0], "video_job_id": result[1]}))
        print("PASS: remote MCP v2 works — 4 tools, token auth, async jobs, download flow")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
