const assert = require("node:assert/strict");
const { after, test } = require("node:test");
const { mkdtempSync, rmSync } = require("node:fs");
const { tmpdir } = require("node:os");
const { join, resolve } = require("node:path");
const { buildSync } = require("esbuild");

const temporary = mkdtempSync(join(tmpdir(), "review-heatmap-web-"));
const outfile = join(temporary, "activity.cjs");
buildSync({
  entryPoints: [resolve(__dirname, "../src/web/activity.ts")],
  bundle: true,
  platform: "node",
  format: "cjs",
  outfile,
});
const { calendarDayKey, formatRecordedTime, reviewSummary } = require(outfile);
after(() => rmSync(temporary, { recursive: true, force: true }));

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
        // Mirrors the fork's existing afterLoadData conversion.
        const localCell = new Date(year, month, day, 0, 0, 0, 0);
        assert.equal(calendarDayKey(localCell), utcKey);
      }
    } finally {
      if (previous === undefined) delete process.env.TZ;
      else process.env.TZ = previous;
    }
  });
}
