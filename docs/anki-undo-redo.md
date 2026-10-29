# Anki undo/redo: what to know before extending it

For maintainers and coding agents who make Better Review Heatmap features
undoable through Anki's Edit > Undo / Redo. Everything below was verified in
Anki's source (tags 2.1.49 and 26.09.3; paths at the end) and against the
real backend.

## Rules of thumb

1. Anki can only undo **collection** data. A setting is undoable only if it
   lives in the collection config key `heatmap` (the add-on's "synced"
   storage). `meta.json` ("local") and `mw.pm.profile` ("profile") are not.
2. Write a user change as one labelled step:
   `add_custom_undo_entry(label)`, `set_config(key, value, undoable=True)`,
   `merge_undo_entries(step)`. In this add-on: `undo.commit_synced_change()`.
3. Never send SQL through `col.db` that does not begin with `SELECT`, never
   assign into `col.conf[...]`, and never mutate what `col.conf[...]` returns.
   All three silently wipe the user's undo history.
4. After Anki undoes or redoes a step, reload the in-memory config and redraw
   the heatmap yourself; Anki will not.
5. Any quiet write clears Redo. Do not write right after an undo.
6. Never create empty or phantom steps: Anki shows and keeps them.

## How the backend undo works

- `rslib/src/undo/mod.rs`: `undo_steps` (newest first) and `redo_steps`.
  `UNDO_LIMIT = 30` steps, shared with everything else in Anki. A step has a
  kind (`Op`), a timestamp, a list of changes and a counter.
- `rslib/src/collection/transact.rs`: every backend operation runs in
  `transact`, which begins a step, records each change, then ends the step.
  - `Op::SkipUndo` (e.g. `set_config(..., undoable=False)`): the step is
    discarded, the undo history is kept, but beginning it **clears redo**.
  - No op at all (`transact_no_undo`): clears undo **and** redo.
  - Any error inside a transaction: rollback, then undo, redo and the study
    queues are all discarded.
  - The collection mtime bump is recorded inside the step and skipped while
    undoing/redoing, so undo restores the previous `col.mod` and redo the
    newer one.
- Undo pops the newest step and applies its changes in reverse inside a new
  transaction; the inverse changes recorded there become the redo step.
- `add_custom_undo_entry(name)` pushes `Op::Custom(name)` at once.
  `has_changes()` is always true for custom ops, so an empty custom step
  still shows as "Undo name" and undoing it does nothing.
- `merge_undo_entries(counter)` folds **every** step newer than the target
  into it, whatever produced them, then resets the global counter to
  `counter`. It raises "target undo op not found" if the target was evicted
  or cleared.
- Config undo records the key's whole previous value. Undo restores the entire
  `heatmap` blob as it was before the step, dropping quiet writes made to it
  afterwards; redo restores the value captured at undo time, which includes
  them.
- `set_config` compares the serialized value: an unchanged value records
  nothing (the `UpdateConfig` step vanishes, but a custom step stays).
- The undo history lives in memory only; restarting Anki or reopening the
  collection empties it.

## What silently clears the history

| Trigger | Undo | Redo |
|---|---|---|
| `set_config(..., undoable=False)`, even with an unchanged value | kept | cleared |
| Any other normal operation (answering, selecting a deck...) | kept | cleared |
| `col.conf[key] = value` (pylib `ConfigManager.set` -> `set_config_json_no_undo`) | cleared | cleared |
| Mutating the `WrappedDict`/`WrappedList` from `col.conf[key]` (written back on `__del__` if changed) | cleared | cleared |
| `col.db.*` SQL not starting with `select` after leading whitespace: `WITH` CTEs, leading comments, `PRAGMA`, `(SELECT ...)` (`dbproxy.rs` `is_dql`) | cleared | cleared |
| Normal sync with changes, full sync | cleared | cleared |
| Check Database, imports, other `transact_no_undo` callers | cleared | cleared |
| A failed backend transaction | cleared | cleared |
| Day rollover while building study queues | cleared | cleared |

Plain reads of `col.conf[key]` do not clear anything; for dicts and lists
they only print "conf key ... should be fetched with col.get_config()".

## The Qt side

- Edit > Undo / Redo call `aqt/operations/collection.py` `undo()` / `redo()`:
  a `CollectionOp` that, on success, runs `on_op_finished`:
  `mw.update_undo_actions()`, then
  `gui_hooks.operation_did_execute(changes, None)`, then
  `gui_hooks.state_did_reset()` if `col.op_made_changes(changes)`. Undo also
  fires `gui_hooks.state_did_undo`; **redo has no hook of its own**, so
  `operation_did_execute` is the only hook covering undo, redo and your own
  operations.
- On failure `CollectionOp` returns before `on_op_finished`, so the Edit menu
  is **not** refreshed, and `undo()` swallows `UndoEmpty` silently. If the
  history was cleared behind the GUI's back, the menu keeps offering
  "Undo X", clicking it does nothing, and the entry never goes away. This is
  exactly what users saw before 2.2.1.
- Menu labels are "Undo {name}" / "Redo {name}" (`aqt/undo.py`). They are
  recomputed only by `mw.update_undo_actions()` (after a successful
  `CollectionOp`, `mw.reset()`, a backup...). Stack changes made elsewhere
  leave the menu stale until then.
- Redraw: `OpChanges.study_queues` is true only for card/deck/deck-config
  changes or config changes from a few ops (`SetCurrentDeck`,
  `UpdatePreferences`, `UpdateDeckConfig`, `ToggleLoadBalancer`;
  `rslib/src/ops.rs`). Undoing a custom config-only step reports
  `config=True`, `study_queues=False`, so `DeckBrowser` / `Overview`
  `op_executed` do **not** redraw. Call `mw.deckBrowser.refresh()` /
  `mw.overview.refresh()` yourself. The Browser's table and sidebar do redraw
  on config changes.
- `mw.reset()` fires `operation_did_execute` with **every** flag set
  (synthesized, handler `None`), then `state_did_reset`, then
  `update_undo_actions()`. Same in 2.1.49 and 26.09. Hook handlers must be
  idempotent and act only on a real difference.
- `sync_did_finish` fires before Anki's post-sync redraw.
- Dialogs opened with `exec()` are modal: no undo can happen while one is
  open.

## API availability

| API | 2.1.49 | 26.09 |
|---|---|---|
| `col.add_custom_undo_entry`, `merge_undo_entries`, `set_config(undoable=)`, `get_config`, `undo_status`, `undo`, `redo` | yes | yes |
| `aqt.operations.CollectionOp(...).run_in_background(initiator=)` | yes | yes |
| `aqt.operations.on_op_finished` | no (private method) | yes |
| `mw.update_undo_actions()`, `col.op_made_changes()`, `mw.reset()` | yes | yes |
| `gui_hooks.operation_did_execute`, `state_did_undo`, `undo_state_did_change` | yes | yes |
| A redo hook | no | no |

## How this add-on does it (since 2.3.0)

- **Storage.** The collection config key `heatmap` (`config.CONF_KEY`) holds
  every undoable setting, including `baseline_gradient`. Profile settings
  (display toggles, streak stats, progress bar, notices) and local settings
  (`baseline_gradient_default`) are deliberately not undoable. A palette
  saved in `meta.json` before 2.3.0 is copied into the collection once, then
  removed from `meta.json` (`config.save_activity_defaults`).
- **Writing.** `undo.commit_synced_change(config, label, change, before=None,
  refresh=True)` reloads synced from the collection, applies `change`, skips
  no-ops, and saves through `ConfigManager.save("synced", undo=label)`, whose
  `_saveSynced` creates the labelled step. `refresh=True` ends with
  `mw.reset()` (redraw and Edit menu); `refresh=False` only calls
  `mw.update_undo_actions()`.
- **Reading back.** `HeatmapController` reloads synced (`load_config`) on
  `operation_did_execute` with `changes.config` and on `sync_did_finish` when
  the stored blob differs from the cache, then redraws the deck list or
  overview.
- **Options dialog.** It edits a tentative copy. A snapshot taken after its
  setup, `three_way_patch(stored, snapshot, edited)` and
  `describe_change(snapshot, edited, reference_source)` turn OK into at most
  one step. Diffs made only of derived data create no step and no write.
- **Gradient dialog.** Edits are previewed live with quiet writes; `done()`
  commits one step per session with `before` = the value stored at open, so
  undo returns to the palette from before the dialog.
- **Renderer.** Automatic reference saves are skipped while
  `col.undo_status().redo` is set, so the redraw after an undo keeps Redo.
- **Labels** are constants in `undo.py`.

## Pitfalls we hit or designed around

1. **Stale cache.** The add-on caches settings in memory. Without a reload
   after undo, redo or sync, its next save writes the old blob back and
   silently reverts the undo (or the other device's change).
2. **libaddon default merging.** After every add-on update `_getStorageObj`
   deep-merges the defaults into the stored blob (only with real version
   stamps; the repo's `_version.py` says `1.0.1`, so dev and tests never run
   it). Never add a default for a key whose absence triggers a migration
   (such as `baseline_gradient`), and expect list values to merge index by
   index.
3. **Phantom steps.** QColor round trips and `30` vs `30.0` normalization;
   dialogs recomputing derived data (reference migrations, the unread legacy
   `activity_reference_date`); deleted decks dropped from `limdecks`;
   `limdate` rounding. Snapshot after dialog setup, judge user intent with
   Python `==`, and use JSON equality only for the backend no-op check.
4. **Labels from data can lie.** Reference migrations keep
   `source: "selected"`; record the user's explicit action instead.
5. **Quiet writes during render clear Redo.** Skip them while
   `undo_status().redo` is set.
6. **Live-preview dialogs.** One step per edit would flood the 30 steps.
   Commit one step per modal session: quietly write the session-start value,
   then write the final value as the undoable step.
7. **Synchronous writes.** The add-on writes on the main thread, like all its
   saves: tiny writes, no async races with dialogs, no busy cursor. If you
   move to `CollectionOp`, remember it is async and its failure path does not
   refresh the menu.
8. **Defaults before the first step.** Keys added by `ensure_activity_defaults`
   are saved right away (`load_config`), so the reload check never sees
   phantom differences.
9. **A reload after an undo must never write**; any write deletes the redo
   step the undo just created.
10. **Profiles.** Synced settings are per collection; `meta.json` is shared by
    every profile on the machine. The palette migration assumes one profile
    per computer: profiles opened after the first get the default palette.
11. **Whole-blob undo.** Undo also rolls back automatic reference updates made
    after the step; the renderer recomputes them.
12. **Other writers.** Older add-on versions on other devices write the same
    blob, and sync is last-writer-wins per config key.

## Checklist: making a new setting undoable

1. Keep it in `config["synced"]`. Add no default if its absence matters.
2. Change it only through `commit_synced_change`, with a new label constant
   phrased "Change Heatmap X" (Anki adds "Undo" / "Redo").
3. If the Options dialog edits it, add its key to `describe_change`; derived
   keys belong in the ignored set.
4. If `mw.reset()` does not redraw the UI that changed it, commit with
   `refresh=False` and redraw that UI yourself.
5. Check that the reload handler redraws every view showing it.
6. Do not write it quietly during render, or guard with
   `undo_status().redo`.
7. Test with `tests/test_undo.py`'s `FakeCollection`: one labelled step, none
   when unchanged, undo/redo round trip, an earlier "Delete Deck" survives,
   no write after an undo.
8. Check in Anki: the Edit menu label, Ctrl+Z / Ctrl+Shift+Z from the main
   window and from the Browser, and a redraw each time.

## Checking against the real backend

Unit tests never open a real collection. To confirm behaviour, run a
throwaway script with pylib against a fresh temporary collection (never a
user's): `Collection(tmp_path)`, commit, check `col.undo_status().undo`, then
`col.undo()` and `col.redo()`.

## Anki source references

- `rslib/src/undo/mod.rs`: undo manager, custom steps, merging.
- `rslib/src/collection/transact.rs`: transactions, SkipUndo, error path.
- `rslib/src/config/mod.rs`: `set_config_json` undoable vs SkipUndo.
- `rslib/src/backend/config.rs`: `set_config_json_no_undo`.
- `rslib/src/backend/dbproxy.rs`: `is_dql` and the undo wipe for `col.db`.
- `rslib/src/ops.rs`: `requires_study_queue_rebuild`.
- `rslib/src/collection/undo.rs`: the mtime change inside each step.
- `pylib/anki/collection.py`: `set_config`, custom entries, undo/redo.
- `pylib/anki/config.py`: `col.conf` and `WrappedDict` write-back.
- `qt/aqt/operations/__init__.py`: `CollectionOp`, `on_op_finished`.
- `qt/aqt/operations/collection.py`: Edit menu undo/redo, `UndoEmpty`.
- `qt/aqt/main.py`: `reset`, `update_undo_actions`.
- `qt/aqt/undo.py`: menu labels.
