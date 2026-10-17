// Shared date helpers: ISO timestamps become locale text; recent ones get a relative form. Uses Intl only.
const parse = (value?: string | null): number | null => {
  if (!value) return null;
  // Date-only values are business dates: read them as local midnight so the day never shifts with the timezone.
  const t = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`).getTime() : Date.parse(value);
  return Number.isNaN(t) ? null : t;
};

/** "17 Oct 2026, 19:17" in the user's locale and timezone; date-only input yields "17 Oct 2026". */
export function formatDateTime(value?: string | null): string {
  const t = parse(value);
  if (t === null) return value ?? "";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value ?? "");
  return new Intl.DateTimeFormat(undefined, dateOnly
    ? { day: "numeric", month: "short", year: "numeric" }
    : { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(t);
}

/** "2 h ago" for the last week, otherwise "" (callers then show the absolute date). */
export function relativeTime(value?: string | null, now: number = Date.now()): string {
  const t = parse(value);
  if (t === null) return "";
  const diff = now - t;
  if (diff < 0 || diff > 7 * 86_400_000) return "";
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto", style: "short" });
  const mins = Math.round(diff / 60_000);
  if (mins < 1) return rtf.format(0, "minute");
  if (mins < 60) return rtf.format(-mins, "minute");
  const hours = Math.round(mins / 60);
  if (hours < 24) return rtf.format(-hours, "hour");
  return rtf.format(-Math.round(hours / 24), "day");
}

/** Absolute date with the relative hint when recent, e.g. "17 Oct 2026, 19:17 · 2 hr. ago". */
export function whenText(value?: string | null): { text: string; rel: string; exact: string } {
  const text = formatDateTime(value);
  return { text, rel: relativeTime(value), exact: value ?? "" };
}
