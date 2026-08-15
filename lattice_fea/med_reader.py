"""Read code_aster MED (HDF5) result files with h5py — no salome/medcoupling.

MED files are HDF5 with a stable-ish schema:
    /ENS_MAA/<mesh>/<iter>/NOE/COO           node coordinates
    /ENS_MAA/<mesh>/<iter>/MAI/<TYP>/NOD     connectivity per element type
    /CHA/<field>/<step>/NOE/<profile>/CO     nodal field values
Component names live in the field group attribute NOM as concatenated
16-char strings; step groups carry NDT (order number) and PDT (time or
frequency value).

The array memory layout (interleaved [x1 y1 z1 ...] vs planar [x.. y.. z..])
is not worth trusting from memory, so we *measure* it: coordinates are
reshaped both ways and validated against the geometry bounding box recorded
at mesh time; connectivity both ways against the known total volume. The
winning layout is then applied to field arrays (same writer, same layout).
"""
from __future__ import annotations

import numpy as np

try:
    import h5py
except ImportError:  # pragma: no cover
    h5py = None

CELL_NODES = {
    "PO1": 1, "SE2": 2, "SE3": 3, "TR3": 3, "TR6": 6, "QU4": 4, "QU8": 8,
    "TE4": 4, "T10": 10, "PY5": 5, "P13": 13, "PE6": 6, "P15": 15,
    "HE8": 8, "H20": 20,
}

# local faces of a 10-node tet, MED/aster numbering:
# corners 0-3, mids: 4=(0,1) 5=(1,2) 6=(2,0) 7=(0,3) 8=(1,3) 9=(2,3)
TET10_FACES = [
    (0, 1, 2, 4, 5, 6),
    (0, 1, 3, 4, 8, 7),
    (1, 2, 3, 5, 9, 8),
    (2, 0, 3, 6, 7, 9),
]
TET4_FACES = [(0, 1, 2), (0, 1, 3), (1, 2, 3), (2, 0, 3)]
# corner of the tet that is NOT on each face above, in the same order
TET_OPPOSITE = (3, 2, 0, 1)


class MedFile:
    def __init__(self, path: str, expect_bbox=None, expect_volume=None):
        if h5py is None:
            raise RuntimeError("h5py is required to read results")
        self.f = h5py.File(path, "r")
        self.expect_bbox = expect_bbox
        self.expect_volume = expect_volume
        self.interleaved = None     # resolved during node read
        self.nodes = None
        self.tets = None            # (n, 4|10) int array, 0-based
        self.tet_npery = 0
        self._read_mesh()

    # ---------------- mesh ----------------
    def _mesh_group(self):
        g = self.f.get("ENS_MAA")
        if g is None:
            raise ValueError("MED file has no mesh (ENS_MAA)")
        mesh_name = list(g.keys())[0]
        mg = g[mesh_name]
        # find iteration subgroup that owns NOE
        for k in mg:
            if isinstance(mg[k], h5py.Group) and "NOE" in mg[k]:
                return mg[k]
        if "NOE" in mg:
            return mg
        raise ValueError("MED mesh: no NOE group found")

    def _read_mesh(self):
        it = self._mesh_group()
        coo = np.asarray(it["NOE"]["COO"], dtype=np.float64).ravel()
        n = coo.size // 3

        cand = {
            True: coo.reshape(n, 3),        # interleaved rows
            False: coo.reshape(3, n).T,     # planar
        }
        self.interleaved = self._pick_coords_layout(cand)
        self.nodes = np.ascontiguousarray(cand[self.interleaved])

        mai = it.get("MAI")
        tet_conn, npery = None, 0
        if mai is not None:
            for typ in ("T10", "TE4"):
                if typ in mai:
                    nod = np.asarray(mai[typ]["NOD"], dtype=np.int64).ravel()
                    k = CELL_NODES[typ]
                    ne = nod.size // k
                    a = nod.reshape(ne, k)      # interleaved layout
                    bpl = nod.reshape(k, ne).T  # planar layout
                    tet_conn = self._pick_conn_layout(a, bpl)
                    npery = k
                    break
        if tet_conn is None:
            raise ValueError("MED file contains no tetrahedra (T10/TE4)")
        self.tets = tet_conn - 1  # MED is 1-based
        self.tet_npery = npery

    def _pick_coords_layout(self, cand) -> bool:
        if self.expect_bbox:
            bb = np.asarray(self.expect_bbox, dtype=float)
            lo, hi = bb[:3], bb[3:]
            tol = max(np.linalg.norm(hi - lo), 1.0) * 1e-3
            for key, xyz in cand.items():
                if (np.allclose(xyz.min(0), lo, atol=tol)
                        and np.allclose(xyz.max(0), hi, atol=tol)):
                    return key

        # No hint (or it didn't match). Reading planar data as interleaved
        # mixes x, y and z into every column, so all three columns end up with
        # near-identical value distributions — whereas a real model almost
        # always has different extents per axis. Score each candidate by how
        # distinguishable its columns are; the scrambled reading scores ~0.
        #
        # This matters: gmsh's MED writer stores coordinates PLANAR, and the
        # previous heuristic chose interleaved for it, which silently
        # scrambled every node position.
        def distinctness(xyz):
            spans = xyz.max(0) - xyz.min(0)
            if not np.all(np.isfinite(spans)) or spans.max() <= 0:
                return -1.0
            means = xyz.mean(0)
            ref = max(spans.max(), 1e-30)
            return float(np.std(spans / ref) + np.std(means / ref))

        scores = {k: distinctness(v) for k, v in cand.items()}
        best = max(scores, key=scores.get)
        if scores[best] > 1e-6:
            return best
        # Genuinely ambiguous (e.g. a perfect cube): fall back to the layout
        # whose coordinates vary least between consecutive entries, which is
        # how contiguous per-component storage reads.
        tv = {k: np.abs(np.diff(v[:, 0])).sum() for k, v in cand.items()}
        return min(tv, key=tv.get)

    def _pick_conn_layout(self, a, b):
        def total_volume(conn):
            c = conn - 1
            if c.min() < 0 or c.max() >= len(self.nodes):
                return -1.0
            p = self.nodes[c[:, :4]]
            v = np.einsum("ij,ij->i",
                          np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]),
                          p[:, 3] - p[:, 0]) / 6.0
            return float(np.abs(v).sum())

        va, vb = total_volume(a), total_volume(b)
        if self.expect_volume and self.expect_volume > 0:
            da = abs(va - self.expect_volume)
            db = abs(vb - self.expect_volume)
            return a if da <= db else b
        # wrong layout scrambles nodes -> vastly inflated total volume
        return a if 0 < va <= vb or vb < 0 else b

    # ---------------- fields ----------------
    def list_fields(self) -> list:
        out = []
        cha = self.f.get("CHA")
        if cha is None:
            return out
        for fname in cha:
            fg = cha[fname]
            ncomp = int(fg.attrs.get("NCO", 1))
            nom = fg.attrs.get("NOM", b"")
            if isinstance(nom, bytes):
                nom = nom.decode("ascii", "replace")
            comps = [nom[i * 16:(i + 1) * 16].strip() for i in range(ncomp)] or ["X1"]
            steps = []
            for sk in fg:
                sg = fg[sk]
                if not isinstance(sg, h5py.Group):
                    continue
                ndt = int(sg.attrs.get("NDT", len(steps) + 1))
                pdt = float(sg.attrs.get("PDT", 0.0))
                steps.append({"key": sk, "ndt": ndt, "value": pdt})
            steps.sort(key=lambda s: s["ndt"])
            out.append({"name": fname, "comps": comps, "steps": steps})
        return out

    def read_field(self, fname: str, step_key: str) -> np.ndarray:
        """Return (n_nodes, ncomp) float array for a nodal field step."""
        fg = self.f["CHA"][fname]
        sg = fg[step_key]
        noe = sg.get("NOE")
        if noe is None:
            raise ValueError(f"field {fname}: not a nodal field")
        prof = list(noe.keys())[0]
        pg = noe[prof]
        vals = np.asarray(pg["CO"], dtype=np.float64).ravel()
        ncomp = int(fg.attrs.get("NCO", 1))
        n = vals.size // ncomp
        if "NO_PROFILE" not in prof and n != len(self.nodes):
            raise ValueError(f"field {fname} uses a partial profile ({prof}); unsupported")
        if self.interleaved:
            return vals.reshape(n, ncomp)
        return vals.reshape(ncomp, n).T

    def _orient_outward(self, boundary: np.ndarray, opposite: np.ndarray) -> np.ndarray:
        """Wind every boundary face so its normal points out of the solid.

        Nothing guarantees this from the element connectivity: the four faces
        of a tet, listed by any fixed corner ordering, come out with mixed
        handedness. Renderers then average per-node normals that partly cancel,
        which shows up as speckle across what should be one flat contour band,
        and any front/back-face test flickers triangle to triangle. Cheap to
        fix once here; impossible to fix in a shader.
        """
        p = self.nodes
        a, b, c = p[boundary[:, 0]], p[boundary[:, 1]], p[boundary[:, 2]]
        n = np.cross(b - a, c - a)
        # inward-pointing if the normal leans towards the opposite corner
        flip = np.einsum("ij,ij->i", n, p[opposite] - a) > 0
        if not flip.any():
            return boundary
        out = boundary.copy()
        # reversing (c1,c2,c3) -> (c1,c3,c2) also swaps mid-sides 1 and 3;
        # mid-side 2 is on edge c2-c3 either way
        out[flip, 1], out[flip, 2] = boundary[flip, 2], boundary[flip, 1]
        if boundary.shape[1] == 6:
            out[flip, 3], out[flip, 5] = boundary[flip, 5], boundary[flip, 3]
        return out

    # ---------------- skin ----------------
    def skin(self):
        """Boundary faces of the tet mesh.

        Returns (tri, used_nodes, remap, edges).

        `tri` is sub-triangulated for shading; `edges` are the ELEMENT FACE
        outlines. Those differ: drawing the sub-triangulation as a wireframe
        shows internal splits that are not element boundaries, which is not
        what a mesh overlay should show. For tet10 the outline follows the
        mid-side nodes (c1-m1-c2-m2-c3-m3) so curved edges stay curved.
        """
        t = self.tets
        if self.tet_npery == 10:
            faces = np.concatenate([t[:, f] for f in TET10_FACES])  # (4E, 6)
            corner = faces[:, :3]
        else:
            faces = np.concatenate([t[:, f] for f in TET4_FACES])   # (4E, 3)
            corner = faces
        # the tet corner NOT on each face — used below to orient it outward
        opposite = np.concatenate([t[:, k] for k in TET_OPPOSITE])
        key = np.sort(corner, axis=1)
        _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        keep = counts[inv] == 1
        boundary = self._orient_outward(faces[keep], opposite[keep])

        if self.tet_npery == 10:
            c1, c2, c3, m1, m2, m3 = (boundary[:, k] for k in range(6))
            tri = np.concatenate([
                np.stack([c1, m1, m3], 1), np.stack([m1, c2, m2], 1),
                np.stack([m3, m2, c3], 1), np.stack([m1, m2, m3], 1)])
        else:
            tri = boundary

        if self.tet_npery == 10:
            c1, c2, c3, m1, m2, m3 = (boundary[:, k] for k in range(6))
            ring = [c1, m1, c2, m2, c3, m3]
        else:
            ring = [boundary[:, 0], boundary[:, 1], boundary[:, 2]]
        seg = []
        for k in range(len(ring)):
            seg.append(np.stack([ring[k], ring[(k + 1) % len(ring)]], 1))
        edges = np.concatenate(seg)
        # one line per shared edge, not two
        edges = np.unique(np.sort(edges, axis=1), axis=0)

        used = np.unique(np.concatenate([tri.ravel(), edges.ravel()]))
        remap = np.full(len(self.nodes), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        return (remap[tri].astype(np.uint32), used, remap,
                remap[edges].astype(np.uint32))

    def close(self):
        self.f.close()
