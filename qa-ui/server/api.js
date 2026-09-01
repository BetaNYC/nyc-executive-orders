// Connect-style middleware exposing the OCR output to the browser.
// Mounted into the Vite dev server (see vite.config.js) so the whole app is
// one process with no proxy and no build step.
//
// Two collections, because the project OCR'd its scans in two runs with two
// different directory layouts:
//
//   pre1974    the 14 bound volumes  sources/gpp/volumes/ocr/<stem>/page_XXXX.json
//   post1974   the 1,083 documents   sources/ocr/<year>/<eo-id>/page_XXXX.json
//
// A "unit" is one volume or one document. Everything below the unit -- the page
// index, the page record, the QA flags, the page image -- is identical for both,
// so only unit discovery is per collection.
//
//   GET /api/collections                                   both collections, with counts
//   GET /api/collections/:c/units                          that collection's units, with status
//   GET /api/collections/:c/units/:u                       that unit's page index
//   GET /api/collections/:c/units/:u/pages/:page           the page's JSON record
//   GET /api/collections/:c/units/:u/text                  the unit's CURRENT corpus text
//   GET /files/:c/:u/pdf                                   the unit's source PDF (Range-capable)
//
// The page image is the SOURCE PDF, rendered in the browser by pdf.js, not a
// PNG on disk. The runs write page PNGs to a scratch directory and prune most
// of them, so a PNG was there for some pages, in some clones, some of the time;
// the PDF is committed (git-LFS) and always there. Both are pixels off the same
// page, and the browser reproduces the run's own geometry -- `dpi` and the
// per-page `rotation.applied_cw` in the record -- so a bbox still lands where
// the model put it. See src/pdfPage.jsx.
//
// ocrRoot is the ONLY place page records live: vlm_ocr writes them there per
// page, via --json-dir, as the OCR proceeds. A page is either recorded or it
// isn't.

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

const PAGE_FILE = /^page_(\d{1,6})\.(png|json)$/;
const YEAR_DIR = /^\d{4}$/;

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

async function subdirs(dir) {
  return (await listDir(dir)).filter((e) => e.isDirectory()).map((e) => e.name);
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

// corpus/eo.json is 15 MB and static while the UI is open, but every page image
// is one request -- so parse it once and re-parse only when the file changes.
const recordCache = new Map();

async function readRecords(file) {
  let stat;
  try {
    stat = await fsp.stat(file);
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
  const key = `${stat.mtimeMs}:${stat.size}`;
  const hit = recordCache.get(file);
  if (hit?.key === key) return hit.records;
  const parsed = await readJson(file);
  const records = Array.isArray(parsed) ? parsed : (parsed?.records ?? []);
  recordCache.set(file, { key, records });
  return records;
}

// A path segment must be a plain name -- no separators, no traversal.
function safeSegment(name) {
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

// --------------------------------------------------------------------------- //
// Unit discovery -- the only part that differs between the two collections      //
// --------------------------------------------------------------------------- //

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

// The 14 bound volumes, in volumes.json order. One flat directory per volume.
async function volumeUnits(col) {
  const raw = await readJson(col.volumesJson);
  return (raw?.volumes ?? [])
    .map((entry) => {
      const stem = volumeStem(entry);
      if (!stem) return null;
      const { minYear, maxYear } = yearsFromStem(stem);
      return {
        id: stem,
        rel: stem,
        pdfRel: entry.local_paths?.[0] ?? null,
        group: null,
        title: entry.description ?? path.basename(entry.local_paths[0]),
        subtitle: `${minYear}–${maxYear}`,
        minYear,
        maxYear,
        expectedPages: null,
      };
    })
    .filter(Boolean);
}

// The post-1974 documents: one directory per EO, under its year. The worklist
// is corpus/eo.json -- the same file run_post1974_ocr.py takes its candidates
// from -- so a scanned document that never got a page record is still listed
// (it reads as `not started`) rather than being silently absent. Any directory
// on disk with no matching record is listed too: a directory is never invisible.
async function documentUnits(col) {
  const records = await readRecords(col.recordsJson);
  const byId = new Map();

  for (const r of records) {
    if (!r?.eo_id || !r?.year) continue;
    // Born-digital documents have a PDF text layer and were never OCR'd, so
    // they are not part of this review surface.
    if (r.text_source !== "ocr" && r.text_source !== "ocr-skipped") continue;
    byId.set(r.eo_id, {
      id: r.eo_id,
      rel: path.posix.join(String(r.year), r.eo_id),
      pdfRel: r.pdf_path ?? null,
      group: String(r.year),
      title: r.title ?? r.eo_id,
      subtitle: r.date_signed ?? String(r.year),
      minYear: String(r.year),
      maxYear: String(r.year),
      expectedPages: r.page_count ?? null,
    });
  }

  for (const year of await subdirs(col.ocrRoot)) {
    if (!YEAR_DIR.test(year)) continue;
    for (const id of await subdirs(path.join(col.ocrRoot, year))) {
      if (byId.has(id)) continue;
      byId.set(id, {
        id,
        rel: path.posix.join(year, id),
        pdfRel: null,
        group: year,
        title: id,
        subtitle: year,
        minYear: year,
        maxYear: year,
        expectedPages: null,
      });
    }
  }

  return [...byId.values()].sort((a, b) =>
    a.rel.localeCompare(b.rel, "en", { numeric: true }),
  );
}

export function createApi({ collections, repoRoot }) {
  const byId = new Map(collections.map((c) => [c.id, c]));

  const unitsOf = (col) =>
    col.kind === "volumes" ? volumeUnits(col) : documentUnits(col);

  async function findUnit(col, id) {
    if (!safeSegment(id)) return null;
    return (await unitsOf(col)).find((u) => u.id === id) ?? null;
  }

  const ocrDir = (col, unit) => path.join(col.ocrRoot, unit.rel);

  // volumes.json and corpus/eo.json are committed data, not user input, but
  // they still name the file this server hands out -- so keep the result inside
  // the repo rather than trusting the path.
  function pdfFile(unit) {
    if (!unit.pdfRel) return null;
    const file = path.resolve(repoRoot, unit.pdfRel);
    return file.startsWith(repoRoot + path.sep) ? file : null;
  }

  // The text the published corpus carries for this unit today -- Tesseract's,
  // for everything the VLM run has not replaced. Whole-document: corpus/eo.json
  // stores one string per EO with no page boundaries in it, so this cannot be
  // narrowed to the page on screen, and the UI says so rather than implying a
  // page-for-page comparison it cannot make.
  async function corpusText(col, unit) {
    if (!col.recordsJson) return null;
    const records = await readRecords(col.recordsJson);
    const record = records.find((r) => r?.eo_id === unit.id);
    if (!record) return null;
    const text = record.full_text ?? "";
    const raw = record.full_text_raw ?? "";
    return {
      unit: unit.id,
      textSource: record.text_source ?? null,
      textQuality: record.text_quality ?? null,
      pageCount: record.page_count ?? null,
      droppedHeader: Boolean(record.dropped_header),
      droppedMarks: record.dropped_marks ?? [],
      text,
      // Only when the cleanup actually changed something: otherwise the raw
      // toggle offers a second copy of the same string.
      textRaw: raw && raw !== text ? raw : null,
    };
  }

  async function readPage(col, unit, page) {
    const record = await readJson(
      path.join(ocrDir(col, unit), pageName(page, "json")),
    );
    return record ? { record } : null;
  }

  const json = (res, status, body) => {
    res.statusCode = status;
    res.setHeader("Content-Type", "application/json; charset=utf-8");
    res.setHeader("Cache-Control", "no-store");
    res.end(JSON.stringify(body));
  };

  // One unit's QA roll-up. Reads every page record it has: 1,794 records is
  // ~0.3 s for the whole post-1974 collection, so this is computed live rather
  // than cached -- a run writing records right now stays visible on reload.
  async function unitSummary(col, unit) {
    const dir = ocrDir(col, unit);
    const [recorded, classifyReport] = await Promise.all([
      pageNumbers(dir, "json"),
      readJson(path.join(dir, "classify_report.json")),
    ]);
    const flagCounts = {};
    let flaggedPageCount = 0;
    let worstCoverage = null;
    for (const page of recorded) {
      const record = (await readPage(col, unit, page))?.record;
      const flags = pageFlags(record);
      if (flags.length) flaggedPageCount += 1;
      for (const flag of flags) flagCounts[flag] = (flagCounts[flag] ?? 0) + 1;
      const coverage = record?.ink_coverage?.covered_fraction;
      if (typeof coverage === "number")
        worstCoverage =
          worstCoverage === null ? coverage : Math.min(worstCoverage, coverage);
    }
    const ocrPageCount = recorded.size;
    const classifiedPageCount = classifyReport?.pages?.length ?? 0;
    const file = pdfFile(unit);
    const status =
      ocrPageCount === 0
        ? classifiedPageCount > 0
          ? "classified"
          : "not-started"
        : unit.expectedPages && ocrPageCount < unit.expectedPages
          ? "incomplete"
          : "ocr";
    return {
      ...unit,
      status,
      ocrPageCount,
      classifiedPageCount,
      flaggedPageCount,
      flagCounts,
      worstCoverage,
      hasPdf: Boolean(file) && fs.existsSync(file),
    };
  }

  // 1,086 units, each a handful of stat/read calls: run them in batches so the
  // list is one short wait rather than a thousand sequential round trips.
  async function listUnits(col) {
    const units = await unitsOf(col);
    const out = [];
    const batch = 32;
    for (let i = 0; i < units.length; i += batch) {
      out.push(
        ...(await Promise.all(
          units.slice(i, i + batch).map((u) => unitSummary(col, u)),
        )),
      );
    }
    return out;
  }

  async function unitIndex(col, unit) {
    const dir = ocrDir(col, unit);
    const [jsons, classifyReport] = await Promise.all([
      pageNumbers(dir, "json"),
      readJson(path.join(dir, "classify_report.json")),
    ]);
    const classifyByPage = new Map(
      (classifyReport?.pages ?? []).map((p) => [p.page, p]),
    );
    // A page the OCR never reached is still a page of the PDF, and the PDF is
    // what the viewer draws -- so a document that failed mid-run still lists
    // its pages, and you can look at the scan that beat it.
    const expected = [];
    for (let p = 1; p <= (unit.expectedPages ?? 0); p += 1) expected.push(p);
    const numbers = [
      ...new Set([...jsons, ...classifyByPage.keys(), ...expected]),
    ].sort((a, b) => a - b);

    const pages = [];
    for (const page of numbers) {
      const entry = {
        page,
        hasJson: jsons.has(page),
        status: "pending",
        elementCount: 0,
      };
      if (entry.hasJson) {
        const found = await readPage(col, unit, page);
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
        // Enough QA summary to rank the whole unit without fetching every page.
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
    return {
      collection: col.id,
      unit: unit.id,
      title: unit.title,
      expectedPages: unit.expectedPages,
      pdfRel: unit.pdfRel,
      pages,
    };
  }

  // Byte-range support, because pdf.js asks for the pieces of a PDF it needs
  // and a bound volume is 63 MB. Without it the browser downloads the whole
  // file to draw page 1.
  function servePdf(req, res, file) {
    const stat = fs.statSync(file);
    res.setHeader("Content-Type", "application/pdf");
    res.setHeader("Accept-Ranges", "bytes");
    res.setHeader("Cache-Control", "no-cache");
    const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? "");
    if (!range) {
      res.setHeader("Content-Length", stat.size);
      return fs.createReadStream(file).pipe(res);
    }
    let [, startText, endText] = range;
    let start = startText === "" ? null : Number(startText);
    let end = endText === "" ? null : Number(endText);
    if (start === null) {
      // A suffix range ("bytes=-500"): the last `end` bytes.
      start = Math.max(0, stat.size - (end ?? 0));
      end = stat.size - 1;
    } else if (end === null || end >= stat.size) {
      end = stat.size - 1;
    }
    if (start > end || start >= stat.size) {
      res.statusCode = 416;
      res.setHeader("Content-Range", `bytes */${stat.size}`);
      return res.end();
    }
    res.statusCode = 206;
    res.setHeader("Content-Range", `bytes ${start}-${end}/${stat.size}`);
    res.setHeader("Content-Length", end - start + 1);
    return fs.createReadStream(file, { start, end }).pipe(res);
  }

  return async function apiMiddleware(req, res, next) {
    const url = new URL(req.url, "http://localhost");
    const parts = url.pathname
      .split("/")
      .filter(Boolean)
      .map(decodeURIComponent);
    if (parts[0] !== "api" && parts[0] !== "files") return next();

    try {
      // GET /api/collections
      if (
        parts[0] === "api" &&
        parts[1] === "collections" &&
        parts.length === 2
      ) {
        return json(res, 200, {
          collections: collections.map((c) => ({
            id: c.id,
            label: c.label,
            unitNoun: c.unitNoun,
            grouped: c.kind === "documents",
            groupNoun: c.groupNoun ?? null,
            // The DPI the run rendered at. Every bbox in every page record of
            // this collection is relative to a raster of that resolution, so
            // the viewer draws the PDF at the same one.
            dpi: c.dpi,
            // Whether /text has anything to serve for this collection.
            hasCorpusText: Boolean(c.recordsJson),
            ocrRoot: c.ocrRoot,
          })),
        });
      }

      const col = parts[0] === "api" ? byId.get(parts[2]) : byId.get(parts[1]);
      if (!col) return json(res, 404, { error: "no such collection" });

      // GET /api/collections/:collection/units
      if (
        parts[0] === "api" &&
        parts[1] === "collections" &&
        parts[3] === "units" &&
        parts.length === 4
      ) {
        return json(res, 200, {
          collection: col.id,
          units: await listUnits(col),
        });
      }

      // GET /api/collections/:collection/units/:unit
      if (
        parts[0] === "api" &&
        parts[1] === "collections" &&
        parts[3] === "units" &&
        parts.length === 5
      ) {
        const unit = await findUnit(col, parts[4]);
        if (!unit) return json(res, 404, { error: "no such unit" });
        return json(res, 200, await unitIndex(col, unit));
      }

      // GET /api/collections/:collection/units/:unit/pages/:page
      if (
        parts[0] === "api" &&
        parts[1] === "collections" &&
        parts[3] === "units" &&
        parts[5] === "pages" &&
        parts.length === 7
      ) {
        const page = Number(parts[6]);
        if (!Number.isInteger(page) || page < 1)
          return json(res, 400, { error: "bad request" });
        const unit = await findUnit(col, parts[4]);
        if (!unit) return json(res, 404, { error: "no such unit" });
        const found = await readPage(col, unit, page);
        if (!found)
          return json(res, 404, { error: "no OCR output for this page" });
        // The record itself is returned verbatim -- the JSON tab is meant to be
        // the file on disk.
        return json(res, 200, found.record);
      }

      // GET /api/collections/:collection/units/:unit/text
      if (
        parts[0] === "api" &&
        parts[1] === "collections" &&
        parts[3] === "units" &&
        parts[5] === "text" &&
        parts.length === 6
      ) {
        const unit = await findUnit(col, parts[4]);
        if (!unit) return json(res, 404, { error: "no such unit" });
        const text = await corpusText(col, unit);
        if (!text)
          return json(res, 404, { error: "no corpus text for this unit" });
        return json(res, 200, text);
      }

      // GET /files/:collection/:unit/pdf
      if (parts[0] === "files" && parts[3] === "pdf" && parts.length === 4) {
        const unit = await findUnit(col, parts[2]);
        if (!unit) return json(res, 404, { error: "no such unit" });
        const file = pdfFile(unit);
        if (!file || !fs.existsSync(file))
          return json(res, 404, { error: "no PDF for this unit" });
        return servePdf(req, res, file);
      }

      return json(res, 404, { error: "not found" });
    } catch (err) {
      return json(res, 500, { error: String(err?.message || err) });
    }
  };
}
