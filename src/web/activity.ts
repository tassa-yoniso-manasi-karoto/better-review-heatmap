// See LICENSE for the add-on's license and additional terms.

/** Recover the original calendar key after cal-heatmap's local-time mapping. */
export function calendarDayKey(date: Date): number {
  return Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 1000;
}

export function formatRecordedTime(milliseconds: number): string {
  const seconds = Math.round(Math.max(0, milliseconds) / 1000);
  if (milliseconds > 0 && seconds === 0) return "<1 s";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder ? `${hours} h ${remainder} min` : `${hours} h`;
}

export function reviewSummary(reviews: number, milliseconds: number): string {
  const duration = formatRecordedTime(milliseconds).replace("<", "&lt;");
  const label = reviews === 1 ? "review" : "reviews";
  return `<b>${duration}</b> recorded · <b>${reviews.toLocaleString()}</b> ${label}`;
}
