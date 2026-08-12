"""Generate example STEP files with gmsh OCC.

    python examples/make_examples.py

Creates:
  bracket.step           single L-bracket with fillet and two bolt holes
  bracket_assembly.step  the same bracket plus a bushing pressed into one hole
"""
import os

import gmsh

OUT = os.path.dirname(os.path.abspath(__file__))


def bracket(occ):
    """L-bracket, mm: 140 x 132 x 13, fillet at the inner corner, 2 holes."""
    leg_v = occ.addBox(0, 0, 0, 36, 132, 13)
    leg_h = occ.addBox(0, 96, 0, 140, 36, 13)
    out, _ = occ.fuse([(3, leg_v)], [(3, leg_h)])
    body = out[0][1]
    occ.synchronize()

    # fillet the inner corner edge (vertical edge at x=36, y=96)
    edges = []
    for dim, tag in gmsh.model.getBoundary([(3, body)], oriented=False, recursive=True):
        if dim != 1:
            continue
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(1, tag)
        if (abs(x0 - 36) < 1e-6 and abs(x1 - 36) < 1e-6
                and abs(y0 - 96) < 1e-6 and abs(y1 - 96) < 1e-6):
            edges.append(tag)
    if edges:
        out = occ.fillet([body], edges, [16])
        body = out[0][1]

    h1 = occ.addCylinder(18, 20, -1, 0, 0, 15, 7)
    h2 = occ.addCylinder(120, 114, -1, 0, 0, 15, 7)
    out, _ = occ.cut([(3, body)], [(3, h1), (3, h2)])
    return out[0][1]


def main():
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)

    # --- single part ---
    gmsh.model.add("bracket")
    occ = gmsh.model.occ
    bracket(occ)
    occ.synchronize()
    gmsh.write(os.path.join(OUT, "bracket.step"))

    # --- bolted joint: two plates, two through-holes for M6 bolts ---
    gmsh.model.add("bolted_plates")
    gmsh.model.setCurrent("bolted_plates")
    occ = gmsh.model.occ
    lower = occ.addBox(0, 0, 0, 90, 50, 8)
    upper = occ.addBox(0, 0, 8, 90, 50, 8)
    holes = []
    for hx in (25, 65):
        holes.append(occ.addCylinder(hx, 25, -1, 0, 0, 18, 3.3))  # ⌀6.6 clearance
    out, _ = occ.cut([(3, lower), (3, upper)], [(3, h) for h in holes])
    occ.synchronize()
    gmsh.write(os.path.join(OUT, "bolted_plates.step"))

    # --- assembly: bracket + bushing in the second hole ---
    gmsh.model.add("bracket_assembly")
    gmsh.model.setCurrent("bracket_assembly")
    occ = gmsh.model.occ
    bracket(occ)
    bushing_outer = occ.addCylinder(120, 114, 0, 0, 0, 13, 7)   # fills the hole
    bushing_bore = occ.addCylinder(120, 114, -1, 0, 0, 15, 4.5)
    out, _ = occ.cut([(3, bushing_outer)], [(3, bushing_bore)])
    occ.synchronize()
    gmsh.write(os.path.join(OUT, "bracket_assembly.step"))

    gmsh.finalize()
    print("wrote bracket.step and bracket_assembly.step")


if __name__ == "__main__":
    main()
