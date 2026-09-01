import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { pageMarkdown, renderMarkdown } from "./markdown.js";
import { FLAG_LABEL, FLAG_SEVERITY, QaPanel, worstSeverity } from "./qa.jsx";
import { PdfPage } from "./pdfPage.jsx";
import { CorpusPanel } from "./corpus.jsx";

const STATUS_LABEL = {
  ocr: "OCR",
  skipped: "skipped (blank)",
  pending: "not processed",
  parse_error: "parse error",
  unreadable: "bad JSON",
  classified_blank: "classified: blank",
  classified_keep: "classified: pending OCR",
};

const UNIT_STATUS_LABEL = {
  "not-started": "not started",
  classified: "classified only",
  incomplete: "incomplete",
  ocr: "OCR'd",
};

// A unit a human still has to look at: it carries page-level QA flags, it never
// ran, or it has fewer page records than the PDF has pages.
const needsReview = (u) =>
  u.flaggedPageCount > 0 ||
  u.status === "not-started" ||
  u.status === "incomplete";

// One line in the unit dropdown. A grouped collection (post-1974) names the
// document, an ungrouped one (the bound volumes) names its year span, because
// the volume stem is far too long to read in a select.
function unitOptionLabel(u) {
  const parts = [
    u.group ? u.id : `${u.minYear}–${u.maxYear}`,
    UNIT_STATUS_LABEL[u.status] ?? u.status,
  ];
  if (u.ocrPageCount) parts.push(`${u.ocrPageCount} pages`);
  else if (u.classifiedPageCount)
    parts.push(`${u.classifiedPageCount} classified`);
  if (u.flaggedPageCount) parts.push(`${u.flaggedPageCount} flagged`);
  // The page image is the source PDF, so a unit without one shows no page.
  if (!u.hasPdf) parts.push("no PDF");
  return parts.join(" · ");
}

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const mid = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
const centerOf = (el) => {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
};

// Rescale around a fixed screen point (pointer, pinch midpoint, ...) so that
// point stays visually still. `origin` is the point (in screen coords) the
// existing `pan` is anchored to, i.e. the image-pane's center.
function zoomAt({
  toScale,
  fromScale,
  fromPan,
  originX,
  originY,
  pointX,
  pointY,
}) {
  const scale = clamp(toScale, MIN_SCALE, MAX_SCALE);
  const f = scale / fromScale;
  const vx = pointX - originX;
  const vy = pointY - originY;
  return {
    scale,
    pan: { x: vx * (1 - f) + fromPan.x * f, y: vy * (1 - f) + fromPan.y * f },
  };
}

function readHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const page = Number(params.get("page"));
  // `volume=` is the old single-collection link shape -- it still resolves.
  const legacy = params.get("volume");
  return {
    collection: params.get("collection") || (legacy ? "pre1974" : null),
    unit: params.get("unit") || legacy || null,
    page: Number.isInteger(page) && page > 0 ? page : null,
  };
}

function writeHash(collection, unit, page) {
  // Nothing selected yet -- leave the incoming hash alone, the unit's page
  // index is still loading and will land on the page it names.
  if (!collection || !unit || !page) return;
  const next =
    `#collection=${encodeURIComponent(collection)}` +
    `&unit=${encodeURIComponent(unit)}&page=${page}`;
  if (next !== window.location.hash)
    window.history.replaceState(null, "", next);
}

const BASE = import.meta.env.BASE_URL;
const enc = encodeURIComponent;
const collectionsUrl = () => `${BASE}api/collections`;
const unitsUrl = (col) => `${BASE}api/collections/${enc(col)}/units`;
const unitUrl = (col, unit) =>
  `${BASE}api/collections/${enc(col)}/units/${enc(unit)}`;
const pageUrl = (col, unit, page) =>
  `${BASE}api/collections/${enc(col)}/units/${enc(unit)}/pages/${page}`;
const textUrl = (col, unit) =>
  `${BASE}api/collections/${enc(col)}/units/${enc(unit)}/text`;
const pdfUrlFor = (col, unit) => `${BASE}files/${enc(col)}/${enc(unit)}/pdf`;

async function getJson(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

export default function App() {
  const [collections, setCollections] = useState([]);
  const [collection, setCollection] = useState(() => readHash().collection);
  const [units, setUnits] = useState([]);
  // Which collection `units` belongs to -- null while a list is in flight, so
  // the page index never gets fetched with a unit from the previous set.
  const [unitsFor, setUnitsFor] = useState(null);
  const [unit, setUnit] = useState(() => readHash().unit);
  const [group, setGroup] = useState("all"); // year filter, grouped collections
  const [reviewOnly, setReviewOnly] = useState(false);
  const [index, setIndex] = useState(null); // { unit, pages: [...] }
  const [page, setPage] = useState(null);
  const [record, setRecord] = useState(null);
  const [corpus, setCorpus] = useState(null);
  const [showBoxes, setShowBoxes] = useState(true);
  const [tab, setTab] = useState("markdown");
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [error, setError] = useState(null);
  const railRef = useRef(null);
  const paneRef = useRef(null);
  const scaleRef = useRef(scale);
  const panRef = useRef(pan);
  scaleRef.current = scale;
  panRef.current = pan;
  const pointersRef = useRef(new Map());
  const pinchRef = useRef(null);
  const dragRef = useRef(null);

  useEffect(() => {
    getJson(collectionsUrl())
      .then(({ collections }) => {
        setCollections(collections);
        setCollection((current) =>
          current && collections.some((c) => c.id === current)
            ? current
            : (collections[0]?.id ?? null),
        );
      })
      .catch((err) => setError(String(err.message)));
  }, []);

  // Collection changed -> load its unit list, then land on the hash's unit.
  useEffect(() => {
    if (!collection) return;
    let cancelled = false;
    setUnits([]);
    setUnitsFor(null);
    setGroup("all");
    getJson(unitsUrl(collection))
      .then(({ units }) => {
        if (cancelled) return;
        setUnits(units);
        setUnitsFor(collection);
        const hash = readHash();
        const wanted = hash.collection === collection ? hash.unit : null;
        setUnit((current) => {
          for (const id of [wanted, current])
            if (id && units.some((u) => u.id === id)) return id;
          return units[0]?.id ?? null;
        });
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(String(err.message));
      });
    return () => {
      cancelled = true;
    };
  }, [collection]);

  // Unit changed -> load its page index, and land on the hash's page if it exists.
  useEffect(() => {
    if (!collection || !unit) return;
    if (unitsFor !== collection || !units.some((u) => u.id === unit)) return;
    let cancelled = false;
    setIndex(null);
    setRecord(null);
    getJson(unitUrl(collection, unit))
      .then((idx) => {
        if (cancelled) return;
        setIndex(idx);
        const hash = readHash();
        const wanted = hash.unit === unit ? hash.page : null;
        const exists = idx.pages.some((p) => p.page === wanted);
        setPage(exists ? wanted : (idx.pages[0]?.page ?? null));
        setError(null);
      })
      .catch((err) => !cancelled && setError(String(err.message)));
    return () => {
      cancelled = true;
    };
  }, [collection, unit, unitsFor, units]);

  const pages = index?.pages ?? [];
  const entry = useMemo(
    () => pages.find((p) => p.page === page) ?? null,
    [pages, page],
  );

  const collectionMeta = collections.find((c) => c.id === collection) ?? null;
  const unitMeta = units.find((u) => u.id === unit) ?? null;
  // 366 of the post-1974 records carry no title, so fall back to the date the
  // order was signed rather than repeating the id already in the dropdown.
  const unitCaption = unitMeta
    ? [
        unitMeta.title === unitMeta.id ? null : unitMeta.title,
        unitMeta.subtitle,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  // Page changed -> load its JSON record (pages with no real OCR output have none).
  useEffect(() => {
    if (!collection || !unit || !page || !entry) return;
    if (!entry.hasJson) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    getJson(pageUrl(collection, unit, page))
      .then((rec) => !cancelled && setRecord(rec))
      .catch(
        (err) => !cancelled && setRecord({ _readError: String(err.message) }),
      );
    return () => {
      cancelled = true;
    };
  }, [collection, unit, page, entry]);

  // Unit changed -> load its corpus text. A median document is 2 kB of it, so
  // this is not worth deferring until the tab is opened.
  useEffect(() => {
    setCorpus(null);
    if (!collection || !unit || !collectionMeta?.hasCorpusText) return;
    let cancelled = false;
    getJson(textUrl(collection, unit))
      .then((body) => !cancelled && setCorpus(body))
      .catch(
        (err) => !cancelled && setCorpus({ error: String(err.message) }),
      );
    return () => {
      cancelled = true;
    };
  }, [collection, unit, collectionMeta?.hasCorpusText]);

  // The Corpus tab only exists for a collection that has corpus text.
  useEffect(() => {
    if (tab === "corpus" && collectionMeta && !collectionMeta.hasCorpusText)
      setTab("markdown");
  }, [tab, collectionMeta]);

  useEffect(
    () => writeHash(collection, unit, page),
    [collection, unit, page],
  );

  const step = useCallback(
    (delta) => {
      const i = pages.findIndex((p) => p.page === page);
      const next = pages[i + delta];
      if (next) setPage(next.page);
    },
    [pages, page],
  );

  useEffect(() => {
    const onKey = (e) => {
      if (e.target.matches("input, select, textarea")) return;
      if (e.key === "ArrowRight" || e.key === "j") step(1);
      else if (e.key === "ArrowLeft" || e.key === "k") step(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  // Keep the selected page visible in the rail when stepping with the keyboard.
  useEffect(() => {
    railRef.current
      ?.querySelector('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [page]);

  // The record for the page on screen, and only that page: `record` still
  // holds the last page's until the new fetch lands, and one page's boxes drawn
  // over another page is exactly the lie this viewer must not tell.
  const pageRecord = record?.page === page ? record : null;

  // The page image is the source PDF, rendered in the browser -- see
  // src/pdfPage.jsx. Nothing here depends on a page PNG existing on disk.
  const pdfUrl =
    collection && unit && index?.pdfRel ? pdfUrlFor(collection, unit) : null;
  const position = pages.findIndex((p) => p.page === page);

  // New page -> forget any zoom/pan left over from the last one.
  useEffect(() => {
    setScale(1);
    setPan({ x: 0, y: 0 });
    pointersRef.current.clear();
    pinchRef.current = null;
    dragRef.current = null;
  }, [pdfUrl, page]);

  // Trackpad pinch (and ctrl+wheel) arrive as wheel events with ctrlKey set.
  // preventDefault has to run on a non-passive listener, which React's
  // synthetic onWheel prop can't give us, so bind natively.
  useEffect(() => {
    const el = paneRef.current;
    if (!el) return;
    const onWheelNative = (e) => {
      if (!pdfUrl) return;
      if (e.ctrlKey) {
        e.preventDefault();
        const origin = centerOf(el);
        const factor = Math.exp(-e.deltaY * 0.01);
        const { scale: nextScale, pan: nextPan } = zoomAt({
          toScale: scaleRef.current * factor,
          fromScale: scaleRef.current,
          fromPan: panRef.current,
          originX: origin.x,
          originY: origin.y,
          pointX: e.clientX,
          pointY: e.clientY,
        });
        setScale(nextScale);
        setPan(nextPan);
      } else if (scaleRef.current > 1) {
        e.preventDefault();
        setPan((p) => ({ x: p.x - e.deltaX, y: p.y - e.deltaY }));
      }
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, [pdfUrl]);

  const zoomButton = useCallback((dir) => {
    if (dir === "fit") {
      setScale(1);
      setPan({ x: 0, y: 0 });
      return;
    }
    const target = clamp(
      scaleRef.current + (dir === "in" ? 0.25 : -0.25),
      MIN_SCALE,
      MAX_SCALE,
    );
    const f = target / scaleRef.current;
    setScale(target);
    setPan((p) => ({ x: p.x * f, y: p.y * f }));
  }, []);

  // Touch pinch-zoom and single-finger/mouse drag-to-pan, via Pointer Events
  // so a phone/tablet pinch and a two-finger trackpad gesture both work.
  const onPointerDown = useCallback(
    (e) => {
      const el = paneRef.current;
      if (!el || !pdfUrl) return;
      el.setPointerCapture(e.pointerId);
      pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pointersRef.current.size === 2) {
        const pts = [...pointersRef.current.values()];
        pinchRef.current = {
          d0: dist(pts[0], pts[1]),
          mid0: mid(pts[0], pts[1]),
          s0: scaleRef.current,
          pan0: panRef.current,
        };
        dragRef.current = null;
      } else if (pointersRef.current.size === 1 && scaleRef.current > 1) {
        dragRef.current = {
          start: { x: e.clientX, y: e.clientY },
          panStart: panRef.current,
        };
      }
    },
    [pdfUrl],
  );

  const onPointerMove = useCallback((e) => {
    if (!pointersRef.current.has(e.pointerId)) return;
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2 && pinchRef.current) {
      const el = paneRef.current;
      const pts = [...pointersRef.current.values()];
      const d1 = dist(pts[0], pts[1]);
      const mid1 = mid(pts[0], pts[1]);
      const { d0, mid0, s0, pan0 } = pinchRef.current;
      const { scale: nextScale, pan: anchoredPan } = zoomAt({
        toScale: s0 * (d1 / d0),
        fromScale: s0,
        fromPan: pan0,
        originX: centerOf(el).x,
        originY: centerOf(el).y,
        pointX: mid0.x,
        pointY: mid0.y,
      });
      setScale(nextScale);
      setPan({
        x: anchoredPan.x + (mid1.x - mid0.x),
        y: anchoredPan.y + (mid1.y - mid0.y),
      });
    } else if (pointersRef.current.size === 1 && dragRef.current) {
      const { start, panStart } = dragRef.current;
      setPan({
        x: panStart.x + (e.clientX - start.x),
        y: panStart.y + (e.clientY - start.y),
      });
    }
  }, []);

  const endPointer = useCallback((e) => {
    pointersRef.current.delete(e.pointerId);
    pinchRef.current = null;
    if (pointersRef.current.size === 1) {
      const [, pt] = [...pointersRef.current.entries()][0];
      dragRef.current =
        scaleRef.current > 1 ? { start: pt, panStart: panRef.current } : null;
    } else {
      dragRef.current = null;
    }
  }, []);

  const groups = useMemo(
    () => [...new Set(units.map((u) => u.group).filter(Boolean))].sort(),
    [units],
  );
  const reviewCount = useMemo(() => units.filter(needsReview).length, [units]);
  const visibleUnits = useMemo(() => {
    const kept = units.filter(
      (u) =>
        (group === "all" || u.group === group) && (!reviewOnly || needsReview(u)),
    );
    // Whatever the filters say, the selected unit stays in its own list.
    return unitMeta && !kept.some((u) => u.id === unitMeta.id)
      ? [unitMeta, ...kept]
      : kept;
  }, [units, group, reviewOnly, unitMeta]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">qa-ui</div>

        <label className="field">
          <span>Set</span>
          <select
            value={collection ?? ""}
            onChange={(e) => setCollection(e.target.value)}
          >
            {collections.length === 0 && <option value="">loading…</option>}
            {collections.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
        </label>

        {groups.length > 1 && (
          <label className="field">
            <span>{collectionMeta?.groupNoun ?? "Group"}</span>
            <select value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="all">all ({units.length})</option>
              {groups.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
        )}

        <label className="field">
          <span>{collectionMeta?.unitNoun ?? "Unit"}</span>
          <select value={unit ?? ""} onChange={(e) => setUnit(e.target.value)}>
            {visibleUnits.length === 0 && (
              <option value="">
                {unitsFor === collection ? "none match the filters" : "loading…"}
              </option>
            )}
            {visibleUnits.map((u) => (
              <option key={u.id} value={u.id}>
                {unitOptionLabel(u)}
              </option>
            ))}
          </select>
        </label>

        <label className="field check" title="Flagged pages, or not fully OCR'd">
          <input
            type="checkbox"
            checked={reviewOnly}
            onChange={(e) => setReviewOnly(e.target.checked)}
          />
          <span>needs review ({reviewCount})</span>
        </label>

        {unitCaption && (
          <div className="unit-title muted" title={unitCaption}>
            {unitCaption}
          </div>
        )}

        <div className="nav">
          <button
            onClick={() => step(-1)}
            disabled={position <= 0}
            title="← or k"
          >
            ‹ Prev
          </button>
          <span className="counter">
            page <strong>{page ?? "–"}</strong>{" "}
            <span className="muted">of {pages.length}</span>
          </span>
          <button
            onClick={() => step(1)}
            disabled={position < 0 || position >= pages.length - 1}
            title="→ or j"
          >
            Next ›
          </button>
        </div>

        <div className="spacer" />

        <div className="toggle" role="group" aria-label="bbox overlay">
          <button
            className={showBoxes ? "on" : ""}
            onClick={() => setShowBoxes(true)}
            title="Draw the model's boxes over the page"
          >
            Boxes
          </button>
          <button
            className={showBoxes ? "" : "on"}
            onClick={() => setShowBoxes(false)}
            title="The page on its own"
          >
            Plain
          </button>
        </div>

        <div className="toggle" role="group" aria-label="zoom">
          <button
            onClick={() => zoomButton("out")}
            disabled={scale <= MIN_SCALE}
          >
            −
          </button>
          <button
            onClick={() => zoomButton("fit")}
            className={scale === 1 ? "on" : ""}
          >
            {scale === 1 ? "Fit" : `${Math.round(scale * 100)}%`}
          </button>
          <button
            onClick={() => zoomButton("in")}
            disabled={scale >= MAX_SCALE}
          >
            +
          </button>
        </div>
      </header>

      {error && <div className="error-bar">{error}</div>}

      <div className="body">
        <nav className="rail" ref={railRef}>
          {pages.map((p) => (
            <button
              key={p.page}
              data-selected={p.page === page}
              className={`rail-item status-${p.status} flag-${worstSeverity(p.flags)} ${
                p.page === page ? "selected" : ""
              }`}
              onClick={() => setPage(p.page)}
              title={[
                STATUS_LABEL[p.status],
                ...(p.flags ?? []).map((f) => FLAG_LABEL[f]),
              ].join(" · ")}
            >
              <span className="rail-page">{p.page}</span>
              <span className="dot" />
            </button>
          ))}
        </nav>

        <main
          className={`pane image-pane${scale > 1 ? " zoomed" : ""}`}
          ref={paneRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
        >
          {pdfUrl && page ? (
            <PdfPage
              url={pdfUrl}
              page={page}
              dpi={collectionMeta?.dpi ?? 200}
              rotationCw={pageRecord?.rotation?.applied_cw ?? 0}
              elements={pageRecord?.elements ?? []}
              uncovered={pageRecord?.ink_coverage?.uncovered_regions ?? []}
              showBoxes={showBoxes}
              transform={`translate(${pan.x}px, ${pan.y}px) scale(${scale})`}
            />
          ) : (
            <div className="empty">
              {index && !index.pdfRel
                ? "no source PDF is recorded for this unit"
                : "no page selected"}
            </div>
          )}
        </main>

        <section className="pane text-pane">
          <PageHeader
            entry={entry}
            record={record}
            tab={tab}
            setTab={setTab}
            hasCorpus={Boolean(collectionMeta?.hasCorpusText)}
          />
          <div className="text-scroll">
            <PageContent
              entry={entry}
              record={record}
              tab={tab}
              page={page}
              corpus={corpus}
              collectionMeta={collectionMeta}
              unitMeta={unitMeta}
            />
          </div>
        </section>
      </div>
    </div>
  );
}

const FLAG_TITLE = {
  truncated: "generation hit --max-tokens, so the tail of this page is missing",
  uncovered_ink: "ink on this page fell outside every box the model returned",
  low_confidence: "some tokens scored below the run's confidence threshold",
  decode_mismatch:
    "the streaming decode diverged from the full-sequence decode",
};

function PageHeader({ entry, record, tab, setTab, hasCorpus }) {
  if (!entry) return <div className="text-head" />;
  const flags = entry.flags ?? [];
  return (
    <div className="text-head">
      <span className={`badge status-${entry.status}`}>
        {STATUS_LABEL[entry.status]}
      </span>
      {entry.status === "ocr" && (
        <span className="muted">{entry.elementCount} elements</span>
      )}
      {flags.map((flag) => (
        <button
          key={flag}
          className={`badge flag-${FLAG_SEVERITY[flag]}`}
          title={`${FLAG_TITLE[flag]} — open the QA tab`}
          onClick={() => setTab("qa")}
        >
          {FLAG_LABEL[flag]}
        </button>
      ))}
      <div className="spacer" />
      <div className="toggle">
        <button
          className={tab === "markdown" ? "on" : ""}
          onClick={() => setTab("markdown")}
        >
          Markdown
        </button>
        <button
          className={tab === "qa" ? "on" : ""}
          onClick={() => setTab("qa")}
        >
          QA
        </button>
        {hasCorpus && (
          <button
            className={tab === "corpus" ? "on" : ""}
            onClick={() => setTab("corpus")}
            title="The text the corpus publishes today (Tesseract's), whole document"
          >
            Corpus
          </button>
        )}
        <button
          className={tab === "json" ? "on" : ""}
          onClick={() => setTab("json")}
        >
          JSON
        </button>
      </div>
    </div>
  );
}

function PageContent({
  entry,
  record,
  tab,
  page,
  corpus,
  collectionMeta,
  unitMeta,
}) {
  // The corpus text is per document, so it is worth reading even on a page the
  // OCR never reached -- it does not depend on `entry` the way the others do.
  if (tab === "corpus") return <CorpusPanel corpus={corpus} page={page} />;

  if (!entry) {
    // A unit that lists no pages at all: no page records, and no page count
    // to fall back on either.
    if (unitMeta && unitMeta.ocrPageCount === 0)
      return (
        <Notice kind="pending" title="No page records">
          Nothing under <code>{collectionMeta?.ocrRoot}</code> for{" "}
          <strong>{unitMeta.id}</strong>
          {unitMeta.expectedPages
            ? ` (${unitMeta.expectedPages} page(s) expected)`
            : ""}
          . The OCR never ran on it, or the run failed. See{" "}
          <code>post1974-run-summary.md</code>.
        </Notice>
      );
    return (
      <div className="empty">
        pick a {(collectionMeta?.unitNoun ?? "unit").toLowerCase()}
      </div>
    );
  }

  if (tab === "qa") return <QaPanel entry={entry} record={record} />;

  if (tab === "json") {
    if (
      entry.status === "classified_blank" ||
      entry.status === "classified_keep"
    ) {
      return (
        <pre className="json">{JSON.stringify(entry.classify, null, 2)}</pre>
      );
    }
    if (!record) return <div className="empty">no JSON for this page</div>;
    return <pre className="json">{JSON.stringify(record, null, 2)}</pre>;
  }

  if (
    entry.status === "classified_blank" ||
    entry.status === "classified_keep"
  ) {
    return (
      <Notice kind="pending" title="Classified, not yet OCR'd">
        <p>
          A <code>--classify-blank-only</code> calibration pass scored this page
          as{" "}
          <strong>
            {entry.classify.blank ? "blank/bleed-through" : "keep"}
          </strong>
          . No model has run on it yet.
        </p>
        {entry.classify.pageStats && <Stats stats={entry.classify.pageStats} />}
      </Notice>
    );
  }

  if (entry.status === "pending") {
    return (
      <Notice kind="pending" title="Not processed">
        <p>
          The page on the left is this page of the source PDF. It has no page
          record under <code>{collectionMeta?.ocrRoot}</code>, so the OCR never
          reached it or it failed.
        </p>
        {unitMeta?.ocrPageCount === 0 && (
          <p>
            No page of <strong>{unitMeta.id}</strong> was recorded. See{" "}
            <code>post1974-run-summary.md</code> for the documents the run lost
            and the command that re-runs them.
          </p>
        )}
      </Notice>
    );
  }

  if (record?._readError) {
    return (
      <Notice kind="error" title="Could not read page JSON">
        {record._readError}
      </Notice>
    );
  }

  if (record?.skipped) {
    return (
      <Notice kind="skipped" title="Page skipped before OCR">
        <p>
          The classifier flagged this page as <code>{record.skipped}</code>, so
          no model was run on it.
        </p>
        {record.page_stats && <Stats stats={record.page_stats} />}
      </Notice>
    );
  }

  if (record?.parse_error) {
    return (
      <>
        <Notice kind="error" title="Model output did not parse as JSON">
          {record.parse_error}
        </Notice>
        <h4 className="sub">raw model output</h4>
        <pre className="json">{record.raw_text}</pre>
      </>
    );
  }

  const elements = record?.elements ?? [];
  if (elements.length === 0)
    return (
      <Notice kind="pending" title="No elements">
        The model returned an empty layout for this page.
      </Notice>
    );

  return (
    <article
      className="markdown"
      dangerouslySetInnerHTML={{
        __html: renderMarkdown(pageMarkdown(elements)),
      }}
    />
  );
}

function Stats({ stats }) {
  return (
    <dl className="stats">
      {Object.entries(stats).map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{typeof v === "number" ? v : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

function Notice({ kind, title, children }) {
  return (
    <div className={`notice ${kind}`}>
      <h3>{title}</h3>
      <div>{children}</div>
    </div>
  );
}
