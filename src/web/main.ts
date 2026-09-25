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
import { calendarDayKey, calendarDateFromKey, reviewSummary, updateTodayProgress } from "./activity";

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

class ReviewHeatmap {
  public static updateTodayProgress = updateTodayProgress;
  private heatmap: CalHeatMap | null;
  private paletteButton: HTMLElement | null;
  private newCardsButton: HTMLElement | null;
  private container: HTMLElement | null;
  private baselineMode: boolean;
  private showNewCards = false;
  private reviewData: ReviewHeatmapData = {};
  private layerStorageKey: string;

  constructor(private options: ReviewHeatmapOptions) {
    this.heatmap = null;
    this.container = document.getElementById("cal-heatmap")?.closest(".rh-container") as HTMLElement | null;
    this.baselineMode = this.container?.classList.contains("rh-baseline") || false;
    this.newCardsButton = document.getElementById("review-heatmap-new-cards");
    this.newCardsButton?.style.setProperty("--rh-review-accent",
      themeAccentRgb(options.showPaletteButton ? "lime" : options.theme));
    this.newCardsButton?.style.setProperty("--rh-new-accent", themeAccentRgb("ice"));
    this.layerStorageKey = `rh-first-reviews:${options.viewSession}:${options.referenceScope}`;
    try {
      this.showNewCards = sessionStorage.getItem(this.layerStorageKey) === "true";
    } catch { /* The toggle also works without web storage. */ }
    this.paletteButton = document.getElementById("review-heatmap-palette");
    this.updateLayerControls();
    window.setInterval(() => this.refreshPaletteVisibility(), 1000);
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
    const applyDayColors = () => {
      const dayColors = this.showNewCards ? this.options.firstReviewColors : this.options.dayColors;
      // Inline fills survive the vendor's asynchronous class updates and
      // highlights. Calendar keys preserve the inherited DST correction.
      heatmap.root.selectAll(".graph-domain rect")
        .style("fill", (cell: CalHeatmapCellData) =>
          dayColors[calendarDayKey(new Date(cell.t))] || null);
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
      afterLoad: applyDayColors,
      onComplete: applyDayColors,
      afterLoadNextDomain: applyDayColors,
      afterLoadPreviousDomain: applyDayColors,
      afterUpdate: applyDayColors,
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
      onClick: (date, nb) => {
        // Click handler that shows cards assigned to a particular date
        // in Anki's card browser

        if (nb === null || nb == 0) {
          // No cards for that day. Preserve highlight and return.
          heatmap.highlight(calTodayDate);
          return;
        }

        if (this.showNewCards) {
          bridgeCommand(`revhm_firstreviews:${this.options.referenceScope},${calendarDayKey(date)}`);
          heatmap.highlight([calTodayDate, date]);
          return;
        }

        // console.log(date)

        // Determine if review history or forecasts
        let isHistory = nb >= 0;

        // Apply deck limits
        let cmd = this.options.whole ? "" : "deck:current ";

        const dayOffset = (calendarDayKey(date) - calendarDayKey(calTodayDate)) / 86400;

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

        // Invoke browser
        bridgeCommand("revhm_browse:" + cmd);

        // Update date highlight to include clicked on date AND today
        heatmap.highlight([calTodayDate, date]);
      },
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
