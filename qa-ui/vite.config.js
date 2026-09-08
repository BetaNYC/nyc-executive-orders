import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { createApi } from "./server/api.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "..");
const at = (rel) => path.resolve(repoRoot, rel);

// vlm_ocr.DEFAULT_DPI. Both runs rendered at it, and every bbox in every page
// record is relative to a raster of that resolution -- so the viewer draws the
// PDF at the same DPI and the boxes land where the model put them. Change this
// only if a collection was re-OCR'd at another --dpi.
const RUN_DPI = 200;

// Two OCR runs, two layouts. Each collection names the committed, durable data:
// ocrRoot holds the per-page JSON, and each unit's page image is its source PDF
// (from corpus/eo.json or volumes.json), rendered in the browser.
//
//   post1974   sources/ocr/<year>/<eo-id>/          the 1,083-document run
//   pre1974    sources/gpp/volumes/ocr/<stem>/      the 14 bound volumes
//
// Post-1974 first: it is the most recent run and the one with open follow-ups
// (see post1974-run-summary.md).
const collections = [
  {
    id: "post1974",
    kind: "documents",
    label: "Post-1974 documents",
    unitNoun: "Document",
    groupNoun: "Year",
    dpi: RUN_DPI,
    ocrRoot: at(process.env.POST1974_OCR_DIR || "sources/ocr"),
    // The worklist run_post1974_ocr.py itself selects from, and where each
    // document's `pdf_path` comes from -- so a scanned document with no page
    // record still appears, as `not started`, and still shows its scan.
    recordsJson: at(process.env.EO_RECORDS || "corpus/eo.json"),
  },
  {
    id: "pre1974",
    kind: "volumes",
    label: "Pre-1974 volumes",
    unitNoun: "Volume",
    dpi: RUN_DPI,
    ocrRoot: at(process.env.OCR_DIR || "sources/gpp/volumes/ocr"),
    volumesJson: at("sources/gpp/volumes.json"),
  },
];

// Serve the OCR data through the same server that serves the app, so
// `npm run dev` (or `npm run preview`) is the only process needed.
const ocrApi = () => ({
  name: "qa-ui-ocr-api",
  configureServer(server) {
    server.middlewares.use(createApi({ collections, repoRoot }));
  },
  configurePreviewServer(server) {
    server.middlewares.use(createApi({ collections, repoRoot }));
  },
});

export default defineConfig({
  base: process.env.VITE_BASE || "/",
  plugins: [react(), ocrApi()],
  server: { port: 5274, open: true },
});
