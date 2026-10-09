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
    """main = this working tree; pr = working tree + demo/pr42.patch (real code, not a flag).

    On a PR checkout (CI) the tree already has the patch, so it is the PR build and main is the
    patch reversed."""
    patch = str(ROOT / "demo" / "pr42.patch")
    other = tmp_path_factory.mktemp("other-build")
    shutil.copytree(ROOT / "shopagent", other / "shopagent")
    is_pr = subprocess.run(["git", "apply", "--check", "--reverse", patch], cwd=other,
                           capture_output=True).returncode == 0
    subprocess.run(["git", "apply", *(["--reverse"] if is_pr else []), patch], cwd=other, check=True)
    main, pr = (other, ROOT) if is_pr else (ROOT, other)
    os.environ["SHOPAGENT_BUILD_MAIN"] = str(main)
    os.environ["SHOPAGENT_BUILD_PR"] = str(pr)
    os.environ["SHOPAGENT_MODEL"] = "reference-sim"
    return {"main": main, "pr": pr}
