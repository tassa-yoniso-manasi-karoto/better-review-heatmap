"""Developer-only effort calibration, dormant unless devmode is on.

This module is imported for every user, so it stays tiny: the rest of the
package is imported lazily, and only in devmode. See docs/calibration.md.
"""

# Top-level on purpose: ConfigManager rewrites pm.profile["heatmap"] from a
# cached copy, which would erase a flag set there from the debug console.
DEVMODE_KEY = "heatmap_devmode"


def is_devmode() -> bool:
    from aqt import mw

    profile = getattr(getattr(mw, "pm", None), "profile", None)
    return bool(profile and profile.get(DEVMODE_KEY, False))


def activate_model(mw, model: dict) -> None:
    """Score with a local fit until the profile closes.

    The first render comes before profile_did_open, and the renderer's cache
    can't see the model, so the view on screen must be redrawn.
    """
    from aqt.gui_hooks import profile_will_close

    from ..metrics import use_effort_model

    use_effort_model(model)
    # Called with no argument, it restores the shipped weights: another profile
    # may have no fit, and may not be in devmode at all.
    profile_will_close.remove(use_effort_model)
    profile_will_close.append(use_effort_model)
    mw._review_heatmap.invalidate_cache()
    if mw.state == "deckBrowser":
        mw.deckBrowser.refresh()
    elif mw.state == "overview":
        mw.overview.refresh()


def _on_profile_did_open() -> None:
    if not is_devmode():
        return

    import traceback

    from aqt import mw

    from .store import Store

    store = Store.for_profile(mw.pm.profileFolder())
    try:
        from .report import read_model

        model = read_model(store.folder)
        if model:
            activate_model(mw, model)
    except Exception:
        store.log_error("activate_model", traceback.format_exc())
    try:
        if store.load_state()["recording"]:
            from .recorder import start_recording

            start_recording(mw, store)
    except Exception:
        store.log_error("profile_did_open", traceback.format_exc())


def initialize_calibration() -> None:
    from aqt.gui_hooks import profile_did_open

    profile_did_open.append(_on_profile_did_open)
