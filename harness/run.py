"""Paired runs: every attack on main and on the PR, each in its own sandbox process.

    uv run python -m harness.run [--only vishing_call] [--no-generated] [--concurrency 8]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from asyncio import create_subprocess_exec as spawn  # argv list, no shell
from datetime import datetime

from harness import db
from harness.scenarios import load_attacks

VERSIONS = ("main", "pr")
RUN_TIMEOUT_S = int(os.environ.get("DIFFERENTIAL_RUN_TIMEOUT", "180"))


def new_run_id() -> str:
    return datetime.now().strftime("r_%Y%m%d_%H%M%S")


def queue(run_id: str, attacks: list[dict]) -> None:
    rows = [db.row(run_id, a["attack_id"], a["family"], a["variant"], v, "status", "harness",
                   {"state": "queued", "title": a["title"], "featured": a["featured"]})
            for a in attacks for v in VERSIONS]
    db.insert(rows)


async def sandbox(run_id: str, attack: dict, version: str, sem: asyncio.Semaphore) -> int:
    async with sem:
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_BASE_URL"}
        proc = await spawn(sys.executable, "-m", "harness.worker", run_id, version,
                           stdin=asyncio.subprocess.PIPE, env=env)
        try:
            await asyncio.wait_for(proc.communicate(json.dumps(attack).encode()), RUN_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            db.insert([db.row(run_id, attack["attack_id"], attack["family"], attack["variant"], version,
                              "status", "harness", {"state": "error", "error": f"timeout after {RUN_TIMEOUT_S}s"})])
        return proc.returncode or 0


async def run_all(run_id: str, attacks: list[dict], concurrency: int) -> None:
    sem = asyncio.Semaphore(concurrency)
    await asyncio.gather(*(sandbox(run_id, a, v, sem) for a in attacks for v in VERSIONS))


def start(run_id: str | None = None, only: str | None = None, generated: bool = True,
          concurrency: int = 8) -> str:
    run_id = run_id or new_run_id()
    attacks = [a for a in load_attacks(generated) if not only or a["family"] == only]
    queue(run_id, attacks)
    asyncio.run(run_all(run_id, attacks, concurrency))
    return run_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one family")
    ap.add_argument("--no-generated", action="store_true")
    ap.add_argument("--concurrency", type=int, default=8)
    args = ap.parse_args()
    db.init()
    run_id = start(only=args.only, generated=not args.no_generated, concurrency=args.concurrency)

    from harness.verdicts import verdicts
    print(f"\nrun_id={run_id}")
    for v in verdicts(run_id):
        print(f"  {v['attack_id']:32} main_bad={v['main_bad']} pr_bad={v['pr_bad']} -> {v['verdict']}")


if __name__ == "__main__":
    main()
