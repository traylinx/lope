"""Per-validator latency ledger and pre-launch budget fit check.

Lope has always measured how long each validator call takes — it prints the
number at the end of every run — but it never remembered it.  Every run
re-derived its timeout from a flag or a config default, so a budget that could
not possibly fit was discovered only by spending the whole budget and failing.
A caller (increasingly, another agent) picks a number like 240 with no way to
know the validator's own p90 is 178s, and the run dies at the ceiling with all
of its work discarded.

This module remembers, and answers one question before launch: given what this
validator has actually done here before, is the ceiling we are about to enforce
big enough for it to finish?  The answer is advisory — it prints, it does not
block — because a first-ever call has no history and must still be allowed to
run.

Storage is a bounded sample of recent call durations per validator at
``$LOPE_HOME/latency.json``.  Timed-out calls are recorded too, as censored
lower bounds: a call killed at 240s proves the true duration exceeds 240s, so
folding it in as 240 biases the estimate upward, which is the safe direction
for a budget check.

Nothing here may ever break a run.  Every public entry point swallows its own
errors: a corrupt or unwritable ledger degrades to "no advice", never to a
failed validation.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = 1

#: Keep the ledger small and recent; provider performance drifts with model
#: and machine changes, and an unbounded file would grow without value.
MAX_SAMPLES_PER_VALIDATOR = 50

#: Below this many observations a p90 is noise, so the fit check stays quiet.
MIN_SAMPLES_FOR_ADVICE = 3

#: A ceiling should clear the observed p90 with room for run-to-run variance.
#: Calibrated against the incident this module exists to prevent: the same
#: prompt on the same validator took 136s and 178s on two consecutive runs
#: (+31%), then exceeded 240s twice under load. A factor of 1.25 would have
#: called that 240s ceiling a good fit, which is precisely the wrong answer.
FIT_SAFETY_FACTOR = 1.5

#: Only these two outcomes say anything about how long the provider needs to
#: think. A launch error, a parse error or a parent-side cancellation produces
#: a duration that is an artefact of the failure, not of the provider's speed,
#: and folding those into the estimate would corrupt it.
_RECORDED_OUTCOMES = {"ok", "provider_timeout"}

#: The outcome that means "the provider hit our wall", not "it answered".
_TIMEOUT_OUTCOME = "provider_timeout"


def enabled() -> bool:
    """Return False when the operator has switched the ledger off."""

    return os.environ.get("LOPE_LATENCY", "").strip().lower() not in {
        "off", "0", "false", "no",
    }


def ledger_path() -> Path:
    """Return the ledger location, honouring LOPE_HOME like the config does."""

    home = os.environ.get("LOPE_HOME", os.path.expanduser("~/.lope"))
    return Path(home) / "latency.json"


@dataclass(frozen=True)
class Stats:
    """Summary of one validator's recent observed call durations."""

    validator: str
    count: int
    p50: float
    p90: float
    maximum: float
    timeouts: int

    def recommended_ceiling(self) -> int:
        """Smallest whole-second budget that clears p90 with variance room."""

        return int(self.p90 * FIT_SAFETY_FACTOR) + 1


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile; `values` must be sorted and non-empty."""

    if not values:
        return 0.0
    rank = max(1, min(len(values), math.ceil(fraction * len(values))))
    return float(values[rank - 1])


def _load() -> Dict[str, Any]:
    try:
        with open(ledger_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "validators": {}}
    if not isinstance(data, dict) or not isinstance(data.get("validators"), dict):
        return {"schema_version": SCHEMA_VERSION, "validators": {}}
    return data


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=".latency-", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def record(validator: str, duration_seconds: float, *, outcome: str = "ok") -> None:
    """Append one observation.  Never raises."""

    if not enabled():
        return
    try:
        name = str(validator or "").strip()
        outcome_name = str(outcome or "").strip()
        if not name or outcome_name not in _RECORDED_OUTCOMES:
            return
        # Round before validating, so a sub-millisecond duration is discarded
        # rather than stored as a meaningless 0.0 sample.
        duration = round(float(duration_seconds), 3)
        if duration <= 0.0:
            return
        timed_out = outcome_name == _TIMEOUT_OUTCOME
        data = _load()
        validators = data.setdefault("validators", {})
        row = validators.setdefault(name, {"samples": []})
        samples = row.get("samples")
        if not isinstance(samples, list):
            samples = []
        samples.append({"d": duration, "t": timed_out})
        row["samples"] = samples[-MAX_SAMPLES_PER_VALIDATOR:]
        data["schema_version"] = SCHEMA_VERSION
        _atomic_write(ledger_path(), data)
    except Exception:
        return


def stats(validator: str) -> Optional[Stats]:
    """Return summary stats for one validator, or None when unknown."""

    if not enabled():
        return None
    try:
        row = (_load().get("validators") or {}).get(str(validator))
        if not isinstance(row, dict):
            return None
        raw = row.get("samples")
        if not isinstance(raw, list) or not raw:
            return None
        durations: List[float] = []
        timeouts = 0
        for sample in raw:
            if not isinstance(sample, dict):
                continue
            try:
                value = float(sample.get("d"))
            except (TypeError, ValueError):
                continue
            if value <= 0.0:
                continue
            durations.append(value)
            if bool(sample.get("t")):
                timeouts += 1
        if not durations:
            return None
        durations.sort()
        return Stats(
            validator=str(validator),
            count=len(durations),
            p50=_percentile(durations, 0.50),
            p90=_percentile(durations, 0.90),
            maximum=durations[-1],
            timeouts=timeouts,
        )
    except Exception:
        return None


def _respects_provider_timeout() -> bool:
    try:
        from .generic_validators import respect_provider_timeout

        return respect_provider_timeout()
    except Exception:
        return False


def effective_ceiling(validator: Any, call_timeout: Optional[int]) -> Optional[int]:
    """Mirror the timeout a call against *validator* would actually enforce."""

    provider_timeout = getattr(validator, "_timeout_override", None)
    if not isinstance(provider_timeout, int):
        return call_timeout
    if call_timeout is None:
        return provider_timeout
    if _respects_provider_timeout():
        return provider_timeout
    return min(provider_timeout, call_timeout)


def fit_warnings(validators: Sequence[Any], call_timeout: Optional[int]) -> List[str]:
    """Return advisory lines about budgets that history says will not fit.

    Two distinct problems get reported, because they have different fixes:

    * a **clamp** — the call ceiling is silently cutting a provider's own
      configured budget, which is invisible in every other output; and
    * a **misfit** — the enforced ceiling is below this validator's observed
      p90 plus variance room, so the call is predicted to be killed.
    """

    lines: List[str] = []
    if not enabled() or call_timeout is None:
        return lines
    for validator in validators or []:
        try:
            name = str(getattr(validator, "name", "") or "")
            if not name:
                continue
            provider_timeout = getattr(validator, "_timeout_override", None)
            enforced = effective_ceiling(validator, call_timeout)
            if enforced is None:
                continue
            clamped = (
                isinstance(provider_timeout, int)
                and provider_timeout > enforced
            )
            summary = stats(name)
            misfit = (
                summary is not None
                and summary.count >= MIN_SAMPLES_FOR_ADVICE
                and enforced < summary.p90 * FIT_SAFETY_FACTOR
            )
            if not clamped and not misfit:
                continue
            detail = [f"budget advice: {name} ceiling {enforced}s"]
            if clamped:
                detail.append(
                    f"clamps its configured {provider_timeout}s "
                    f"(pass --respect-provider-timeout to honour the config)"
                )
            if misfit and summary is not None:
                detail.append(
                    f"is below observed p90 {summary.p90:.0f}s over "
                    f"{summary.count} call(s) — predicted timeout; "
                    f"raise --timeout to >= {summary.recommended_ceiling()}s"
                )
            lines.append("  " + "; ".join(detail))
        except Exception:
            continue
    return lines
