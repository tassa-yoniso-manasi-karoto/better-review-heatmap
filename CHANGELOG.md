# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

<!--
FORMAT REQUIREMENTS:

- Version headers MUST be: ## [x.y.z] or ## [x.y.z-tag] - YYYY-MM-DD
  Examples: ## [1.0.0] - 2024-01-15  or  ## [1.0.0-alpha] - Unreleased
- Section headers MUST be: ### Added, ### Changed, ### Fixed, ### Deprecated,
  ### Removed, or ### Security (exactly these names, case-sensitive)
- List items MUST start with "- " (hyphen + space)
- Keep one blank line between sections

Guidelines for LLMs updating this changelog from git history:

- Only include changes relevant to end users (ordinary Anki users, not developers)
- Heed the chronology and thus do NOT list "fixes" for things that were never released: they're not fixes from the user's perspective
- Omit internal/technical changes (system diagnostics, refactoring, etc.)
- Aggregate related small improvements into single descriptive lines
- Keep entries concise: one line per feature/change, avoid technical jargon
- Focus on user benefits: what can they do now? what's better for them?
- This file must remain machine-parsable (Keep a Changelog format)
- The use of em dashes or hyphen as punctuation is absolutely forbidden
-->


## [2.1.0] - 2026-09-24

### Added
- Added support for a **new-card-only heatmap** and a toggle to switch back and forth
- Added **Time-only** and **Custom Workload** modes with configurable metric weights
- Added an **in-app changelog tab** natively rendered in markdown

### Changed
- **Overhauled entirely workload math**
- **Adjusted scoring for workload "linear" and "review-weighted" modes**
- Configured automatic references to **refresh every 30 days** to keep targets relevant as study habits evolve
- Shifted the automatic target baseline to the 90th percentile to represent a more challenging and ambitious goal
- Restricted the today's progress bar display to baseline mode only
- Renamed settings tabs and reorganized mode selection UI

### Fixed
- Upgraded the fixed threshold scale to an **adaptive scale** based on median historical activity
- Fixed an issue where the workload of individual decks was unfairly evaluated against collectionwide goals
- Restricted custom gradient colors to reference-day baseline coloring

## [2.0.2] - 2026-09-14

### Changed
- Adjusted the wording of the popup presented on first installation

## [2.0.1] - 2026-09-13

### Fixed
- Restored compatibility with Anki Qt5 builds

## [2.0.0] - 2026-09-13

### Added
- Added workload measurement options that account for review length
- Added a progress bar displaying today's workload completion compared to a reference day
- Added in-app palette editing controls to customize workload colors
- Added a compact toolbar beside the heatmap for simpler control

### Changed
- Shifted to visually rewarding workload colors and continuous gradients
- Simplified the selection of a reference day for target setting
- Removed promotional UI elements and unneeded libraries

### Fixed
- Fixed an issue where clicking on a specific deck's historical data would ignore the current deck filter

## [1.0.2] - 2025-10-06

### Fixed
- Fixed a timezone adjustment bug that caused incorrect display during Daylight Saving Time transitions

## [1.0.1] - 2022-05-24

### Fixed

- Fixed due counts showing NaN for counts >1000 *(thanks to @chkmte for the report)*
- Fixed an error that could appear when deleting an ignored deck *(thanks to /u/GabiGrice_FalcoBraun & @purequant for the report)*
- Fixed an error that could appear when switching profiles *(thanks to @rafaelcdepaula for the report)*
- Fixed an issue that could cause the add-on to raise an error when importing colpkg files
- Added a workaround to enable compatibility with the Stats Plus add-on *(thanks to @MickyAnne, @kguy18, @swerage, and @agentca for the reports and testing)*

## [1.0.0] - 2022-05-15

**IMPORTANT**: While likely compatible with earlier versions, this add-on release has only been extensively tested with Anki 2.1.49 and up. The AnkiWeb upload is therefore limited to Anki 2.1.49+ for the time being (but might be expanded to earlier releases in the future).

If you are on an earlier Anki release there is no need to worry: Copying the AnkiWeb add-on code into the app will automatically download a compatible version of the add-on for you. However, please be aware that it will not include the latest changes below.

### Added

- Added support for **Anki 2.1.50 and up**. Both Qt6 and Qt5 builds are supported. On Apple silicon the add-on now benefits from the performance improvements that Anki's native Qt6 build provides.
- Added an option to **exclude review history entries** created by manually **rescheduling or forgetting** cards. This option is enabled by default, but can be disabled under Settings → Fine Tuning → *Exclude manual reschedules from history*
- Added a number of changes that should improve the **rendering performance** of the heatmap when switching between Anki screens


### Fixed

- Fixed an issue where invalid card scheduling could cause the heatmap to disappear
- Fixed an issue where clicking on today's repetitions would not draw up any cards in the browser
- Fixed an issue where config changes could not be written
- On recent Anki versions: Potentially fixed a number of issues with time-zone handling that would sometimes cause mismapping of repetitions to the wrong days. If you are still experiencing issues like this, I would kindly ask you to please [report them here](https://github.com/glutanimate/review-heatmap/issues/151) in order to help me troubleshoot them. 

### Changed

- Completely refactored the add-on codebase and rewrote many parts of it, making Review Heatmap easier to maintain and extend in the future
- Switched to new canonical add-on APIs where possible, reducing the risk for future breakages as new Anki versions get released
- Removed heatmap toggle hotkey as it was not particularly discoverable nor particularly useful and caused keymap conflicts with other add-ons
- Raised the minimum Anki version requirement to 2.1.28. However, only 2.1.49 and up were tested extensively, so your mileage on earlier releases might vary.

## 1.0.0-beta.2 - 2022-05-15

Temporary test release. Changelog entry merged into v1.0.0 changes.

## [1.0.0-beta.1] - 2020-04-30

### Added

- Added support for **Anki 2.1.24** and up (database access, new hook system, new finder extensions)

### Fixed

- Improved **timezone handling**. This should hopefully fix some of the issues users were experiencing around DST changes. If you are still experiencing issues [please let me know](https://github.com/glutanimate/review-heatmap/issues/71). Troubleshooting timezone-related problems [is hard](https://www.youtube.com/watch?v=-5wpm-gesOY), so I'm grateful for every additional tester that can help replicate things (#71)
- Fixed **daily average** calculation towards beginning of streaks (#40, #50)
- Improved assignment of forecasts to calendar days
- Added a workaround for an Anki bug present on 2.1.14 and lower

### Changed

- **Dropped support for Anki 2.0** (thus the major add-on version bump)
- Added debugging utilities that allow users to submit detailed debug logs when troubleshooting
- Added link to changelog in options screen
- **Removed the `seen:` query phrase**. This had been deprecated in the past, and with Anki 2.1.24 now no longer supporting addon-provided queries for filtered decks, it no longer made sense to keep it.

## [0.7.0-beta.1] - 2018-10-28

### Added

- Full **Anki 2.1 support** (and 2.0 backwards compatibility)
    - Yes, it's finally here!
- **Completely reworked months mode**
    - shows a **continuous timeline** of activity, instead of being bound to a yearly schedule
    - more **granular insight** into monthly activity, and activity by weekday
- A **multitude of new options**, granting you more control over your stats than ever before:
    - the ability to set a **starting date** for the heatmap
    - the ability to **exclude certain decks** from being tracked
    - an option to **exclude deleted cards** from your stats
    - the ability to set a custom hotkey to toggle the heatmap
- **Secondary actions** when **Shift-clicking** heatmap **buttons**:
    - quickly move to the very beginning of your review history
    - quickly move to the last scheduled review
    - instantly change the display mode or color theme
    - and more!
- **Support** for the **[Night Mode](https://ankiweb.net/shared/info/1496166067) add-on**
    - all **themes** now come with beautiful **night mode versions** that are automatically switched on when Night Mode is active (requires Night Mode version 2.2.3 and up)
    - special thanks to [Michał Krassowski](https://github.com/krassowski) for laying the groundwork on Night Mode's side!
- A **beautiful new UI** with updated buttons, new tooltips, and more polish than ever before


### Changed

- **Completely rewrote** the add-on. Review Heatmap's codebase hadn't really changed that much since the add-on' first release nearly two years ago. This rewrite, although arduous and very time-consuming, was long overdue and now provides a stable and maintainable base for future additions to Review Heatmap.
- Fully **redesigned options dialog**, which can now also be accessed directly from the heatmap.
- On Anki 2.1 the heatmap now loads asynchronously, i.e. without blocking page load, which should make for faster switching between Anki's screens even with long review histories
- History and forecast limits are now inactive by default. The previous default of limiting the displayed data to a period of 365 days could be confusing sometimes.

### Fixed

- Fixed a series of elusive bugs surrounding different time-zones, daily cutoff settings, and other date mismatches (#23)
- Fixed incompatibilities with the Night Mode add-on (#9)
- Fixed view scrolling sometimes not working (#16)
- Fixes some macOS- and Windows-specific layout and sizing issues
- Quite a few other smaller issues which would take too long to list here


### Deprecated

- the `seen:` query phrase has been superseded by the new `rid:` phrase, and is likely to be removed in the future

### Other

- Throughout the redesign of Review Heatmap I had to develop a completely new set new tools for building and testing my add-ons. The fruits of this work will also benefit my other projects, making for a faster iteration time, and more 2.1 add-on releases coming soon!
- Review Heatmap and all of the other add-ons I'm working on are now tested against all platforms supported by Anki. If you are a macOS or Windows user you should see a lot of improvements in ironing out platform-specific kinks in the future.


## [0.6.1-anki21-alpha] - 2018-09-23

### Added

- cursory Anki 2.1 support

### Fixed

- A number of smaller bug fixes and improvements

## [0.6.0] - 2017-02-19
### Note

**Important**: As this version uses a new file structure you will have to remove older versions of the add-on before installing it.


### Added
- Toggle heatmap display on the deck browser and overview pages by using CTRL + R

### Changed
- New modularized add-on structure which should make maintenance easier


## [0.5.2] - 2017-01-15
### Fixed
- Fix review count tooltips for counts over 999 (#3)

## [0.5.1] - 2017-01-11
### Fixed
- Fix an error in the calculation of "days learned" (thanks to David Bailey!)

## [0.5.0] - 2017-01-10
### Added
- Stats on average review counts and days learned, each graded with a custom color scale
- Customizable calendar modes - display the days as a continuous year, or split them up into months
- Customizable color schemes - choose between 5 different color palettes
- You can now decide where the heatmap will appear
- All of the above and all other settings can now be contolled through an options menu
- The heatmap will also appear in the stats window now. This one is unaffacted by any limits and follows the interval settings set in the stats window.

### Changed

- Limit heatmap data range to 365 days for past reviews and 90 for pending reviews by default. This should improve performance on lower-end machines. These limits can be lifted in the options menu.

### Fixed

- Fix negative pending review counts (thanks to David Bailey!)

## 0.2.0 - 2017-01-01
### Changed
- Switched to relative scaling and scoring of user activity

## 0.1.0 - 2016-12-31

First release of Review Heatmap.
