"""Read CalculiX .frd result files.

The format is fixed-column ASCII. Blocks are introduced by a key line whose
first token identifies it:

    2C   node block      -1<node> <x> <y> <z>
    3C   element block
   1PSTEP / 100C<name>   a result block header, then -4/-5 component lines
                         and -1<node> <values...> data lines

Only what Lattice displays is parsed: nodal displacement, stress and the
frequencies of an eigenvalue run. Anything else is skipped rather than
guessed at.
"""
from __future__ import annotations

import numpy as np

# CalculiX writes -1/-2 continuation markers in the first 3 columns and then
# fixed 12-character fields.
_W = 12


def _fields(line: str, start: int, n: int) -> list:
    out = []
    for k in range(n):
        chunk = line[start + k * _W: start + (k + 1) * _W]
        if not chunk.strip():
            break
        out.append(float(chunk))
    return out


class FrdFile:
    """Nodes and nodal result blocks of a .frd file."""

    def __init__(self, path: str):
        self.node_tags: list = []
        self.coords: list = []
        self.blocks: list = []          # {"name", "step", "comps", "values"}
        self._parse(path)
        self.nodes = np.asarray(self.coords, dtype=np.float64).reshape(-1, 3) \
            if self.coords else np.zeros((0, 3))
        self.tag_index = {t: i for i, t in enumerate(self.node_tags)}

    def _parse(self, path: str) -> None:
        cur = None
        mode = None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                raw = line.rstrip("\n")
                # Block keys sit in the first six columns, right-aligned with
                # the trailing 'C': "    2C" for nodes, "  100C" for a result.
                key = raw[:6].strip()

                if key == "2C":
                    mode = "nodes"
                    continue
                if key == "3C":
                    mode = "elems"          # connectivity is not needed here
                    continue
                if key == "100C":
                    # The step value (1.0 for a static step, the eigen-
                    # frequency in Hz for a *FREQUENCY run) is the only
                    # decimal token on the header line.
                    val = 0.0
                    for tok in raw[6:].split():
                        if "." in tok:
                            val = _safe_float(tok)
                            break
                    cur = {"name": "", "step": val, "comps": [], "values": {}}
                    self.blocks.append(cur)
                    mode = "result"
                    continue

                if mode == "nodes" and raw.startswith(" -1"):
                    self.node_tags.append(int(raw[3:13]))
                    self.coords.extend(_fields(raw, 13, 3))
                elif mode == "result" and cur is not None:
                    if raw.startswith(" -4"):
                        cur["name"] = raw[5:13].strip()
                    elif raw.startswith(" -5"):
                        name = raw[5:13].strip()
                        # "ALL" is a magnitude pseudo-entry, not a component
                        if name and name.upper() != "ALL":
                            cur["comps"].append(name)
                    elif raw.startswith(" -1"):
                        tag = int(raw[3:13])
                        cur["values"][tag] = _fields(raw, 13, len(cur["comps"]) or 6)
                    elif raw.startswith(" -3"):
                        mode = None

    def field(self, name: str, step: int = 0) -> "tuple[list, np.ndarray]|None":
        """(component names, array[n_nodes, n_comp]) for one result block."""
        hits = [b for b in self.blocks if b["name"].upper().startswith(name.upper())]
        if not hits:
            return None
        b = hits[min(step, len(hits) - 1)]
        ncomp = len(b["comps"])
        arr = np.full((len(self.node_tags), ncomp), np.nan)
        for tag, vals in b["values"].items():
            i = self.tag_index.get(tag)
            if i is not None and len(vals) >= ncomp:
                arr[i] = vals[:ncomp]
        return b["comps"], arr

    def frequencies(self) -> list:
        """Hz per mode, from the step value of each displacement block.

        CalculiX writes eigenfrequencies in the block header of a *FREQUENCY
        run; each mode is its own DISP block.
        """
        return [b["step"] for b in self.blocks
                if b["name"].upper().startswith("DISP") and b["step"]]


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0
