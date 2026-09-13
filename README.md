# Review Heatmap

An Anki add-on for visualizing study activity, streaks, and upcoming reviews.
This fork extends [Glutanimate's Review Heatmap](https://github.com/glutanimate/review-heatmap)
and retains the inherited daylight-saving fix.

## Usage

Open **Tools → Review Heatmap Options → Activity** and choose a measure:

- **Review count (classic):** original review counts and color scaling;
  selected by default.
- **Study time (linear):** Anki's recorded review duration.
- **Workload (review-weighted):** gives review count more influence than time,
  using `reviews × ∛(average recorded minutes per review)` internally.

Hover over a past day to see its recorded time and review count. Streaks still
count days with reviews, and future dates show cards due.

Study time and Workload offer two color scales:

- **Fixed scale:** stable thresholds independent of other days.
- **Automatic baseline:** selects a strong recent study day, or lets you choose
  one. The reference stays fixed until recalculated. Automatic selection uses
  the 75th percentile of active days in the previous 60 completed days, with at
  least seven days of recorded activity; otherwise, fixed thresholds apply.

Time comes directly from Anki. Set **Deck Options → Timers → Maximum answer
seconds** to suit your cards; a one-time reminder explains this setting.

Existing review history and settings are preserved. The heatmap is rebuilt
from Anki's review log; its display cache is not a separate history archive.

## Build and install

Qt 5 support has been removed; this fork requires a Qt 6 version of Anki.

Building requires Python, Node.js, and npm. In a Python virtual environment, run:

```sh
python -m pip install -r requirements.txt
npm ci
python scripts/build_worktree.py
```

The script packages the current files using Qt 6's UI compiler, esbuild, and
Python's standard library, without changing Git history. End users do not need
the build dependencies.

Open `build/review-heatmap-workload-preview.ankiaddon` in Anki and restart.

## Credits and license

Original add-on copyright © 2016–2022 Aristotelis P.
([Glutanimate](https://glutanimate.com/)). Includes d3.js (BSD) and cal-heatmap
(MIT). Distributed under GNU AGPLv3 with additional terms; see [LICENSE](LICENSE)
and the bundled library notices.
