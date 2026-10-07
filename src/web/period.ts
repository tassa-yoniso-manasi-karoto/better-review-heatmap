// See LICENSE for the add-on's license and additional terms.

import { calendarDateFromKey } from "./activity";

/** A colour threshold as the renderer builds them: value <= threshold → class. */
export type Level = [threshold: number, cssClass: string];

export interface StatsLevels {
  streak: Level[];
  percentage: Level[];
  cards: Level[];
  firstCards: Level[];
}

export interface PeriodStats {
  start: number;
  end: number;
  days: number;
  activeDays: number;
  total: number;
  milliseconds: number;
  average: number;
  percent: number;
  longest: number;
  final: number;
}

const DAY = 86400;

/** The statistics line for the calendar days from start to end inclusive. */
export function periodStats(
  start: number, end: number, today: number,
  activity: (day: number) => [count: number, milliseconds: number],
): PeriodStats {
  if (end < start) [start, end] = [end, start];
  let activeDays = 0, total = 0, milliseconds = 0, longest = 0, run = 0;
  let lastActive: number | null = null;
  for (let day = start; day <= end; day += DAY) {
    const [count, time] = activity(day);
    if (count <= 0) continue;
    activeDays += 1;
    total += count;
    milliseconds += time;
    run = lastActive === day - DAY ? run + 1 : 1;
    longest = Math.max(longest, run);
    lastActive = day;
  }
  const days = (end - start) / DAY + 1;
  // Like the lifetime figure, the streak survives an unfinished today.
  const final = lastActive !== null &&
    (lastActive === end || (end === today && lastActive === end - DAY)) ? run : 0;
  return {
    start, end, days, activeDays, total, milliseconds, longest, final,
    average: Math.round(total / Math.max(activeDays, 1)),
    percent: Math.round(100 * activeDays / days),
  };
}

/** Pick the colour class for a value the way the renderer does for its line. */
export function levelClass(value: number, levels: Level[]): string {
  let cssClass = "rh-col0";
  for (const [threshold, name] of levels) {
    cssClass = name;
    if (value <= threshold) break;
  }
  return cssClass;
}

export function pluralize(count: number, unit: string): string {
  return `${count} ${unit}${Math.abs(count) > 1 ? "s" : ""}`;
}

function formatDay(day: number, withYear: boolean): string {
  const options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  if (withYear) options.year = "numeric";
  return calendarDateFromKey(day).toLocaleDateString("en-US", options);
}

/** "Mar 3 – Mar 20, 2026": the year once, unless the period spans two. */
export function formatPeriod(start: number, end: number): string {
  if (start === end) return formatDay(start, true);
  const sameYear =
    calendarDateFromKey(start).getFullYear() === calendarDateFromKey(end).getFullYear();
  return `${formatDay(start, !sameYear)} – ${formatDay(end, true)}`;
}
