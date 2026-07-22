from __future__ import annotations

from argparse import Namespace

import pytest

from lope.config import LopeCfg
from lope.implement import (
    RosterError,
    ImplementRoster,
    apply_phase_filter,
    build_swarm_prompt,
    parse_name_list,
    render_dry_run,
    resolve_implement_roster,
)
from lope.models import Phase, SprintDoc


class _Pool:
    def __init__(self, names):
        self._names = list(names)

    def names(self):
        return list(self._names)


def _cfg(primary="pi"):
    return LopeCfg(
        validators=["pi", "claude", "opencode", "antigravity"],
        primary=primary,
        timeout=900,
        parallel=True,
    )


def _doc():
    return SprintDoc(
        slug="x",
        title="SPRINT-X",
        phases=[
            Phase(index=1, name="one", goal="do one", criteria=["one works"]),
            Phase(index=2, name="two", goal="do two", checks=["pytest"]),
        ],
    )


def test_parse_name_list_dedupes_and_trims():
    assert parse_name_list("pi, antigravity,pi,, claude") == [
        "pi",
        "antigravity",
        "claude",
    ]


def test_resolve_roster_from_flags():
    args = Namespace(agents="pi,antigravity", escalate_to="claude,opencode")
    roster = resolve_implement_roster(
        args,
        _cfg(),
        _Pool(["pi", "claude", "opencode", "antigravity"]),
        interactive=False,
    )
    assert roster.agents == ["pi", "antigravity"]
    assert roster.escalation_agents == ["claude", "opencode"]
    assert roster.primary == "pi"
    assert roster.validators == ["claude", "opencode", "pi", "antigravity"]


def test_resolve_roster_interactive_defaults():
    args = Namespace(agents=None, escalate_to=None)
    answers = iter(["", ""])
    roster = resolve_implement_roster(
        args,
        _cfg(primary="pi"),
        _Pool(["pi", "claude", "opencode", "antigravity"]),
        interactive=True,
        input_fn=lambda: next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert roster.agents == ["pi"]
    assert roster.escalation_agents == ["claude", "opencode"]
    assert roster.primary == "pi"


def test_resolve_roster_interactive_flag_forces_prompt_mode():
    args = Namespace(agents=None, escalate_to=None, interactive=True)
    answers = iter(["1", "2"])
    roster = resolve_implement_roster(
        args,
        _cfg(primary="pi"),
        _Pool(["pi", "claude", "opencode"]),
        interactive=None,
        input_fn=lambda: next(answers),
        print_fn=lambda *a, **k: None,
    )
    assert roster.agents == ["pi"]
    assert roster.escalation_agents == ["claude"]


def test_resolve_roster_requires_flags_when_noninteractive():
    args = Namespace(agents=None, escalate_to="claude,opencode")
    with pytest.raises(RosterError, match="needs --agents"):
        resolve_implement_roster(
            args,
            _cfg(),
            _Pool(["pi", "claude", "opencode"]),
            interactive=False,
        )


def test_resolve_roster_rejects_unknown_names():
    args = Namespace(agents="pi,ghost", escalate_to="claude")
    with pytest.raises(RosterError, match="ghost"):
        resolve_implement_roster(
            args,
            _cfg(),
            _Pool(["pi", "claude", "opencode"]),
            interactive=False,
        )


def test_swarm_prompt_uses_selected_escalation_agents_not_hardcoded_pair():
    roster = ImplementRoster(
        agents=["pi", "antigravity"],
        escalation_agents=["kimi", "qwen"],
        validators=["kimi", "qwen", "pi", "antigravity"],
        primary="pi",
    )
    prompt = build_swarm_prompt(_doc().phases[0], _doc(), roster, fix_context=["fix x"])
    assert "Escalation agents: kimi, qwen" in prompt
    assert "Implementation agents: pi, antigravity" in prompt
    assert "Claude and OpenCode" not in prompt
    assert "Do not ask the human" in prompt
    assert "do not invoke Lope" in prompt
    assert "outer Lope process performs fallback" in prompt
    assert "strict Lope-in-the-loop" not in prompt
    assert "fix x" in prompt


def test_apply_phase_filter_limits_doc_and_rejects_missing_phase():
    original = _doc()
    doc = apply_phase_filter(original, 2)
    assert [p.index for p in doc.phases] == [2]
    assert [p.index for p in original.phases] == [1, 2]
    with pytest.raises(RosterError, match="Phase 99 not found"):
        apply_phase_filter(_doc(), 99)


def test_render_dry_run_has_roster_and_single_writer_contract():
    roster = ImplementRoster(
        agents=["pi"],
        escalation_agents=["claude", "opencode"],
        validators=["claude", "opencode", "pi"],
        primary="pi",
    )
    text = render_dry_run(_doc(), roster)
    assert "Lope implement dry-run: SPRINT-X" in text
    assert "Writing lead: pi" in text
    assert "single-writer safety model" in text
