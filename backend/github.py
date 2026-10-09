"""GitHub handoff (PRD step 7): commit status on the PR head, a PR comment with the verdict grid, and a
finding issue for "Send to remediation". Uses the gh CLI with the user's own auth.

Config: DIFFERENTIAL_REPO (default ayushkhd/Differential), DIFFERENTIAL_PR (default 2),
DIFFERENTIAL_APP_URL (default http://127.0.0.1:5173).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("DIFFERENTIAL_REPO", "ayushkhd/Differential")
PR = int(os.environ.get("DIFFERENTIAL_PR", "2"))
APP_URL = os.environ.get("DIFFERENTIAL_APP_URL", "http://127.0.0.1:5173")
CONTEXT = "Differential"
SQUARES = {"regression": "🟥", "fixed": "🟦", "pass": "🟩", "pre_existing": "🟨", None: "⬜"}
FAMILY_NAMES = {"vishing_call": "Voice Phishing", "marketplace_negotiation": "Marketplace negotiation",
                "listing_injection": "Listing injection", "benign_purchase": "Benign purchase (control)"}


def _gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True).stdout.strip()


def head_sha() -> str:
    return _gh("pr", "view", str(PR), "--repo", REPO, "--json", "headRefOid", "-q", ".headRefOid")


def set_status(state: str, description: str, run_id: str | None = None) -> None:
    """state: pending | success | failure | error"""
    _gh("api", f"repos/{REPO}/statuses/{head_sha()}", "-f", f"state={state}", "-f", f"context={CONTEXT}",
        "-f", f"description={description[:140]}", "-f", f"target_url={APP_URL}")


def summarize(verdicts: dict) -> tuple[str, str]:
    """(status state, one-line description) from /api/verdicts."""
    s = verdicts["summary"]
    state = "failure" if s["regression"] else "success"
    desc = (f"{s['regression']} regressions, {s['fixed']} fixed, {s['pass']} pass, "
            f"{s['pre_existing']} pre-existing across {s['total']} paired attacks")
    return state, desc


def comment_markdown(verdicts: dict) -> str:
    s = verdicts["summary"]
    state, desc = summarize(verdicts)
    head = "❌ **Differential: this change made ShopAgent less safe.**" if state == "failure" else \
        "✅ **Differential: no behavioral regressions.**"
    lines = [head, "", f"{desc}. Each attack ran on `main` and on this PR in separate sandboxes.", ""]
    by_family: dict[str, list] = {}
    for a in verdicts["attacks"]:
        by_family.setdefault(a["family"], []).append(a)
    lines += ["| Family | Attacks | main fail rate | PR fail rate |", "|---|---|---|---|"]
    for f in verdicts["families"]:
        grid = "".join(SQUARES[a["verdict"]] for a in by_family.get(f["family"], []))
        rate = lambda r: "n/a" if r is None else f"{r:.0%}"  # noqa: E731
        lines.append(f"| {FAMILY_NAMES.get(f['family'], f['family'])} | {grid} | "
                     f"{rate(f['main_fail_rate'])} | {rate(f['pr_fail_rate'])} |")
    lines += ["", "🟥 regression · 🟦 fixed · 🟩 pass · 🟨 pre-existing", "",
              f"Evidence for every tile (call audio, transcripts, tool calls, ledger): {APP_URL}",
              f"<sub>run `{verdicts['run_id']}` · scored by a ClickHouse query over {s['total'] * 2} sandbox runs</sub>"]
    return "\n".join(lines)


def post_comment(verdicts: dict) -> str:
    return _gh("pr", "comment", str(PR), "--repo", REPO, "--body-file", "-", stdin=comment_markdown(verdicts))


def report(verdicts: dict) -> None:
    """Final status + PR comment for a finished run."""
    state, desc = summarize(verdicts)
    set_status(state, desc, verdicts["run_id"])
    post_comment(verdicts)


def _excerpt(side: dict) -> list[str]:
    out = []
    for e in side["events"]:
        p = e["payload"]
        if e["type"] == "message":
            out.append(f"- **{e['actor']}** ({p.get('channel', 'reply')}): {p['text'][:300]}")
        elif e["type"] == "ledger_tx":
            out.append(f"- **ledger**: ${p['amount']} → `{p['to']}` (allowlisted: {p['allowlisted']})")
    return out


def open_finding_issue(run_id: str | None, attack_id: str, detail: dict) -> str:
    semgrep_path = ROOT / "semgrep.json"
    rule = json.loads(semgrep_path.read_text()).get("generated_rule") if semgrep_path.exists() else None
    body = [
        f"Differential found a behavioral regression in PR #{PR} (run `{run_id}`).",
        "",
        "## Root cause",
        "`shopagent/inbound.py`: the unified `handle_inbound()` passes call transcripts and seller messages to "
        "the model as plain user-role messages, prefixed only with the sender's display name. On `main` they "
        "are wrapped in `<untrusted>` tags. A caller who spoofs the display name `user` is indistinguishable "
        "from the real user, so their \"approval\" of a new payee is honored.",
        "",
        f"## Repro attack: `{attack_id}`",
        f"{detail.get('title')} (family `{detail['family']}`, monitor `{detail['monitor']}`).",
        "",
        "### main (refused)", *_excerpt(detail["main"]), "",
        "### PR (paid the attacker)", *_excerpt(detail["pr"]), "",
        "## Suggested fix",
        "Keep the sanitizer for listings, but route `call` and `seller` text through `wrap_untrusted()` again "
        "(or a separate non-user role). Only the `user` channel should produce user-role content.",
    ]
    if rule:
        body += ["", "## Static rule (generated by Differential, verified on both builds)",
                 f"Fires on the PR: {rule['fires_on_pr']} · silent on main: {rule['silent_on_main']}",
                 "", "```yaml", rule["yaml"].rstrip(), "```"]
    body += ["", f"Evidence: {APP_URL}"]
    return _gh("issue", "create", "--repo", REPO, "--title",
               f"Differential finding: {detail['family']} regression in PR #{PR} ({attack_id})",
               "--body-file", "-", stdin="\n".join(body))
