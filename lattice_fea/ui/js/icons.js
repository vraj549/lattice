// Tree icons.
//
// A production pre-processor tells you what a tree row IS before you read its
// label — Ansys, Femap and HyperMesh all lean on the icon, not the text. These
// are 16px line glyphs on a single path each, stroked in currentColor so the
// row's colour class carries the meaning (violet = constraint, amber = load).

const NS = "http://www.w3.org/2000/svg";

const PATHS = {
  model: "M8 1.6 14.2 5v6L8 14.4 1.8 11V5z M1.8 5 8 8.4 14.2 5 M8 8.4v6",
  geometry: "M8 1.6 14.2 5v6L8 14.4 1.8 11V5z M1.8 5 8 8.4 14.2 5 M8 8.4v6",
  solid: "M8 2.4 13.4 5.4v5.2L8 13.6 2.6 10.6V5.4z",
  interface: "M2.4 6.4h7.2v7.2H2.4z M6.4 2.4h7.2v7.2H6.4z",
  connection: "M6.6 9.4 9.4 6.6 M9.2 5.2l1-1a2.7 2.7 0 0 1 3.6 3.6l-1 1 "
            + "M6.8 10.8l-1 1a2.7 2.7 0 0 1-3.6-3.6l1-1",
  bolt: "M5.4 2.4h5.2l1.2 2-1.2 2H5.4L4.2 4.4z M7.2 6.4v7.2 M6.2 9.2h3.6 M6.2 11.2h3.6",
  tie: "M2.6 8h10.8 M5.4 4.8v6.4 M10.6 4.8v6.4",
  probe: "M8 1.8v3.4 M8 10.8v3.4 M1.8 8h3.4 M10.8 8h3.4 "
       + "M8 5.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z",
  mesh: "M8 2.2 14 12.6H2z M5 7.4h6 M8 2.2v10.4 M6.4 10h3.2",
  analysis: "M2.6 2.6h10.8v10.8H2.6z M6.4 5.8 10.6 8l-4.2 2.2z",
  settings: "M2.4 5.2h11.2 M2.4 10.8h11.2 M6 3.6v3.2 M10.4 9.2v3.2",
  support: "M8 2.4v6.2 M8 8.6 4.4 13.2h7.2z M3.4 13.6h9.2",
  load: "M8 2.4v8.4 M4.8 7.6 8 10.8l3.2-3.2 M3.4 13.4h9.2",
  solution: "M2 13.6h12 M3.4 12.2V8.6 M8 12.2V3.4 M12.6 12.2v-4",
  contours: "M2 10.6c3-2.8 4 1 6-1s3-2.8 6-1 M2 6.6c3-2.8 4 1 6-1s3-2.8 6-1",
  modes: "M1.8 8c2.1-5.4 4.1 5.4 6.2 0s4.1-5.4 6.2 0",
  frf: "M2 13.2h12 M2.6 11.4h2.8L7 4.4l1.6 7h4.8",
  random: "M2 13.4h12 M2.4 10.4 4 7.6l1.2 3.6L7 5.6l1.8 4.4 1.6-2.8 1.6 3.2 1.6-1.6",
  reactions: "M8 2.6v10.8 M5.2 5.4 8 2.6l2.8 2.8 M5.2 10.6 8 13.4l2.8-2.8",
  warning: "M8 2.6 14.2 13.4H1.8z M8 6.4v3.4 M8 11.4h.01",
};

/** Icon element for a tree row. `cls` carries the colour role. */
export function icon(name, cls) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 16 16");
  svg.setAttribute("class", `ticon${cls ? " " + cls : ""}${name === "solid" ? " fill" : ""}`);
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(NS, "path");
  p.setAttribute("d", PATHS[name] || PATHS.solid);
  svg.append(p);
  return svg;
}

/** Icon name for a result item, so the Solution branch is scannable. */
export const RESULT_ICONS = {
  contours: "contours", modes: "modes", frf: "frf", random: "random",
  bolts: "bolt", reactions: "reactions", warnings: "warning",
};
