An Anki add-on for visualizing study activity, streaks, and upcoming reviews.
This fork extends [Glutanimate's Review Heatmap](https://github.com/glutanimate/review-heatmap)
and retains the inherited daylight-saving fix of [@olliecheng](https://github.com/olliecheng/review-heatmap).

<p align="center">
    <img src="https://github.com/tassa-yoniso-manasi-karoto/better-review-heatmap/raw/refs/heads/master/screenshots/animated.avif" alt="Better Review Heatmap Demo" width="800" />
</p>

## Usage

Open **Tools → Review Heatmap Options → Activity** and choose a measure:

- **Review count (classic):** original review counts and color scaling.
- **Workload (linear):** balances reviews and recorded time with equal ½/½
  weights. “Linear” describes equal weighting, not a count × time calculation.
- **Workload (review-weighted)** (default): favors review volume with ⅗/⅖
  review/time weights, while still crediting longer answers.
- **Workload (custom):** choose your own review/time weights; editing either
  adjusts the other so they total 1.
- **Recorded time:** colors reflect total Anki-recorded minutes alone.

Workload modes calculate credit for each answer, then add it for the day.
Repeated answers count separately. See [DESIGN.md](DESIGN.md) for formulas
and the reasons behind them.

Workload and recorded-time modes offer two color scales:

- **Fixed scale** (default): stable thresholds independent of other days.
- **Automatic baseline:** selects a strong recent study day, or lets you choose
  one. Automatic selection uses the 75th percentile by score of included active
  days in the previous 60 completed days, requiring at least seven such days;
  otherwise, fixed thresholds apply. References are saved separately per mode,
  custom weight, and history filters. Handpick a day that matches your goals.

With a baseline, the target and full progress bar represent **85% of the
reference day's score**. White marks the reference date; the color jump at the
target intentionally distinguishes days below and above it. The progress bar
can be enabled under **Appearance** for any nonclassic mode using a baseline.

> [!IMPORTANT]
> Time comes directly from Anki. Set **Deck Options → Timers → Maximum answer
> seconds** to suit your cards; a one-time reminder explains this setting.

On the calendar:

- Hover over a past day to see its recorded time and review count. Streaks still
  count days with reviews, and future dates show cards due.
- Existing review history and settings are preserved. The heatmap is rebuilt
  from Anki's review log; its display cache is not a separate history archive.

Saved reference dates are retained during formula upgrades. If the original
durations are unavailable, the old snapshot is kept and the fixed scale is
used until you choose another reference.

## Build and install

Building requires Python, Node.js, npm, and the system Qt 5 UI compiler
(`uic-qt5`) on your PATH. In a Python virtual environment, run:

```sh
python -m pip install -r requirements.txt
npm ci
python scripts/build_worktree.py
```

The script compiles forms for both Qt 5 and Qt 6, bundles JavaScript with
esbuild, and packages the current files without changing Git history.
End users do not need the build dependencies.

Open the generated `build/better-review-heatmap-<version>.ankiaddon` in Anki
and restart.

> [!NOTE]
> This fork requires Anki 2.1.50 or later. The same archive supports Qt 5 and Qt 6.

## Credits and license

Based on the Anki add-on [Review Heatmap](https://github.com/glutanimate/review-heatmap/) by Glutanimate.

Original add-on copyright © 2016–2022 Aristotelis P.
([Glutanimate](https://glutanimate.com/)). Fork modifications copyright © 2026
tassa-yoniso-manasi-karoto and © 2025 Oliver Cheng. Includes d3.js (BSD) and cal-heatmap
(MIT). Distributed under GNU AGPLv3 with additional terms; see [LICENSE](LICENSE)
and the bundled library notices.
