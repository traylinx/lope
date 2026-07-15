"""Lope — multi-CLI validator ensemble for AI work.

Version: 0.12.0


Structured sprint modes (`negotiate`, `execute`, `implement`, `audit`) cover
multi-phase work with validator retry and zero-human sprint implementation.
Single-shot modes (`ask`, `review`, `vote`, `compare`, `pipe`) cover cross-model
Q&A, file critique, structured votes, A/B comparison, and stdin-fed fan-out.
Roster management (`team`) adds, removes, lists, and smoke-tests validators
from any chat window — no JSON editing. `team add --from-curl` parses a pasted
curl command into a registered HTTP provider in one step. Persistent judgment
comes from `memory` and `deliberate`. Objective evidence comes from `gate` and
`check`. Graph mode (`flow`) runs declarative DOT workflows where nodes dispatch
into the same executors (agent / ensemble review / shell gate / judge-router)
and edges carry conditions and loops, bounded by visit caps. Maintenance is
`update` / `upgrade`, which refreshes git checkouts and installed host skills.
Any CLI implements; any CLI validates.
"""

__version__ = "0.14.0"

from .models import (
    ExecutionReport,
    EscalationRequired,
    Phase,
    PhaseVerdict,
    Proposal,
    Round,
    SprintDoc,
    ValidatorResult,
    VerdictStatus,
)
from .validators import (
    AiderValidator,
    ClaudeCodeValidator,
    CodexValidator,
    EnsemblePool,
    GeminiCliValidator,
    OpencodeValidator,
    StubValidator,
    Validator,
    ValidatorPool,
    parse_opencode_verdict,
)
from .executor import ImplementationResult, PhaseExecutor
from .negotiator import Negotiator
from .auditor import Auditor
from .cli_discovery import CliInfo, defaults, discover
from .config import LopeCfg, load, save, default_path
from .selector import is_interactive, run_selector
from .implement import ImplementRoster

__all__ = [
    "AiderValidator",
    "Auditor",
    "ClaudeCodeValidator",
    "CliInfo",
    "CodexValidator",
    "EnsemblePool",
    "ExecutionReport",
    "EscalationRequired",
    "ImplementationResult",
    "ImplementRoster",
    "LopeCfg",
    "Negotiator",
    "Phase",
    "PhaseExecutor",
    "PhaseVerdict",
    "Proposal",
    "Round",
    "SprintDoc",
    "Validator",
    "ValidatorPool",
    "ValidatorResult",
    "VerdictStatus",
    "OpencodeValidator",
    "GeminiCliValidator",
    "StubValidator",
    "default_path",
    "defaults",
    "discover",
    "is_interactive",
    "load",
    "parse_opencode_verdict",
    "run_selector",
    "save",
]
