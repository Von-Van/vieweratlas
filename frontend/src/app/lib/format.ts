/** Thousands separators everywhere a count is shown. */
export function fmt(n: number): string {
  return n.toLocaleString("en-US");
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const PERIOD = /^([A-Z][a-z]{2}) (\d{1,2}) – ([A-Z][a-z]{2}) (\d{1,2}), (\d{4})$/;

/**
 * The aggregator writes `collectionPeriod` as "Aug 13 – Aug 26, 2026": the year
 * on the end date only. Returns UTC midnights, or null for anything else
 * ("as of …", the PENDING placeholder), which callers show verbatim.
 */
export function parsePeriod(period: string): { start: Date; end: Date } | null {
  const m = PERIOD.exec(period.trim());
  if (!m) return null;
  const [, m1, d1, m2, d2, y] = m;
  const startMonth = MONTHS.indexOf(m1);
  const endMonth = MONTHS.indexOf(m2);
  if (startMonth < 0 || endMonth < 0) return null;
  const endYear = Number(y);
  // A window that crosses New Year starts in the previous year.
  const startYear = startMonth > endMonth ? endYear - 1 : endYear;
  const start = new Date(Date.UTC(startYear, startMonth, Number(d1)));
  const end = new Date(Date.UTC(endYear, endMonth, Number(d2)));
  return end >= start ? { start, end } : null;
}

/** "Aug 13 – Aug 26, 2026" → "13–26 Aug 2026". Anything unparsed passes through. */
export function formatPeriod(period: string): string {
  const parsed = parsePeriod(period);
  if (!parsed) return period;
  const { start, end } = parsed;
  const d = (date: Date) => date.getUTCDate();
  const mon = (date: Date) => MONTHS[date.getUTCMonth()];
  const yr = (date: Date) => date.getUTCFullYear();
  if (yr(start) !== yr(end)) return `${d(start)} ${mon(start)} ${yr(start)} – ${d(end)} ${mon(end)} ${yr(end)}`;
  if (mon(start) !== mon(end)) return `${d(start)} ${mon(start)} – ${d(end)} ${mon(end)} ${yr(end)}`;
  return `${d(start)}–${d(end)} ${mon(end)} ${yr(end)}`;
}

/**
 * Every day in the period, labelled the way `viewerHistory[].date` is ("Aug 13"),
 * so days a channel was never seen live can be found by absence.
 */
export function periodDays(period: string): string[] | null {
  const parsed = parsePeriod(period);
  if (!parsed) return null;
  const days: string[] = [];
  for (let t = parsed.start.getTime(); t <= parsed.end.getTime(); t += 86_400_000) {
    const date = new Date(t);
    days.push(`${MONTHS[date.getUTCMonth()]} ${String(date.getUTCDate()).padStart(2, "0")}`);
    if (days.length > 400) return null;
  }
  return days;
}

/** "Aug 13" → "13 Aug", to match the rest of the site's dates. */
export function dayLabel(day: string): string {
  const [mon, dd] = day.split(" ");
  return dd ? `${Number(dd)} ${mon}` : day;
}

/** ISO timestamp → "2026-10-07"; null when absent or unparseable. */
export function isoDay(timestamp: string | undefined): string | null {
  if (!timestamp) return null;
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(timestamp);
  return m ? m[1] : null;
}

const languageNames = (() => {
  try {
    return new Intl.DisplayNames(["en"], { type: "language" });
  } catch {
    return null;
  }
})();

/** Broadcaster language tag as exported ("En", "Zh-hk") → "English". */
export function languageName(tag: string): string {
  if (!languageNames || !/^[a-z]{2,3}(-[a-z0-9]{2,8})?$/i.test(tag)) return tag;
  try {
    return languageNames.of(tag.toLowerCase()) ?? tag;
  } catch {
    return tag;
  }
}

/** Axis labels: 0, 500, 10k, 2.5k, 1.2M. */
export function axisNumber(n: number): string {
  if (n >= 1_000_000) return `${+(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${+(n / 1_000).toFixed(1)}k`;
  return String(n);
}
