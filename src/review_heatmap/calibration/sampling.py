"""Who gets asked what, and with what probability. Pure, with an injected RNG.

Sampling on inputs such as duration, state or button does not bias a regression
that conditions on them, but every probability must still reach the log.
"""

from dataclasses import dataclass
from random import Random
from typing import Iterable, Optional

from .store import baseline_days_left

EASE_AGAIN = 1
LONG_ANSWER_MS = 60_000
ROTATION = ("gain", "grasp")
ROTATION_P = 1 / len(ROTATION)
DAY_RATING_MIN_ANSWERS = 20

_HOUR_MS = 3_600_000


@dataclass(frozen=True)
class AnswerSample:
    answer_p: float
    why_p: float
    rotation: Optional[str]  # None: the cost question was not sampled
    why: bool


def answer_probability(state: dict, taken_ms: int, is_new: bool) -> float:
    """Takes the uncapped time: a preset's cap would hide every long answer."""
    weight = 1 + 6 * (taken_ms > LONG_ANSWER_MS) + 2 * is_new
    return min(1.0, state["answer_rate"] * weight)


def why_probability(state: dict, ease: int, was_review: bool) -> float:
    return state["lapse_reason_rate"] if ease == EASE_AGAIN and was_review else 0.0


def sample_answer(
    state: dict, rng: Random, taken_ms: int, is_new: bool, ease: int, was_review: bool,
) -> Optional[AnswerSample]:
    """None if this answer gets no prompt. The two draws are independent."""
    answer_p = answer_probability(state, taken_ms, is_new)
    why_p = why_probability(state, ease, was_review)
    ask_cost = rng.random() < answer_p
    ask_why = rng.random() < why_p
    if not (ask_cost or ask_why):
        return None
    rotation = rng.choice(ROTATION) if ask_cost else None
    return AnswerSample(answer_p, why_p, rotation, ask_why)


def prompts_open(state: dict, now: int, shown_today: int, cost_ratings: int) -> bool:
    """Whether answer and sharpness prompts may show. Day ratings ignore this:
    they start on day 1 and judge every future formula."""
    return (
        baseline_days_left(state, now) == 0
        and shown_today < state["daily_cap"]
        and cost_ratings < state["stop_after_cost_ratings"]
    )


def is_block_start(last_activity_ms: int, now: int, gap_hours: float) -> bool:
    return now - last_activity_ms >= gap_hours * _HOUR_MS


def evening_begins_ms(day_start_ms: int, day_start_hour: int, evening_hour: int) -> int:
    """When a study day that began at that local hour reaches the evening hour.

    A study day runs past midnight, so 1 a.m. counts as the evening of the day
    before: the hours are measured from its start, not read off the clock.
    """
    return day_start_ms + (evening_hour - day_start_hour) % 24 * _HOUR_MS


def prompts_since(events: Iterable[dict], start_ms: int) -> int:
    """Prompts counting towards the daily cap, skipped ones included."""
    return sum(
        event["ts"] >= start_ms and event["kind"] != "day" for event in events
    )


def prompt_counts(events: Iterable[dict]) -> dict:
    """Answers per question from "prompt" events, plus prompts and skips."""
    counts = dict.fromkeys(("cost", "why", "sharpness", "tiring", *ROTATION), 0)
    counts.update(prompts=0, skipped=0)
    for event in events:
        counts["prompts"] += 1
        counts["skipped"] += bool(event["skipped"])
        for question in event["answers"]:
            if question in counts:
                counts[question] += 1
    return counts
