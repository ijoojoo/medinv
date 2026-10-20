import {
  businessToday,
  calendarDate,
  daysInMonth,
  moveDay,
  moveMonth,
  weekday,
} from "./dates";

export const datePresets = [
  ["all", "不限日期"],
  ["today", "今天"],
  ["yesterday", "昨天"],
  ["week", "本周"],
  ["lastWeek", "上周"],
  ["month", "本月"],
  ["lastMonth", "上月"],
  ["quarter", "本季度"],
  ["lastQuarter", "上季度"],
  ["year", "今年"],
  ["lastYear", "去年"],
  ["7", "近7天"],
  ["30", "近30天"],
  ["90", "近90天"],
] as const;
export type DatePreset = (typeof datePresets)[number][0];
export function presetRange(key: DatePreset, today = businessToday()) {
  const y = Number(today.slice(0, 4)),
    m = Number(today.slice(5, 7));
  let from = today,
    to = today;
  const monthEnd = (value: string) => {
    const year = Number(value.slice(0, 4)),
      month = Number(value.slice(5, 7));
    return calendarDate(year, month, daysInMonth(year, month));
  };
  if (key === "all") from = to = "";
  else if (key === "yesterday") from = to = moveDay(today, -1);
  else if (["7", "30", "90"].includes(key))
    from = moveDay(today, 1 - Number(key));
  else if (key === "week") from = moveDay(today, -weekday(today));
  else if (key === "lastWeek") {
    to = moveDay(today, -weekday(today) - 1);
    from = moveDay(to, -6);
  } else if (key === "month") from = calendarDate(y, m, 1);
  else if (key === "lastMonth") {
    from = moveMonth(calendarDate(y, m, 1), -1);
    to = monthEnd(from);
  } else if (key === "quarter")
    from = calendarDate(y, Math.floor((m - 1) / 3) * 3 + 1, 1);
  else if (key === "lastQuarter") {
    from = moveMonth(calendarDate(y, Math.floor((m - 1) / 3) * 3 + 1, 1), -3);
    to = monthEnd(moveMonth(from, 2));
  } else if (key === "year") from = calendarDate(y, 1, 1);
  else if (key === "lastYear") {
    from = calendarDate(y - 1, 1, 1);
    to = calendarDate(y - 1, 12, 31);
  }
  return { date_from: from, date_to: to };
}
