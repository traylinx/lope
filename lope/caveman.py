"""
Intelligent caveman mode — token-efficient validator communication.

Sounds primitive (drop articles, grunt in fragments). Actually smart:
code, paths, line numbers, error messages stay verbatim — only wrapper
prose gets compressed. Terse like a caveman, linguistically precise.

Adapted from JuliusBrussee/caveman (MIT License).
Integrated into lope to cut validator prompt/response tokens by ~50-65%.

Default: full mode ON. Users opt out via LOPE_CAVEMAN=off.

When validators review code, they don't need polished prose — they need
precise verdicts. Caveman mode instructs validators to:
  - Drop articles, filler, hedging, pleasantries
  - Keep exact: code, paths, line numbers, error messages
  - Use fragments: "Race in auth middleware. Fix at token.go:142."
  - Rationale: 1-3 terse sentences, not paragraphs

Controlled via LOPE_CAVEMAN env var:
  - "full" (default): drop articles, fragments OK, short synonyms
  - "lite": no filler/hedging but keep articles + full sentences
  - "off": disable (verbose validator responses)

Acknowledgement: core rules from github.com/JuliusBrussee/caveman (MIT).
"""

import os

_MODE = os.environ.get("LOPE_CAVEMAN", "full").lower()


# ── Directives injected into validator prompts ─────────────────

CAVEMAN_VALIDATOR_DIRECTIVE = "" if _MODE == "off" else """\
TOKEN EFFICIENCY: respond terse. Drop articles (a/an/the), filler (just/really/\
basically), hedging (I think/perhaps/might), pleasantries (sure/happy to help). \
Fragments OK. Keep exact: code, paths, line numbers, commands, error messages. \
Rationale = 1-3 short sentences, not paragraphs. \
Pattern: [thing] [action] [reason]. No preamble."""

CAVEMAN_LITE_DIRECTIVE = """\
Be concise. No filler or hedging. Keep technical precision. \
Rationale = 2-3 sentences max."""


def get_directive() -> str:
    """Return the appropriate caveman directive for the current mode."""
    if _MODE == "off":
        return ""
    if _MODE == "lite":
        return CAVEMAN_LITE_DIRECTIVE
    return CAVEMAN_VALIDATOR_DIRECTIVE  # "full" or "ultra"
