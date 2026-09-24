// See LICENSE for the add-on's license and additional terms.

import type { TodayProgress } from "./types";

type SeenProgress = TodayProgress & { percent: number };
const PROGRESS_STORAGE_KEY = "review-heatmap:last-today-progress";
const PROGRESS_DELAY_MS = 650;
const PROGRESS_DURATION_MS = 1400;
let stopProgressAnimation: (() => void) | undefined;

function readSeenProgress(context: string): SeenProgress | null {
  try {
    const state = JSON.parse(sessionStorage.getItem(PROGRESS_STORAGE_KEY) || "null");
    if (state?.context === context && Number.isFinite(state.percent) &&
        state.percent >= 0 && typeof state.color === "string") return state;
  } catch { /* Anki can disable web storage; the current value still works. */ }
  return null;
}

function saveSeenProgress(state: SeenProgress | null): void {
  try {
    if (state) sessionStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(state));
    else sessionStorage.removeItem(PROGRESS_STORAGE_KEY);
  } catch { /* Progress must remain usable without web storage. */ }
}

function bezier(t: number, x1: number, y1: number, x2: number, y2: number): number {
  const curve = (s: number, a: number, b: number) =>
    3 * (1 - s) ** 2 * s * a + 3 * (1 - s) * s ** 2 * b + s ** 3;
  let low = 0, high = 1;
  for (let i = 0; i < 16; i++) {
    const middle = (low + high) / 2;
    if (curve(middle, x1, x2) < t) low = middle;
    else high = middle;
  }
  return curve((low + high) / 2, y1, y2);
}

// The supplied multi-step motion: creep, gather pace, then settle.
function progressEase(t: number): number {
  if (t <= 0) return 0;
  if (t >= 1) return 1;
  if (t < 0.09) return 0.05 * bezier(t / 0.09, 0.4, 0, 0.55, 0.5);
  if (t < 0.45) return 0.05 + 0.47 * bezier((t - 0.09) / 0.36, 0.3, 0.3, 0.45, 0.8);
  return 0.52 + 0.48 * bezier((t - 0.45) / 0.55, 0.3, 0.45, 0.6, 0.9);
}

function colorChannels(color: string): number[] | null {
  if (/^#[\da-f]{6}$/i.test(color)) {
    return [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16)).concat(1);
  }
  const match = /^rgba?\(([\d.,\s]+)\)$/.exec(color);
  if (!match) return null;
  const channels = match[1].split(",").map(Number);
  if (channels.length === 3) channels.push(1);
  return channels.length === 4 && channels.every(Number.isFinite) ? channels : null;
}

function mixColor(from: string, to: string, fraction: number): string {
  const start = colorChannels(from), end = colorChannels(to);
  if (!start || !end) return to;
  const channels = start.map((value, i) => {
    const mixed = value + (end[i] - value) * fraction;
    return i === 3 ? mixed.toFixed(4) : Math.round(mixed);
  });
  return `rgba(${channels.join(", ")})`;
}

function animateTodayProgress(
  target: SeenProgress, render: (state: SeenProgress) => void,
  connected: () => boolean,
): void {
  let shown = readSeenProgress(target.context) || target;
  let timer: number | undefined, frame: number | undefined;
  const cancelScheduled = () => {
    window.clearTimeout(timer);
    if (frame !== undefined) window.cancelAnimationFrame(frame);
  };
  const stop = () => {
    cancelScheduled();
    document.removeEventListener("visibilitychange", onVisibilityChange);
    window.removeEventListener("pagehide", stop);
    if (stopProgressAnimation === stop) stopProgressAnimation = undefined;
  };
  const paint = (state: SeenProgress) => {
    shown = state;
    render(state);
    // Save what was displayed, including partial animation, not the target.
    saveSeenProgress(state);
  };
  const start = () => {
    if (!connected()) { stop(); return; }
    if (document.hidden) return;
    paint(shown);
    if ((shown.percent === target.percent && shown.color === target.color) ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      paint(target);
      stop();
      return;
    }
    const from = shown;
    timer = window.setTimeout(() => {
      let started: number | undefined;
      const tick = (now: number) => {
        if (!connected()) { stop(); return; }
        if (document.hidden) return;
        if (started === undefined) started = now;
        const elapsed = Math.min(1, (now - started) / PROGRESS_DURATION_MS);
        const fraction = progressEase(elapsed);
        paint(elapsed === 1 ? target : {
          ...target,
          percent: from.percent + (target.percent - from.percent) * fraction,
          color: fraction === 0 ? from.color : mixColor(from.color, target.color, fraction),
        });
        if (elapsed < 1) frame = window.requestAnimationFrame(tick);
        else stop();
      };
      frame = window.requestAnimationFrame(tick);
    }, PROGRESS_DELAY_MS);
  };
  const onVisibilityChange = () => {
    cancelScheduled();
    start();
  };
  stopProgressAnimation = stop;
  document.addEventListener("visibilitychange", onVisibilityChange);
  window.addEventListener("pagehide", stop);
  render(shown);
  start();
}

/** Append one progress indicator without changing Anki's localized summary. */
export function updateTodayProgress(data: TodayProgress | null): void {
  stopProgressAnimation?.();
  document.getElementById("review-heatmap-progress")?.remove();
  const summary = document.getElementById("studiedToday");
  if (!summary) return;
  if (!data) { saveSeenProgress(null); return; }

  const wrapper = document.createElement("span");
  wrapper.id = "review-heatmap-progress";
  wrapper.className = "rh-today-progress";
  const track = document.createElement("span");
  track.className = "rh-today-progress-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Today's activity");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  const fill = document.createElement("span");
  fill.className = "rh-today-progress-fill";
  const label = document.createElement("span");
  label.className = "rh-today-progress-label";
  track.appendChild(fill);
  wrapper.append(track, label);
  summary.appendChild(wrapper);

  if (data.percent === null || !Number.isFinite(data.percent)) {
    saveSeenProgress(null);
    fill.style.width = "0%";
    label.textContent = "No baseline";
    wrapper.title = "Choose a reference day in Review Heatmap Options → Display Mode, " +
      "or allow automatic selection after seven completed study days.";
    track.setAttribute("aria-valuetext", "No activity baseline available");
  } else {
    animateTodayProgress({ ...data, percent: Math.max(0, data.percent) }, state => {
      const bounded = Math.min(100, state.percent);
      const text = `${state.percent.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
      fill.style.width = `${bounded}%`;
      fill.style.backgroundColor = state.color;
      label.textContent = text;
      wrapper.title = `Today's activity: ${text} of baseline. ` +
        "100% is 85% of the reference day's activity. Uses the heatmap's included decks.";
      track.setAttribute("aria-valuenow", String(bounded));
      track.setAttribute("aria-valuetext", `${text} of baseline`);
    }, () => wrapper.isConnected);
  }
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
