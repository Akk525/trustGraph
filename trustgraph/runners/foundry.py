from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from trustgraph.models import FoundryResult

_NOT_INSTALLED_MSG = (
    "Foundry not installed or forge not found; generated test saved but not executed."
)


def find_foundry_root(start_path: str) -> str | None:
    """Walk up from start_path looking for foundry.toml."""
    current = Path(start_path).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "foundry.toml").exists():
            return str(candidate)
    return None


def run_forge_test(
    foundry_root: str,
    test_path: str | None = None,
    verbose: bool = True,
) -> FoundryResult:
    """Run `forge test` in foundry_root. Returns a FoundryResult."""
    forge = shutil.which("forge")
    if not forge:
        return FoundryResult(
            ran=False,
            passed=None,
            output=_NOT_INSTALLED_MSG,
            test_path=test_path or "",
        )

    cmd = [forge, "test"]
    if verbose:
        cmd.append("-vvv")
    if test_path:
        cmd.extend(["--match-path", test_path])

    try:
        proc = subprocess.run(
            cmd,
            cwd=foundry_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = proc.stdout + proc.stderr
        passed = proc.returncode == 0
    except FileNotFoundError:
        return FoundryResult(
            ran=False,
            passed=None,
            output=_NOT_INSTALLED_MSG,
            test_path=test_path or "",
        )
    except subprocess.TimeoutExpired:
        return FoundryResult(
            ran=True,
            passed=False,
            output="forge test timed out after 120 seconds.",
            test_path=test_path or "",
        )

    return FoundryResult(
        ran=True,
        passed=passed,
        output=output,
        test_path=test_path or "",
    )
