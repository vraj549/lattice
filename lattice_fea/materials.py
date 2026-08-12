"""Built-in material library.

UI-facing units: E in GPa, density in kg/m^3, yield/ultimate in MPa.
Solver units (mm-t-s): E in MPa (x1000), density in tonne/mm^3 (x1e-12).
"""

LIBRARY = [
    {"id": "steel-s235",   "name": "Structural steel S235",  "E_GPa": 210.0, "nu": 0.30, "rho_kgm3": 7850, "yield_MPa": 235},
    {"id": "steel-4140",   "name": "Alloy steel 4140",       "E_GPa": 205.0, "nu": 0.29, "rho_kgm3": 7850, "yield_MPa": 655},
    {"id": "ss-304",       "name": "Stainless 304",          "E_GPa": 193.0, "nu": 0.29, "rho_kgm3": 8000, "yield_MPa": 215},
    {"id": "ss-316",       "name": "Stainless 316",          "E_GPa": 193.0, "nu": 0.27, "rho_kgm3": 7990, "yield_MPa": 205},
    {"id": "al-6061-t6",   "name": "Aluminium 6061-T6",      "E_GPa": 68.9,  "nu": 0.33, "rho_kgm3": 2700, "yield_MPa": 276},
    {"id": "al-7075-t6",   "name": "Aluminium 7075-T6",      "E_GPa": 71.7,  "nu": 0.33, "rho_kgm3": 2810, "yield_MPa": 503},
    {"id": "ti-6al-4v",    "name": "Titanium Ti-6Al-4V",     "E_GPa": 113.8, "nu": 0.34, "rho_kgm3": 4430, "yield_MPa": 880},
    {"id": "brass-c360",   "name": "Brass C360",             "E_GPa": 97.0,  "nu": 0.31, "rho_kgm3": 8500, "yield_MPa": 124},
    {"id": "bronze-c932",  "name": "Bearing bronze C93200",  "E_GPa": 100.0, "nu": 0.34, "rho_kgm3": 8800, "yield_MPa": 125},
    {"id": "copper",       "name": "Copper (annealed)",      "E_GPa": 117.0, "nu": 0.34, "rho_kgm3": 8940, "yield_MPa": 70},
    {"id": "abs",          "name": "ABS plastic",            "E_GPa": 2.3,   "nu": 0.35, "rho_kgm3": 1060, "yield_MPa": 40},
    {"id": "peek",         "name": "PEEK",                   "E_GPa": 3.9,   "nu": 0.38, "rho_kgm3": 1320, "yield_MPa": 100},
    {"id": "pc",           "name": "Polycarbonate",          "E_GPa": 2.4,   "nu": 0.37, "rho_kgm3": 1200, "yield_MPa": 62},
]


# Metric coarse-thread bolts: nominal d (mm), tensile stress area As (mm^2).
# Suggested preload = 0.65 * As * 640 MPa (class 8.8 yield) — a starting point,
# not a substitute for a real joint calc.
BOLT_SIZES = [
    {"name": "M3",  "d": 3.0,  "As": 5.03},
    {"name": "M4",  "d": 4.0,  "As": 8.78},
    {"name": "M5",  "d": 5.0,  "As": 14.2},
    {"name": "M6",  "d": 6.0,  "As": 20.1},
    {"name": "M8",  "d": 8.0,  "As": 36.6},
    {"name": "M10", "d": 10.0, "As": 58.0},
    {"name": "M12", "d": 12.0, "As": 84.3},
    {"name": "M14", "d": 14.0, "As": 115.0},
    {"name": "M16", "d": 16.0, "As": 157.0},
    {"name": "M20", "d": 20.0, "As": 245.0},
    {"name": "M24", "d": 24.0, "As": 353.0},
]


def suggest_preload_N(As_mm2: float) -> float:
    return round(0.65 * As_mm2 * 640.0)


def to_solver_units(mat: dict) -> dict:
    """UI material -> code_aster (mm-t-s) values."""
    return {
        "E": float(mat["E_GPa"]) * 1000.0,          # GPa -> MPa
        "NU": float(mat["nu"]),
        "RHO": float(mat["rho_kgm3"]) * 1e-12,      # kg/m^3 -> tonne/mm^3
    }
