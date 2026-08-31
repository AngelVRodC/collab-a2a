"""Normalising self-reported usage from whatever agent you happen to run.

Every coding agent exposes its usage differently, and most expose it nowhere a
shell script can reach: Claude Code hands its status line a JSON blob, Codex
has no status line at all and writes token counts to session files, opencode
has a plugin hook but no shell one. Waiting for them to converge is not a plan.

So there is one **canonical shape** collab understands, and everything else is
translated into it:

    model              str    what is answering, e.g. "Opus 5", "gpt-5"
    cost_usd           float  spend so far on this session
    quota_used_pct     float  percent of your allowance used, if there is one number
    quota_five_hour    float  percent of a short rolling window used
    quota_seven_day    float  percent of a long rolling window used
    quota_reset_at     str    when the window rolls over
    context_pct        float  percent of the context window in use
    tokens_in          int    tokens consumed
    tokens_out         int    tokens produced
    lines_added        int    lines written
    lines_removed      int

Quota is always **percent used**, never percent remaining. Some agents report
the opposite — Antigravity's status line gives `quota.remaining_fraction` — and
mixing the two silently turns "42% left" into "42% burned", which is exactly
backwards when you are deciding who can take on more work. Anything named
*remaining* is inverted on the way in.

Every field is optional. An agent that knows only its model reports only that,
and the roster shows what it has.

Anything can produce this — `collab stats --report '{"quota_five_hour": 42}'`
is a whole integration. The nested shapes below are conveniences for agents
that already emit something close.
"""

from __future__ import annotations

import json
from typing import Any

#: Fields we understand, and how to coerce them.
CANONICAL: dict[str, type] = {
    "model": str,
    "cost_usd": float,
    "quota_used_pct": float,
    "quota_five_hour": float,
    "quota_seven_day": float,
    "quota_reset_at": str,
    "context_pct": float,
    "tokens_in": int,
    "tokens_out": int,
    "lines_added": int,
    "lines_removed": int,
}

#: Fields that arrive as "how much is left" and mean the opposite of ours.
INVERTED = {
    "remaining_fraction": "quota_used_pct",
    "remaining_percentage": "quota_used_pct",
    "quota_remaining_pct": "quota_used_pct",
    "context_remaining_percentage": "context_pct",
}

#: Names other tools use for the same things.
ALIASES = {
    "model_name": "model",
    "display_name": "model",
    "cost": "cost_usd",
    "total_cost_usd": "cost_usd",
    "spend_usd": "cost_usd",
    "context": "context_pct",
    "context_used_pct": "context_pct",
    "context_percentage": "context_pct",
    "used_percentage": "context_pct",
    "input_tokens": "tokens_in",
    "output_tokens": "tokens_out",
    "prompt_tokens": "tokens_in",
    "completion_tokens": "tokens_out",
    "total_lines_added": "lines_added",
    "total_lines_removed": "lines_removed",
    "five_hour": "quota_five_hour",
    "seven_day": "quota_seven_day",
    "quota_5h": "quota_five_hour",
    "quota_7d": "quota_seven_day",
    "weekly": "quota_seven_day",
    "reset_time": "quota_reset_at",
    "resets_at": "quota_reset_at",
    "total_input_tokens": "tokens_in",
    "total_output_tokens": "tokens_out",
}

#: Room for something we have not thought of, without letting a participant
#: push arbitrary volume into everyone else's roster.
MAX_EXTRA_FIELDS = 6
MAX_STRING = 64


def _coerce(field: str, value: Any) -> Any | None:
    kind = CANONICAL[field]
    try:
        if kind is str:
            text = str(value).strip()
            return text[:MAX_STRING] or None
        if kind is int:
            return int(float(value))
        number = round(float(value), 4)
        # Percentages that arrive as 0..1 are still percentages.
        if field.startswith(("quota_", "context")) and 0 < number <= 1:
            number = round(number * 100, 1)
        return number
    except (TypeError, ValueError):
        return None


def _invert(field: str, value: Any) -> Any | None:
    """Turn "how much is left" into "how much is used"."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # A fraction (0..1) and a percentage (0..100) both appear in the wild.
    used = (1 - number) * 100 if 0 <= number <= 1 else 100 - number
    return round(max(0.0, min(used, 100.0)), 1)


def _take(out: dict[str, Any], key: str, value: Any) -> None:
    if key in INVERTED:
        field = INVERTED[key]
        if (inverted := _invert(field, value)) is not None:
            out.setdefault(field, inverted)
        return
    field = key if key in CANONICAL else ALIASES.get(key, "")
    if not field or field not in CANONICAL:
        return
    if (coerced := _coerce(field, value)) is not None:
        out.setdefault(field, coerced)


def normalise(data: Any) -> dict[str, Any]:
    """Turn whatever an agent produced into the canonical shape.

    Accepts the flat canonical form, Claude Code's status line payload, and the
    loosely nested shapes other tools tend to emit. Unknown keys are ignored
    rather than rejected, so a newer agent reporting more than we know about
    still gets its recognisable half through.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, Any] = {}

    # Flat, canonical or aliased.
    for key, value in data.items():
        if not isinstance(value, (dict, list)):
            _take(out, key, value)

    # A "stats"/"usage" wrapper.
    for wrapper in ("stats", "usage", "metrics"):
        inner = data.get(wrapper)
        if isinstance(inner, dict):
            for key, value in inner.items():
                if not isinstance(value, (dict, list)):
                    _take(out, key, value)

    # Claude Code: model.display_name, cost.total_cost_usd, context_window.*
    if isinstance(model := data.get("model"), dict):
        _take(out, "display_name", model.get("display_name") or model.get("id"))
    for wrapper in ("cost", "context_window", "tokens"):
        inner = data.get(wrapper)
        if isinstance(inner, dict):
            for key, value in inner.items():
                if not isinstance(value, (dict, list)):
                    _take(out, key, value)

    # Rolling windows: {"rate_limits": {"five_hour": {"used_percentage": 42}}}
    # and single-figure quota: {"quota": {"remaining_fraction": 0.58, ...}}
    limits = data.get("rate_limits") or data.get("limits") or data.get("quota")
    if isinstance(limits, dict):
        for window, value in limits.items():
            if window in INVERTED and not isinstance(value, dict):
                _take(out, window, value)
                continue
            field = ALIASES.get(window, "")
            if field == "quota_reset_at" and not isinstance(value, dict):
                _take(out, window, value)
                continue
            if field not in ("quota_five_hour", "quota_seven_day"):
                continue
            if isinstance(value, dict):
                for inner_key, inner in value.items():
                    if inner_key in INVERTED and (
                            got := _invert(field, inner)) is not None:
                        out.setdefault(field, got)
                        break
                    if inner_key in ("used_percentage", "used_pct"):
                        if (coerced := _coerce(field, inner)) is not None:
                            out.setdefault(field, coerced)
                        break
                continue
            if value is not None and (coerced := _coerce(field, value)) is not None:
                out.setdefault(field, coerced)

    return out


def sanitise(reported: dict[str, Any]) -> dict[str, Any]:
    """What is safe to put on everyone else's roster.

    Usage travels to every participant, so it is capped in both size and shape:
    scalars only, a handful of unknown keys at most, short strings.
    """
    out: dict[str, Any] = {}
    extras = 0
    for key, value in (reported or {}).items():
        if isinstance(value, (dict, list)):
            continue
        if key in CANONICAL:
            if (coerced := _coerce(key, value)) is not None:
                out[key] = coerced
            continue
        if extras >= MAX_EXTRA_FIELDS or not isinstance(key, str):
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[key[:MAX_STRING]] = value
            extras += 1
        elif isinstance(value, str):
            out[key[:MAX_STRING]] = value[:MAX_STRING]
            extras += 1
    return out
