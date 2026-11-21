#!/usr/bin/env python3
"""Push a finished job's output to the public GitHub repo (fast CDN).

Usage: python upload_to_github.py <job_id>
  - copies outputs/<job_id>.<ext> into the local clone's outputs/
  - git add / commit / push (auth via `gh auth setup-git` — run
    `gh auth login --web` once and `gh auth setup-git` after it)
  - records github_url in jobs/<job_id>.json so download_output and
    job_status return the fast raw.githubusercontent.com link instead
    of the slow tunnel /files/ link.

Requires: local clone at GITHUB_REPO_DIR (default: sibling dir
muse-remote-queue next to this script's parent, i.e.
.../files/muse-remote-queue), already cloned from
https://github.com/Chirag862/muse-remote-queue (public repo).
The repo must be PUBLIC for the raw link to download without auth.

GitHub limits to respect: 100 MB per file (our videos are ~10 MB),
repo soft limit ~1 GB total — old outputs can be deleted later.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "Chirag862/muse-remote-queue"
DEFAULT_REPO_DIR = HERE.parent / "muse-remote-queue"


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: upload_to_github.py <job_id>")
        return 2
    job_id = sys.argv[1]
    job_path = HERE / "jobs" / f"{job_id}.json"
    if not job_path.exists():
        print(f"no such job: {job_id}")
        return 1
    job = json.loads(job_path.read_text())
    if job.get("status") != "done" or not job.get("output_file"):
        print(f"job {job_id} is {job.get('status')}, not done yet")
        return 1
    if job.get("github_url"):
        print(f"job {job_id} already has github_url: {job['github_url']}")
        return 0
    src = HERE / "outputs" / job["output_file"]
    if not src.exists():
        print(f"output file missing: {src}")
        return 1

    repo_dir = Path(__import__("os").environ.get("GITHUB_REPO_DIR", "") or DEFAULT_REPO_DIR)
    if not (repo_dir / ".git").exists():
        print(f"no git clone at {repo_dir} — clone it first:")
        print(f"  git clone https://github.com/{REPO}.git {repo_dir}")
        return 1

    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir).stdout.strip()
    out_dir = repo_dir / "outputs"
    out_dir.mkdir(exist_ok=True)
    shutil.copyfile(src, out_dir / job["output_file"])
    _run(["git", "add", f"outputs/{job['output_file']}"], cwd=repo_dir)
    _run(["git", "-c", "user.name=Chirag862",
          "-c", "user.email=chiragmadhavi862@gmail.com",
          "commit", "-m", f"output for {job_id}"], cwd=repo_dir)
    _run(["git", "push", "origin", branch], cwd=repo_dir)

    url = (f"https://raw.githubusercontent.com/{REPO}/{branch}"
           f"/outputs/{job['output_file']}")
    job["github_url"] = url
    job_path.write_text(json.dumps(job, indent=2))
    print(f"job {job_id} -> {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
