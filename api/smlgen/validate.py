"""SML validation - shells out to the `sml-cli` npm package (the authoritative
external validator per the build plan), writing the generated files to a temp
directory in the on-disk repo layout it expects.

`npx sml-cli validate <dir>` exits 1 on failure, 0 on success, and prints
`[ERROR]`/`[WARNING]`-prefixed lines per file plus a final "Validation
FAILED/PASSED" line - confirmed by running it against a real hand-built SML
repo (sample-dev) during development.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class SmlCliNotFound(RuntimeError):
    pass


def validate_sml(files: dict[str, str]) -> dict:
    npx = shutil.which("npx")
    if not npx:
        raise SmlCliNotFound("npx not found on PATH - install Node.js to run sml-cli validation.")

    with tempfile.TemporaryDirectory(prefix="sml-wizard-validate-") as tmp:
        root = Path(tmp)
        for rel_path, content in files.items():
            p = root / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

        proc = subprocess.run(
            [npx, "--yes", "sml-cli", "validate", str(root)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return {
            "passed": proc.returncode == 0,
            "returncode": proc.returncode,
            "output": (proc.stdout or "") + (proc.stderr or ""),
        }
