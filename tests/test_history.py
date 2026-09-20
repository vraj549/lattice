"""Run the undo/redo JS tests under pytest, so `pytest` stays the one way to
check the project.

Undo lived inside main.js, tangled with the DOM and the viewer, and so had no
tests. It was completely dead — every edit through mutate() was dropped and
the button stayed greyed out for the whole session — and nothing caught it.
Anything that decides whether someone can get their work back is worth being
testable on its own.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "test_history.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_undo_redo():
    proc = subprocess.run([shutil.which("node"), SCRIPT], capture_output=True,
                          text=True, timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
