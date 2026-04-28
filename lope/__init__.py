"""Lope — multi-CLI validator ensemble for AI work.

Three structured modes (`negotiate`, `execute`, `audit`) cover multi-phase
sprints. Five single-shot modes (`ask`, `review`, `vote`, `compare`, `pipe`)
cover cross-model Q&A, file critique, structured votes, A/B comparison,
and stdin-fed fan-out. One roster-management mode (`team`) adds, removes,
lists, and smoke-tests validators from any chat window — no JSON editing.
`team add --from-curl` parses a pasted curl command and turns it into a
registered HTTP provider in one step. Any CLI implements; any CLI validates.
"""

__version__ = "0.7.2"

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
