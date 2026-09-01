// The page image: the source PDF, drawn in the browser by pdf.js.
//
// The runs also write page PNGs, but into a scratch directory they prune as
// they go, so a PNG is there for some pages in some clones. The PDF is
// committed and always there, so this reproduces the run's own geometry from
// the record instead:
//
//   * scale = dpi/72, the DPI the run rendered at (vlm_ocr.DEFAULT_DPI = 200);
//   * rotation = the page's own /Rotate plus `rotation.applied_cw` from the
//     page record -- vlm_ocr renders pages ALREADY UPRIGHT, so two of the
//     fourteen volumes are turned 90 degrees before the model ever sees them,
//     and every bbox on those pages is relative to the upright raster;
//   * element bboxes are in the SMART-RESIZED image's pixel space (the model's
//     image processor rounds the input to multiples of 28px), so they are
//     rescaled here exactly as vlm_ocr.rescale_bbox does;
//   * ink_coverage.uncovered_regions are already in raster pixels.
//
// Get any of that wrong and the boxes drift off the text, which is the one
// thing this viewer exists to show.

import { useEffect, useRef, useState } from "react";
import * as pdfjsLib from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjsLib.GlobalWorkerOptions.workerSrc = workerUrl;

// vlm_ocr.CATEGORY_COLORS, so a box is the same colour here as in the overlay
// PNGs the pipeline writes and in anything else that reads those records.
const CATEGORY_COLORS = {
  Caption: "#a6761d",
  Footnote: "#7f7f7f",
  Formula: "#d62728",
  Handwriting: "#ff00ff",
  "List-item": "#2ca02c",
  "Page-footer": "#8c564b",
  "Page-header": "#8c564b",
  Picture: "#9467bd",
  "Section-header": "#ff7f0e",
  Table: "#17becf",
  Text: "#1f77b4",
  Title: "#e377c2",
};
const UNKNOWN_CATEGORY_COLOR = "#000000";
const UNCOVERED_INK_COLOR = "#ff0000";

// vlm_ocr.IMAGE_FACTOR / MIN_PIXELS / MAX_PIXELS -- dots.ocr's image processor
// (Qwen2-VL style) resizes its input and returns bboxes in THAT image's space.
const IMAGE_FACTOR = 28;
const MIN_PIXELS = 56 * 56;
const MAX_PIXELS = 11_289_600;

// Python's round() is half-to-even, and JS's Math.round is half-up. The two
// disagree exactly when a raster dimension is an odd multiple of 14, which is
// common enough to shift every box on such a page by a percent or two.
function roundHalfEven(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff > 0.5) return floor + 1;
  if (diff < 0.5) return floor;
  return floor % 2 === 0 ? floor : floor + 1;
}

// vlm_ocr.smart_resize, verbatim.
function smartResize(height, width) {
  const roundByFactor = (x) =>
    Math.max(IMAGE_FACTOR, roundHalfEven(x / IMAGE_FACTOR) * IMAGE_FACTOR);
  let hBar = roundByFactor(height);
  let wBar = roundByFactor(width);
  if (hBar * wBar > MAX_PIXELS) {
    const beta = Math.sqrt((height * width) / MAX_PIXELS);
    hBar = Math.max(
      IMAGE_FACTOR,
      Math.floor(height / beta / IMAGE_FACTOR) * IMAGE_FACTOR,
    );
    wBar = Math.max(
      IMAGE_FACTOR,
      Math.floor(width / beta / IMAGE_FACTOR) * IMAGE_FACTOR,
    );
  } else if (hBar * wBar < MIN_PIXELS) {
    const beta = Math.sqrt(MIN_PIXELS / (height * width));
    hBar = Math.ceil((height * beta) / IMAGE_FACTOR) * IMAGE_FACTOR;
    wBar = Math.ceil((width * beta) / IMAGE_FACTOR) * IMAGE_FACTOR;
  }
  return [hBar, wBar];
}

// One document is opened per unit and kept, because stepping through pages must
// not re-download it. Two at a time: the one on screen, and the one you just
// came from if you go back.
const docs = new Map();

function loadDoc(url) {
  let pending = docs.get(url);
  if (!pending) {
    pending = pdfjsLib.getDocument({ url }).promise;
    docs.set(url, pending);
    for (const [old, doc] of docs) {
      if (docs.size <= 2) break;
      docs.delete(old);
      doc.then((d) => d.destroy()).catch(() => {});
    }
  }
  return pending;
}

function boxPath(bbox) {
  const [x1, y1, x2, y2] = bbox;
  return {
    x: Math.min(x1, x2),
    y: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  };
}

export function PdfPage({
  url,
  page,
  dpi,
  rotationCw = 0,
  elements = [],
  uncovered = [],
  showBoxes = true,
  transform,
}) {
  const canvasRef = useRef(null);
  const [raster, setRaster] = useState(null);
  const [state, setState] = useState({ status: "loading", message: null });

  useEffect(() => {
    if (!url || !page) return;
    let cancelled = false;
    let task = null;
    setState({ status: "loading", message: null });
    loadDoc(url)
      .then((doc) => {
        if (page > doc.numPages)
          throw new Error(`the PDF has ${doc.numPages} page(s)`);
        return doc.getPage(page);
      })
      .then((pdfPage) => {
        if (cancelled) return null;
        const canvas = canvasRef.current;
        if (!canvas) return null;
        const viewport = pdfPage.getViewport({
          scale: dpi / 72,
          rotation: (pdfPage.rotate + rotationCw) % 360,
        });
        const width = Math.round(viewport.width);
        const height = Math.round(viewport.height);
        canvas.width = width;
        canvas.height = height;
        setRaster({ width, height });
        task = pdfPage.render({
          canvasContext: canvas.getContext("2d"),
          viewport,
        });
        return task.promise;
      })
      .then(() => !cancelled && setState({ status: "ready", message: null }))
      .catch((err) => {
        if (cancelled || err?.name === "RenderingCancelledException") return;
        const missing =
          err?.name === "MissingPDFException" || err?.status === 404;
        setState({
          status: "error",
          message: missing
            ? "no PDF for this unit"
            : String(err?.message || err),
        });
      });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [url, page, dpi, rotationCw]);

  // The model saw the smart-resized image, so its boxes are in that space.
  const scale =
    raster && elements.length
      ? (() => {
          const [resizedH, resizedW] = smartResize(raster.height, raster.width);
          return { x: raster.width / resizedW, y: raster.height / resizedH };
        })()
      : { x: 1, y: 1 };

  return (
    <>
    <div
      className={`page-stage${state.status === "ready" ? "" : " busy"}`}
      style={{ transform }}
    >
      <canvas ref={canvasRef} />
      {raster && showBoxes && (
        <svg
          viewBox={`0 0 ${raster.width} ${raster.height}`}
          preserveAspectRatio="none"
        >
          {elements.map((el, i) => {
            if (!el.bbox || el.bbox.length !== 4) return null;
            const color =
              CATEGORY_COLORS[el.category] ?? UNKNOWN_CATEGORY_COLOR;
            const box = boxPath([
              el.bbox[0] * scale.x,
              el.bbox[1] * scale.y,
              el.bbox[2] * scale.x,
              el.bbox[3] * scale.y,
            ]);
            return (
              <g key={`el-${i}`}>
                <rect {...box} fill="none" stroke={color} strokeWidth={3} />
                <text
                  x={box.x + 4}
                  y={box.y - 6}
                  fontSize={24}
                  fill={color}
                  stroke="#fff"
                  strokeWidth={5}
                  paintOrder="stroke"
                >
                  {el.category ?? "Unknown"}
                </text>
              </g>
            );
          })}
          {uncovered.map((region, i) => {
            if (!region.bbox || region.bbox.length !== 4) return null;
            return (
              <g key={`ink-${i}`}>
                <rect
                  {...boxPath(region.bbox)}
                  fill="none"
                  stroke={UNCOVERED_INK_COLOR}
                  strokeWidth={5}
                />
                <text
                  x={region.bbox[0] + 4}
                  y={region.bbox[1] - 6}
                  fontSize={24}
                  fill={UNCOVERED_INK_COLOR}
                  stroke="#fff"
                  strokeWidth={5}
                  paintOrder="stroke"
                >
                  UNCOVERED INK
                </text>
              </g>
            );
          })}
        </svg>
      )}
    </div>
      {state.status !== "ready" && (
        // A sibling, not a child: until the first render lands the stage has no
        // size of its own to centre anything in.
        <div className={`page-state ${state.status}`}>
          {state.status === "loading" ? "rendering…" : state.message}
        </div>
      )}
    </>
  );
}
