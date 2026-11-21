#!/usr/bin/env python3
"""Upload a finished job file to the Render-hosted muse-remote server.

Usage:
  python upload_to_render.py <render_base_url> <token> <job_id> <file_path>

Example:
  python upload_to_render.py https://muse-remote.onrender.com <token> \\
      video-abc123def456 /path/to/video.mp4

POSTs the file as multipart field "file" to
  POST {base}/jobs/<job_id>/upload?token=<token>
and prints the returned download_url on success.

The server saves it as outputs/<job_id>.<ext> (extension from the
uploaded filename) and marks the job done, so the other Muse can then
download it via download_output(job_id) / GET /files/<job_id>.
No secrets are stored in this file — the token is a CLI argument.
"""
import sys

import httpx


def main() -> int:
    if len(sys.argv) != 5:
        print("usage: upload_to_render.py <render_base_url> <token> <job_id> <file_path>")
        return 2
    base = sys.argv[1].rstrip("/")
    token, job_id, path = sys.argv[2], sys.argv[3], sys.argv[4]
    url = f"{base}/jobs/{job_id}/upload?token={token}"
    try:
        with open(path, "rb") as f:
            files = {"file": (path.split("/")[-1], f, "application/octet-stream")}
            with httpx.Client(trust_env=False, timeout=300) as client:
                r = client.post(url, files=files)
    except FileNotFoundError:
        print(f"file not found: {path}")
        return 1
    except Exception as e:
        print(f"upload failed: {e}")
        return 1
    try:
        data = r.json()
    except Exception:
        print(f"HTTP {r.status_code}: {r.text[:300]}")
        return 1
    if r.status_code != 200 or not data.get("ok"):
        print(f"HTTP {r.status_code}: {data}")
        return 1
    print(data["download_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
