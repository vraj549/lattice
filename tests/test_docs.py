"""The documentation must agree with the code.

Docs rot silently: a default changes, a capability is added, and the install
guide keeps telling people something that is no longer true. These assert the
claims a reader would act on.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import ccx_writer, config  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


DOCS = {"README.md": None, "docs/INSTALL.md": None,
        "docs/SOLVERS.md": None, "docs/METHODS.md": None}


@pytest.fixture(scope="module")
def docs():
    return {k: _read(k) for k in DOCS}


def test_every_documentation_link_resolves(docs):
    for name, text in docs.items():
        base = os.path.dirname(os.path.join(ROOT, name))
        for link in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]*)?\)", text):
            if link.startswith("http"):
                continue
            target = os.path.normpath(os.path.join(base, link))
            assert os.path.isfile(target), f"{name} links to missing {link}"


def test_every_env_var_is_documented(docs):
    """A knob nobody can find is not a knob."""
    src = _read("lattice_fea/config.py")
    used = set(re.findall(r'"(LATTICE_[A-Z_]+)"', src))
    missing = sorted(v for v in used if v not in docs["docs/INSTALL.md"])
    assert not missing, f"undocumented environment variables: {missing}"


def test_calculix_capabilities_match_the_docs(docs):
    caps = ccx_writer.CAPABILITIES
    text = docs["docs/SOLVERS.md"] + docs["README.md"]
    for t in caps["types"]:
        assert t in text.lower(), f"CalculiX runs {t} but the docs do not say so"
    # things it cannot do must be stated, not left to be discovered
    for absent in ("harmonic", "random"):
        assert absent not in caps["types"]
    assert "Bolts" in docs["docs/SOLVERS.md"]


def test_documented_defaults_are_the_real_defaults(docs):
    cfg = config.SolverConfig()
    install = docs["docs/INSTALL.md"]
    assert cfg.ccx_threads == 1
    assert cfg.docker_image in install, "the docker image in the docs is not the default"
    assert "codeastersolver/codeaster-seq" in install, (
        "the known-broken image must stay documented as broken — it was the "
        "default once and people will still have it configured")
    assert str(cfg.memory_mb) in install
    assert str(cfg.time_limit_s) in install


def test_bolt_sizes_in_the_readme_match_the_table(docs):
    ui = _read("lattice_fea/ui/js/ui.js")
    sizes = re.findall(r'id:\s*"([^"]+)",\s*label:\s*"[^"]*",\s*d:', ui)
    metric = [x for x in sizes if x.startswith("M")]
    assert metric[0] == "M1.6" and metric[-1] == "M8"
    assert f"{metric[0]}–{metric[-1]}" in docs["README.md"], (
        "the README states a bolt size range that the table does not match")
    for s in ("0-80", "2-56", "4-40", "6-32"):
        assert s in sizes and f"#{s}" in docs["README.md"]


def test_install_covers_every_platform(docs):
    install = docs["docs/INSTALL.md"]
    for token in ("macOS", "Linux", "Windows", "WSL2", "calculix-ccx",
                  "python -m venv", "pip install -e .", "doctor"):
        assert token in install, f"the install guide never mentions {token}"


def test_apple_silicon_guidance_is_current(docs):
    """It used to say the only option there was the demo solver. CalculiX runs
    natively on Apple Silicon, and telling people otherwise sends them away."""
    install = docs["docs/INSTALL.md"]
    assert "Apple Silicon" in install
    i = install.index("Apple Silicon")
    assert "CalculiX" in install[i:i + 600], (
        "the Apple Silicon note must point at CalculiX, which works there")


def test_install_warns_about_the_pip_version(docs):
    """`pip install -e .` fails on the pip bundled with a Python 3.9 venv, and
    fails in a way that looks like success — the app only breaks later, with
    a missing-module error that points nowhere near the cause."""
    for name in ("README.md", "docs/INSTALL.md"):
        assert "--upgrade pip" in docs[name], (
            f"{name} tells people to run an install that fails on stock pip")
