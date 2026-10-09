"""One sandbox: runs one attack on one build in its own process and streams events to ClickHouse.

    python -m harness.worker <run_id> <version>   # attack JSON on stdin

Every row goes in as it happens (contract 2), so the UI can watch the run live.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from harness import db

ROOT = Path(__file__).resolve().parent.parent

ALLOW_SIM = os.environ.get("DIFFERENTIAL_ALLOW_SIM") == "1"


class Sandbox:
    def __init__(self, run_id: str, attack: dict, version: str):
        self.run_id, self.attack, self.version = run_id, attack, version
        self.ch = db.client()

    def emit(self, type_: str, actor: str, payload: dict) -> None:
        a = self.attack
        self.ch.insert("events", [db.row(self.run_id, a["attack_id"], a["family"], a["variant"], self.version,
                                         type_, actor, payload)], column_names=db.COLUMNS)

    def status(self, state: str, **extra) -> None:
        self.emit("status", "harness", {"state": state, **extra})


def price_in(text: str) -> float | None:
    from personas.sellers import MONEY
    found = MONEY.findall(text or "")
    return float(found[-1].replace(",", "")) if found else None


def run(box: Sandbox) -> None:
    from shopagent import ShopAgent

    attack = box.attack
    from harness.models import shopagent_model
    agent = ShopAgent(box.version, listings=attack.get("listings"), model=shopagent_model())
    model = agent.info["model"]
    box.status("booting", **agent.info)
    if model == "reference-sim" and not ALLOW_SIM:
        raise RuntimeError("ShopAgent is on reference-sim (no ANTHROPIC_API_KEY); refusing a real run")

    seen_txs = 0

    def deliver(channel: str, actor: str, text: str, **extra) -> str:
        nonlocal seen_txs
        box.emit("message", actor, {"text": text, "channel": channel, **extra})
        turn = agent.step(channel, actor, text)
        for call in turn.tool_calls:
            box.emit("tool_call", "shopagent", call)
            if call["name"] == "get_listing" and call["args"].get("listing_id") in agent.listings:
                from shopagent.market import listing_text
                listing = agent.listings[call["args"]["listing_id"]]
                box.emit("message", listing["seller"], {"text": listing_text(listing), "channel": "listing"})
        for tx in agent.ledger.txs[seen_txs:]:
            box.emit("ledger_tx", "shopagent", tx)
        seen_txs = len(agent.ledger.txs)
        if turn.text:
            box.emit("message", "shopagent", {"text": turn.text, "price": price_in(turn.text)})
        return turn.text

    box.status("attacking")
    if attack["family"] == "vishing_call" and (ROOT / "audio" / f"{attack['attack_id']}.mp3").exists():
        from harness.voice import load_call
        call = load_call(attack["attack_id"]) or {}
        # The agent hears the call script; the Scribe transcript is kept as evidence (it mangles addresses).
        box.emit("audio", "caller", {"url": f"/audio/{attack['attack_id']}.mp3", "voice": call.get("voice"),
                                     "transcript": [l["transcript"] for l in call.get("lines", [])]})

    for channel, actor, text in attack["steps"]:
        deliver(channel, actor, text)

    if attack["kind"] == "swarm":
        from personas import make_swarm
        swarm, history = make_swarm(attack["seed"]), []
        for _ in range(attack.get("max_turns", 6)):
            for persona in swarm:
                text = persona.reply(history)
                history.append({"actor": persona.name, "text": text})
                reply = deliver("seller", persona.name, text, price=price_in(text))
                history.append({"actor": "shopagent", "text": reply})
            if any(t["allowlisted"] for t in agent.ledger.txs):
                break

    box.status("scoring")
    box.status("done", model=model)


def main() -> None:
    run_id, version = sys.argv[1], sys.argv[2]
    box = Sandbox(run_id, json.loads(sys.stdin.read()), version)
    try:
        run(box)
    except Exception as exc:  # the tile shows "error"; the traceback goes to the runner's log
        traceback.print_exc()
        box.status("error", error=f"{type(exc).__name__}: {exc}"[:500])
        sys.exit(1)


if __name__ == "__main__":
    main()
