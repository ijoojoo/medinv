/** Calendar dates are not timestamps. Python date / the existing API use years 1–9999. */
export const BUSINESS_TIME_ZONE = "Asia/Shanghai";
export type DateRules = {
  required?: boolean;
  min?: string;
  max?: string;
  disabledDate?: (date: string) => boolean;
};
export type DateResult = { value: string; error: string };
export const daysInMonth = (year: number, month: number) =>
  [
    31,
    year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0) ? 29 : 28,
    31,
    30,
    31,
    30,
    31,
    31,
    30,
    31,
    30,
    31,
  ][month - 1] || 0;
export const calendarDate = (year: number, month: number, day: number) =>
  `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
export function parseDate(raw: unknown, rules: DateRules = {}): DateResult {
  const text = String(raw ?? "").trim();
  const fail = (error: string): DateResult => ({ value: "", error });
  if (!text)
    return rules.required ? fail("请填写日期") : { value: "", error: "" };
  const match =
    /^(\d{4})(\d{2})(\d{2})$/.exec(text) ||
    /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!match) return fail("请输入完整的 YYYYMMDD 或 YYYY-MM-DD 日期");
  const [year, month, day] = match.slice(1).map(Number) as [
    number,
    number,
    number,
  ];
  if (
    year < 1 ||
    year > 9999 ||
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth(year, month)
  )
    return fail("日期不存在，请检查年月日");
  const value = calendarDate(year, month, day);
  const min = rules.min ? parseDate(rules.min).value : "";
  const max = rules.max ? parseDate(rules.max).value : "";
  if (min && value < min) return fail(`日期不能早于 ${min}`);
  if (max && value > max) return fail(`日期不能晚于 ${max}`);
  if (rules.disabledDate?.(value))
    return fail("此日期不可用，请选择允许的日期");
  return { value, error: "" };
}
export function businessToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: BUSINESS_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)!.value;
  return `${part("year").padStart(4, "0")}-${part("month")}-${part("day")}`;
}
// Arithmetic is used for calendar movement only, never to accept invalid input.
export function moveDay(value: string, delta: number) {
  let [y, m, d] = value.split("-").map(Number) as [number, number, number];
  d += delta;
  while (d < 1) {
    if (--m < 1) {
      m = 12;
      y--;
    }
    if (y < 1) return "0001-01-01";
    d += daysInMonth(y, m);
  }
  while (d > daysInMonth(y, m)) {
    d -= daysInMonth(y, m);
    if (++m > 12) {
      m = 1;
      y++;
    }
    if (y > 9999) return "9999-12-31";
  }
  return calendarDate(y, m, d);
}
export function moveMonth(value: string, delta: number) {
  const [y, m, d] = value.split("-").map(Number) as [number, number, number];
  const index = Math.max(
    0,
    Math.min(9999 * 12 - 1, (y - 1) * 12 + m - 1 + delta),
  );
  const year = Math.floor(index / 12) + 1,
    month = (index % 12) + 1;
  return calendarDate(year, month, Math.min(d, daysInMonth(year, month)));
}
/** Monday=0, Gregorian calendar; no browser time zone conversion. */
export function weekday(value: string) {
  const [y, m, d] = value.split("-").map(Number) as [number, number, number];
  let days =
    (y - 1) * 365 +
    Math.floor((y - 1) / 4) -
    Math.floor((y - 1) / 100) +
    Math.floor((y - 1) / 400);
  for (let month = 1; month < m; month++) days += daysInMonth(y, month);
  return (days + d - 1) % 7;
}
