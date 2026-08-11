import { marked } from "marked";
import DOMPurify from "dompurify";

// Mirrors element_to_markdown() in vlm_ocr.py's records_to_markdown(), so what
// you read here is what scripts/render_volume_document.py would emit for that
// page. Keep the two in sync if the pipeline's Markdown mapping changes.
export function elementToMarkdown(el) {
  const category = el.category ?? "Text";
  const text = el.text ?? "";
  if (category === "Picture")
    return `*[Picture — bbox ${JSON.stringify(el.bbox ?? null)}]*`;
  if (category === "Title" && text) return `# ${text}`;
  if (category === "Section-header" && text) return `## ${text}`;
  return text;
}

export function pageMarkdown(elements = []) {
  return elements.map(elementToMarkdown).join("\n\n");
}

marked.setOptions({ gfm: true, breaks: false });

// Table elements arrive as raw <table> HTML from the model, so the rendered
// Markdown has to allow HTML through -- sanitize it rather than trust it.
export function renderMarkdown(md) {
  return DOMPurify.sanitize(marked.parse(md ?? ""));
}
