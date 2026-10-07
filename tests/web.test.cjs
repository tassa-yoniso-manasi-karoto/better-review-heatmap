const assert = require("node:assert/strict");
const { after, test } = require("node:test");
const { mkdtempSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const { build, buildSync } = require("esbuild");

const temporary = mkdtempSync(join(tmpdir(), "review-heatmap-web-"));
const outfile = join(temporary, "activity.cjs");
buildSync({
  entryPoints: [resolve(__dirname, "../src/web/activity.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile,
});
const { calendarDayKey, calendarDateFromKey, formatRecordedTime, reviewSummary } = require(outfile);
const periodOutfile = join(temporary, "period.cjs");
buildSync({
  entryPoints: [resolve(__dirname, "../src/web/period.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile: periodOutfile,
});
const { periodStats, levelClass, pluralize, formatPeriod } = require(periodOutfile);
after(() => rmSync(temporary, { recursive: true, force: true }));

async function heatmapPage(t) {
  const target = join(temporary, "heatmap.cjs");
  await build({
    entryPoints: [resolve(__dirname, "../src/web/main.ts")], bundle: true,
    platform: "node", format: "cjs", outfile: target, loader: { ".css": "text" },
    plugins: [{ name: "calendar-fixture", setup(builder) {
      builder.onLoad({ filter: /cal-heatmap\.js$/ }, () => ({ contents: `
        export class CalHeatMap {
          page = "previous year";
          styles = {};
          root = { selectAll: () => {
            const selection = { style: (name, value) => { this.styles[name] = value; return selection; } };
            return selection;
          } };
          get cellColor() { return this.styles.fill; }
          init(options) { this.options = options; globalThis.testCalendar = this; options.afterLoad(); }
          setLegend(legend) { this.options.legend = legend; }
          update(data) { this.options.data = data; this.options.afterUpdate(); }
          highlight(dates) { this.highlighted = dates; }
          rewind() { this.rewoundTo = this.options.start; }
        }
      ` }));
    } }],
  });
  const names = ["document", "window", "sessionStorage", "pycmd", "ReviewHeatmap", "testCalendar"];
  const original = Object.fromEntries(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  t.after(() => {
    for (const name of names) {
      if (original[name]) Object.defineProperty(globalThis, name, original[name]);
      else delete globalThis[name];
    }
  });
  const classes = new Set(["rh-container", "rh-baseline", "rh-theme-magenta"]);
  // The server-rendered statistics lines of both layers.
  const lines = {
    ".rh-review-stats .streak": { innerHTML: "lifetime review line" },
    ".rh-new-card-stats .streak": { innerHTML: "lifetime new-card line" },
  };
  // Anki's page parser reaches the lines after the script has run.
  const dom = { statsParsed: true, listeners: {} };
  const container = { classList: {
    contains: name => classes.has(name),
    toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
  }, querySelector: selector => dom.statsParsed ? lines[selector] : null };
  const toggle = { attributes: {}, style: { setProperty(name, value) { this[name] = value; } },
    setAttribute(name, value) { this.attributes[name] = value; } };
  const palette = {};
  // The latest page script owns the calendar's listeners, as in the browser.
  const calendarElement = { closest: () => container, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; } };
  const elements = {
    "cal-heatmap": calendarElement,
    "review-heatmap-new-cards": toggle, "review-heatmap-palette": palette,
  };
  const storage = new Map(), commands = [];
  globalThis.document = { createElement: () => ({}), head: { appendChild() {} },
    getElementById: id => elements[id], readyState: "complete",
    addEventListener(type, handler) { dom.listeners[type] = handler; } };
  let refreshPalette;
  globalThis.window = { setInterval: callback => { refreshPalette = callback; } };
  globalThis.sessionStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key) };
  globalThis.pycmd = (command, callback) => { commands.push(command); callback?.(true); };
  delete require.cache[require.resolve(target)];
  require(target);
  const day = Date.UTC(2026, 2, 9) / 1000;
  const cell = { t: new Date(2026, 2, 9).getTime(), v: 2 };
  const options = {
    domain: "year", subdomain: "day", range: 1, start: day * 1000,
    stop: (day + 86400) * 1000, today: day * 1000, offset: 4,
    legend: [1, 2, 3], firstReviewLegend: [0.5, 1, 2],
    referenceScope: "global", viewSession: "collection-a", theme: "magenta",
    showPaletteButton: true, dayColors: { [day]: "#ffffff" },
    firstReviews: { [day]: 2 }, firstReviewColors: { [day]: "#4a95e8" }, history: {},
    statsLevels: {
      streak: [[0, "rh-col0"], [14, "rh-col12"], [30, "rh-col14"], [90, "rh-col16"],
        [180, "rh-col19"], [365, "rh-col20"]],
      percentage: [[0, "rh-col0"], [25, "rh-col11"], [50, "rh-col12"], [60, "rh-col13"],
        [70, "rh-col14"], [80, "rh-col15"], [85, "rh-col16"], [90, "rh-col17"],
        [95, "rh-col18"], [99, "rh-col19"]],
      cards: [[0, "rh-col0"], [5, "rh-col11"], [10, "rh-col12"], [20, "rh-col13"], [30, "rh-col14"],
        [40, "rh-col15"], [50, "rh-col16"], [60, "rh-col17"], [80, "rh-col18"], [160, "rh-col19"]],
      firstCards: [[0, "rh-col0"], [1, "rh-col11"], [2, "rh-col12"], [4, "rh-col13"]],
    },
    mouseHint: false,
  };
  const normal = { [day]: 5, [day + 86400]: -20 };
  const heatmap = new globalThis.ReviewHeatmap(options);
  // Synthetic mouse events on a day's rect, which carries the vendor's datum.
  const cellTarget = (dayKey, v) => ({ tagName: "rect", __data__: { t: calendarDateFromKey(dayKey).getTime(), v } });
  const fire = (type, dayKey, v, button) => {
    const event = { button, target: dayKey === undefined ? {} : cellTarget(dayKey, v), prevented: false,
      preventDefault() { this.prevented = true; } };
    calendarElement.listeners[type]?.(event);
    return event;
  };
  const mouse = {
    // Anki eats the middle button's release, so the press browses.
    middleClick: (dayKey, v) => fire("mousedown", dayKey, v, 1),
    leftClick: (dayKey, v = 1) => fire("click", dayKey, v, 0),
    hover: (dayKey, v = 1) => fire("mouseover", dayKey, v, 0),
    leave: () => fire("mouseleave"),
  };
  return { heatmap, options, normal, toggle, palette, classes, cell, day, refreshPalette, commands,
    lines, storage, mouse, dom };
}

test("first-review toggle preserves the calendar page and restores normal colors and data", async t => {
  const { heatmap, options, normal, toggle, palette, classes, cell, day, refreshPalette, commands, mouse } =
    await heatmapPage(t);
  assert.equal(toggle.style["--rh-review-accent"], "116, 186, 88"); // Lime in Baseline
  assert.equal(toggle.style["--rh-new-accent"], "93, 162, 235"); // Ice
  heatmap.create(normal);
  const calendar = globalThis.testCalendar;
  assert.equal(calendar.cellColor(cell), "#ffffff");
  heatmap.onToggleNewCards();
  assert.deepEqual(calendar.options.data, options.firstReviews);
  assert.equal(calendar.page, "previous year");
  assert.equal(calendar.cellColor(cell), "#4a95e8");
  assert.equal(toggle.attributes["aria-checked"], "true");
  assert(classes.has("rh-theme-ice") && !classes.has("rh-baseline"));
  assert.equal(palette.hidden, false);
  refreshPalette();
  assert.equal(palette.hidden, false);
  heatmap.onHmGradient();
  assert.equal(commands.at(-1), "revhm_gradient");
  assert.match(calendar.options.subDomainTitleFormat(false, { date: "March 9" }, cell), /2.*new cards first reviewed/);
  mouse.middleClick(day, 2);
  assert.equal(commands.at(-1), `revhm_firstreviews:global,${day}`);
  heatmap.onToggleNewCards();
  assert.deepEqual(calendar.options.data, normal);
  assert.deepEqual(calendar.options.legend, options.legend);
  assert.equal(calendar.page, "previous year");
  assert.equal(calendar.cellColor(cell), "#ffffff");
  assert(classes.has("rh-theme-magenta") && classes.has("rh-baseline"));
  assert.equal(palette.hidden, false);
  // The same scope remembers its layer on redraw; other decks start normally.
  heatmap.onHmGradient();
  assert.equal(commands.at(-1), "revhm_gradient");
  heatmap.onToggleNewCards();
  classes.add("rh-baseline");
  new globalThis.ReviewHeatmap(options).create(normal);
  assert.deepEqual(globalThis.testCalendar.options.data, options.firstReviews);
  new globalThis.ReviewHeatmap({ ...options, referenceScope: "deck:2" }).create(normal);
  assert.deepEqual(globalThis.testCalendar.options.data, normal);
  new globalThis.ReviewHeatmap({ ...options, showPaletteButton: false }).create(normal);
  assert.equal(palette.hidden, true);
  assert.equal(toggle.style["--rh-review-accent"], "234, 78, 156"); // Current Magenta theme
});

test("calendar bounds, highlights, navigation and browser days agree across clock changes", async t => {
  const { options, commands, mouse } = await heatmapPage(t);
  const previous = process.env.TZ;
  try {
    for (const [zone, date, previousDayHours] of [
      ["Australia/Sydney", "2025-10-05", 23],
      ["Australia/Sydney", "2026-04-05", 25],
      ["America/New_York", "2026-03-08", 23],
      ["America/New_York", "2026-11-01", 25],
      ["Europe/Berlin", "2026-03-29", 23],
      ["Europe/Berlin", "2026-10-25", 25],
      ["Australia/Lord_Howe", "2026-10-04", 23.5],
      ["Australia/Lord_Howe", "2026-04-05", 24.5],
      ["America/Santiago", "2026-09-06", 23],
      ["Asia/Bangkok", "2026-01-01", 24],
      ["America/New_York", "2026-12-31", 24],
    ]) {
      process.env.TZ = zone;
      const day = Date.parse(`${date}T00:00:00Z`) / 1000;
      const heatmap = new globalThis.ReviewHeatmap({ ...options,
        start: (day - 86400) * 1000, stop: (day + 86400) * 1000, today: day * 1000,
        whole: true,
      });
      heatmap.create({ [day]: 1 });
      const calendar = globalThis.testCalendar;
      const cal = calendar.options;
      for (const [field, expected] of [["start", day], ["today", day], ["highlight", day],
        ["minDate", day - 86400], ["maxDate", day + 86400]]) {
        assert.equal(calendarDayKey(cal[field]), expected, `${zone} ${field}`);
      }
      assert.equal(cal.onClick, null);
      const local = calendarDateFromKey(day);
      const parsed = cal.afterLoadData({ [day - 86400]: 2, [day]: 3, [day + 86400]: 4 });
      assert.deepEqual(Object.entries(parsed).map(([key, value]) =>
        [calendarDayKey(new Date(Number(key) * 1000)), value]),
      [[day - 86400, 2], [day, 3], [day + 86400, 4]]);
      assert.match(cal.subDomainTitleFormat(true, { date }, { t: local.getTime(), v: 0 }), /No.*reviews/);
      heatmap.onHmHome({ shiftKey: false });
      assert.equal(calendarDayKey(calendar.rewoundTo), day);
      for (const age of [-1, 0]) {
        const clicked = calendarDateFromKey(day + age * 86400);
        const sent = commands.length;
        // Only the middle button browses, and only a day with cards; a left
        // click (twice: pick, then clear) never opens the browser.
        assert.equal(mouse.middleClick(day + age * 86400, 0).prevented, true);
        mouse.leftClick(day + age * 86400, 1);
        mouse.leftClick(day + age * 86400, 1);
        assert.equal(commands.length, sent);
        assert.equal(mouse.middleClick(day + age * 86400, 1).prevented, true);
        const bounds = commands.at(-1).split(":").slice(2).map(Number);
        const start = new Date(clicked.getFullYear(), clicked.getMonth(), clicked.getDate(), 4);
        const end = new Date(clicked.getFullYear(), clicked.getMonth(), clicked.getDate() + 1, 4);
        assert.deepEqual(bounds, [start.getTime(), end.getTime()], `${zone} browser bounds`);
        assert.equal((bounds[1] - bounds[0]) / 3600000, age === -1 ? previousDayHours : 24);
      }
      mouse.middleClick(day + 86400, -1);
      assert.equal(commands.at(-1), "revhm_browse:prop:due=1");
    }
    process.env.TZ = "UTC";
    for (const [date, firstMonth, endMonth] of [
      ["2026-04-30", "2026-02-01", "2026-08-01"],
      ["2026-10-31", "2026-08-01", "2027-02-01"],
    ]) {
      const heatmap = new globalThis.ReviewHeatmap({ ...options, domain: "month", range: 6,
        today: Date.parse(date), start: Date.parse("2024-01-01"), stop: Date.parse(date),
      });
      heatmap.create({});
      assert.equal(globalThis.testCalendar.options.start.getTime(), Date.parse(firstMonth));
      assert.equal(globalThis.testCalendar.options.maxDate.getTime(), Date.parse(endMonth));
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

// Minimal DOM and frame clock: exercise navigation without waiting in real time.
function progressPage(t) {
  let summary;
  class Element {
    children = []; style = {}; attributes = {}; parent = null;
    get isConnected() { return this === summary || !!this.parent?.isConnected; }
    append(...children) { children.forEach(child => this.appendChild(child)); }
    appendChild(child) { child.parent = this; this.children.push(child); }
    setAttribute(name, value) { this.attributes[name] = value; }
    remove() {
      if (this.parent) this.parent.children = this.parent.children.filter(child => child !== this);
      this.parent = null;
    }
  }
  summary = new Element();
  summary.appendChild(new Element()); // Anki's existing localized text.
  const document = Object.assign(new EventTarget(), {
    hidden: false,
    createElement: () => new Element(),
    getElementById: id => id === "studiedToday" ? summary :
      summary.children.find(child => child.id === id),
  });
  let nextFrame = 0, reducedMotion = false;
  const frames = new Map(), storage = new Map();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const window = Object.assign(new EventTarget(), {
    setTimeout, clearTimeout,
    requestAnimationFrame: callback => { frames.set(++nextFrame, callback); return nextFrame; },
    cancelAnimationFrame: id => frames.delete(id),
    matchMedia: () => ({ matches: reducedMotion }),
  });
  const globals = {
    document, window,
    sessionStorage: {
      getItem: key => storage.get(key) || null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: key => storage.delete(key),
    },
  };
  const original = Object.fromEntries(Object.keys(globals).map(key => [key,
    Object.getOwnPropertyDescriptor(globalThis, key)]));
  Object.assign(globalThis, globals);
  const reload = () => {
    window.dispatchEvent(new Event("pagehide"));
    delete require.cache[outfile];
    return require(outfile).updateTodayProgress;
  };
  t.after(() => {
    window.dispatchEvent(new Event("pagehide"));
    for (const [key, descriptor] of Object.entries(original)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  });
  return {
    document, reload, summary,
    reduceMotion: () => { reducedMotion = true; },
    frame: now => {
      const callbacks = [...frames.values()];
      frames.clear();
      callbacks.forEach(callback => callback(now));
    },
    visible: () => {
      const [track, label] = summary.children.at(-1).children;
      return { fill: track.children[0], track, label };
    },
  };
}

test("progress remembers displayed values across page reloads and interrupted animation", t => {
  const page = progressPage(t);
  const data = { context: "profile-day-baseline", percent: 30, color: "#ff0000" };
  page.reload()(data);
  const update = page.reload();
  update({ ...data, percent: 70, color: "rgba(0, 255, 0, 0.8)" });
  assert.equal(page.visible().fill.style.width, "30%");
  t.mock.timers.tick(649);
  page.frame(0);
  assert.equal(page.visible().label.textContent, "30%");
  t.mock.timers.tick(1);
  page.frame(0);
  page.frame(630);
  const partial = page.visible().fill.style.width;
  assert.ok(parseFloat(partial) > 30 && parseFloat(partial) < 70);
  assert.notEqual(page.visible().fill.style.backgroundColor, data.color);
  const resume = page.reload();
  resume({ ...data, percent: 160, color: "#0000ff" });
  assert.equal(page.visible().fill.style.width, partial);
  t.mock.timers.tick(650);
  page.frame(1000);
  page.frame(2400);
  assert.equal(page.visible().fill.style.width, "100%");
  assert.equal(page.visible().label.textContent, "160%");
  assert.equal(page.visible().fill.style.backgroundColor, "#0000ff");
  assert.equal(page.visible().track.attributes["aria-valuenow"], "100");
  assert.equal(page.summary.children.length, 2);
});

test("progress waits for visibility and skips unrelated or reduced-motion transitions", t => {
  const page = progressPage(t);
  const update = page.reload();
  const data = { context: "today", percent: 20, color: "#ff0000" };
  update(data);
  page.document.hidden = true;
  update({ ...data, percent: 60 });
  t.mock.timers.tick(3000);
  page.frame(3000);
  assert.equal(page.visible().fill.style.width, "20%");
  page.document.hidden = false;
  page.document.dispatchEvent(new Event("visibilitychange"));
  t.mock.timers.tick(650);
  page.frame(4000);
  page.frame(5400);
  assert.equal(page.visible().fill.style.width, "60%");
  update({ ...data, context: "new-day-or-baseline", percent: 5 });
  assert.equal(page.visible().fill.style.width, "5%");
  page.reduceMotion();
  update({ ...data, context: "new-day-or-baseline", percent: 50 });
  assert.equal(page.visible().fill.style.width, "50%");
  assert.match(page.summary.children.at(-1).title, /85% of the reference day's activity/);
  assert.equal(page.visible().track.attributes["aria-valuetext"], "50% of baseline");
  update(null);
  assert.equal(page.summary.children.length, 1);
});

test("recorded durations stay readable at unit boundaries", () => {
  assert.equal(formatRecordedTime(0), "0 s");
  assert.equal(formatRecordedTime(100), "<1 s");
  assert.equal(formatRecordedTime(22500), "23 s");
  assert.equal(formatRecordedTime(45000 * 60), "45 min");
  assert.equal(formatRecordedTime(3600000), "1 h");
  assert.equal(formatRecordedTime(6780000), "1 h 53 min");
});

test("tooltip shows real counts and times, including zero durations", () => {
  assert.equal(reviewSummary(15, 2700000), "<b>45 min</b> recorded · <b>15</b> reviews");
  assert.equal(reviewSummary(1, 0), "<b>0 s</b> recorded · <b>1</b> review");
  assert.match(reviewSummary(1, 100), /&lt;1 s/);
});

for (const timezone of ["UTC", "Asia/Bangkok", "America/New_York", "Europe/Berlin"]) {
  test(`tooltip calendar keys survive DST conversion in ${timezone}`, () => {
    const previous = process.env.TZ;
    process.env.TZ = timezone;
    try {
      for (const [year, month, day] of [
        [2026, 2, 7], [2026, 2, 8], [2026, 2, 9],
        [2026, 2, 29], [2026, 9, 25], [2026, 10, 1], [2026, 10, 2],
      ]) {
        const utcKey = Date.UTC(year, month, day) / 1000;
        const localCell = calendarDateFromKey(utcKey);
        assert.equal(calendarDayKey(localCell), utcKey);
      }
    } finally {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    }
  });
}

test("a click picks a period whose statistics follow the pointer until fixed", async t => {
  const { options, normal, day, lines, storage, mouse, commands, dom } = await heatmapPage(t);
  const D = 86400;
  const history = {
    [day - 8 * D]: [10, 600000], [day - 7 * D]: [30, 1800000], [day - 4 * D]: [20, 60000],
    [day - 3 * D]: [40, 120000], [day - 2 * D]: [50, 3000000], [day - D]: [60, 240000],
  };
  const firstReviews = { [day - 2 * D]: 1, [day]: 2 };
  const page = { ...options, history, firstReviews };
  // The script runs while the page is still being parsed, lines not yet there.
  dom.statsParsed = false;
  globalThis.document.readyState = "loading";
  const heatmap = new globalThis.ReviewHeatmap(page);
  heatmap.create(normal);
  assert.equal(dom.listeners.DOMContentLoaded, undefined); // Nothing saved to apply.
  dom.statsParsed = true;
  const calendar = globalThis.testCalendar;
  const review = () => lines[".rh-review-stats .streak"].innerHTML;
  const newCards = () => lines[".rh-new-card-stats .streak"].innerHTML;
  // Anki redraws the whole page, lifetime lines included, before a new script runs.
  const redraw = page => {
    lines[".rh-review-stats .streak"].innerHTML = "lifetime review line";
    lines[".rh-new-card-stats .streak"].innerHTML = "lifetime new-card line";
    const script = new globalThis.ReviewHeatmap(page);
    script.create(normal);
    return script;
  };
  // The border of a day: [stroke, width], or null outside the period.
  const border = dayKey => {
    const cell = { t: calendarDateFromKey(dayKey).getTime() };
    const stroke = globalThis.testCalendar.styles.stroke(cell);
    return stroke && [stroke, globalThis.testCalendar.styles["stroke-width"](cell)];
  };
  const thin = ["var(--rh-period-stroke)", "1px"], thick = ["var(--rh-period-stroke)", "2.5px"];
  assert.equal(review(), "lifetime review line");
  assert.equal(calendar.options.onClick, null);
  assert.equal(calendarDayKey(calendar.options.highlight), day);

  // A first click anchors the period on that day alone.
  mouse.leftClick(day - 4 * D);
  assert.deepEqual(JSON.parse(storage.get("rh-period:collection-a:global")), { anchor: day - 4 * D, end: null });
  assert.deepEqual([border(day - 5 * D), border(day - 4 * D), border(day - 3 * D)], [null, thick, null]);
  assert.match(review(), /^<span class="streak-info">Daily average:<\/span> <span [^>]*class="sstats rh-col13">20 cards</);
  assert.match(review(), /Days learned:<\/span> <span [^>]*class="sstats rh-col19">100%</);
  assert.match(review(), /Streak at end:<\/span> <span [^>]*class="sstats rh-col12">1 day</);
  assert.doesNotMatch(review(), /Mar 5/);
  assert.equal(lines[".rh-review-stats .streak"].title,
    "Mar 5, 2026, 1 day: 1 min recorded, 20 reviews. Click another day to fix the period, or its first day again to clear it.");

  // Hovering extends it either way; future days count up to today only.
  mouse.hover(day, -20);
  assert.match(review(), /rh-col16">43 cards<.*rh-col15">80%<.*Longest streak:.*rh-col12">4 days<.*Streak at end:.*rh-col12">4 days</);
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 5 – Mar 9, 2026, 5 days: 57 min recorded, 170 reviews\./);
  assert.match(newCards(), /New cards\/day:<\/span> <span [^>]*rh-col12">2 cards<.*Days with new cards:.*rh-col12">40%<.*rh-col12">1 day<.*Streak at end:.*rh-col12">1 day</);
  assert.match(lines[".rh-new-card-stats .streak"].title, /5 days: 3 new cards first reviewed\./);
  assert.deepEqual([border(day - 5 * D), border(day - 4 * D), border(day - 3 * D), border(day), border(day + D)],
    [null, thick, thin, thick, null]);
  // The vendor's year background rect has no day: it is never outlined.
  assert.equal(globalThis.testCalendar.styles.stroke(1234567890), null);
  assert.equal(globalThis.testCalendar.styles["stroke-width"]({}), null);
  const toToday = review();
  mouse.hover(day + D, -20);
  assert.equal(review(), toToday);
  assert.deepEqual(border(day), thick);
  mouse.hover(day - 8 * D);
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 1 – Mar 5, 2026, 5 days/);
  assert.match(review(), /rh-col13">20 cards<.*rh-col13">60%<.*rh-col12">2 days<.*Streak at end:.*rh-col12">1 day</);
  assert.deepEqual([border(day - 8 * D), border(day - 6 * D), border(day - 4 * D), border(day - 2 * D)],
    [thick, thin, thick, null]);
  mouse.leave();
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 5, 2026, 1 day/);
  assert.equal(border(day - 8 * D), null);

  // A second click fixes the period; the pointer no longer matters.
  mouse.leftClick(day, -20);
  assert.equal(review(), toToday);
  assert.match(lines[".rh-review-stats .streak"].title,
    /5 days: 57 min recorded, 170 reviews\. Click the period's first or last day to clear it, or another day to start over\.$/);
  mouse.hover(day - 8 * D);
  mouse.leave();
  assert.equal(review(), toToday);
  assert.deepEqual([border(day - 4 * D), border(day - 1 * D), border(day)], [thick, thin, thick]);
  assert.deepEqual(JSON.parse(storage.get("rh-period:collection-a:global")), { anchor: day - 4 * D, end: day });

  // Future days never start a period; another past day starts a new one.
  mouse.leftClick(day + D, -20);
  assert.equal(review(), toToday);
  mouse.leftClick(day - 7 * D);
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 2, 2026, 1 day/);
  assert.deepEqual([border(day - 7 * D), border(day)], [thick, null]);
  // Clicking the anchor again clears everything.
  mouse.leftClick(day - 7 * D);
  assert.equal(review(), "lifetime review line");
  assert.equal(newCards(), "lifetime new-card line");
  assert.equal(lines[".rh-review-stats .streak"].title, "");
  assert.equal(storage.has("rh-period:collection-a:global"), false);
  assert.equal(border(day - 7 * D), null);

  // A fixed period survives a page redraw of the same scope, reaching the
  // lines once the parser has produced them; clicking an edge clears it.
  mouse.leftClick(day - 4 * D);
  mouse.leftClick(day - 2 * D);
  dom.statsParsed = false;
  redraw(page);
  assert.equal(calendarDayKey(globalThis.testCalendar.options.highlight), day);
  assert.deepEqual([border(day - 4 * D), border(day - 3 * D), border(day - 2 * D)], [thick, thin, thick]);
  assert.equal(review(), "lifetime review line");
  dom.statsParsed = true;
  dom.listeners.DOMContentLoaded();
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 5 – Mar 7, 2026, 3 days/);
  assert.match(review(), /rh-col15">37 cards<.*rh-col19">100%<.*rh-col12">3 days<.*Streak at end:.*rh-col12">3 days</);
  redraw({ ...page, referenceScope: "deck:2" });
  assert.equal(review(), "lifetime review line");
  assert.equal(border(day - 3 * D), null);
  redraw(page);
  assert.match(lines[".rh-review-stats .streak"].title, /^Mar 5 – Mar 7, 2026/);
  mouse.leftClick(day - 2 * D);
  assert.equal(review(), "lifetime review line");
  assert.equal(storage.has("rh-period:collection-a:global"), false);

  // Browsing moved to the middle button; a left click never browses.
  assert.equal(mouse.middleClick(day - 2 * D, 50).prevented, true);
  assert.match(commands.at(-1), /^revhm_browse:deck:current rid:\d+:\d+$/);
  const sent = commands.length;
  mouse.middleClick(day - 5 * D, null);
  mouse.leftClick(day - 2 * D, 50);
  mouse.leftClick(day - 2 * D, 50);
  assert.equal(commands.length, sent);
});

test("the tooltip explains the mouse buttons while Python asks for it", async t => {
  const { options, normal, day, mouse, commands } = await heatmapPage(t);
  new globalThis.ReviewHeatmap({ ...options, mouseHint: true }).create(normal);
  const cell = { t: calendarDateFromKey(day).getTime(), v: 2 };
  const format = { date: "March 9", count: "2", connector: "on" };
  assert.match(globalThis.testCalendar.options.subDomainTitleFormat(false, format, cell),
    /^<b>2<\/b> cards <b>reviewed<\/b> on March 9<span class="rh-mouse-hint">New: middle-click opens the browser, click two days to select a period<\/span>$/);
  // The day is reported once per page, on the first hover.
  const sent = commands.length;
  mouse.hover(day);
  mouse.hover(day - 86400);
  assert.deepEqual(commands.slice(sent), [`revhm_mousehint:${day}`]);
  // Python stops asking once the note was seen on enough days.
  new globalThis.ReviewHeatmap(options).create(normal);
  assert.equal(globalThis.testCalendar.options.subDomainTitleFormat(false, format, cell),
    "<b>2</b> cards <b>reviewed</b> on March 9");
  mouse.hover(day);
  assert.equal(commands.length, sent + 1);
});

test("period statistics mirror the lifetime line's rules", () => {
  const D = 86400, today = Date.UTC(2026, 2, 9) / 1000;
  const counts = { [today - 8 * D]: 10, [today - 7 * D]: 30, [today - 4 * D]: 20,
    [today - 3 * D]: 40, [today - 2 * D]: 50, [today - D]: 60 };
  const activity = day => [counts[day] || 0, (counts[day] || 0) * 1000];
  const toToday = periodStats(today, today - 4 * D, today, activity);
  assert.deepEqual(toToday, { start: today - 4 * D, end: today, days: 5, activeDays: 4, total: 170,
    milliseconds: 170000, average: 43, percent: 80, longest: 4, final: 4 });
  // A finished day with no reviews ends the streak; an unfinished today does not.
  assert.equal(periodStats(today - 8 * D, today - 6 * D, today, activity).final, 0);
  assert.equal(periodStats(today - 8 * D, today - 7 * D, today, activity).final, 2);
  assert.equal(periodStats(today - 8 * D, today - 6 * D, today - 6 * D, activity).final, 2);
  assert.equal(periodStats(today - 8 * D, today - 5 * D, today - 5 * D, activity).final, 0);
  const empty = periodStats(today - 6 * D, today - 5 * D, today, activity);
  assert.deepEqual([empty.days, empty.activeDays, empty.average, empty.percent, empty.longest, empty.final],
    [2, 0, 0, 0, 0, 0]);
  const levels = [[0, "rh-col0"], [14, "rh-col12"], [30, "rh-col14"]];
  assert.deepEqual([0, 1, 14, 15, 30, 31].map(value => levelClass(value, levels)),
    ["rh-col0", "rh-col12", "rh-col12", "rh-col14", "rh-col14", "rh-col14"]);
  assert.equal(levelClass(5, []), "rh-col0");
  assert.deepEqual([pluralize(0, "day"), pluralize(1, "day"), pluralize(2, "card")], ["0 day", "1 day", "2 cards"]);
  assert.equal(formatPeriod(today, today), "Mar 9, 2026");
  assert.equal(formatPeriod(today - 8 * D, today), "Mar 1 – Mar 9, 2026");
  assert.equal(formatPeriod(Date.UTC(2025, 11, 28) / 1000, Date.UTC(2026, 0, 4) / 1000), "Dec 28, 2025 – Jan 4, 2026");
});
