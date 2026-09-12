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
