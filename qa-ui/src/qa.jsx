// The QA tab: the three signals vlm_ocr.py records because the model emits no
// confidence of its own -- whether generation was cut off, how sure the
// decoder was about what it wrote, and whether any ink on the page fell
// outside every box it returned.
//
// None of these is ground truth. They are for ranking a review queue: a page
// with a red flag here is worth a human's eyes before one without.

// A token log-probability at or below this is drawn as fully unconfident. Real
// values run from 0 (certain) down to about -3 on these scans, so this sets the
// meter's floor, not a pass/fail line -- that is logprobs.threshold, chosen by
// the pipeline's --low-logprob-threshold.
const LOGPROB_FLOOR = -3;
// Elements holding this much ink but returning almost no text per unit of ink
// are the "box over a dense block that came back nearly empty" case.
const DENSE_INK_PX = 2000;
const SPARSE_CHARS_PER_1K = 2;
const WORST_ELEMENTS_SHOWN = 8;

export const FLAG_LABEL = {
  truncated: "truncated",
  uncovered_ink: "uncovered ink",
  low_confidence: "low confidence",
  decode_mismatch: "decode mismatch",
};

// Critical = content is provably missing or wrong. Warning = worth a look.
export const FLAG_SEVERITY = {
  truncated: "critical",
  uncovered_ink: "critical",
  low_confidence: "warn",
  decode_mismatch: "warn",
};

export const worstSeverity = (flags = []) =>
  flags.some((f) => FLAG_SEVERITY[f] === "critical")
    ? "critical"
    : flags.length
      ? "warn"
      : "ok";

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const pct = (v, digits = 1) =>
  v == null ? "–" : `${(v * 100).toFixed(digits)}%`;
const num = (v) => (v == null ? "–" : v.toLocaleString());

// 0 = as unconfident as the meter shows, 1 = certain.
const confidenceOf = (logprob) =>
  logprob == null ? null : clamp(1 - logprob / LOGPROB_FLOOR, 0, 1);

function Meter({ value, severity = "ok" }) {
  if (value == null) return null;
  return (
    <div className={`meter sev-${severity}`}>
      <span style={{ width: `${clamp(value * 100, 0, 100)}%` }} />
    </div>
  );
}

function StatTile({ label, value, sub, severity = "ok" }) {
  return (
    <div className={`tile sev-${severity}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

export function QaPanel({ entry, record }) {
  if (
    entry?.status === "classified_blank" ||
    entry?.status === "classified_keep"
  ) {
    return (
      <div className="notice pending">
        <h3>Not yet OCR'd</h3>
        <div>
          A <code>--classify-blank-only</code> calibration pass scored this
          page's ink stats but no model has run on it. There is nothing to score
          here yet — see the Markdown tab for the blank/keep verdict.
        </div>
      </div>
    );
  }
  if (!record) {
    return (
      <div className="notice pending">
        <h3>No QA signals</h3>
        <div>This page has no OCR output to measure.</div>
      </div>
    );
  }
  if (record.skipped) {
    return (
      <div className="notice skipped">
        <h3>Page skipped before OCR</h3>
        <div>
          The blank/bleed-through classifier caught this page, so no model ran
          and there is nothing to score. Its ink statistics are on the Markdown
          tab.
        </div>
      </div>
    );
  }

  const cov = record.ink_coverage;
  const lp = record.logprobs;
  // Content = tokens inside some element's text. The "all" scope additionally
  // contains the JSON scaffolding, above all bbox coordinates, which are far
  // less certain than the text and would otherwise dominate every statistic.
  const content = lp?.content ?? null;
  // Layout = bbox and category values: where the model put each region and what
  // it called it. Kept separate because it is routinely far less certain than
  // the text and usually harmlessly so.
  const layout = lp?.layout ?? null;
  const flags = entry?.flags ?? [];
  const truncated = record.finish_reason === "length";
  const uncovered = cov?.uncovered_regions ?? [];
  // Ink cut out as printed rules -- a masthead line, a column border -- before
  // anything was grouped or measured. Shown because a page where this is large
  // and `uncovered` is empty is one to spot check rather than trust. Absent on
  // records written before the filter existed.
  const rulePx = cov?.rule_px ?? null;

  // A run made before these fields existed has nothing to say -- "no flags"
  // would read as a clean bill of health it hasn't earned.
  const measured = Boolean(cov || lp || record.finish_reason);

  return (
    <div className="qa">
      {flags.length === 0 && measured && (
        <div className="notice ok">
          <h3>No flags on this page</h3>
          <div>
            Generation ended on its own, every region of ink landed inside a box
            or beside one, and no token scored below the confidence threshold.
            That is not proof it is correct — only that the three cheap checks
            found nothing.
          </div>
        </div>
      )}

      <div className="tiles">
        <StatTile
          label="Ink covered"
          value={pct(cov?.covered_fraction, 2)}
          sub={
            cov
              ? `${num(cov.uncovered_px)} px outside every box`
              : "not measured"
          }
          severity={uncovered.length ? "critical" : "ok"}
        />
        <StatTile
          label="Uncovered regions"
          value={cov ? uncovered.length : "–"}
          sub={
            uncovered.length
              ? "drawn in red on the overlay"
              : rulePx
                ? `none; ${num(rulePx)} px cut as printed rules`
                : "none above the size floor"
          }
          severity={uncovered.length ? "critical" : "ok"}
        />
        <StatTile
          label="Low-confidence tokens"
          value={content ? num(content.n_below_threshold) : "–"}
          sub={
            content
              ? `of ${num(content.n_tokens)} content tokens, below ${lp.threshold}`
              : lp
                ? "no text matched back to tokens"
                : "not recorded"
          }
          severity={content?.n_below_threshold > 0 ? "warn" : "ok"}
        />
        <StatTile
          label="Weakest layout token"
          value={layout ? layout.min : "–"}
          sub={
            layout
              ? `over ${num(layout.n_tokens)} bbox + category tokens`
              : "not measured"
          }
          severity="ok"
        />
        <StatTile
          label="Generation ended"
          value={record.finish_reason ?? "–"}
          sub={
            truncated
              ? "hit --max-tokens: output is cut off"
              : "model stopped on its own"
          }
          severity={truncated ? "critical" : "ok"}
        />
      </div>

      {truncated && (
        <div className="notice error">
          <h3>Output was truncated</h3>
          <div>
            The model ran into <code>--max-tokens</code> before it finished this
            page. Anything the JSON still parsed into is real, but the tail of
            the page is missing entirely. Re-run this page with a higher{" "}
            <code>--max-tokens</code>.
          </div>
        </div>
      )}

      <h4 className="sub">ink coverage</h4>
      {!cov ? (
        <p className="muted">
          Not measured — this run was made with <code>--no-ink-coverage</code>.
        </p>
      ) : (
        <>
          <Meter
            value={cov.covered_fraction}
            severity={uncovered.length ? "critical" : "ok"}
          />
          <p className="muted small">
            Share of the page's ink that falls inside some returned box, with
            printed rules left out of both sides of the fraction. Measured over
            the full page height and the sides' interior, so a header or footer
            counts in both the numerator and the denominator, while the binding
            and the scanner-bed edge stay out of both —{" "}
            {cov.roi &&
              `measured over ${cov.roi[0]},${cov.roi[1]}–${cov.roi[2]},${cov.roi[3]}`}
            .
          </p>
          {rulePx > 0 && (
            <p className="muted small">
              {num(rulePx)} px of ink was cut out as printed rules before this
              was measured — ink running continuously across a window with a
              stroke too thin to be type, which is a masthead line or a column
              border the model was right not to transcribe. It counts neither as
              returned nor as dropped. If this is large and no region is listed
              above, spot check the page rather than trust it.
            </p>
          )}
          {uncovered.length > 0 && (
            <table className="qa-table">
              <thead>
                <tr>
                  <th>Region</th>
                  <th>Ink px</th>
                  <th>Box (x1, y1, x2, y2)</th>
                </tr>
              </thead>
              <tbody>
                {uncovered.map((r, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td className="numeric">{num(r.ink_px)}</td>
                    <td className="numeric">{r.bbox.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      <h4 className="sub">token confidence — page text</h4>
      {!lp ? (
        <p className="muted">
          Not recorded — this run was made with <code>--no-token-logprobs</code>
          .
        </p>
      ) : !content ? (
        <p className="muted">
          No element text could be matched back to the tokens that produced it,
          so there is nothing to score at this scope.
        </p>
      ) : (
        <>
          <dl className="stats">
            <div>
              <dt>min</dt>
              <dd>{content.min}</dd>
            </div>
            <div>
              <dt>5th pct</dt>
              <dd>{content.p05}</dd>
            </div>
            <div>
              <dt>median</dt>
              <dd>{content.p50}</dd>
            </div>
            <div>
              <dt>mean</dt>
              <dd>{content.mean}</dd>
            </div>
          </dl>
          <p className="muted small">
            The decoder's own log-probability for each token it chose, over the{" "}
            {num(content.n_tokens)} tokens that make up the page's text. Not
            calibrated — the model is regularly confident and wrong, especially
            on a character it simply misread — but low values do cluster where
            it was guessing.
            {lp.attribution === "chunk" && (
              <>
                {" "}
                <strong>
                  Per-token offsets could not be reconstructed for this page, so
                  tokens were attributed by streamed chunk — the scope may
                  include some adjacent JSON structure.
                </strong>
              </>
            )}
          </p>
          {content.worst_tokens?.length > 0 && (
            <div className="chips">
              {content.worst_tokens.map((t, i) => (
                <span
                  key={i}
                  className="chip"
                  title={`token #${t.index} in the generated sequence, id ${t.token_id}, logprob ${t.logprob}`}
                >
                  <code>{t.text === "" ? "·" : t.text}</code>
                  <em>{t.logprob}</em>
                </span>
              ))}
            </div>
          )}
        </>
      )}

      <h4 className="sub">token confidence — layout detection</h4>
      {!layout ? (
        <p className="muted">
          No bbox or category values could be matched back to their tokens.
        </p>
      ) : (
        <>
          <dl className="stats">
            <div>
              <dt>min</dt>
              <dd>{layout.min}</dd>
            </div>
            <div>
              <dt>5th pct</dt>
              <dd>{layout.p05}</dd>
            </div>
            <div>
              <dt>median</dt>
              <dd>{layout.p50}</dd>
            </div>
            <div>
              <dt>mean</dt>
              <dd>{layout.mean}</dd>
            </div>
          </dl>
          <p className="muted small">
            The {num(layout.n_tokens)} tokens making up bbox coordinates and
            category names — where the model decided each region sits and what
            kind of region it is. Expect this to be worse than the text scope,
            and expect that to be fine: hesitating between a box edge at 1893
            and 1894 costs nothing. What is worth a look is an uncertain{" "}
            <em>category</em> (a Table read as Text loses its structure) or a
            coordinate the model was wildly unsure of, since a bad box can crop
            content out of the text it returns.
          </p>
          {layout.worst_tokens?.length > 0 && (
            <div className="chips">
              {layout.worst_tokens.map((t, i) => (
                <span
                  key={i}
                  className="chip"
                  title={`token #${t.index} in the generated sequence, id ${t.token_id}, logprob ${t.logprob}`}
                >
                  <code>{t.text === "" ? "·" : t.text}</code>
                  <em>{t.logprob}</em>
                </span>
              ))}
            </div>
          )}
        </>
      )}

      {lp?.all && (
        <p className="muted small">
          Across <strong>all {num(lp.all.n_tokens)} generated tokens</strong> —
          JSON punctuation and key names included — min is {lp.all.min}, median{" "}
          {lp.all.p50}, {num(lp.all.n_below_threshold)} below {lp.threshold}.
        </p>
      )}

      <ElementTable
        elements={record.elements ?? []}
        threshold={lp?.threshold}
      />
    </div>
  );
}

// Worst-first ranking of elements by one of their per-field confidence stats.
function rankedByField(elements, field) {
  return elements
    .map((el, i) => ({ el, i, min: el.logprob_stats?.[field]?.min ?? null }))
    .filter((row) => row.min != null)
    .sort((a, b) => a.min - b.min)
    .slice(0, WORST_ELEMENTS_SHOWN);
}

function ConfidenceRows({ rows, threshold }) {
  return rows.map(({ el, i, min }) => {
    const severity =
      threshold != null && min <= threshold
        ? "critical"
        : min < -0.5
          ? "warn"
          : "ok";
    return (
      <tr key={i}>
        <td>{el.category ?? "Text"}</td>
        <td className="snippet">
          {(el.text ?? "").slice(0, 60) || <em>—</em>}
        </td>
        <td className="numeric">{min}</td>
        <td className="meter-col">
          <Meter value={confidenceOf(min)} severity={severity} />
        </td>
      </tr>
    );
  });
}

function ElementTable({ elements, threshold }) {
  const scored = rankedByField(elements, "text");
  // Ranked on whichever of the two layout fields the model was less sure of.
  const layoutRows = elements
    .map((el, i) => {
      const stats = el.logprob_stats ?? {};
      const mins = [stats.bbox?.min, stats.category?.min].filter(
        (v) => v != null,
      );
      return { el, i, min: mins.length ? Math.min(...mins) : null };
    })
    .filter((row) => row.min != null)
    .sort((a, b) => a.min - b.min)
    .slice(0, WORST_ELEMENTS_SHOWN);

  const sparse = elements.filter(
    (el) =>
      el.ink &&
      el.ink.ink_px >= DENSE_INK_PX &&
      el.ink.chars_per_1k_ink != null &&
      el.ink.chars_per_1k_ink < SPARSE_CHARS_PER_1K,
  );

  if (scored.length === 0 && layoutRows.length === 0 && sparse.length === 0)
    return null;

  return (
    <>
      {sparse.length > 0 && (
        <>
          <h4 className="sub">boxes with ink but little text</h4>
          <p className="muted small">
            These boxes sit over a lot of ink and returned very few characters
            for it — the shape of a region the model saw but did not read.
          </p>
          <ul className="plain">
            {sparse.map((el, i) => (
              <li key={i}>
                <span className="badge status-pending">
                  {el.category ?? "Text"}
                </span>{" "}
                {num(el.ink.ink_px)} ink px, {el.ink.chars} chars (
                {el.ink.chars_per_1k_ink} per 1k ink)
              </li>
            ))}
          </ul>
        </>
      )}

      {scored.length > 0 && (
        <>
          <h4 className="sub">least-confident elements — text</h4>
          <table className="qa-table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Text</th>
                <th>Min</th>
                <th className="meter-col">Confidence</th>
              </tr>
            </thead>
            <tbody>
              <ConfidenceRows rows={scored} threshold={threshold} />
            </tbody>
          </table>
        </>
      )}

      {layoutRows.length > 0 && (
        <>
          <h4 className="sub">least-confident elements — layout</h4>
          <p className="muted small">
            Ranked on whichever the model was less sure of, the box or the
            category.
          </p>
          <table className="qa-table">
            <thead>
              <tr>
                <th>Category</th>
                <th>Text</th>
                <th>Min</th>
                <th className="meter-col">Confidence</th>
              </tr>
            </thead>
            <tbody>
              <ConfidenceRows rows={layoutRows} threshold={threshold} />
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
