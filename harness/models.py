"""Model selection for ShopAgent runs, passed in through ShopAgent(model=...).

The PRD wants a small model so builds separate. Older small models (claude-haiku-4-5) reject the
`effort` parameter that shopagent.model.ClaudeModel always sends, so this adapter drops it when
SHOPAGENT_EFFORT=none. Everything else (system prompt, tools, transcript) is ShopAgent's own.
"""

from __future__ import annotations

import os

from shopagent.model import (SYSTEM_PROMPT, TOOL_SCHEMAS, ClaudeModel, Decision, ToolUse, _to_messages,
                             make_model)


class NoEffortClaudeModel(ClaudeModel):
    def decide(self, transcript: list[dict]) -> Decision:
        resp = self.client.messages.create(
            model=self.name, max_tokens=4096, system=SYSTEM_PROMPT, tools=TOOL_SCHEMAS,
            messages=_to_messages(transcript),
        )
        if resp.stop_reason == "refusal":
            return Decision(text="[model declined to respond]")
        raw = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        uses = [ToolUse(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        return Decision(text=text, tool_uses=uses, raw_blocks=raw)


def shopagent_model():
    choice = os.environ.get("SHOPAGENT_MODEL", "auto")
    if os.environ.get("SHOPAGENT_EFFORT") == "none" and choice not in ("auto", "reference-sim"):
        return NoEffortClaudeModel(model=choice)
    return make_model()
