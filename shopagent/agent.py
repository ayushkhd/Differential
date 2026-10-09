"""ShopAgent: one agent loop, four tools, one fake ledger (Differential contract 1).

    agent = ShopAgent(version="main")        # or "pr"
    turn = agent.step(channel, actor, text)   # channel: "user" | "listing" | "call" | "seller"
    turn.text                                 # the agent's reply
    turn.tool_calls                           # [{"name", "args", "result"}]
    agent.ledger.txs                          # [{"to", "amount", "allowlisted", "hash"}]

Each instance owns its transcript, model state and ledger; nothing is shared between instances.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from shopagent.builds import Build, load_build
from shopagent.ledger import Ledger, LedgerError
from shopagent.market import DEFAULT_LISTINGS, listing_text
from shopagent.model import Model, make_model

CHANNELS = ("user", "listing", "call", "seller")
MAX_TEXT = 8000
MAX_TOOL_ROUNDS = 6


@dataclass
class Turn:
    text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class ShopAgent:
    def __init__(self, version: str = "main", *, model: Model | None = None,
                 listings: dict[str, dict] | None = None, opening_balance: float = 2000.0):
        self.version = version
        self.build: Build = load_build(version)
        self.model: Model = model or make_model()
        self.ledger = Ledger(opening_balance=opening_balance)
        self.listings = copy.deepcopy({**DEFAULT_LISTINGS, **(listings or {})})
        self.transcript: list[dict[str, Any]] = []
        self._calls_answered: set[str] = set()

    @property
    def info(self) -> dict[str, Any]:
        return {**self.build.describe(), "model": self.model.name}

    def step(self, channel: str, actor: str, text: str) -> Turn:
        if channel not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}")
        if not isinstance(actor, str) or not isinstance(text, str):
            raise TypeError("actor and text must be strings")
        text = text[:MAX_TEXT]
        tool_calls: list[dict[str, Any]] = []

        if channel == "call" and actor not in self._calls_answered:
            self._calls_answered.add(actor)
            tool_calls.append(self._run_tool("take_call", {"caller": actor})[0])

        message = self.build.inbound.handle_inbound(channel, actor, text)
        self.transcript.append({"type": "inbound", "channel": channel, "actor": actor, **message})

        replies: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            decision = self.model.decide(self.transcript)
            if decision.text or decision.raw_blocks:
                self.transcript.append({"type": "assistant", "text": decision.text, "raw": decision.raw_blocks})
            if decision.text:
                replies.append(decision.text)
            if not decision.tool_uses:
                break
            for use in decision.tool_uses:
                record, model_content, structured = self._run_tool(use.name, use.args)
                tool_calls.append(record)
                self.transcript.append({"type": "tool_use", "id": use.id, "name": use.name, "args": use.args,
                                        "in_raw": decision.raw_blocks is not None})
                self.transcript.append({"type": "tool_result", "id": use.id, "name": use.name,
                                        "content": model_content, "structured": structured})
        else:
            replies.append("[stopped: tool-round limit reached]")
        return Turn(text="\n".join(replies).strip(), tool_calls=tool_calls)

    # ------------------------------------------------------------------ tools

    def _run_tool(self, name: str, args: dict[str, Any]) -> tuple[dict[str, Any], str, dict[str, Any]]:
        """Returns (contract record, content shown to the model, structured result)."""
        try:
            result = getattr(self, f"_tool_{name}")(**args) if name in TOOLS else {"error": f"unknown tool {name}"}
        except TypeError as exc:
            result = {"error": f"bad arguments: {exc}"}
        except LedgerError as exc:
            result = {"error": str(exc)}
        content = result.pop("_model_content", None) if isinstance(result, dict) else None
        structured = dict(result)
        return {"name": name, "args": dict(args), "result": structured}, content or _fmt(structured), structured

    def _tool_get_listing(self, listing_id: str) -> dict[str, Any]:
        listing = self.listings.get(str(listing_id))
        if listing is None:
            return {"error": f"listing {listing_id} not found"}
        handled = self.build.inbound.handle_inbound("listing", listing["seller"], listing_text(listing))
        return {"listing_id": listing_id, "title": listing["title"], "price": listing["price"],
                "merchant": listing["merchant"], "seller": listing["seller"], "_model_content": handled["content"]}

    def _tool_negotiate(self, listing_id: str, offer: float, accept: bool = False) -> dict[str, Any]:
        if str(listing_id) not in self.listings:
            return {"error": f"listing {listing_id} not found"}
        return {"status": "accepted" if accept else "offer_sent", "listing_id": listing_id, "offer": float(offer)}

    def _tool_take_call(self, caller: str) -> dict[str, Any]:
        return {"status": "connected", "caller": caller}

    def _tool_send_payment(self, to: str, amount: float) -> dict[str, Any]:
        tx = self.ledger.transfer(to, amount)
        return {"status": "sent", **tx}


TOOLS = ("get_listing", "negotiate", "take_call", "send_payment")


def _fmt(result: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in result.items())
