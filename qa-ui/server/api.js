// Connect-style middleware exposing the pre-1974 volumes to the browser.
// Mounted into the Vite dev server (see vite.config.js) so the whole app is
// one process with no proxy and no build step -- ported from an early
// prototype, pointed at the 14 canonical volumes and their committed output
// instead of arbitrary experiment run directories.
//
//   GET /api/volumes                        the 14 volumes from volumes.json, with status
//   GET /api/volumes/:volume                 that volume's page index (one entry per page)
//   GET /api/volumes/:volume/pages/:page     the page's JSON record (real OCR only)
//   GET /files/:volume/:kind/page_NNNN.png   raw or overlay page image (local render only)
//
// Two roots, deliberately kept apart:
//   ocrRoot     sources/gpp/volumes/ocr/<stem>/  committed page_XXXX.json +
//               classify_report.json -- durable, works from a fresh clone.
//   rendersRoot vlm-ocr-runs/<stem>/raw|overlays/  local scratch PNGs --
//               not committed, only present if you've run the render/OCR
//               step locally.
//
// ocrRoot is the ONLY place page records live: vlm_ocr writes them there per
// page, via --json-dir, as the OCR proceeds. There used to be a second copy
// under rendersRoot/<stem>/json/ that ocrRoot only caught up with when a volume
// finished, so this file read both and flagged run-dir-only pages as `live`.
// That copy is gone -- a page is either recorded or it isn't.

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

const PAGE_FILE = /^page_(\d{1,6})\.(png|json)$/;
const IMAGE_KINDS = new Set(["overlays", "raw"]);

// The QA flags a page can carry, worst first -- this order is what the rail and
// the header badges sort by. Computed here rather than in the browser so the
// page list and the page view can never disagree about what is wrong.
const FLAG_ORDER = [
  "truncated",
  "uncovered_ink",
  "low_confidence",
  "decode_mismatch",
];

function pageFlags(record) {
  const flags = [];
  if (record?.finish_reason === "length") flags.push("truncated");
  if (record?.ink_coverage?.uncovered_regions?.length)
    flags.push("uncovered_ink");
  if (record?.logprobs?.content?.n_below_threshold > 0)
    flags.push("low_confidence");
  if (record?.debug_decode_matches_stream === false)
    flags.push("decode_mismatch");
  return flags.sort((a, b) => FLAG_ORDER.indexOf(a) - FLAG_ORDER.indexOf(b));
}

async function listDir(dir) {
  try {
    return await fsp.readdir(dir, { withFileTypes: true });
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

// Page numbers present directly in `dir` (no subfolder), as a Set of ints.
async function pageNumbers(dir, ext) {
  const entries = await listDir(dir);
  const nums = new Set();
  for (const entry of entries) {
    if (!entry.isFile()) continue;
    const m = PAGE_FILE.exec(entry.name);
    if (m && m[2] === ext) nums.add(Number(m[1]));
  }
  return nums;
}

async function readJson(file) {
  try {
    return JSON.parse(await fsp.readFile(file, "utf8"));
  } catch (err) {
    if (err.code === "ENOENT") return null;
    if (err instanceof SyntaxError)
      return { _readError: `invalid JSON: ${err.message}` };
    throw err;
  }
}

// A volume stem must be a plain directory name -- no separators, no traversal.
function safeStem(name) {
  if (
    !name ||
    name.includes("/") ||
    name.includes("\\") ||
    name.startsWith(".")
  )
    return null;
  if (name === "." || name === "..") return null;
  return name;
}

function pageName(page, ext) {
  return `page_${String(page).padStart(4, "0")}.${ext}`;
}

// volumes.json's own filename scheme is `<start>_<end>_<Mayor(s)>_<kind>.pdf`
// (see sources/gpp/volumes/README.md) -- the stem is the directory
// scripts/run_volume_ocr.py has each run write its page records into.
function volumeStem(entry) {
  const rel = entry.local_paths?.[0];
  return rel ? path.basename(rel, path.extname(rel)) : null;
}

function yearsFromStem(stem) {
  const [start, end] = stem.split("_");
  return {
    minYear: start?.slice(0, 4) ?? null,
    maxYear: end?.slice(0, 4) ?? null,
  };
}

export function createApi({ ocrRoot, rendersRoot, volumesJson }) {
  async function readPage(stem, page) {
    const record = await readJson(path.join(ocrRoot, stem, pageName(page, "json")));
    return record ? { record } : null;
  }

  const json = (res, status, body) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify(body));
  };

  async function loadVolumes() {
    const raw = await readJson(volumesJson);
    return (raw?.volumes ?? [])
      .map((entry) => {
        const stem = volumeStem(entry);
        if (!stem) return null;
        return {
          stem,
          filename: path.basename(entry.local_paths[0]),
          description: entry.description,
          ...yearsFromStem(stem),
        };
      })
      .filter(Boolean);
  }

  async function listVolumes() {
    const volumes = await loadVolumes();
    const out = [];
    for (const v of volumes) {
      const ocrDir = path.join(ocrRoot, v.stem);
      const [recorded, classifyReport] = await Promise.all([
        pageNumbers(ocrDir, "json"),
        readJson(path.join(ocrDir, "classify_report.json")),
      ]);
      const ocrPageCount = recorded.size;
      const classifiedPageCount = classifyReport?.pages?.length ?? 0;
      const status =
        ocrPageCount > 0
          ? "ocr"
          : classifiedPageCount > 0
            ? "classified"
            : "not-started";
      out.push({
        ...v,
        ocrPageCount,
        classifiedPageCount,
        status,
      });
    }
    return out;
  }

  async function volumeIndex(stem) {
    const ocrDir = path.join(ocrRoot, stem);
    const renderDir = path.join(rendersRoot, stem);
    const [jsons, raws, overlays, classifyReport] = await Promise.all([
      pageNumbers(ocrDir, "json"),
      pageNumbers(path.join(renderDir, "raw"), "png"),
      pageNumbers(path.join(renderDir, "overlays"), "png"),
      readJson(path.join(ocrDir, "classify_report.json")),
    ]);
    if (
      jsons.size === 0 &&
      raws.size === 0 &&
      overlays.size === 0 &&
      !classifyReport?.pages?.length
    ) {
      return null;
    }
    const classifyByPage = new Map(
      (classifyReport?.pages ?? []).map((p) => [p.page, p]),
    );
    const numbers = [
      ...new Set([...raws, ...overlays, ...jsons, ...classifyByPage.keys()]),
    ].sort((a, b) => a - b);

    const pages = [];
    for (const page of numbers) {
      const entry = {
        page,
        hasOverlay: overlays.has(page),
        hasRaw: raws.has(page),
        hasJson: jsons.has(page),
        status: "pending",
        elementCount: 0,
      };
      if (entry.hasJson) {
        const found = await readPage(stem, page);
        const record = found?.record ?? null;
        entry.status = "ocr";
        if (record?._readError) {
          entry.status = "unreadable";
        } else if (record?.skipped) {
          entry.status = "skipped";
          entry.skipped = record.skipped;
          entry.pageStats = record.page_stats ?? null;
        } else if (record?.parse_error) {
          entry.status = "parse_error";
        }
        entry.elementCount = record?.elements?.length ?? 0;
        // Enough QA summary to rank the whole volume without fetching every page.
        entry.flags = pageFlags(record);
        entry.coverage = record?.ink_coverage?.covered_fraction ?? null;
        entry.uncoveredRegions =
          record?.ink_coverage?.uncovered_regions?.length ?? 0;
        entry.finishReason = record?.finish_reason ?? null;
        entry.minLogprob = record?.logprobs?.content?.min ?? null;
        entry.lowTokens = record?.logprobs?.content?.n_below_threshold ?? null;
        entry.layoutMinLogprob = record?.logprobs?.layout?.min ?? null;
      } else if (classifyByPage.has(page)) {
        // A classify-blank-only calibration pass scored this page, but it
        // hasn't gone through real OCR yet.
        const c = classifyByPage.get(page);
        entry.status = c.blank ? "classified_blank" : "classified_keep";
        entry.classify = { blank: c.blank, pageStats: c.page_stats };
      }
      pages.push(entry);
    }
    return { stem, pages };
  }

  return async function apiMiddleware(req, res, next) {
    const url = new URL(req.url, "http://localhost");
    const parts = url.pathname
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
    if (parts[0] !== "api" && parts[0] !== "files") return next();

    try {
      // GET /api/volumes
      if (parts[0] === "api" && parts[1] === "volumes" && parts.length === 2) {
        return json(res, 200, {
          volumes: await listVolumes(),
          ocrRoot,
          rendersRoot,
        });
      }

      // GET /api/volumes/:volume
      if (parts[0] === "api" && parts[1] === "volumes" && parts.length === 3) {
        const stem = safeStem(parts[2]);
        const index = stem && (await volumeIndex(stem));
        if (!index) return json(res, 404, { error: "no such volume" });
        return json(res, 200, index);
      }

      // GET /api/volumes/:volume/pages/:page
      if (
        parts[0] === "api" &&
        parts[1] === "volumes" &&
        parts[3] === "pages" &&
        parts.length === 5
      ) {
        const stem = safeStem(parts[2]);
        const page = Number(parts[4]);
        if (!stem || !Number.isInteger(page) || page < 1) {
          return json(res, 400, { error: "bad request" });
        }
        const found = await readPage(stem, page);
        if (!found)
          return json(res, 404, { error: "no OCR output for this page" });
        // The record itself is returned verbatim -- the JSON tab is meant to be
        // the file on disk.
        return json(res, 200, found.record);
      }

      // GET /files/:volume/:kind/page_NNNN.png
      if (parts[0] === "files" && parts.length === 4) {
        const stem = safeStem(parts[1]);
        const kind = parts[2];
        if (!stem || !IMAGE_KINDS.has(kind) || !PAGE_FILE.test(parts[3])) {
          return json(res, 400, { error: "bad request" });
        }
        const file = path.join(rendersRoot, stem, kind, parts[3]);
        if (!fs.existsSync(file))
          return json(res, 404, { error: "no such image" });
        res.setHeader("Content-Type", "image/png");
        res.setHeader("Cache-Control", "no-cache");
        return fs.createReadStream(file).pipe(res);
      }

      return json(res, 404, { error: "not found" });
    } catch (err) {
      return json(res, 500, { error: String(err?.message || err) });
    }
  };
}
