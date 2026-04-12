"""CLI auto-discovery — probe all known AI CLIs, return what's available."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import List, Optional

VERSION = 1


@dataclass
class CliInfo:
    name: str
    binary: str
    display_name: str
    tier: int
    is_default: bool
    # For generic providers: if set, this CLI is added via subprocess provider config
    generic_command: Optional[List[str]] = None


KNOWN_CLIS = [
    # Tier 1: Hardcoded validators with custom parsing
    CliInfo(
        name="claude",
        binary="claude",
        display_name="Claude Code",
        tier=1,
        is_default=True,
    ),
    CliInfo(
        name="opencode",
        binary="opencode",
        display_name="OpenCode",
        tier=1,
        is_default=True,
    ),
    CliInfo(
        name="gemini",
        binary="gemini",
        display_name="Gemini CLI",
        tier=2,
        is_default=False,
    ),
    CliInfo(
        name="codex",
        binary="codex",
        display_name="OpenAI Codex",
        tier=2,
        is_default=False,
    ),
    CliInfo(
        name="vibe",
        binary="vibe",
        display_name="Mistral Vibe",
        tier=2,
        is_default=False,
        generic_command=["vibe", "run", "{prompt}"],
    ),
    CliInfo(
        name="aider",
        binary="aider",
        display_name="Aider",
        tier=3,
        is_default=False,
    ),
    # Tier 2: Auto-provisioned via generic subprocess
    CliInfo(
        name="ollama",
        binary="ollama",
        display_name="Ollama (local)",
        tier=2,
        is_default=False,
        generic_command=["ollama", "run", "qwen3:8b", "{prompt}"],
    ),
    CliInfo(
        name="goose",
        binary="goose",
        display_name="Goose (Block)",
        tier=2,
        is_default=False,
        generic_command=["goose", "run", "--text", "{prompt}"],
    ),
    CliInfo(
        name="interpreter",
        binary="interpreter",
        display_name="Open Interpreter",
        tier=3,
        is_default=False,
        generic_command=["interpreter", "--fast", "-y", "{prompt}"],
    ),
    CliInfo(
        name="llama-cpp",
        binary="llama-cli",
        display_name="llama.cpp",
        tier=3,
        is_default=False,
        generic_command=["llama-cli", "-p", "{prompt}", "--no-display-prompt"],
    ),
    CliInfo(
        name="gh-copilot",
        binary="gh",
        display_name="GitHub Copilot CLI",
        tier=3,
        is_default=False,
        generic_command=["gh", "copilot", "suggest", "{prompt}"],
    ),
    CliInfo(
        name="amazon-q",
        binary="q",
        display_name="Amazon Q",
        tier=3,
        is_default=False,
        generic_command=["q", "chat", "{prompt}"],
    ),
]


def discover() -> List[CliInfo]:
    """Probe all known CLIs via shutil.which, return only those found. Never raises."""
    found: List[CliInfo] = []
    for cli in KNOWN_CLIS:
        try:
            if shutil.which(cli.binary) is not None:
                found.append(cli)
        except Exception:
            pass
    return found


def defaults(available: List[CliInfo]) -> List[CliInfo]:
    """Return tier-1 defaults that are available. Fallback to first two available."""
    defaults_found = [c for c in available if c.is_default]
    if defaults_found:
        return defaults_found
    if available:
        return available[:2]
    return []
