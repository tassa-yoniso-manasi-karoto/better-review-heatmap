An Anki add-on for visualizing study activity, streaks, and upcoming reviews.
This fork extends [Glutanimate's Review Heatmap](https://github.com/glutanimate/review-heatmap)
and retains the inherited daylight-saving fix of [@olliecheng](https://github.com/olliecheng/review-heatmap).

<p align="center">
    <img src="https://github.com/tassa-yoniso-manasi-karoto/better-review-heatmap/raw/refs/heads/master/screenshots/animated.avif" alt="Better Review Heatmap Demo" width="800" />
</p>

## Usage

Open **Tools → Review Heatmap Options → Display Mode** and choose a measure:

- **Review count (classic):** original review counts and color scaling.
- **Workload (linear):** gives equal weight to review count and time.
- **Workload (review-weighted):** gives review count slightly more influence
  than time.
- **Workload (custom):** allows you to choose your own balance between reviews
  and time.
- **Recorded time:** scores days strictly by study duration.
- **⭐ FSRS-based (exp.)** (default): based on review-weighted workload, except each answer counts a little more when its card is new, recently learned or difficult for you, and a little less when it is mature and easy.
- **🧪 FSRS-based + sustained concentration (exp.):** the same but workload calculations additionally rewards long uninterrupted stretches on cards with long review (x s/card) time, reward fades once a sitting runs past about an hour.

The experimental measures come with sensible defaults. **Calibrate…**, next to
the measure, lists every setting with an explanation on hover. Only settings
you change are kept, so improved defaults in future versions still reach you;
**Restore defaults** forgets your changes. See [DESIGN.md](DESIGN.md) for the formulas.

Workload modes offer two color scales:

- **Classic:** scale dynamically adapts to your median historical activity.
- **Baseline:** selects a strong recent study day, or lets you choose one.
  Automatic selection uses the **90th percentile** of active days and
  **refreshes every 30 days** to keep targets relevant as your study habits
  evolve. **I would strongly recommend taking the time to handpick the
  reference day yourself to choose something that matches your goals.**

With a baseline, the target and full progress bar represent **85% of the
reference day's score**.

> [!IMPORTANT]
> Time comes directly from Anki. Set **Deck Options → Timers → Maximum answer
> seconds** to suit your cards.

Days on the heatmap answer to the mouse:

- **Click a day, then a second one** to select a period: while you hover, the
  statistics line below switches to the daily average, days learned and
  streaks of just those days, and the heatmap outlines them. The second click
  fixes the period; clicking its first or last day clears it. This works in the
  new-card view and on a single deck's overview too.
- **Middle-click a day** to open its cards in the browser.

Just like in the original:

- Hover over a past day to see its recorded time and review count. Streaks still
  count days with reviews, and future dates show cards due.
- Existing review history and settings are preserved. The heatmap is rebuilt
  from Anki's review log; its display cache is not a separate history archive.

## Build and install

Users should [download the addon from Ankiweb](https://ankiweb.net/shared/info/1868371602).

#### For developers:
Building requires Python, Node.js, npm, and the system Qt 5 UI compiler
(`uic-qt5`) on your PATH. In a Python virtual environment, run:

```sh
python -m pip install -r requirements.txt
npm ci
python scripts/build_worktree.py
```

The script compiles forms for both Qt 5 and Qt 6, bundles JavaScript with
esbuild, and packages the current files without changing Git history.

Open the generated `build/better-review-heatmap-<version>.ankiaddon` in Anki
and restart.

> [!NOTE]
> This fork requires Anki 2.1.49 or later. The same archive supports Qt 5 and Qt 6.

## Credits and license

Based on the Anki add-on [Review Heatmap](https://github.com/glutanimate/review-heatmap/) by Glutanimate.

Original add-on copyright © 2016–2022 Aristotelis P. ([Glutanimate](https://glutanimate.com/)).

Fork modifications copyright © 2026 tassa-yoniso-manasi-karoto and © 2025 Oliver Cheng.

Includes d3.js (BSD) and cal-heatmap (MIT). Distributed under GNU AGPLv3 with additional terms; see [LICENSE](LICENSE) and the bundled library notices.
