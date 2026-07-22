"""High-level zero-human sprint implementation orchestration.

`lope execute` is the low-level phase runner. `lope implement` is the
opinionated wrapper Sebastian asked for: choose the CLIs/agents first, then
run the whole sprint without asking the human again. Writers run sequentially
for repo safety; infrastructure failure can move to the next explicitly
selected writer without allowing parallel patch writes.
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

    `agents` are the selected implementation CLIs. `primary` is tried first.
    Remaining implementation and escalation agents are bounded sequential
    fallbacks for infrastructure failures only. Validators remain independent.
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
        run_timeout=cfg.run_timeout,
        allow_unbounded_run=cfg.allow_unbounded_run,
        max_calls=cfg.max_calls,
        max_input_bytes=cfg.max_input_bytes,
        max_output_bytes=cfg.max_output_bytes,
        request_policy=cfg.request_policy,
        max_chunks=cfg.max_chunks,
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
        "Role: Autonomous Implementation Agent",
        "Primary Directive: implement this sprint phase directly; the outer Lope process performs fallback and validation.",
        "",
        "## Execution protocol",
        "- Uninterrupted momentum: implement now. Do not ask the human for input, approval, or clarification.",
        "- Internal resolution: solve repository-local blockers, dependency conflicts, and structural questions directly.",
        "- Orchestration boundary: do not invoke Lope, another AI CLI, an MCP agent, or a nested model process.",
        "- Outer ownership: the outer Lope process performs fallback, escalation, and validation after this writer returns.",
        "- Strict constraint: the human is out of the loop after roster selection.",
        "- Repository safety: you are the single file-writing agent for this phase. Do not spawn parallel editors in the same checkout.",
        "",
    ]
    try:
        from .minimality import implementation_directive
        minimality = implementation_directive(domain=getattr(doc, "domain", "engineering"))
    except Exception:  # pragma: no cover - defensive; prompt assembly must not fail
        minimality = ""
    if minimality:
        parts.extend(["## Minimality discipline", minimality, ""])
    parts.extend([
        "## Selected team",
        f"- Implementation agents: {', '.join(roster.agents)}",
        f"- Escalation agents: {', '.join(roster.escalation_agents)}",
        f"- First writing lead: {roster.primary}",
        "",
        f"## Sprint: {doc.title}",
        f"## Phase {phase.index}: {phase.name}",
        "",
        f"Goal: {phase.goal}",
        "",
    ])
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
    invocation_context=None,
) -> ExecutionReport:
    """Run the selected sprint with the chosen roster."""

    run_cfg = clone_cfg_for_roster(cfg, roster)
    pool = build_validator_pool(run_cfg)
    if invocation_context is not None:
        pool._invocation_context = invocation_context
    from .implementation_runner import ordered_writers

    primary = pool.primary_validator()
    writer_names = _unique(list(roster.agents) + list(roster.escalation_agents))
    try:
        writers = ordered_writers(pool, writer_names)
    except ValueError as exc:
        raise RosterError(str(exc)) from exc
    pool._implementation_candidate_count = len(writers)

    print_fn(
        f"Implementer: {primary.name}  ·  Escalation: {', '.join(roster.escalation_agents)}  ·  "
        f"Validators: {', '.join(roster.validators)}"
    )
    print_fn(f"Timeout: {run_cfg.timeout}s  ·  Mode: implement / zero-human")
    print_fn()

    def implementation_fn(phase, fix_context=None):
        prompt = build_swarm_prompt(phase, doc, roster, fix_context=fix_context)
        print_fn(f"\n>>> Phase {phase.index}: {phase.name}")
        from .request_plan import PlanAction, plan_request

        plan = plan_request(
            prompt,
            mode="implement",
            validators=writers,
            policy=run_cfg.request_policy,
            max_chunks=run_cfg.max_chunks,
            max_calls=run_cfg.max_calls,
            max_input_bytes=run_cfg.max_input_bytes,
            per_call_timeout=run_cfg.timeout,
            parallel=False,
            allow_chunk=False,
            source_label=f"phase {phase.index} implementation",
            kind="markdown",
        )
        if invocation_context is not None:
            invocation_context.add_request_plan(plan.to_dict())
        if plan.action == PlanAction.REJECT:
            return ImplementationResult(
                ok=False,
                summary=f"implementation request rejected: {plan.reason}",
                error=plan.reason + (f"; {plan.mitigation}" if plan.mitigation else ""),
            )
        from .implementation_runner import invoke_writer_failover

        return invoke_writer_failover(
            writers,
            prompt,
            run_cfg.timeout,
            phase_index=phase.index,
            context=invocation_context,
            print_fn=print_fn,
        )

    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=implementation_fn,
        max_rounds_per_phase=max_rounds_per_phase,
        timeout_seconds=run_cfg.timeout,
        gate_runner=gate_runner,
        request_policy=run_cfg.request_policy,
        max_chunks=run_cfg.max_chunks,
        max_calls=run_cfg.max_calls,
        max_input_bytes=run_cfg.max_input_bytes,
        parallel=run_cfg.parallel,
    )
    return executor.run(doc)
