/* 
Review Heatmap Add-on for Anki

Copyright (C) 2016-2022  Aristotelis P. <https//glutanimate.com/>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version, with the additions
listed at the end of the accompanied license file.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.

NOTE: This program is subject to certain additional terms pursuant to
Section 7 of the GNU Affero General Public License.  You should have
received a copy of these additional terms immediately following the
terms and conditions of the GNU Affero General Public License which
accompanied this program.

If not, please request a copy through one of the means of contact
listed here: <https://glutanimate.com/contact/>.

Any modifications to this file must keep this entire header intact.
*/

import calHeatmapCss from "./_vendor/cal-heatmap.css";
import reviewHeatmapCss from "./css/review-heatmap.css";
import { themeAccentRgb, themeCss } from "./themes";

var __vite_style__ = document.createElement('style');
__vite_style__.textContent = calHeatmapCss + "\n" + themeCss() + "\n" + reviewHeatmapCss;
document.head.appendChild(__vite_style__);

import { CalHeatMap } from "./_vendor/cal-heatmap.js";
import { ReviewHeatmapOptions, ReviewHeatmapData } from "./types";
import { bridgeCommand } from "./bridge";
import {
  calendarDayKey, calendarDateFromKey, formatRecordedTime, reviewSummary, updateTodayProgress,
} from "./activity";
import { formatPeriod, levelClass, periodStats, pluralize } from "./period";

interface CalHeatmapFormatData {
  count: string | undefined;
  name: string;
  connector: string;
  date: string;
}

interface CalHeatmapCellData {
  v: number; // count
  t: number; // timestamp
}

/** A clicked first day and, once fixed by a second click, the last. */
interface Period {
  anchor: number;
  end: number | null;
}

interface StatsLine {
  element: HTMLElement;
  original: string;
  newCards: boolean;
}

// The selected days share one border, like a travel site's date picker:
// thin inside, thick on the first day and on the last or hovered day.
const PERIOD_STROKE = "var(--rh-period-stroke)";
const PERIOD_STROKE_WIDTH = "1px";
const PERIOD_EDGE_STROKE_WIDTH = "2.5px";

// Anki loads each view as a document, so this script runs before the parser
// reaches the statistics lines below the calendar: they are looked up when
// needed, and their lifetime markup is kept here to restore it.
const lifetimeLines = new WeakMap<Element, string>();

class ReviewHeatmap {
  public static updateTodayProgress = updateTodayProgress;
  private heatmap: CalHeatMap | null;
  private paletteButton: HTMLElement | null;
  private newCardsButton: HTMLElement | null;
  private calendar: HTMLElement | null;
  private container: HTMLElement | null;
  private baselineMode: boolean;
  private showNewCards = false;
  private reviewData: ReviewHeatmapData = {};
  private layerStorageKey: string;
  private periodStorageKey: string;
  private period: Period | null = null;
  private hovered: number | null = null;
  private today: number;

  constructor(private options: ReviewHeatmapOptions) {
    this.heatmap = null;
    this.calendar = document.getElementById("cal-heatmap");
    this.container = this.calendar?.closest(".rh-container") as HTMLElement | null;
    this.today = Math.floor(options.today / 1000);
    this.baselineMode = this.container?.classList.contains("rh-baseline") || false;
    this.newCardsButton = document.getElementById("review-heatmap-new-cards");
    this.newCardsButton?.style.setProperty("--rh-review-accent",
      themeAccentRgb(options.showPaletteButton ? "lime" : options.theme));
    this.newCardsButton?.style.setProperty("--rh-new-accent", themeAccentRgb("ice"));
    this.layerStorageKey = `rh-first-reviews:${options.viewSession}:${options.referenceScope}`;
    try {
      this.showNewCards = sessionStorage.getItem(this.layerStorageKey) === "true";
    } catch { /* The toggle also works without web storage. */ }
    this.periodStorageKey = `rh-period:${options.viewSession}:${options.referenceScope}`;
    try {
      const saved = JSON.parse(sessionStorage.getItem(this.periodStorageKey) || "null");
      if (this.isDay(saved?.anchor) && (saved.end === null || this.isDay(saved.end))) {
        this.period = { anchor: saved.anchor, end: saved.end };
      }
    } catch { /* A period is picked again after a page change without storage. */ }
    this.paletteButton = document.getElementById("review-heatmap-palette");
    this.updateLayerControls();
    window.setInterval(() => this.refreshPaletteVisibility(), 1000);
  }

  private isDay(value: unknown): value is number {
    return typeof value === "number" && Number.isInteger(value) && value >= 0 &&
      value % 86400 === 0 && value <= this.today;
  }

  private statsLines(): StatsLine[] {
    const lines: StatsLine[] = [];
    for (const [selector, newCards] of [[".rh-review-stats .streak", false],
                                        [".rh-new-card-stats .streak", true]] as const) {
      const element = this.container?.querySelector(selector) as HTMLElement | null;
      if (!element) continue;
      if (!lifetimeLines.has(element)) lifetimeLines.set(element, element.innerHTML);
      lines.push({ element, original: lifetimeLines.get(element)!, newCards });
    }
    return lines;
  }

  private setPaletteVisibility(visible: boolean) {
    if (this.paletteButton) {
      this.paletteButton.hidden = !visible;
    }
  }

  private refreshPaletteVisibility() {
    bridgeCommand("revhm_palettevisible", (visible) =>
      this.setPaletteVisibility(visible === true));
  }

  public create(data: ReviewHeatmapData) {
    this.reviewData = data;
    const calTodayDate = calendarDateFromKey(this.options.today / 1000);
    let calStartDate = new Date(calTodayDate);
    let calMinDate = calendarDateFromKey(this.options.start / 1000);
    let calMaxDate = calendarDateFromKey(this.options.stop / 1000);

    // Running overview of 6-month activity in month view:
    if (this.options.domain === "month") {
      let padding = this.options.range / 2;
      // TODO: fix
      let paddingLower = Math.round(padding - 1);
      let paddingUpper = Math.round(padding + 1);

      calStartDate.setDate(1);
      calStartDate.setMonth(calStartDate.getMonth() - paddingLower);

      // Start at first data point if history < 6 months
      if (calMinDate.getTime() > calStartDate.getTime()) {
        calStartDate = calMinDate;
      }

      let tempDate = new Date(calTodayDate);
      tempDate.setDate(1);
      tempDate.setMonth(tempDate.getMonth() + paddingUpper);

      // Always go back to centered view after scrolling back then forward
      if (tempDate.getTime() > calMaxDate.getTime()) {
        calMaxDate = tempDate;
      }
    }

    let heatmap = new CalHeatMap();
    const decorate = () => {
      const dayColors = this.showNewCards ? this.options.firstReviewColors : this.options.dayColors;
      // Inline fills survive the vendor's asynchronous class updates and
      // highlights. Calendar keys preserve the inherited DST correction.
      heatmap.root.selectAll(".graph-domain rect")
        .style("fill", (cell: CalHeatmapCellData) =>
          dayColors[calendarDayKey(new Date(cell.t))] || null);
      this.strokeCells(heatmap);
    };

    // console.log("Date: options.today " + new Date(options.today))
    // console.log("Date: calTodayDate "+ calTodayDate)
    // console.log("Date: Date() "+ new Date())

    heatmap.init({
      domain: this.options.domain,
      subDomain: this.options.subdomain,
      range: this.options.range,
      minDate: calMinDate,
      maxDate: calMaxDate,
      cellSize: 10,
      verticalOrientation: false,
      dayLabel: true,
      domainMargin: [1, 1, 1, 1],
      itemName: ["card", "cards"],
      highlight: calTodayDate,
      today: calTodayDate,
      start: calStartDate,
      legend: this.showNewCards ? this.options.firstReviewLegend : this.options.legend,
      displayLegend: false,
      domainLabelFormat: this.options.domLabForm,
      tooltip: true,
      afterLoad: decorate,
      onComplete: decorate,
      afterLoadNextDomain: decorate,
      afterLoadPreviousDomain: decorate,
      afterUpdate: decorate,
      subDomainTitleFormat: (
        isEmpty: boolean,
        formatData: CalHeatmapFormatData,
        cellData: CalHeatmapCellData
      ): string => {
        // format tooltips
        let tooltip: string;

        if (this.showNewCards) {
          const count = this.options.firstReviews[calendarDayKey(new Date(cellData.t))] || 0;
          return count
            ? `<b>${count.toLocaleString()}</b> new ${count === 1 ? "card" : "cards"} first reviewed on ${formatData.date}`
            : `<b>No</b> first reviews on ${formatData.date}`;
        }

        const recorded = this.options.history[calendarDayKey(new Date(cellData.t))];
        if (recorded && cellData.v >= 0) {
          return `${reviewSummary(recorded[0], recorded[1])} on ${formatData.date}`;
        }

        let count = formatData.count;
        if (count !== undefined && count.startsWith("-")) {
          count = count.substring(1);
        }

        if (isEmpty) {
          tooltip = `<b>No</b> ${cellData.t > calTodayDate.getTime() ? "cards due" : "reviews"
            } on ${formatData.date}`;
        } else {
          const label = Math.abs(cellData.v) == 1 ? "card" : "cards";
          tooltip = `<b>${count}</b> ${label} <b>${cellData.v < 0 ? "due" : "reviewed"
            }</b> ${formatData.connector} ${formatData.date}`;
        }

        return tooltip;
      },
      // The left button picks a period and the middle button browses a day
      // (see bindCellEvents).
      onClick: null,
      afterLoadData: function afterLoadData(timestamps: ReviewHeatmapData) {
        // Cal-heatmap always uses the local timezone, which is problematic
        // when supplying UTC start-of-day times.
        //
        // This workaround updates the supplied timestamps to force
        // cal-heatmap to display times in UTC. E.g.:
        //   - input datetime (UTC): 2018-01-02 00:00:00 UTC+0000 (UTC)
        //   - cal-heatmap datetime: 2018-01-01 20:00:00 UTC-0400 (EDT)
        //   - workaround datetime:  2018-01-02 00:00:00 UTC-0400 (EDT)
        //
        // Please note that this change will skew any programmatic data
        // output from cal-heatmap, e.g. when implementing an onClick
        // handler. You will have to take the updated datetime into
        // account in that case.
        //
        // cf.: https://github.com/wa0x6e/cal-heatmap/issues/122
        //      https://github.com/wa0x6e/cal-heatmap/issues/126
        let results: ReviewHeatmapData = {};
        for (let timestamp_string in timestamps) {
          // Values are activity measures; keys represent UTC calendar days.
          let value = timestamps[timestamp_string];
          const date = calendarDateFromKey(Number(timestamp_string));
          const localSeconds = Math.floor(date.getTime() / 1000);

          results[localSeconds] = value;
        }

        return results;
      },
      data: this.showNewCards ? this.options.firstReviews : data,
    });

    this.heatmap = heatmap;
    this.bindCellEvents();
    this.renderPeriod();
    if (this.period && document.readyState === "loading") {
      // A saved period reaches the statistics lines once they are parsed.
      document.addEventListener("DOMContentLoaded", () => this.renderPeriod(), { once: true });
    }
  }

  /** Show the cards of one day in Anki's browser. */
  private browse(date: Date, nb: number | null) {
    if (nb === null || nb == 0) {
      return; // No cards for that day.
    }

    if (this.showNewCards) {
      bridgeCommand(`revhm_firstreviews:${this.options.referenceScope},${calendarDayKey(date)}`);
      return;
    }

    // Apply deck limits
    let cmd = this.options.whole ? "" : "deck:current ";

    const dayOffset = (calendarDayKey(date) - this.today) / 86400;

    // Construct search command
    if (nb >= 0) {
      // Review log
      // @ts-expect-error
      if (!window.rhNewFinderAPI) {
        // Use custom finder based on revlog ID range
        // Construct each local rollover separately: study days need not
        // contain 24 elapsed hours across a clock change.
        const cutoff1 = new Date(date.getFullYear(), date.getMonth(),
          date.getDate(), this.options.offset).getTime();
        const cutoff2 = new Date(date.getFullYear(), date.getMonth(),
          date.getDate() + 1, this.options.offset).getTime();
        cmd += "rid:" + cutoff1 + ":" + cutoff2;
      } else {
        cmd += "prop:rated=" + dayOffset;
      }
    } else {
      // Forecast
      cmd += "prop:due=" + dayOffset;
    }

    bridgeCommand("revhm_browse:" + cmd);
  }

  // Period picker
  // -------------------------------------------------------------------

  /** The vendor binds a day's data to its rect, so events need no lookup. */
  private cellData(target: EventTarget | null): CalHeatmapCellData | null {
    const element = target as (Element & { __data__?: CalHeatmapCellData }) | null;
    const data = element?.__data__;
    if (typeof element?.tagName !== "string" || element.tagName.toLowerCase() !== "rect" ||
        typeof data?.t !== "number") {
      return null;
    }
    return data;
  }

  private bindCellEvents() {
    const calendar = this.calendar;
    if (!calendar) return;
    calendar.addEventListener("click", event => {
      const cell = event.button === 0 ? this.cellData(event.target) : null;
      if (cell) this.pickDay(calendarDayKey(new Date(cell.t)));
    });
    calendar.addEventListener("mousedown", event => {
      // Anki's webview consumes the middle button's release (its paste
      // shortcut on Linux), so the press is the only event a page receives.
      const cell = event.button === 1 ? this.cellData(event.target) : null;
      if (!cell) return;
      event.preventDefault(); // Neither a paste nor an autoscroll.
      this.browse(new Date(cell.t), cell.v);
    });
    calendar.addEventListener("mouseover", event => {
      const cell = this.cellData(event.target);
      if (!cell) return; // Gaps and the tooltip keep the last day.
      const day = calendarDayKey(new Date(cell.t));
      if (day !== this.hovered) {
        this.hovered = day;
        this.onHoverChange();
      }
    });
    calendar.addEventListener("mouseleave", () => {
      if (this.hovered === null) return;
      this.hovered = null;
      this.onHoverChange();
    });
  }

  private onHoverChange() {
    // Only an unfixed period follows the pointer.
    if (this.period && this.period.end === null) this.renderPeriod();
  }

  /** A click starts a period, fixes its end, clears it from an edge, or starts over. */
  private pickDay(day: number) {
    if (day > this.today) return; // Periods describe study history.
    const period = this.period;
    if (!period) {
      this.period = { anchor: day, end: null };
    } else if (period.end === null) {
      this.period = day === period.anchor ? null : { anchor: period.anchor, end: day };
    } else {
      this.period = day === period.anchor || day === period.end ? null : { anchor: day, end: null };
    }
    this.savePeriod();
    this.renderPeriod();
  }

  private savePeriod() {
    try {
      if (this.period) sessionStorage.setItem(this.periodStorageKey, JSON.stringify(this.period));
      else sessionStorage.removeItem(this.periodStorageKey);
    } catch { /* Optional persistence across page redraws. */ }
  }

  /** The period's first and last day: fixed, or the anchor and the hovered day. */
  private periodEdges(): [number, number] | null {
    const period = this.period;
    if (!period) return null;
    const end = period.end ?? (this.hovered === null ? period.anchor : Math.min(this.hovered, this.today));
    return [period.anchor, end];
  }

  private periodBounds(): [number, number] | null {
    const edges = this.periodEdges();
    return edges && (edges[1] < edges[0] ? [edges[1], edges[0]] : edges);
  }

  /** Inline strokes survive the vendor's class updates, like the fills. */
  private strokeCells(heatmap: CalHeatMap | null = this.heatmap) {
    const bounds = this.periodBounds();
    const edges = this.periodEdges();
    const width = (cell: CalHeatmapCellData): string | null => {
      // A domain's background rect carries no day and must stay unstroked.
      if (!bounds || !edges || typeof cell?.t !== "number") return null;
      const day = calendarDayKey(new Date(cell.t));
      if (day < bounds[0] || day > bounds[1]) return null;
      return edges.includes(day) ? PERIOD_EDGE_STROKE_WIDTH : PERIOD_STROKE_WIDTH;
    };
    heatmap?.root.selectAll(".graph-domain rect")
      .style("stroke", (cell: CalHeatmapCellData) => width(cell) && PERIOD_STROKE)
      .style("stroke-width", width);
  }

  private renderPeriod() {
    this.strokeCells();
    const bounds = this.periodBounds();
    for (const line of this.statsLines()) {
      const period = bounds && this.periodLine(bounds, line.newCards);
      line.element.innerHTML = period ? period.html : line.original;
      line.element.title = period ? period.title : "";
    }
  }

  /** The statistics line recomputed for the period from the page's own data. */
  private periodLine([start, end]: [number, number], newCards: boolean): { html: string; title: string } {
    const stats = periodStats(start, end, this.today, newCards
      ? day => [this.options.firstReviews[day] || 0, 0]
      : day => this.options.history[day] || [0, 0]);
    const levels = this.options.statsLevels;
    const what = newCards ? "first reviews" : "review activity";
    const summary = newCards
      ? `${stats.total.toLocaleString()} new ${stats.total === 1 ? "card" : "cards"} first reviewed`
      : `${formatRecordedTime(stats.milliseconds)} recorded, ${stats.total.toLocaleString()} ` +
        `${stats.total === 1 ? "review" : "reviews"}`;
    const next = this.period?.end === null
      ? "Click another day to fix the period, or its first day again to clear it."
      : "Click the period's first or last day to clear it, or another day to start over.";
    const value = (text: string, cssClass: string, hint: string) =>
      `<span title="${hint}" class="sstats ${cssClass}">${text}</span>`;
    const label = (text: string) => `<span class="streak-info">${text}</span>`;
    // Spaces between the spans separate the words as the lifetime line's do.
    const html = [
      label(newCards ? "New cards/day:" : "Daily average:"),
      value(pluralize(stats.average, "card"),
        levelClass(stats.average, newCards ? levels.firstCards : levels.cards),
        newCards ? "Average first reviews on days with new cards in the period"
          : "Average reviews on active days in the period"),
      label(newCards ? "Days with new cards:" : "Days learned:"),
      value(`${stats.percent}%`, levelClass(stats.percent, levels.percentage),
        `Percentage of days with ${what} in the period`),
      label("Longest streak:"),
      value(pluralize(stats.longest, "day"), levelClass(stats.longest, levels.streak),
        `Longest continuous streak of ${what} in the period`),
      label("Streak at end:"),
      value(pluralize(stats.final, "day"), levelClass(stats.final, levels.streak),
        `Streak of ${what} running on the period's last day`),
    ].join(" ");
    const title = `${formatPeriod(start, end)}, ${pluralize(stats.days, "day")}: ${summary}. ${next}`;
    return { html, title };
  }

  private updateLayerControls() {
    this.container?.classList.toggle("rh-new-cards", this.showNewCards);
    this.container?.classList.toggle("rh-baseline", this.baselineMode && !this.showNewCards);
    this.container?.classList.toggle(`rh-theme-${this.options.theme}`, !this.showNewCards);
    this.container?.classList.toggle("rh-theme-ice", this.showNewCards);
    this.newCardsButton?.setAttribute("aria-checked", String(this.showNewCards));
    if (this.newCardsButton) {
      const label = this.showNewCards ? "Show all reviews" : "Show first reviews of new cards";
      this.newCardsButton.title = label;
    }
    this.setPaletteVisibility(this.options.showPaletteButton);
  }

  public onToggleNewCards() {
    if (!this.heatmap) return;
    this.showNewCards = !this.showNewCards;
    try {
      sessionStorage.setItem(this.layerStorageKey, String(this.showNewCards));
    } catch { /* Optional persistence across page redraws. */ }
    this.updateLayerControls();
    this.heatmap.options.data = this.showNewCards ? this.options.firstReviews : this.reviewData;
    this.heatmap.setLegend(this.showNewCards ? this.options.firstReviewLegend : this.options.legend);
    this.heatmap.update(this.heatmap.options.data);
  }

  public onHmHome(event: KeyboardEvent, button) {
    if (event.shiftKey) {
      bridgeCommand("revhm_modeswitch");
    } else {
      this.heatmap.rewind();
    }
  }

  public onHmNavigate(
    event: KeyboardEvent,
    button,
    direction: "next" | "prev"
  ) {
    if (direction === "next") {
      if (event.shiftKey) {
        this.heatmap.jumpTo(this.heatmap.options.maxDate, false); // shift-click to jump to limit
      } else {
        this.heatmap.next(this.heatmap.options.range);
      }
    } else {
      if (event.shiftKey) {
        this.heatmap.jumpTo(this.heatmap.options.minDate, false); // shift-click to jump to limit
      } else {
        this.heatmap.previous(this.heatmap.options.range);
      }
    }
  }

  public onHmOpts(event: KeyboardEvent, button) {
    if (event.shiftKey) {
      bridgeCommand("revhm_themeswitch");
    } else {
      bridgeCommand(`revhm_opts:${this.options.referenceScope}`);
    }
  }

  public onHmGradient() {
    bridgeCommand("revhm_gradient");
  }

  public onChooseReference() {
    bridgeCommand(`revhm_choosereference:${this.options.referenceScope}`);
  }

  public onDismissReference(button: HTMLButtonElement) {
    bridgeCommand(`revhm_dismissreference:${this.options.referenceScope}`, saved => {
      if (saved === true) button.closest(".rh-reference-reminder")?.remove();
    });
  }

}

globalThis.ReviewHeatmap = ReviewHeatmap;
