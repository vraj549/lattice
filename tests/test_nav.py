"""Run the viewport-navigation JS tests under pytest, so `pytest` stays the
one way to check the project.

Navigation lived in a controller with no tests, and the defect was
geometric: the Top view sat exactly on the turntable's pole, so a horizontal
drag moved the camera 0.05 of 100 units and the model spun in place. That is
measurable, which means it is testable, which means it should never have been
possible to ship it twice.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("script", ["test_nav.mjs", "test_spacemouse.mjs"])
def test_js(script):
    proc = subprocess.run([shutil.which("node"), os.path.join(HERE, script)],
                          capture_output=True, text=True, timeout=120)
    print(proc.stdout)
    assert proc.returncode == 0, proc.stdout + proc.stderr
