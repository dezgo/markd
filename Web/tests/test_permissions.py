"""Shell scripts meant to be run are executable in the index.

setup.sh sat at mode 644 from the day it was committed, so a fresh checkout on
the server could only run it as `bash setup.sh`. deploy.sh was 755, which is
why that one always worked and this went unnoticed.

Git tracks the executable bit but Windows checkouts do not carry it, so the
index is the only place this can be asserted.
"""

import subprocess
from pathlib import Path

import pytest

REPO_WEB = Path(__file__).resolve().parent.parent


def _indexed_scripts():
    out = subprocess.run(["git", "ls-files", "-s", "--", "*.sh"],
                         cwd=REPO_WEB, capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        mode, _, rest = line.partition(" ")
        rows.append((rest.split("\t", 1)[1], mode))
    return rows


def test_there_are_scripts_to_check():
    assert _indexed_scripts(), "no .sh files found — this test is vacuous"


@pytest.mark.parametrize("name,mode", _indexed_scripts())
def test_a_shell_script_is_executable(name, mode):
    assert mode == "100755", (
        f"{name} is mode {mode}; run: git update-index --chmod=+x {name}")
