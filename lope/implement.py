"""High-level zero-human sprint implementation orchestration.

`lope execute` is the low-level phase runner. `lope implement` is the
opinionated wrapper Sebastian asked for: choose the CLIs/agents first, then
run the whole sprint without asking the human again. v1 deliberately uses a
single-writer model for repo safety. The selected roster is still injected
into the prompt and validator loop; literal parallel patch writing belongs in
a later worktree-backed implementation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from typing import Callable, Iterable, List, Optional, Sequence

from .config import LopeCfg
from .executor import ImplementationResult, PhaseExecutor
from .models import ExecutionReport, SprintDoc
from .validators import build_validator_pool


class RosterError(ValueError):
    """Raised when an implement roster cannot be resolved."""


@dataclass(frozen=True)
class ImplementRoster:
    """Resolved agent roster for `lope implement`.

    `agents` are the selected implementation CLIs. v1 uses `primary` (the
    first agent) as the only file-writing subprocess to avoid same-checkout
    races. `escalation_agents` are selected problem-solving/review CLIs and
    are included in the validator pool plus prompt contract.
    """

    agents: List[str]
    escalation_agents: List[str]
    validators: List[str]
    primary: str


def _pool_validators(pool) -> list:
    if hasattr(pool, "validators"):
        return list(pool.validators())
    return list(getattr(pool, "_validators", None) or getattr(pool, "_ordered", None) or [])


def _pool_names(pool) -> List[str]:
    if hasattr(pool, "names"):
        return list(pool.names())
    return [getattr(v, "name", "") for v in _pool_validators(pool) if getattr(v, "name", "")]


def parse_name_list(raw: Optional[str]) -> List[str]:
    """Parse comma separated CLI names, preserving order and uniqueness."""

    if raw is None:
        return []
    names: List[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        if item not in names:
            names.append(item)
    return names


def _unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out


def _default_escalation_names(available: Sequence[str], primary: str) -> List[str]:
    # No hardcoded "Claude + OpenCode" default here. The whole point of
    # `lope implement` is that the operator selects the roster first. For the
    # interactive empty-Enter path, use the configured team order only.
    return [n for n in available if n != primary][:2]


def _validate_names(label: str, selected: Sequence[str], available: Sequence[str]) -> List[str]:
    missing = [n for n in selected if n not in available]
    if missing:
        raise RosterError(
            f"Unknown {label}: {', '.join(missing)}. Available: {', '.join(available) or '(none)'}"
        )
    return list(selected)


def _parse_selection(raw: str, available: Sequence[str]) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    selected: List[str] = []
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        if token.isdigit():
            idx = int(token) - 1
            if 0 <= idx < len(available):
                name = available[idx]
            else:
                continue
        else:
            name = token
        if name not in selected:
            selected.append(name)
    return selected


def _prompt_for_names(
    label: str,
    available: Sequence[str],
    default: Sequence[str],
    *,
    input_fn: Callable[[], str] = input,
    print_fn: Callable[..., None] = print,
) -> List[str]:
    print_fn(f"\nSelect {label} agents:")
    for i, name in enumerate(available, start=1):
        marker = "  (default)" if name in default else ""
        print_fn(f"  [{i}] {name}{marker}")
    print_fn(f"Default: {', '.join(default) or '(none)'}")
    print_fn("> ", end="", flush=True)
    raw = input_fn()
    selected = _parse_selection(raw, available) or list(default)
    return _validate_names(label, selected, available)


def resolve_implement_roster(
    args,
    cfg: LopeCfg,
    pool,
    *,
    input_fn: Callable[[], str] = input,
    print_fn: Callable[..., None] = print,
    interactive: Optional[bool] = None,
) -> ImplementRoster:
    """Resolve the roster, prompting only when attached to a TTY.

    In non-interactive mode both `--agents` and `--escalate-to` are required.
    That keeps CI/agent runs deterministic and enforces the requested "second
    step" selection instead of silently hardcoding Claude/OpenCode.
    """

    available = _pool_names(pool)
    if not available:
        raise RosterError("No validators available. Run: lope team list")

    primary_default = cfg.primary if cfg.primary in available else available[0]
    agent_default = [primary_default]
    escalation_default = _default_escalation_names(available, primary_default)

    if interactive is None:
        if getattr(args, "interactive", False):
            interactive = True
        else:
            interactive = sys.stdin.isatty() and sys.stdout.isatty()

    agents = parse_name_list(getattr(args, "agents", None))
    escalation = parse_name_list(getattr(args, "escalate_to", None))

    if not agents:
        if interactive:
            agents = _prompt_for_names(
                "implementation", available, agent_default,
                input_fn=input_fn, print_fn=print_fn,
            )
        else:
            raise RosterError(
                "lope implement needs --agents in non-interactive mode. "
                f"Available: {', '.join(available)}"
            )
    else:
        agents = _validate_names("implementation agents", agents, available)

    if not escalation:
        if interactive:
            escalation = _prompt_for_names(
                "escalation", available, escalation_default,
                input_fn=input_fn, print_fn=print_fn,
            )
        else:
            raise RosterError(
                "lope implement needs --escalate-to in non-interactive mode. "
                f"Available: {', '.join(available)}"
            )
    else:
        escalation = _validate_names("escalation agents", escalation, available)

    primary = agents[0]
    if not primary:
        raise RosterError("No implementation agent selected")
    validators = _unique(list(escalation) + list(agents))
    return ImplementRoster(
        agents=list(agents),
        escalation_agents=list(escalation),
        validators=validators,
        primary=primary,
    )


def clone_cfg_for_roster(cfg: LopeCfg, roster: ImplementRoster) -> LopeCfg:
    """Create a run-local config scoped to the chosen roster."""

    return LopeCfg(
        validators=list(roster.validators),
        primary=roster.primary,
        timeout=cfg.timeout,
        parallel=cfg.parallel,
        providers=list(cfg.providers),
        learned_adapters=dict(cfg.learned_adapters),
    )


def apply_phase_filter(doc: SprintDoc, phase_index: Optional[int]) -> SprintDoc:
    """Return a doc limited to one phase when requested."""

    if phase_index is None:
        return doc
    phase = doc.get_phase(phase_index)
    if phase is None:
        available = ", ".join(str(p.index) for p in doc.phases) or "none"
        raise RosterError(f"Phase {phase_index} not found. Available phases: {available}")
    return dataclass_replace(doc, phases=[phase])


def build_swarm_prompt(
    phase,
    doc: SprintDoc,
    roster: ImplementRoster,
    fix_context=None,
) -> str:
    """Build the implementer prompt for one phase."""

    parts: List[str] = [
        "Role: Autonomous Swarm Orchestrator",
        "Primary Directive: execute this sprint phase completely with strict Lope-in-the-loop discipline.",
        "",
        "## Execution protocol",
        "- Uninterrupted momentum: implement now. Do not ask the human for input, approval, or clarification.",
        "- Internal resolution: solve blockers, dependency conflicts, and structural questions inside the selected Lope team context.",
        "- Targeted escalation: direct complex technical and architectural reasoning to the selected escalation agents only.",
        "- Strict constraint: the human is out of the loop after roster selection.",
        "- Repository safety: you are the single file-writing agent for this phase. Do not spawn parallel editors in the same checkout.",
        "",
        "## Selected team",
        f"- Implementation agents: {', '.join(roster.agents)}",
        f"- Escalation agents: {', '.join(roster.escalation_agents)}",
        f"- Writing lead for this run: {roster.primary}",
        "",
        f"## Sprint: {doc.title}",
        f"## Phase {phase.index}: {phase.name}",
        "",
        f"Goal: {phase.goal}",
        "",
    ]
    if phase.criteria:
        parts.append("## Exit criteria")
        parts.extend(f"- {c}" for c in phase.criteria)
        parts.append("")
    if phase.artifacts:
        parts.append("## Files / artifacts / deliverables")
        parts.extend(f"- {a}" for a in phase.artifacts)
        parts.append("")
    if phase.checks:
        parts.append("## Tests / checks / success metrics")
        parts.extend(f"- {c}" for c in phase.checks)
        parts.append("")
    if fix_context:
        parts.append("## Required fixes from prior Lope review")
        if isinstance(fix_context, (list, tuple)):
            parts.extend(f"- {f}" for f in fix_context)
        else:
            parts.append(str(fix_context))
        parts.append("")
    parts.extend([
        "## Completion contract",
        "Implement the phase directly in the repository. Run the relevant checks when possible.",
        "When done, return a terse summary with files changed and commands/tests run.",
        "Do not wait for the human. If uncertain, choose the smallest safe implementation that satisfies the phase and let the Lope validators catch issues.",
    ])
    return "\n".join(parts)


def render_dry_run(doc: SprintDoc, roster: ImplementRoster) -> str:
    phases = ", ".join(str(p.index) for p in doc.phases) or "none"
    return "\n".join([
        f"Lope implement dry-run: {doc.title}",
        f"Phases: {phases}",
        f"Implementation agents: {', '.join(roster.agents)}",
        f"Escalation agents: {', '.join(roster.escalation_agents)}",
        f"Validators: {', '.join(roster.validators)}",
        f"Writing lead: {roster.primary}",
        "Mode: zero-human after roster selection, single-writer safety model",
    ])


def run_implement(
    doc: SprintDoc,
    cfg: LopeCfg,
    roster: ImplementRoster,
    *,
    gate_runner=None,
    max_rounds_per_phase: int = 3,
    print_fn: Callable[..., None] = print,
) -> ExecutionReport:
    """Run the selected sprint with the chosen roster."""

    run_cfg = clone_cfg_for_roster(cfg, roster)
    pool = build_validator_pool(run_cfg)
    primary = pool.primary_validator()

    print_fn(
        f"Implementer: {primary.name}  ·  Escalation: {', '.join(roster.escalation_agents)}  ·  "
        f"Validators: {', '.join(roster.validators)}"
    )
    print_fn(f"Timeout: {run_cfg.timeout}s  ·  Mode: implement / zero-human")
    print_fn()

    def implementation_fn(phase, fix_context=None):
        prompt = build_swarm_prompt(phase, doc, roster, fix_context=fix_context)
        print_fn(f"\n>>> Phase {phase.index}: {phase.name}")
        print_fn(f">>> Delegating to {primary.name} ({run_cfg.timeout}s timeout)...")
        try:
            output = primary.generate(prompt, timeout=run_cfg.timeout)
        except NotImplementedError:
            return ImplementationResult(
                ok=False,
                summary=f"{primary.name} does not support .generate(); pick another --agents lead.",
            )
        except Exception as exc:
            return ImplementationResult(
                ok=False,
                summary=f"{primary.name} subprocess failed: {type(exc).__name__}: {exc}",
                error=f"{type(exc).__name__}: {exc}",
            )
        summary = (output or "").strip()[:2000]
        if not summary:
            summary = f"{primary.name} completed phase {phase.index} (no stdout summary)"
        print_fn(f">>> {primary.name} returned {len(output or '')} chars")
        return ImplementationResult(ok=True, summary=summary)

    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=implementation_fn,
        max_rounds_per_phase=max_rounds_per_phase,
        timeout_seconds=run_cfg.timeout,
        gate_runner=gate_runner,
    )
    return executor.run(doc)
