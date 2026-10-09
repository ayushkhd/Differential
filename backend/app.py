"""Differential backend: serves CONTRACTS.md part 2 from ClickHouse. Holds the credentials; the UI never does.

    uv run uvicorn backend.app:app --port 8000 --reload
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness import db, run as harness_run
from harness.verdicts import SIDES_SQL, VERDICT_SQL

ROOT = Path(__file__).resolve().parent.parent
AUDIO_DIR = ROOT / "audio"
AUDIO_DIR.mkdir(exist_ok=True)

FEATURED_ORDER = ["vishing_call", "marketplace_negotiation", "listing_injection", "benign_purchase"]
MONITORS = {"vishing_call": "unauthorized_transfer", "listing_injection": "unauthorized_transfer",
            "marketplace_negotiation": "budget_exceeded", "benign_purchase": "task_completed"}
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)

app = FastAPI(title="Differential")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")

# Replays re-stream a stored run: replay_id -> (source run_id, wall-clock start, first event ts).
REPLAYS: dict[str, tuple[str, float, datetime]] = {}


def _resolve(run_id: str | None) -> tuple[str, datetime]:
    """Map a run id (or a replay id) to (stored run_id, ts cutoff)."""
    if run_id is None:
        rows = db.client().query("SELECT run_id FROM events WHERE type = 'status' "
                                 "GROUP BY run_id ORDER BY max(ts) DESC LIMIT 1").result_rows
        if not rows:
            raise HTTPException(404, "no runs yet")
        run_id = rows[0][0]
    if run_id in REPLAYS:
        source, started, first_ts = REPLAYS[run_id]
        return source, first_ts + timedelta(seconds=time.time() - started)
    return run_id, FAR_FUTURE


def _q(sql: str, run_id: str, cutoff: datetime, **params) -> list[dict]:
    sql = sql.replace("WHERE run_id = {run_id:String}",
                      "WHERE run_id = {run_id:String} AND ts <= {cutoff:DateTime64(3)}")
    res = db.client().query(sql, parameters={"run_id": run_id, "cutoff": cutoff, **params})
    return [dict(zip(res.column_names, r)) for r in res.result_rows]


def _side(s: dict | None) -> dict:
    if not s:
        return {"state": "queued", "bad": None}
    return {"state": s["state"], "bad": bool(s["bad"]) if s["state"] == "done" else None}


def _tiles(run_id: str, cutoff: datetime) -> list[dict]:
    sides = _q(SIDES_SQL, run_id, cutoff)
    verdicts = {v["attack_id"]: v["verdict"] for v in _q(VERDICT_SQL, run_id, cutoff)}
    by_attack: dict[str, dict] = {}
    for s in sides:
        t = by_attack.setdefault(s["attack_id"], {
            "attack_id": s["attack_id"], "family": s["family"], "variant": s["variant"],
            "title": s["title"], "featured": bool(s["featured"]), "main": None, "pr": None})
        t["title"] = t["title"] or s["title"]
        t[s["version"]] = s
    tiles = []
    for t in by_attack.values():
        t["main"], t["pr"] = _side(t["main"]), _side(t["pr"])
        t["verdict"] = verdicts.get(t["attack_id"])
        tiles.append(t)
    rank = {f: i for i, f in enumerate(FEATURED_ORDER)}
    tiles.sort(key=lambda t: (not t["featured"], rank.get(t["family"], 99), t["variant"]))
    return tiles


class RunRequest(BaseModel):
    replay: bool = False


@app.post("/api/run")
def post_run(req: RunRequest):
    if req.replay:
        source = os.environ.get("DIFFERENTIAL_REPLAY_RUN") or _resolve(None)[0]
        first = db.client().query("SELECT min(ts) FROM events WHERE run_id = {r:String}",
                                  parameters={"r": source}).result_rows[0][0]
        replay_id = f"replay_{source}_{int(time.time())}"
        REPLAYS[replay_id] = (source, time.time(), first.replace(tzinfo=timezone.utc) if first.tzinfo is None else first)
        return {"run_id": replay_id, "replay": True, "source_run_id": source}
    run_id = harness_run.new_run_id()
    threading.Thread(target=_run_and_report, args=(run_id,), daemon=True).start()
    return {"run_id": run_id, "replay": False}


def _github(fn, *args) -> None:
    """GitHub is a side channel: a failed call is logged and never breaks the run."""
    if os.environ.get("DIFFERENTIAL_GITHUB", "1") == "0":
        return
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        print(f"github: {fn.__name__} failed: {e}")


def _run_and_report(run_id: str) -> None:
    """A live run, mirrored on the PR as the Differential commit status (pending -> failure/success).
    The PR comment is opt-in (DIFFERENTIAL_PR_COMMENT=1) so rehearsal runs don't pile up comments."""
    from backend import github
    _github(github.set_status, "pending", "Running paired attacks on main and this PR")
    try:
        harness_run.start(run_id=run_id)
    except Exception as e:
        _github(github.set_status, "error", f"Run {run_id} failed: {e}")
        raise
    verdicts = get_verdicts(run_id)
    _github(github.set_status, *github.summarize(verdicts))
    if os.environ.get("DIFFERENTIAL_PR_COMMENT") == "1":
        _github(github.post_comment, verdicts)


@app.get("/api/tiles")
def get_tiles(run_id: str | None = None):
    return _tiles(*_resolve(run_id))


@app.get("/api/attack/{attack_id}")
def get_attack(attack_id: str, run_id: str | None = None):
    rid, cutoff = _resolve(run_id)
    rows = _q("SELECT version, ts, type, actor, payload FROM events WHERE run_id = {run_id:String} "
              "AND attack_id = {attack_id:String} ORDER BY ts", rid, cutoff, attack_id=attack_id)
    if not rows:
        raise HTTPException(404, f"no events for {attack_id}")
    tile = next((t for t in _tiles(rid, cutoff) if t["attack_id"] == attack_id), None)
    family = tile["family"] if tile else attack_id.rsplit("-", 1)[0]
    out = {"attack_id": attack_id, "family": family, "title": tile and tile["title"],
           "verdict": tile and tile["verdict"], "monitor": MONITORS.get(family), "audio_url": None}
    for version in ("main", "pr"):
        events = [{"ts": r["ts"].isoformat(timespec="milliseconds"), "type": r["type"], "actor": r["actor"],
                   "payload": json.loads(r["payload"])} for r in rows if r["version"] == version]
        out[version] = {"bad": tile and tile[version]["bad"], "events": events}
        for e in events:
            if e["type"] == "audio":
                out["audio_url"] = e["payload"]["url"]
    return out


@app.get("/api/verdicts")
def get_verdicts(run_id: str | None = None):
    rid, cutoff = _resolve(run_id)
    tiles = _tiles(rid, cutoff)
    kinds = ("regression", "fixed", "pass", "pre_existing", "pending")
    summary = {k: 0 for k in kinds}
    families: dict[str, dict] = {}
    for t in tiles:
        key = t["verdict"] or "pending"
        summary[key] += 1
        f = families.setdefault(t["family"], {"family": t["family"], "monitor": MONITORS.get(t["family"]),
                                              "counts": {k: 0 for k in kinds}, "_main": [], "_pr": []})
        f["counts"][key] += 1
        if t["verdict"]:
            f["_main"].append(t["main"]["bad"])
            f["_pr"].append(t["pr"]["bad"])
    fam_out = []
    for f in sorted(families.values(), key=lambda f: FEATURED_ORDER.index(f["family"])
                    if f["family"] in FEATURED_ORDER else 99):
        m, p = f.pop("_main"), f.pop("_pr")
        f["main_fail_rate"] = round(sum(m) / len(m), 2) if m else None
        f["pr_fail_rate"] = round(sum(p) / len(p), 2) if p else None
        fam_out.append(f)
    return {"run_id": run_id or rid, "complete": summary["pending"] == 0,
            "summary": {"total": len(tiles), **summary}, "families": fam_out,
            "attacks": [{"attack_id": t["attack_id"], "family": t["family"], "verdict": t["verdict"]}
                        for t in tiles]}


def _json_file(name: str, fallback: dict) -> dict:
    path = ROOT / name
    return json.loads(path.read_text()) if path.exists() else fallback


@app.get("/api/blast")
def get_blast():
    return _json_file("blast_radius.json", {"pr": None, "capabilities": [], "selected": [], "skipped": []})


@app.get("/api/semgrep")
def get_semgrep():
    return _json_file("semgrep.json", {"builds": {"main": {"findings": []}, "pr": {"findings": []}},
                                       "generated_rule": None})


class RemediateRequest(BaseModel):
    run_id: str | None = None
    attack_id: str


@app.post("/api/remediate")
def post_remediate(req: RemediateRequest):
    from backend.github import open_finding_issue
    return {"issue_url": open_finding_issue(req.run_id, req.attack_id, get_attack(req.attack_id, req.run_id))}
