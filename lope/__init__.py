"""Lope — autonomous sprint runner with multi-CLI validator ensemble."""

__version__ = "0.4.3"

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
