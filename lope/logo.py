"""
Lope ASCII logos with Mistral-inspired warm color palette.

Four variants:
- banner()    Full Mistral-style horizontal bands + LOPE wordmark (README, splash)
- box()       Minimalist loop box (version, status header)
- mascot()    Pixel creature with loop body (configure splash, gimmicks)
- tiny()      One-line `LOPE »` prefix (errors, inline)

All respect NO_COLOR env var and TTY detection — plain text fallback if not a terminal.

Colors are taken from Mistral AI's brand palette:
  - Cream  (#F5EDD8)
  - Yellow (#FFC107 → ANSI 220)
  - Orange (#FF8C00 → ANSI 208)
  - Red    (#E53935 → ANSI 196)
  - Dark   (#B71C1C → ANSI 124)
"""

from __future__ import annotations

import os
import sys

# ── ANSI color codes ───────────────────────────────────────────

_CREAM = "\033[38;5;230m"
_YELLOW = "\033[38;5;220m"
_ORANGE = "\033[38;5;208m"
_RED = "\033[38;5;196m"
_DARKRED = "\033[38;5;124m"
_BOLD_ORANGE = "\033[1;38;5;208m"
_BOLD_RED = "\033[1;38;5;196m"
_BOLD_YELLOW = "\033[1;38;5;220m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _use_color() -> bool:
    """Return True if ANSI colors should be used."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("LOPE_NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _c(text: str, color: str) -> str:
    """Wrap text in ANSI color if TTY supports it."""
    if _use_color():
        return f"{color}{text}{_RESET}"
    return text


# ── BANNER: Mistral horizontal bands + LOPE wordmark ───────────

def banner() -> str:
    """Full Mistral-style banner with lion-face O. Use for README and splash."""
    # L O(lion) P E — O is replaced with a lion face: mane (█), eyes (◉), nose (▽), mouth (◡)
    lines = [
        _c("▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓", _CREAM),
        _c("████████████████████████████████████████████████", _YELLOW),
        _c("                                                ", _ORANGE),
        _c("   ██      ██████    ██████   ███████           ", _ORANGE),
        _c("   ██     ██◉  ◉██   ██   ██  ██                ", _ORANGE),
        _c("   ██     ██ ▽▽ ██   ██████   █████             ", _ORANGE),
        _c("   ██     ██ ◡◡ ██   ██       ██                ", _ORANGE),
        _c("   ██████  ██████    ██       ███████           ", _ORANGE),
        _c("                                                ", _ORANGE),
        _c("████████████████████████████████████████████████", _RED),
        _c("▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓", _DARKRED),
        "",
        "    " + _c("any cli implements  ·  any cli validates", _DIM),
    ]
    return "\n".join(" " + line for line in lines)


# ── BOX: minimalist loop signature ─────────────────────────────

def box(version: str = "") -> str:
    """Compact signature box. Use for `lope version` and headers."""
    version_line = f"sprint runner {version}" if version else "sprint runner"
    # Box interior is 17 chars wide. L  O  P  E = 10 chars. Pad: 3 + 10 + 4 = 17.
    return "\n".join([
        "   " + _c("╭─────────────────╮", _YELLOW),
        "   " + _c("│", _YELLOW) + "   " + _c("L  O  P  E", _BOLD_ORANGE) + "    " + _c("│", _YELLOW),
        "   " + _c("│", _YELLOW) + "        " + _c("⟲", _RED) + "        " + _c("│", _YELLOW),
        "   " + _c("╰─────────────────╯", _YELLOW),
        "   " + _c(f" {version_line}", _DIM),
    ])


# ── MASCOT: pixel creature, Mistral-cat-inspired ───────────────

def mascot(line: str = "runs the validator loop") -> str:
    """Pixel mascot. Use for `lope configure` splash and random gimmicks."""
    return "\n".join([
        "        " + _c("▄▄▄▄▄▄", _YELLOW) + "                 " + _c("~~~", _DIM),
        "       " + _c("█", _YELLOW) + _c(" ◉  ◉ ", _BOLD_RED) + _c("█", _YELLOW) + "           " + _c("~", _DIM),
        "       " + _c("█", _YELLOW) + _c("  ▽▽  ", _RED) + _c("█", _YELLOW) + "         " + _c("~", _DIM),
        "        " + _c("▀▀▀▀▀▀", _YELLOW),
        "          " + _c("│", _DIM),
        "       " + _c("╭──┴──╮", _ORANGE),
        "       " + _c("│", _ORANGE) + " " + _c("LOPE", _BOLD_ORANGE) + " " + _c("│", _ORANGE),
        "       " + _c("╰─────╯", _ORANGE),
        "          " + _c("╱ ╲", _DIM),
        "         " + _c("╱   ╲", _DIM),
        "",
        "  " + _c(line, _DIM),
    ])


# ── TINY: one-line prefix ──────────────────────────────────────

def tiny() -> str:
    """One-line `▓▓ LOPE »` prefix. Use for errors and inline."""
    return (
        _c("▓", _ORANGE) + _c("▓", _RED)
        + " " + _c("LOPE", _BOLD_ORANGE)
        + " " + _c("»", _DIM)
    )


# ── GIMMICK: random surprise on CLI commands ───────────────────

_GIMMICK_LINES = [
    "runs the validator loop",
    "sniffs out the bugs",
    "peers into the code",
    "woke up for this sprint",
    "does a little dance",
    "beams quietly",
    "shipped. noticed.",
    "ensemble assembled",
    "majority vote incoming",
    "reading the files...",
    "thinking about it",
    "consulting the oracles",
    "N validators · 1 verdict",
    "watching for drift",
]


def random_gimmick() -> str:
    """Return a mascot with a random one-liner. Use for gimmick hooks."""
    import random
    return mascot(random.choice(_GIMMICK_LINES))


def maybe_gimmick(rate: float = 0.15) -> str:
    """Maybe return a mascot gimmick. Respects LOPE_GIMMICK_OFF env var."""
    if os.environ.get("LOPE_GIMMICK_OFF"):
        return ""
    if not _use_color():
        return ""
    import random
    if random.random() > rate:
        return ""
    return random_gimmick()
