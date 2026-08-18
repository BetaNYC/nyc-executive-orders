import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { pageMarkdown, renderMarkdown } from "./markdown.js";
import { FLAG_LABEL, FLAG_SEVERITY, QaPanel, worstSeverity } from "./qa.jsx";

const STATUS_LABEL = {
  ocr: "OCR",
  skipped: "skipped (blank)",
  pending: "not processed",
  parse_error: "parse error",
  unreadable: "bad JSON",
  classified_blank: "classified: blank",
  classified_keep: "classified: pending OCR",
};

const VOLUME_STATUS_LABEL = {
  "not-started": "not started",
  classified: "classified only",
  ocr: "OCR'd",
};

const pad = (n) => String(n).padStart(4, "0");

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
  return {
    volume: params.get("volume") || null,
    page: Number.isInteger(page) && page > 0 ? page : null,
  };
}

function writeHash(volume, page) {
  // Nothing selected yet -- leave the incoming hash alone, the volume's page
  // index is still loading and will land on the page it names.
  if (!volume || !page) return;
  const next = `#volume=${encodeURIComponent(volume)}&page=${page}`;
  if (next !== window.location.hash)
    window.history.replaceState(null, "", next);
}

const BASE = import.meta.env.BASE_URL;
const volumesUrl = () => `${BASE}api/volumes`;
const volumeUrl = (volume) =>
  `${BASE}api/volumes/${encodeURIComponent(volume)}`;
const pageUrl = (volume, page) =>
  `${BASE}api/volumes/${encodeURIComponent(volume)}/pages/${page}`;
const imgUrl = (volume, kind, page) =>
  `${BASE}files/${encodeURIComponent(volume)}/${kind}/page_${pad(page)}.png`;

async function getJson(url) {
  const res = await fetch(url);
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

export default function App() {
  const [volumes, setVolumes] = useState([]);
  const [volume, setVolume] = useState(() => readHash().volume);
  const [index, setIndex] = useState(null); // { stem, pages: [...] }
  const [page, setPage] = useState(null);
  const [record, setRecord] = useState(null);
  const [imageKind, setImageKind] = useState("overlays");
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
    getJson(volumesUrl())
      .then(({ volumes }) => {
        setVolumes(volumes);
        setVolume((current) =>
          current && volumes.some((v) => v.stem === current)
            ? current
            : (volumes[0]?.stem ?? null),
        );
      })
      .catch((err) => setError(String(err.message)));
  }, []);

  // Volume changed -> load its page index, and land on the hash's page if it exists.
  useEffect(() => {
    if (!volume) return;
    let cancelled = false;
    setIndex(null);
    setRecord(null);
    getJson(volumeUrl(volume))
      .then((idx) => {
        if (cancelled) return;
        setIndex(idx);
        const wanted = readHash().volume === volume ? readHash().page : null;
        const exists = idx.pages.some((p) => p.page === wanted);
        setPage(exists ? wanted : (idx.pages[0]?.page ?? null));
        setError(null);
      })
      .catch((err) => !cancelled && setError(String(err.message)));
    return () => {
      cancelled = true;
    };
  }, [volume]);

  const pages = index?.pages ?? [];
  const entry = useMemo(
    () => pages.find((p) => p.page === page) ?? null,
    [pages, page],
  );

  // Page changed -> load its JSON record (pages with no real OCR output have none).
  useEffect(() => {
    if (!volume || !page || !entry) return;
    if (!entry.hasJson) {
      setRecord(null);
      return;
    }
    let cancelled = false;
    getJson(pageUrl(volume, page))
      .then((rec) => !cancelled && setRecord(rec))
      .catch(
        (err) => !cancelled && setRecord({ _readError: String(err.message) }),
      );
    return () => {
      cancelled = true;
    };
  }, [volume, page, entry]);

  useEffect(() => writeHash(volume, page), [volume, page]);

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

  const hasImage =
    entry && (imageKind === "overlays" ? entry.hasOverlay : entry.hasRaw);
  // Overlays are only written for OCR'd pages; fall back so there's always a page to look at.
  const shownKind = hasImage
    ? imageKind
    : entry?.hasRaw
      ? "raw"
      : entry?.hasOverlay
        ? "overlays"
        : null;
  const imageUrl =
    volume && page && shownKind ? imgUrl(volume, shownKind, page) : null;
  const position = pages.findIndex((p) => p.page === page);

  // New image -> forget any zoom/pan left over from the last one.
  useEffect(() => {
    setScale(1);
    setPan({ x: 0, y: 0 });
    pointersRef.current.clear();
    pinchRef.current = null;
    dragRef.current = null;
  }, [imageUrl]);

  // Trackpad pinch (and ctrl+wheel) arrive as wheel events with ctrlKey set.
  // preventDefault has to run on a non-passive listener, which React's
  // synthetic onWheel prop can't give us, so bind natively.
  useEffect(() => {
    const el = paneRef.current;
    if (!el) return;
    const onWheelNative = (e) => {
      if (!imageUrl) return;
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
  }, [imageUrl]);

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
      if (!el || !imageUrl) return;
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
    [imageUrl],
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">qa-ui</div>

        <label className="field">
          <span>Volume</span>
          <select
            value={volume ?? ""}
            onChange={(e) => setVolume(e.target.value)}
          >
            {volumes.length === 0 && <option value="">no volumes found</option>}
            {volumes.map((v) => (
              <option key={v.stem} value={v.stem}>
                {v.minYear}–{v.maxYear} ·{" "}
                {VOLUME_STATUS_LABEL[v.status] ?? v.status} · {v.ocrPageCount}{" "}
                ocr'd
                {v.classifiedPageCount
                  ? `, ${v.classifiedPageCount} classified`
                  : ""}
              </option>
            ))}
          </select>
        </label>

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

        <div className="toggle" role="group" aria-label="page image">
          <button
            className={shownKind === "overlays" ? "on" : ""}
            onClick={() => setImageKind("overlays")}
            disabled={!entry?.hasOverlay}
          >
            Overlay
          </button>
          <button
            className={shownKind === "raw" ? "on" : ""}
            onClick={() => setImageKind("raw")}
            disabled={!entry?.hasRaw}
          >
            Raw
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
          {imageUrl ? (
            <img
              key={imageUrl}
              src={imageUrl}
              alt={`page ${page} (${shownKind})`}
              className="fit"
              draggable={false}
              style={{
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})`,
              }}
            />
          ) : (
            <div className="empty">
              no page image — run <code>scripts/run_volume_ocr.py</code> locally
              to render pages
            </div>
          )}
          {entry && shownKind !== imageKind && (
            <div className="floating-note">
              no {imageKind === "overlays" ? "overlay" : "raw"} image for this
              page — showing {shownKind}
            </div>
          )}
        </main>

        <section className="pane text-pane">
          <PageHeader entry={entry} record={record} tab={tab} setTab={setTab} />
          <div className="text-scroll">
            <PageContent entry={entry} record={record} tab={tab} />
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

function PageHeader({ entry, record, tab, setTab }) {
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

function PageContent({ entry, record, tab }) {
  if (!entry) return <div className="empty">pick a volume</div>;

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
        This page was rendered but has no output under{" "}
        <code>sources/gpp/volumes/ocr/</code> — OCR hasn't run on this volume
        yet.
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
