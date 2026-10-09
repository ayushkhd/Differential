"""Resolve the code for a build version so ShopAgent(version="main") and ShopAgent(version="pr")
run the real code from each git ref, not a behavior flag.

Only `shopagent/inbound.py` differs between builds (PR #42 changes it). Resolution order:
  1. SHOPAGENT_BUILD_<VERSION>  path to an inbound.py file (or a checkout containing shopagent/inbound.py)
  2. git ref: main -> SHOPAGENT_MAIN_REF (default "main"), pr -> SHOPAGENT_PR_REF
     (default "fix/sanitize-listing-input"); tries the local branch, then origin/<ref>.
  3. version "local" -> the working-tree file.

Loading executes the build's code in this process. Isolation between builds is the harness's job
(one sandbox per run); this module only guarantees each version gets its own module object.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import types
from dataclasses import dataclass
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
INBOUND_PATH = "shopagent/inbound.py"
DEFAULT_REFS = {"main": "main", "pr": "fix/sanitize-listing-input"}


class BuildNotFound(RuntimeError):
    pass


@dataclass(frozen=True)
class Build:
    version: str
    source: str  # "git:<ref>@<sha>" | "path:<file>" | "working-tree"
    sha256: str
    inbound: types.ModuleType

    def describe(self) -> dict:
        return {"version": self.version, "source": self.source, "inbound_sha256": self.sha256[:12]}


_cache: dict[tuple[str, str], Build] = {}


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _source_for(version: str) -> tuple[str, str]:
    override = os.environ.get(f"SHOPAGENT_BUILD_{version.upper()}")
    if override:
        p = Path(override)
        if p.is_dir():
            p = p / INBOUND_PATH
        if not p.is_file():
            raise BuildNotFound(f"SHOPAGENT_BUILD_{version.upper()}={override} has no inbound.py")
        return f"path:{p}", p.read_text()
    if version == "local":
        return "working-tree", (PACKAGE_DIR / "inbound.py").read_text()
    ref = os.environ.get(f"SHOPAGENT_{version.upper()}_REF", DEFAULT_REFS.get(version))
    if not ref:
        raise BuildNotFound(f"Unknown version '{version}'. Use main, pr, local or set SHOPAGENT_BUILD_{version.upper()}")
    for candidate in (ref, f"origin/{ref}"):
        code = _git("show", f"{candidate}:{INBOUND_PATH}")
        if code is not None:
            sha = (_git("rev-parse", "--short", candidate) or "").strip()
            return f"git:{candidate}@{sha}", code
    raise BuildNotFound(
        f"Build '{version}': git ref '{ref}' has no {INBOUND_PATH}. Create the branch (see README) "
        f"or point SHOPAGENT_BUILD_{version.upper()} at an inbound.py."
    )


def load_build(version: str) -> Build:
    source, code = _source_for(version)
    digest = hashlib.sha256(code.encode()).hexdigest()
    key = (version, digest)
    if key not in _cache:
        module = types.ModuleType(f"shopagent_build_{version}_{digest[:8]}")
        module.__file__ = source
        exec(compile(code, f"<{version}:{INBOUND_PATH}>", "exec"), module.__dict__)
        if not callable(getattr(module, "handle_inbound", None)):
            raise BuildNotFound(f"Build '{version}' ({source}) does not define handle_inbound()")
        _cache[key] = Build(version, source, digest, module)
    return _cache[key]
