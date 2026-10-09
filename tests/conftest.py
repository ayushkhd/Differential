import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session", autouse=True)
def builds(tmp_path_factory):
    """main = this working tree; pr = working tree + demo/pr42.patch (real code, not a flag)."""
    pr = tmp_path_factory.mktemp("pr-build")
    shutil.copytree(ROOT / "shopagent", pr / "shopagent")
    subprocess.run(["git", "apply", str(ROOT / "demo" / "pr42.patch")], cwd=pr, check=True)
    os.environ["SHOPAGENT_BUILD_MAIN"] = str(ROOT)
    os.environ["SHOPAGENT_BUILD_PR"] = str(pr)
    os.environ["SHOPAGENT_MODEL"] = "reference-sim"
    return {"main": ROOT, "pr": pr}
