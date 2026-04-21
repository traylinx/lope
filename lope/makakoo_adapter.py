"""
MakakooAdapterValidator — lope ↔ Makakoo OS universal-bridge adapter.

Shells out to `makakoo adapter call <name>` and hydrates the emitted JSON
into lope's ValidatorResult + PhaseVerdict dataclasses. One class handles
every registered adapter — no per-provider Python subclass, ever.

Resolution order (see validators.build_validator_pool):
    hardcoded  →  cfg.providers  →  KNOWN_CLIS  →  Makakoo adapters

So `lope negotiate --validators claude,openclaw` works the moment
`~/.makakoo/adapters/registered/openclaw.toml` exists, without the user
editing their lope config.

Disable via env:  LOPE_MAKAKOO_ADAPTERS=0
Override bin:     MAKAKOO_BIN=/path/to/makakoo
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from .models import PhaseVerdict, ValidatorResult, VerdictStatus
from .validators import Validator

log = logging.getLogger("lope.makakoo_adapter")


def _resolve_bin() -> Optional[str]:
    explicit = os.environ.get("MAKAKOO_BIN")
    if explicit and os.path.isfile(explicit):
        return explicit
    return shutil.which("makakoo")


def _adapters_root() -> Path:
    if (env := os.environ.get("MAKAKOO_ADAPTERS_HOME")):
        return Path(env)
    # Matches AdapterRegistry::default_root() on the Rust side.
    return Path.home() / ".makakoo" / "adapters"


def enumerate_registered_adapters() -> List[str]:
    """Names of every `~/.makakoo/adapters/registered/*.toml`.

    Safe to call when the directory doesn't exist — returns [].
    """
    root = _adapters_root() / "registered"
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.toml"))


class MakakooAdapterValidator(Validator):
    """One validator, any adapter manifest — driven by `makakoo adapter call`.

    Every call shells into `makakoo adapter call <name>` with the prompt
    on stdin and parses the ValidatorResult JSON from stdout. Transport
    (HTTP / subprocess / MCP) + output parsing (lope-verdict-block /
    openai-chat / plain / custom) are handled by the Makakoo host
    binary. This class is a thin hydrator.
    """

    def __init__(self, adapter_name: str, bin_override: Optional[str] = None):
        self._adapter_name = adapter_name
        self._bin = bin_override or _resolve_bin()

    @property
    def name(self) -> str:
        return self._adapter_name

    def available(self) -> bool:
        if os.environ.get("LOPE_MAKAKOO_ADAPTERS") == "0":
            return False
        if not self._bin:
            return False
        # Adapter must be registered OR call-time `--bundled` must be
        # enabled; we consider the validator available as long as the
        # binary exists — resolution happens at call time.
        return True

    def validate(self, prompt: str, timeout: int = 480) -> ValidatorResult:
        started = time.time()
        if not self._bin:
            return self._infra_error("makakoo binary not on PATH", time.time() - started)
        try:
            proc = subprocess.run(
                [
                    self._bin,
                    "adapter",
                    "call",
                    self._adapter_name,
                    "--timeout",
                    str(timeout),
                ],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout + 30,  # generous: host may spin up sandboxes
                shell=False,
            )
        except subprocess.TimeoutExpired:
            duration = time.time() - started
            return self._infra_error(
                f"makakoo adapter call timeout after {timeout}s", duration
            )
        except FileNotFoundError:
            return self._infra_error("makakoo binary vanished mid-call", time.time() - started)
        except Exception as e:  # pragma: no cover — defensive
            return self._infra_error(f"subprocess error: {e}", time.time() - started)

        duration = time.time() - started
        if proc.returncode != 0:
            # exit 1 from the CLI means "adapter not found" (registry miss)
            # — surface that cleanly instead of silently swallowing.
            msg = (proc.stderr or "").strip()[:500]
            return self._infra_error(
                f"makakoo adapter call exit {proc.returncode}: {msg}", duration
            )

        return _hydrate_result(proc.stdout, self._adapter_name, duration)

    def _infra_error(self, msg: str, duration: float) -> ValidatorResult:
        return ValidatorResult(
            validator_name=self._adapter_name,
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=msg,
                duration_seconds=duration,
                validator_name=self._adapter_name,
            ),
            raw_response="",
            error=msg,
        )


def _hydrate_result(stdout: str, fallback_name: str, fallback_duration: float) -> ValidatorResult:
    """Turn the JSON emitted by `makakoo adapter call` into a ValidatorResult."""
    try:
        payload: Dict = json.loads(stdout)
    except json.JSONDecodeError as e:
        return ValidatorResult(
            validator_name=fallback_name,
            verdict=PhaseVerdict(
                status=VerdictStatus.INFRA_ERROR,
                rationale=f"invalid JSON from makakoo adapter call: {e}",
                duration_seconds=fallback_duration,
                validator_name=fallback_name,
            ),
            raw_response=stdout[:2000],
            error=f"invalid JSON: {e}",
        )

    verdict_dict = payload.get("verdict") or {}
    status = _parse_status(verdict_dict.get("status"))
    verdict = PhaseVerdict(
        status=status,
        confidence=float(verdict_dict.get("confidence", 0.5)),
        rationale=str(verdict_dict.get("rationale", "")),
        required_fixes=list(verdict_dict.get("required_fixes") or []),
        nice_to_have=list(verdict_dict.get("nice_to_have") or []),
        duration_seconds=float(
            verdict_dict.get("duration_seconds", fallback_duration)
        ),
        validator_name=verdict_dict.get("validator_name") or fallback_name,
        stage=verdict_dict.get("stage"),
        evidence_gate_triggered=bool(
            verdict_dict.get("evidence_gate_triggered", False)
        ),
    )
    return ValidatorResult(
        validator_name=payload.get("validator_name") or fallback_name,
        verdict=verdict,
        raw_response=str(payload.get("raw_response", ""))[:20000],
        error=str(payload.get("error", "")),
        flag_error_hint=str(payload.get("flag_error_hint", "")),
    )


def _parse_status(raw) -> VerdictStatus:
    if isinstance(raw, str):
        try:
            return VerdictStatus(raw.strip().upper())
        except ValueError:
            pass
    return VerdictStatus.INFRA_ERROR
