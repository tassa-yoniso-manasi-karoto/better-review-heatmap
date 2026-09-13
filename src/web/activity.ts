// See LICENSE for the add-on's license and additional terms.

import type { TodayProgress } from "./types";

/** Append one progress indicator without changing Anki's localized summary. */
export function updateTodayProgress(data: TodayProgress | null): void {
  document.getElementById("review-heatmap-progress")?.remove();
  const summary = document.getElementById("studiedToday");
  if (!data || !summary) return;

  const wrapper = document.createElement("span");
  wrapper.id = "review-heatmap-progress";
  wrapper.className = "rh-today-progress";
  const track = document.createElement("span");
  track.className = "rh-today-progress-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Today's workload");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("span");
  fill.className = "rh-today-progress-fill";
  const label = document.createElement("span");
  label.className = "rh-today-progress-label";

  if (data.percent === null || !Number.isFinite(data.percent)) {
    fill.style.width = "0%";
    label.textContent = "No baseline";
    wrapper.title = "Choose a reference day in Review Heatmap Options → Activity, " +
      "or allow automatic selection after seven completed study days.";
    track.setAttribute("aria-valuetext", "No workload baseline available");
  } else {
    const percent = Math.max(0, data.percent);
    const bounded = Math.min(100, percent);
    const text = `${percent.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    fill.style.width = `${bounded}%`;
    fill.style.backgroundColor = data.color;
    label.textContent = text;
    wrapper.title = `Today's workload: ${text} of baseline. ` +
      "100% is 85% of the reference day's workload. Uses the heatmap's included decks.";
    track.setAttribute("aria-valuenow", String(bounded));
    track.setAttribute("aria-valuetext", `${text} of workload baseline`);
  }
  track.appendChild(fill);
  wrapper.append(track, label);
  summary.appendChild(wrapper);
}

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
