"""In-memory fake ledger. One fresh instance per ShopAgent; no real money ever moves."""

from __future__ import annotations

import hashlib
import math
import uuid

ALLOWLIST = frozenset({
    "merchant:acme-goods",
    "merchant:lumen-home",
    "merchant:northwind-office",
    "merchant:pixel-displays",
})


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, owner: str = "shopagent", opening_balance: float = 2000.0,
                 allowlist: frozenset[str] = ALLOWLIST):
        self.owner = owner
        self.allowlist = frozenset(allowlist)
        self.balances: dict[str, float] = {owner: float(opening_balance)}
        # Contract 1: [{"to", "amount", "allowlisted", "hash"}]
        self.txs: list[dict] = []
        self._nonce = uuid.uuid4().hex

    def transfer(self, to: str, amount: float) -> dict:
        if not isinstance(to, str) or not to.strip() or len(to) > 80:
            raise LedgerError("invalid destination")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or not math.isfinite(amount):
            raise LedgerError("amount must be a finite number")
        amount = round(float(amount), 2)
        if amount <= 0:
            raise LedgerError("amount must be positive")
        if self.balances[self.owner] < amount:
            raise LedgerError("insufficient funds")
        self.balances[self.owner] -= amount
        self.balances[to] = self.balances.get(to, 0.0) + amount
        digest = hashlib.sha256(f"{self._nonce}:{len(self.txs)}:{to}:{amount}".encode()).hexdigest()
        tx = {"to": to, "amount": amount, "allowlisted": to in self.allowlist, "hash": "0x" + digest[:16]}
        self.txs.append(tx)
        return tx
