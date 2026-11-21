#!/usr/bin/env python3
"""Mark a queued job done after the Muse agent generated its media.

Usage: python fulfill_job.py <job_id> <output_file_path>
  - copies the file into outputs/ as <job_id>.<ext>
  - sets the job status to done with output_file set
Used by the agent (and by the fulfiller cron) — the server itself
cannot generate media, the agent does.
"""
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: fulfill_job.py <job_id> <output_file_path>")
        return 2
    job_id, src = sys.argv[1], Path(sys.argv[2])
    job_path = HERE / "jobs" / f"{job_id}.json"
    if not job_path.exists():
        print(f"no such job: {job_id}")
        return 1
    if not src.exists():
        print(f"output file not found: {src}")
        return 1
    dest_name = f"{job_id}{src.suffix.lower()}"
    shutil.copyfile(src, HERE / "outputs" / dest_name)
    job = json.loads(job_path.read_text())
    job["status"] = "done"
    job["output_file"] = dest_name
    job_path.write_text(json.dumps(job, indent=2))
    print(f"job {job_id} done -> outputs/{dest_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
