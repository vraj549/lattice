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


# ------------------------------------------------ what a failure says it is

def test_the_headline_is_the_line_that_says_what_is_wrong():
    """A text file renamed .step reported "Could not read file
    '…/geometry.step'" — true, and useless. The line that actually diagnosed
    it sat further up the log. extract_errors keeps context around each match,
    so the first line it returns is often an ordinary Info line; only a line
    carrying an error marker can be the headline.
    """
    from lattice_fea.solver import extract_errors, headline

    log = [
        "Info    : Reading '/tmp/x/geometry.step'...",
        " **** ERR StepFile : Undefined Parsing: Line 2: Incorrect syntax: "
        "unexpected TYPE, expecting STEP ****",
        "Error   : Could not read file '/tmp/x/geometry.step'",
        "Traceback (most recent call last):",
        '  File "/x/gmsh_worker.py", line 10, in <module>',
        "RuntimeError: geometry import failed",
    ]
    h = headline(extract_errors(log))
    assert "Incorrect syntax" in h, h
    assert "Could not read file" not in h, h


def test_a_generic_line_is_still_used_when_it_is_all_there_is():
    """Preferring the specific line must not mean printing nothing when there
    is no specific line."""
    from lattice_fea.solver import extract_errors, headline

    log = ["Info    : Reading", "Error   : Could not read file '/tmp/x.step'"]
    assert "Could not read file" in headline(extract_errors(log))
    assert headline([]) is None


def test_the_ui_does_not_keep_its_own_copy_of_mesh_format():
    """MESH_FORMAT lived in two places and drifted: the writer moved to 3
    while the browser still compared against 2, so a mesh that genuinely
    needed rewriting was reported as current. The server now sends it, and the
    literal left in the UI is only a floor for the moment before /api/config
    arrives — it must still not be behind.
    """
    import re

    from lattice_fea import meshing

    ui = open(os.path.join(ROOT, "lattice_fea", "ui", "js", "ui.js"),
              encoding="utf-8").read()
    m = re.search(r"export const MESH_FORMAT = (\d+);", ui)
    assert m, "the UI fallback constant went missing"
    assert int(m.group(1)) == meshing.MESH_FORMAT, (
        f"ui.js says {m.group(1)}, meshing.py says {meshing.MESH_FORMAT}")
    assert "S.config?.mesh_format" in ui, (
        "the UI must prefer the value the server sends over its own literal")


@pytest.mark.parametrize("log,want", [
    # code_aster's own fatal message, as it appears in the .mess file
    ([" <F> <FACTOR_10> La matrice est singuliere ou presque singuliere.",
      "   Le noeud N123 a un pivot nul."], "FACTOR_10"),
    # as_run's status code, a different shape entirely
    (["running", "<F>_ERROR", "done"], "<F>_ERROR"),
    # and the same failure under ERREUR_F='EXCEPTION', as a Python exception
    (["Traceback (most recent call last):",
      '  File "run.comm", line 42, in <module>',
      "AsterError: <FACTOR_10> matrix is singular"], "AsterError"),
])
def test_a_failed_solve_names_the_line_that_says_why(log, want):
    """Only as_run's status codes ("<F>_ERROR") were recognised. code_aster's
    own messages use "<F> <MESSAGE_ID>" — space, not underscore — so the line
    that actually named the failure was never picked out of the log, and the
    user got an exit code. <A> is a warning and is deliberately not matched.
    """
    from lattice_fea.solver import extract_errors, headline
    assert want in (headline(extract_errors(log)) or "")


def test_a_warning_is_not_reported_as_the_failure():
    from lattice_fea.solver import extract_errors, headline
    log = ["<A> <CALCULEL_11> a warning nobody needs as a headline",
           "<F> <FACTOR_10> the actual failure"]
    assert "FACTOR_10" in headline(extract_errors(log))
