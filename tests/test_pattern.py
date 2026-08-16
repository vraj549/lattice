"""Run the bolt-pattern JS tests under pytest, so `pytest` stays the one way
to check the project.

The mapping is geometry, and geometry is where a silent mistake becomes a
joint bolted to the wrong hole — it is worth real tests even though it lives
in the browser. Node is optional: skip rather than fail where it is absent.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "test_pattern.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_bolt_pattern_mapping():
    proc = subprocess.run([shutil.which("node"), SCRIPT], capture_output=True,
                          text=True, timeout=60)
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
