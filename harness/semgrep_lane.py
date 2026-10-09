"""Semgrep lane (PRD build plan P1-2/3): scan both builds, then turn the confirmed regression into a static
rule and verify it fires on the PR build and stays silent on main.

    uv run python -m harness.semgrep_lane [--run-id r_...]

Writes semgrep.json (served at /api/semgrep). Semgrep is the lane that gains the rule; it never "missed".
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import anthropic

from harness import db  # noqa: F401  (loads .env)
from harness.blast import pr_diff

ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-opus-5-5"
REFS = {"main": "main", "pr": "fix/sanitize-listing-input"}
RULE_ID = "differential.inbound-text-as-user-role"
SEMGREP = ["uvx", "--from", "semgrep", "semgrep"]

RULE_PROMPT = """Differential confirmed a behavioral regression in this PR: inbound call transcripts and seller
messages from third parties now reach the model as plain user-role messages (no untrusted wrapper), so a
caller who spoofs the display name "user" gets their payment approval honored. On main the same text was
wrapped with wrap_untrusted().

Write one Semgrep rule (YAML, language python, id {rule_id}, severity ERROR) that matches the root-cause
pattern in the PR version of shopagent/inbound.py: returning a {{"role": "user", "content": ...}} message whose
content is not produced by wrap_untrusted() or sanitize(), inside handle_inbound(). It must match the PR file
and must NOT match the main file. Prefer pattern / pattern-not / pattern-inside. Message: one sentence.
Return only the YAML, no fences.

PR diff:
```diff
{diff}
```

main version:
```python
{main_src}
```

PR version:
```python
{pr_src}
```
{feedback}"""


def export_build(ref: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", ref, "shopagent"], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
    return dest


def scan(target: Path, config: str) -> list[dict]:
    out = subprocess.run([*SEMGREP, "scan", "--config", config, "--json", "--quiet", "--metrics", "off",
                          str(target)], capture_output=True, text=True)
    results = json.loads(out.stdout or "{}").get("results", [])
    return [{"rule_id": r["check_id"], "path": str(Path(r["path"]).relative_to(target)),
             "line": r["start"]["line"], "message": r["extra"]["message"].strip(),
             "severity": r["extra"]["severity"]} for r in results]


def write_rule(builds: dict[str, Path], attack_id: str | None, attempts: int = 3) -> dict | None:
    client = anthropic.Anthropic()
    src = {v: (p / "shopagent" / "inbound.py").read_text() for v, p in builds.items()}
    feedback = ""
    for _ in range(attempts):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": RULE_PROMPT.format(
                rule_id=RULE_ID, diff=pr_diff(), main_src=src["main"], pr_src=src["pr"], feedback=feedback)}])
        yaml = "".join(b.text for b in resp.content if b.type == "text").strip()
        yaml = yaml.removeprefix("```yaml").removeprefix("```").removesuffix("```").strip() + "\n"
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(yaml)
        hits = {v: [h for h in scan(p, f.name) if h["rule_id"].endswith(RULE_ID)] for v, p in builds.items()}
        fires, silent = bool(hits["pr"]), not hits["main"]
        if fires and silent:
            return {"id": RULE_ID, "yaml": yaml, "fires_on_pr": True, "silent_on_main": True,
                    "from_attack": attack_id, "pr_matches": hits["pr"], "model": MODEL}
        feedback = (f"\nYour previous rule matched the PR file {len(hits['pr'])} times and the main file "
                    f"{len(hits['main'])} times. It must match PR at least once and main zero times. Fix it.\n"
                    f"Previous rule:\n{yaml}")
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--attack-id", default="vishing_call-0")
    ap.add_argument("--baseline", default="p/python", help="Semgrep registry config for the baseline scan")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        builds = {v: export_build(ref, Path(tmp) / v) for v, ref in REFS.items()}
        baseline = {v: scan(p, args.baseline) for v, p in builds.items()}
        rule = write_rule(builds, args.attack_id)
        out = {"baseline_config": args.baseline,
               "builds": {v: {"findings": baseline[v] + (rule["pr_matches"] if rule and v == "pr" else [])}
                          for v in builds},
               "generated_rule": rule}
    if rule:
        (ROOT / "semgrep" / "rules").mkdir(parents=True, exist_ok=True)
        (ROOT / "semgrep" / "rules" / f"{RULE_ID}.yml").write_text(rule["yaml"])
    (ROOT / "semgrep.json").write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps({v: len(b["findings"]) for v, b in out["builds"].items()}),
          "rule verified" if rule else "no verified rule")
    if rule:
        print(rule["yaml"])


if __name__ == "__main__":
    main()
