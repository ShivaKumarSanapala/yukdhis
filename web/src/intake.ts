// What the omni-input accepts, and how it decides between "analyse a circular" and "ask the assistant".
export const ALLOWED = { ".pdf": "PDF", ".docx": "Word", ".html": "HTML", ".htm": "HTML", ".md": "Markdown", ".markdown": "Markdown", ".txt": "Text" } as const;
export const ACCEPT = Object.keys(ALLOWED).join(",");
export const MAX_FILE_BYTES = 10 * 1024 * 1024;
export const MAX_TEXT_BYTES = 500 * 1024;
export const MAX_BATCH = 10;
/** Longest question the assistant endpoint takes (the API caps text at 8000 characters). */
export const MAX_QUESTION = 7500;
/** Longest text the box holds (pasted circulars can be long; the API caps analysed text at 500 KB). */
export const MAX_INPUT = 120_000;
/** Pasted text this long that reads like a circular starts an analysis instead of a question. */
export const CIRCULAR_MIN_CHARS = 1500;

export type Intent = "analyse" | "ask";
export type Attachment =
  | { id: string; kind: "file"; file: File }
  | { id: string; kind: "sample"; sampleId: string; title: string }
  | { id: string; kind: "url"; url: string };

export const extension = (name: string) => (name.match(/\.[^.]+$/)?.[0] ?? "").toLowerCase();
export const sizeText = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
export const textBytes = (text: string) => new TextEncoder().encode(text).length;

export function validateFiles(list: File[], have: Attachment[]): { ok: File[]; bad: string[] } {
  const ok: File[] = [], bad: string[] = [];
  for (const f of list) {
    const ext = extension(f.name);
    if (!(ext in ALLOWED)) bad.push(`${f.name}: use PDF, DOCX, HTML, Markdown or text`);
    else if (f.size > MAX_FILE_BYTES) bad.push(`${f.name}: larger than 10 MB`);
    else if (f.size === 0) bad.push(`${f.name}: empty file`);
    else if (!have.some((a) => a.kind === "file" && a.file.name === f.name && a.file.size === f.size)) ok.push(f);
  }
  return { ok, bad };
}

/** A single http(s) URL and nothing else, or null. */
export function asUrl(text: string): URL | null {
  const t = text.trim();
  if (!t || /\s/.test(t)) return null;
  try {
    const url = new URL(t);
    return /^https?:$/.test(url.protocol) ? url : null;
  } catch { return null; }
}
/** rbi.org.in links, or any link to a .pdf / .html document, are circulars to analyse. */
export function isCircularUrl(text: string): boolean {
  const url = asUrl(text);
  return Boolean(url && (/(^|\.)rbi\.org\.in$/i.test(url.hostname) || /\.(pdf|html?)$/i.test(url.pathname)));
}
export function urlProblem(value: string): string {
  if (!value.trim()) return "";
  return asUrl(value) ? "" : "Enter a full http(s) link, e.g. https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12524";
}

/** Long pasted text that reads like a regulatory circular (reference number, directions, "shall" clauses). */
export function looksLikeCircular(text: string): boolean {
  if (text.trim().length < CIRCULAR_MIN_CHARS) return false;
  const signals = [
    /RBI\/\d{4}-\d{2,4}\/\d+/i, /\b(circular|directions?|master direction|notification)\b/i, /\bshall\b/i, /\bregulated entit/i,
    /\b(para(graph)?|chapter|annex(ure)?)\b/i, /\beffective (from|date)\b/i,
  ].filter((re) => re.test(text)).length;
  return signals >= 2;
}

export function detectIntent(text: string, attachments: Attachment[]): Intent {
  if (attachments.length) return "analyse";
  return isCircularUrl(text) || looksLikeCircular(text) ? "analyse" : "ask";
}
