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
          root = { selectAll: () => ({ style: (_, color) => { this.cellColor = color; } }) };
          init(options) { this.options = options; globalThis.testCalendar = this; options.afterLoad(); }
          setLegend(legend) { this.options.legend = legend; }
          update(data) { this.options.data = data; this.options.afterUpdate(); }
          highlight() {}
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
  const container = { classList: {
    contains: name => classes.has(name),
    toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
  } };
  const toggle = { attributes: {}, style: { setProperty(name, value) { this[name] = value; } },
    setAttribute(name, value) { this.attributes[name] = value; } };
  const palette = {};
  const elements = {
    "cal-heatmap": { closest: () => container },
    "review-heatmap-new-cards": toggle, "review-heatmap-palette": palette,
  };
  const storage = new Map(), commands = [];
  globalThis.document = { createElement: () => ({}), head: { appendChild() {} },
    getElementById: id => elements[id] };
  let refreshPalette;
  globalThis.window = { setInterval: callback => { refreshPalette = callback; } };
  globalThis.sessionStorage = { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) };
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
  };
  const normal = { [day]: 5, [day + 86400]: -20 };
  const heatmap = new globalThis.ReviewHeatmap(options);
  return { heatmap, options, normal, toggle, palette, classes, cell, day, refreshPalette, commands };
}

test("first-review toggle preserves the calendar page and restores normal colors and data", async t => {
  const { heatmap, options, normal, toggle, palette, classes, cell, day, refreshPalette, commands } =
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
  calendar.options.onClick(new Date(cell.t), 2);
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
  const { options, commands } = await heatmapPage(t);
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
        cal.onClick(clicked, 1);
        const bounds = commands.at(-1).split(":").slice(2).map(Number);
        const start = new Date(clicked.getFullYear(), clicked.getMonth(), clicked.getDate(), 4);
        const end = new Date(clicked.getFullYear(), clicked.getMonth(), clicked.getDate() + 1, 4);
        assert.deepEqual(bounds, [start.getTime(), end.getTime()], `${zone} browser bounds`);
        assert.equal((bounds[1] - bounds[0]) / 3600000, age === -1 ? previousDayHours : 24);
      }
      cal.onClick(calendarDateFromKey(day + 86400), -1);
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
