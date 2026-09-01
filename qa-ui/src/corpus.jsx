// The Corpus tab: the text the published corpus carries for this document
// TODAY -- Tesseract's, for everything the VLM run has not replaced. This is
// what the VLM output is meant to be compared against, so it is shown verbatim,
// not rendered.
//
// One thing to keep straight: corpus/eo.json stores ONE string per EO, with no
// page boundaries in it, so this is the whole document while every other tab is
// the page on screen. The panel says so rather than letting a reader assume a
// page-for-page comparison.

import { useState } from "react";

const QUALITY_SEVERITY = {
  clean: "ok",
  "minor-noise": "warn",
  "needs-review": "critical",
  "no-text": "critical",
};

export function CorpusPanel({ corpus, page }) {
  const [showRaw, setShowRaw] = useState(false);
  const [wrap, setWrap] = useState(true);

  if (!corpus) return <div className="empty">loading the corpus text…</div>;
  if (corpus.error) return <div className="empty">{corpus.error}</div>;

  const raw = showRaw && corpus.textRaw ? corpus.textRaw : null;
  const text = raw ?? corpus.text;
  const quality = corpus.textQuality;

  return (
    <div className="corpus">
      <div className="corpus-head">
        <span className={`badge quality-${QUALITY_SEVERITY[quality] ?? "ok"}`}>
          {quality ?? "unrated"}
        </span>
        <span className="muted">
          text_source <code>{corpus.textSource ?? "–"}</code>
        </span>
        <span className="muted">{text.length.toLocaleString()} chars</span>
        <span className="muted">
          {`${corpus.pageCount ?? "?"} page${corpus.pageCount === 1 ? "" : "s"}`}
        </span>
        <div className="spacer" />
        {corpus.textRaw && (
          <div className="toggle">
            <button
              className={showRaw ? "" : "on"}
              onClick={() => setShowRaw(false)}
              title="full_text: what the corpus publishes"
            >
              Clean
            </button>
            <button
              className={showRaw ? "on" : ""}
              onClick={() => setShowRaw(true)}
              title="full_text_raw: before the header and mark cleanup"
            >
              Raw
            </button>
          </div>
        )}
        <button
          className={wrap ? "on" : ""}
          onClick={() => setWrap((w) => !w)}
          title="Wrap long lines"
        >
          Wrap
        </button>
      </div>

      <p className="corpus-note muted">
        The whole document, not page {page ?? "–"} — the corpus stores one text
        per order, with no page boundaries in it.
        {corpus.droppedHeader && " A microfilm header was dropped from it."}
        {corpus.droppedMarks?.length
          ? ` Dropped marks: ${corpus.droppedMarks.map((m) => `“${m}”`).join(", ")}.`
          : ""}
      </p>

      <pre className={`corpus-text${wrap ? " wrap" : ""}`}>{text}</pre>
    </div>
  );
}
