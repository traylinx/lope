"""Lope CLI — autonomous sprint runner with multi-CLI validation."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from . import (
    Auditor,
    LopeCfg,
    Negotiator,
    PhaseExecutor,
    SprintDoc,
    defaults,
    discover,
    load as load_config,
    save as save_config,
    default_path,
    run_selector,
    is_interactive,
)
from .validators import build_validator_pool


def main():
    parser = argparse.ArgumentParser(
        prog="lope",
        description=(
            "Autonomous sprint runner with multi-CLI validator ensemble. "
            "Any AI CLI implements, any AI CLI validates. Supports 14 built-in CLIs "
            "(claude, opencode, gemini, codex, vibe, aider, ollama, goose, interpreter, "
            "llama-cpp, gh-copilot, amazon-q, pi, qwen) plus infinite custom providers via JSON. "
            "Three domains: engineering, business, research. "
            "Caveman mode (LOPE_CAVEMAN env var) compresses validator prompts 50-65%."
        ),
        epilog=(
            "Config: ~/.lope/config.json (set LOPE_HOME to override). "
            "Custom providers: add to 'providers' array in config. "
            "Caveman mode: LOPE_CAVEMAN=full|lite|off. "
            "Docs: https://github.com/traylinx/lope"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    # Shared pool-override flags — added to every subcommand that loads a
    # validator pool. CLI flags take precedence over env vars (LOPE_VALIDATORS,
    # LOPE_PRIMARY, LOPE_TIMEOUT, LOPE_PARALLEL), which take precedence over
    # per-project ./.lope/config.json, which takes precedence over the user
    # global ~/.lope/config.json. See docs/reference.md "Config precedence".
    def _add_pool_flags(p):
        p.add_argument("--validators", default=None,
                       help="Comma-separated validator list, e.g. opencode,gemini")
        p.add_argument("--primary", default=None,
                       help="Name of the primary validator (must be in --validators)")
        p.add_argument("--timeout", type=int, default=None,
                       help="Per-validator timeout in seconds")
        p.set_defaults(parallel=None)
        parallel_group = p.add_mutually_exclusive_group()
        parallel_group.add_argument("--parallel", dest="parallel", action="store_true",
                                    help="Run validators in parallel")
        parallel_group.add_argument("--sequential", dest="parallel", action="store_false",
                                    help="Run validators sequentially")

    # negotiate
    neg = sub.add_parser("negotiate", help="Draft a sprint doc via multi-round validation")
    neg.add_argument("goal", help="Sprint goal description")
    neg.add_argument("--out", default=None, help="Output path for sprint doc")
    neg.add_argument("--max-rounds", type=int, default=3)
    neg.add_argument("--context", default="", help="Additional context")
    neg.add_argument("--domain", default="engineering",
                     choices=["engineering", "business", "research"],
                     help="Domain: engineering (default), business, or research")
    _add_pool_flags(neg)

    # execute
    exe = sub.add_parser("execute", help="Run sprint phases with validator-in-the-loop")
    exe.add_argument("sprint_doc", help="Path to sprint doc markdown")
    exe.add_argument("--phase", type=int, default=None, help="Run specific phase only")
    exe.add_argument("--manual", action="store_true",
                     help="Human-in-the-loop mode: wait for Enter between phases "
                          "(legacy pre-v0.4.0 behavior). Default is autonomous "
                          "via primary validator's generate() method.")
    _add_pool_flags(exe)

    # audit
    aud = sub.add_parser("audit", help="Generate scorecard from sprint results")
    aud.add_argument("sprint_doc", help="Path to sprint doc markdown")
    aud.add_argument("--no-journal", action="store_true", help="Skip journal write")
    _add_pool_flags(aud)

    # ask — fan out ONE question to every validator, collect N raw answers.
    # No sprint, no phases, no verdict parsing. Just multi-model Q&A.
    ask = sub.add_parser(
        "ask",
        help="Ask every validator the same question, collect N answers",
    )
    ask.add_argument("question", help="The question to fan out (quoted)")
    ask.add_argument("--json", action="store_true",
                     help="Emit machine-readable JSON instead of human sections")
    ask.add_argument("--context", default="",
                     help="Optional context prepended to every validator's prompt")
    _add_pool_flags(ask)

    # review — read a file, fan out a review prompt, collect N per-model critiques.
    rev = sub.add_parser(
        "review",
        help="Fan out a file review to every validator, collect N critiques",
    )
    rev.add_argument("file", help="Path to the file to review")
    rev.add_argument("--focus", default="",
                     help="Optional focus area (e.g. 'security', 'perf', 'tests')")
    rev.add_argument("--json", action="store_true",
                     help="Emit machine-readable JSON instead of human sections")
    # v0.7 structured-mode flags. Default review behavior is unchanged: raw
    # per-validator critique blocks. `--consensus`/`--structured` switch on
    # the dedup + scoring pipeline; `--format` picks the renderer.
    rev.add_argument("--consensus", action="store_true",
                     help="Merge, dedupe, and consensus-rank findings (v0.7)")
    rev.add_argument("--structured", dest="consensus", action="store_true",
                     help="Alias for --consensus")
    rev.add_argument("--min-consensus", dest="min_consensus", type=float, default=0.0,
                     help="Drop findings with consensus_score below this threshold (default: 0.0)")
    rev.add_argument("--similarity", type=float, default=0.85,
                     help="Cross-validator dedup similarity threshold (default: 0.85)")
    rev.add_argument("--format", dest="output_format", default="text",
                     choices=["text", "json", "markdown", "markdown-pr", "sarif"],
                     help="Output format for consensus mode (default: text)")
    rev.add_argument("--include-raw", dest="include_raw", action="store_true",
                     help="Append raw per-validator responses to consensus output")
    _add_pool_flags(rev)

    # vote — each validator picks one of the provided options; tally + print winner.
    # Addresses "option drift" (pi design review 2026-04-22): every validator
    # receives the IDENTICAL option list inside the same prompt so there's no
    # post-hoc reconciliation of differently-interpreted options.
    vote = sub.add_parser(
        "vote",
        help="Each validator picks one option; tally + print winner",
    )
    vote.add_argument("prompt", help="The question / proposal to vote on")
    vote.add_argument("--options", required=True,
                      help="Comma-separated option labels (e.g. 'A,B,C' or 'yes,no,maybe')")
    vote.add_argument("--json", action="store_true",
                      help="Emit JSON with per-voter picks + tallies")
    _add_pool_flags(vote)

    # compare — each validator picks the better of two files against explicit criteria.
    # Addresses "criteria opacity" (pi design review 2026-04-22): `--criteria`
    # is passed explicitly into every validator's prompt so "better" is defined,
    # not invented per-model.
    comp = sub.add_parser(
        "compare",
        help="Each validator picks the better of two files against explicit criteria",
    )
    comp.add_argument("file_a", help="First file path (labelled 'A' in voting)")
    comp.add_argument("file_b", help="Second file path (labelled 'B' in voting)")
    comp.add_argument("--criteria", default="correctness and clarity",
                      help="Comma-separated evaluation dimensions (default: 'correctness and clarity')")
    comp.add_argument("--json", action="store_true",
                      help="Emit JSON with per-voter picks + tallies")
    _add_pool_flags(comp)

    # pipe — read stdin, fan out to validators, print responses.
    # Addresses "partial failure" (pi design review 2026-04-22): default is
    # fire-and-forget per-validator isolation; --require-all opt-in for strict
    # (exit non-zero if ANY validator errors).
    pp = sub.add_parser(
        "pipe",
        help="Read stdin as the prompt, fan out to validators, print answers",
    )
    pp.add_argument("--require-all", action="store_true",
                    help="Exit non-zero if any validator errors (default: continue, show [ERROR] per-section)")
    pp.add_argument("--json", action="store_true",
                    help="Emit JSON instead of human sections")
    _add_pool_flags(pp)

    # team — grandma-friendly validator management via CLI flags so any LLM
    # running in a chat window can translate natural language ("add openclaw
    # to lope") into the right invocation. Four verbs: list / add / remove / test.
    team = sub.add_parser(
        "team",
        help="Manage your validator team: list / add / remove / test teammates",
    )
    team_sub = team.add_subparsers(dest="team_cmd")

    team_sub.add_parser("list", help="Show current team members (default if no subcommand)")

    t_add = team_sub.add_parser(
        "add",
        help="Add a new teammate. Provide --cmd (subprocess) OR --url (HTTP). "
             "Adds to providers AND enables in validators unless --disabled.",
    )
    t_add.add_argument("name", help="Teammate name (e.g. openclaw, my-ollama, mistral-pod)")
    t_add.add_argument("--cmd", default=None,
                       help="Subprocess command, e.g. \"openclaw chat --prompt {prompt}\". "
                            "Tokens split via shlex. If {prompt} is missing, it's appended.")
    t_add.add_argument("--stdin", action="store_true",
                       help="Pipe prompt via stdin instead of argv substitution")
    t_add.add_argument("--url", default=None,
                       help="HTTP endpoint URL (implies HTTP type). OpenAI-compatible shape by default.")
    t_add.add_argument("--model", default=None,
                       help="Model name for HTTP body (required unless --body-json used)")
    t_add.add_argument("--key-env", default=None,
                       help="Env var name holding the API key (e.g. OPENAI_API_KEY). "
                            "Stored as ${VAR} in headers — expanded at call time, not saved in plaintext.")
    t_add.add_argument("--key-header", default="Authorization",
                       help="Auth header name (default: Authorization)")
    t_add.add_argument("--key-prefix", default="Bearer ",
                       help="Auth token prefix (default: 'Bearer '). Use '' for raw keys.")
    t_add.add_argument("--response-path", default=None,
                       help="JSON dot-path to extract answer (default: choices.0.message.content)")
    t_add.add_argument("--body-json", default=None,
                       help="Raw JSON body override — replaces OpenAI-compatible shape entirely")
    t_add.add_argument("--from-curl", default=None,
                       help="Paste an entire curl command (quoted). Auto-extracts URL, headers, "
                            "body, and response_path; auto-injects {prompt} into the user-content "
                            "field. Credential-bearing headers must use ${VAR}, or pass --key-env "
                            "so lope swaps the literal for you.")
    t_add.add_argument("--wrap", default=None,
                       help="Prompt wrapper template, e.g. 'Answer tersely: {prompt}'")
    t_add.add_argument("--timeout", type=int, default=None,
                       help="Per-call timeout override in seconds")
    t_add.add_argument("--primary", action="store_true",
                       help="Make this the primary validator (used by execute() for implementation)")
    t_add.add_argument("--disabled", action="store_true",
                       help="Save provider config but don't add to active validators list")
    t_add.add_argument("--force", action="store_true",
                       help="Overwrite an existing provider with the same name")

    t_rm = team_sub.add_parser(
        "remove",
        help="Remove a teammate from providers + validators + primary",
    )
    t_rm.add_argument("name", help="Teammate name to remove")

    t_test = team_sub.add_parser(
        "test",
        help="Smoke-test one teammate with a prompt (defaults to 'Say hello in one word.')",
    )
    t_test.add_argument("name", help="Teammate name to test")
    t_test.add_argument("prompt", nargs="?", default="Say hello in one word.",
                        help="Test prompt (default: 'Say hello in one word.')")
    t_test.add_argument("--timeout", type=int, default=60,
                        help="Timeout in seconds (default: 60)")

    # status
    sub.add_parser("status", help="Show available validators and config")

    # configure
    sub.add_parser("configure", help="Interactive validator picker")

    # install
    inst = sub.add_parser("install", help="Install lope skills into CLI hosts")
    inst.add_argument("--host", default="all", help="Target host (claude, codex, gemini, opencode, cursor, all)")

    # version
    sub.add_parser("version", help="Show version")

    # docs — prints the complete lope reference
    sub.add_parser("docs", help="Print the complete lope reference to stdout")

    args = parser.parse_args()

    if args.command is None:
        from .logo import banner
        print()
        print(banner())
        print()
        parser.print_help()
        sys.exit(0)

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s | %(message)s",
    )

    if args.command == "version":
        from . import __version__ as _v
        from .logo import box
        print()
        print(box(f"v{_v}"))
        print()
        return

    if args.command == "docs":
        _cmd_docs()
        return

    if args.command == "status":
        _cmd_status()
        return

    if args.command == "configure":
        _cmd_configure()
        return

    if args.command == "install":
        _cmd_install(args.host)
        return

    if args.command == "negotiate":
        from .runlock import acquire as _runlock
        with _runlock("negotiate"):
            _cmd_negotiate(args)
        return

    if args.command == "execute":
        from .runlock import acquire as _runlock
        with _runlock("execute"):
            _cmd_execute(args)
        return

    if args.command == "audit":
        _cmd_audit(args)
        return

    if args.command == "ask":
        _cmd_ask(args)
        return

    if args.command == "review":
        _cmd_review(args)
        return

    if args.command == "vote":
        _cmd_vote(args)
        return

    if args.command == "compare":
        _cmd_compare(args)
        return

    if args.command == "pipe":
        _cmd_pipe(args)
        return

    if args.command == "team":
        _cmd_team(args)
        return


def _cmd_docs():
    """Print the complete lope reference (docs/reference.md) to stdout."""
    from pathlib import Path
    here = Path(__file__).resolve().parent.parent  # lope/ -> repo root
    ref = here / "docs" / "reference.md"
    if not ref.is_file():
        # Fallback: try ~/.lope if we're imported from a non-repo install
        home_ref = Path.home() / ".lope" / "docs" / "reference.md"
        if home_ref.is_file():
            ref = home_ref
        else:
            print(
                f"ERROR: lope reference not found at {ref} or {home_ref}.\n"
                f"Reinstall lope: https://github.com/traylinx/lope",
                file=sys.stderr,
            )
            sys.exit(1)
    print(ref.read_text(encoding="utf-8"))


def _cmd_status():
    from .logo import box, maybe_gimmick
    available = discover()
    cfg = load_config(default_path())
    print()
    print(box())
    print("\nLope — Validator Status")
    print("-" * 40)
    print(f"\nDetected built-in CLIs:")
    for cli in available:
        marker = " * DEFAULT" if cli.is_default else ""
        print(f"  {cli.display_name:<20} ({cli.name}){marker}")
    if not available:
        print("  (none detected)")

    if cfg and cfg.providers:
        print(f"\nCustom providers ({len(cfg.providers)}):")
        for p in cfg.providers:
            name = p.get("name", "?")
            ptype = p.get("type", "?")
            print(f"  {name:<20} ({ptype})")

    if cfg:
        print(f"\nConfig: {default_path()}")
        print(f"  Validators: {', '.join(cfg.validators)}")
        print(f"  Primary: {cfg.primary}")
        print(f"  Parallel: {cfg.parallel}")
        print(f"  Timeout: {cfg.timeout}s")

        # v0.4.0: show learned adapters (self-healed CLI invocations)
        if cfg.learned_adapters:
            import time as _t
            from .healer import is_adapter_expired, LEARNED_ADAPTER_TTL_SECONDS
            print(f"\nLearned adapters ({len(cfg.learned_adapters)}):")
            now = _t.time()
            for cli_name, adapter in cfg.learned_adapters.items():
                age_days = int((now - adapter.timestamp) / 86400) if adapter.timestamp > 0 else -1
                warn = ""
                if is_adapter_expired(adapter, now):
                    warn = " [EXPIRED — will re-verify on next run]"
                elif age_days >= 60:
                    warn = " [aging — re-verify soon]"
                src = adapter.source_cli or "?"
                conf = f"{adapter.confidence:.2f}" if adapter.confidence > 0 else "?"
                print(f"  {cli_name:<20} from {src}, {age_days}d ago, "
                      f"conf={conf}{warn}")

        # v0.4.0: show recent heal events from the journal
        from .journal import read_recent
        recent = read_recent(limit=5)
        heal_events = [e for e in recent if str(e.get("event", "")).startswith("heal_")]
        if heal_events:
            print(f"\nRecent heal events ({len(heal_events)}):")
            for evt in heal_events[-5:]:
                ts = evt.get("timestamp", 0)
                age = int((__import__("time").time() - ts) / 60) if ts else -1
                print(f"  {evt.get('event', '?'):<16} {evt.get('cli', '?'):<16} {age}m ago")
    else:
        print(f"\nNo config found. Run: lope configure")
    print()
    # Random gimmick (15% chance)
    gimmick = maybe_gimmick(rate=0.15)
    if gimmick:
        print(gimmick)
        print()


def _cmd_configure():
    from .logo import mascot
    print()
    print(mascot("let's set up your validators"))
    print()
    available = discover()
    if not available:
        print("No AI CLIs detected. Install at least one of: claude, opencode, gemini, codex, aider")
        sys.exit(1)
    cfg = run_selector(available)
    path = default_path()
    save_config(cfg, path)
    print(f"\nConfig saved to {path}")
    print(f"  Validators: {', '.join(cfg.validators)}")
    print(f"  Primary: {cfg.primary}")
    print(f"  Parallel: {cfg.parallel}")


def _cmd_install(host: str):
    """Install SKILL.md files into CLI host skill directories.

    Canonical installer is the bash script at repo root (./install).
    This Python command delegates to it for consistency.
    """
    pkg_dir = Path(__file__).parent.parent
    install_script = pkg_dir / "install"
    if not install_script.exists():
        print(f"Install script not found at {install_script}")
        print("Use: git clone https://github.com/traylinx/lope.git ~/.lope && ~/.lope/install")
        sys.exit(1)
    import subprocess
    args = [str(install_script)]
    if host != "all":
        args.extend(["--host", host])
    subprocess.run(args, check=False)


def _ensure_config(args=None):
    """Load or create config using the v0.4.0 layered precedence chain.

    Precedence (lowest → highest): built-in defaults < user global <
    per-project < env vars < CLI flags. Never mutates the global file
    unless the user has zero validators configured and runs `lope configure`.
    """
    from .config import load_layered

    # Extract CLI overrides from args (negotiate/execute/audit all carry
    # these via _add_pool_flags).
    cli_overrides: Dict[str, Any] = {}
    if args is not None:
        if getattr(args, "validators", None):
            cli_overrides["validators"] = [
                s.strip() for s in args.validators.split(",") if s.strip()
            ]
        if getattr(args, "primary", None):
            cli_overrides["primary"] = args.primary
        if getattr(args, "timeout", None) is not None:
            cli_overrides["timeout"] = args.timeout
        if getattr(args, "parallel", None) is not None:
            cli_overrides["parallel"] = args.parallel

    cfg = load_layered(cli_overrides=cli_overrides)

    # If no validators are configured anywhere, fall back to the legacy
    # first-run UX: interactive picker if stdin is a TTY, auto-pick defaults
    # otherwise. This path only fires when the user has literally never
    # configured lope and has not passed any CLI flags or env vars.
    if not cfg.validators:
        available = discover()
        if not available:
            print("No AI CLIs detected. Install at least one of: claude, opencode, gemini, codex, aider")
            sys.exit(1)
        if is_interactive() and not os.path.exists(default_path()):
            cfg = run_selector(available)
            save_config(cfg, default_path())
        else:
            defs = defaults(available)
            cfg = LopeCfg(
                validators=[c.name for c in defs],
                primary=defs[0].name if defs else "",
                timeout=cfg.timeout,
                parallel=cfg.parallel,
                providers=cfg.providers,
                learned_adapters=cfg.learned_adapters,
            )

    pool = build_validator_pool(cfg)
    return cfg, pool


def _http_llm_fallback(system: str, user: str, llm_url: str) -> str:
    """Optional hosted-LLM fallback when the primary validator can't draft.

    Only used when the user explicitly sets LOPE_LLM_URL. Not the default
    path — the default path is to use the primary CLI validator itself.
    """
    import json as _json
    import urllib.error
    import urllib.request

    llm_model = os.environ.get("LOPE_LLM_MODEL", "gpt-4o-mini")
    llm_api_key = os.environ.get("LOPE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    payload = _json.dumps({
        "model": llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 4096,
        "temperature": 0.7,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"
    req = urllib.request.Request(
        f"{llm_url}/chat/completions",
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        hints = []
        if e.code == 401:
            hints.append("Set LOPE_LLM_API_KEY (or OPENAI_API_KEY).")
        elif e.code == 400:
            hints.append(f"Model '{llm_model}' may not exist. Set LOPE_LLM_MODEL.")
        elif e.code == 404:
            hints.append(f"Endpoint not found at {llm_url}. Check LOPE_LLM_URL.")
        raise RuntimeError(
            f"LLM fallback failed — HTTP {e.code}: {e.reason}\n"
            f"  URL:   {llm_url}/chat/completions\n"
            f"  Model: {llm_model}\n"
            f"  Body:  {body}\n"
            + ("  " + "\n  ".join(hints) if hints else "")
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"LLM fallback failed — cannot reach {llm_url}: {e.reason}"
        ) from None
    return data["choices"][0]["message"]["content"]


def _cmd_negotiate(args):
    print(f"\nLope negotiate: {args.goal}\n")
    cfg, pool = _ensure_config(args)

    # Drafter = the primary validator in the pool. Lope's core premise:
    # any CLI implements, any CLI validates. So drafting a proposal is
    # just the primary CLI implementing; reviewers then vote on it.
    # No separate hosted LLM endpoint required.
    primary = pool.primary_validator()
    timeout = cfg.timeout
    print(f"Drafter: {primary.name}  ·  Reviewers: {', '.join(v.name for v in pool.reviewers()) or '(none — need at least 2 validators for real ensemble review)'}")
    print()

    def llm_call(system: str, user: str) -> str:
        combined = f"{system}\n\n{user}"
        # Build drafter fallback chain: primary first, then all other
        # validators in pool order. This mirrors ValidatorPool's
        # INFRA_ERROR fallback but for the drafter stage.
        # Build drafter fallback chain from the pool. EnsemblePool uses
        # `_validators`, ValidatorPool uses `_ordered`. Try both for safety.
        all_validators = getattr(pool, '_validators', None) or getattr(pool, '_ordered', [primary])
        drafter_chain = [primary] + [v for v in all_validators if v is not primary]
        errors = []
        for idx, drafter in enumerate(drafter_chain):
            try:
                if idx > 0:
                    print(f"[drafter fallback] {primary.name} failed, trying {drafter.name}...")
                return drafter.generate(combined, timeout=timeout)
            except NotImplementedError:
                errors.append(f"{drafter.name}: does not support drafting")
                continue
            except (RuntimeError, OSError, Exception) as e:
                msg = str(e).splitlines()[0] if str(e) else type(e).__name__
                errors.append(f"{drafter.name}: {msg[:120]}")
                continue
        # All drafters failed — try HTTP fallback if user opted in.
        llm_url = os.environ.get("LOPE_LLM_URL")
        if llm_url:
            try:
                return _http_llm_fallback(system, user, llm_url)
            except Exception as e:
                errors.append(f"HTTP fallback ({llm_url}): {str(e).splitlines()[0][:120]}")
        # Complete failure — give the user actionable next steps.
        error_summary = "\n".join(f"    - {e}" for e in errors)
        raise RuntimeError(
            f"All {len(drafter_chain)} drafters in the pool failed:\n"
            f"{error_summary}\n"
            f"\n  Diagnose: run `lope status` to see your validator pool\n"
            f"  Fix: edit ~/.lope/config.json — set 'primary' to a CLI that works\n"
            f"        (try: claude, opencode, or vibe if available)\n"
            f"  Or: set LOPE_LLM_URL + LOPE_LLM_API_KEY to a hosted endpoint"
        ) from None

    negotiator = Negotiator(
        llm_call=llm_call,
        validator_pool=pool,
        max_rounds=args.max_rounds,
        domain=args.domain,
    )
    try:
        result = negotiator.converge(args.goal, args.context)
    except RuntimeError as e:
        print()
        print("lope negotiate failed:")
        for line in str(e).splitlines():
            print(f"  {line}")
        print()
        sys.exit(2)

    if isinstance(result, SprintDoc):
        out_path = args.out or f"SPRINT-{result.slug.upper()}.md"
        result.save(out_path)
        print(f"Sprint doc saved to: {out_path}")
        print(f"Rounds: {len(negotiator.rounds)}")
        print(f"\nRun: lope execute {out_path}")
        from .logo import maybe_gimmick
        gimmick = maybe_gimmick(rate=0.25)
        if gimmick:
            print()
            print(gimmick)
    else:
        print(f"Negotiation escalated: {result}")
        sys.exit(1)


def _cmd_execute(args):
    doc = SprintDoc.from_markdown(
        Path(args.sprint_doc).read_text(), path=args.sprint_doc
    )

    # v0.4.0: fail loudly on zero-phase sprints instead of silently reporting
    # "All phases passed!" The old behavior was a false-success hazard — a
    # sprint doc with the wrong heading level (`##` instead of `###`) parsed
    # as zero phases and shipped as a clean PASS.
    if not doc.phases:
        print(
            f"\nERROR: sprint doc at {args.sprint_doc} contains 0 phases.\n"
            f"  Each phase must start with a level-3 heading: `### Phase N: <name>`\n"
            f"  Each phase must have non-empty **Files:** / **Artifacts:** /\n"
            f"  **Deliverables:** and **Tests:** / **Checks:** / **Success Metrics:** lists.\n"
            f"  Run `lope docs` or `lope negotiate --help` for the sprint doc format.",
            file=sys.stderr,
        )
        sys.exit(2)

    print(f"\nLope execute: {doc.title} ({len(doc.phases)} phases)\n")
    cfg, pool = _ensure_config(args)

    # v0.4.0: autonomous implementation via primary validator's generate()
    # method. The primary CLI runs as a subprocess in the current working
    # directory and writes files directly (claude, codex, opencode, aider,
    # gemini-cli all have their own filesystem tools). Lope's role is to
    # orchestrate the prompt, capture the summary, and hand the result to
    # the validator ensemble for review.
    #
    # Opt out with --manual for legacy human-in-the-loop flow.
    primary = pool.primary_validator()

    if args.manual:
        def implementation_fn(phase, fix_context=None):
            from .executor import ImplementationResult
            print(f"\n{'='*50}")
            print(f"Phase {phase.index}: {phase.name}")
            print(f"Goal: {phase.goal}")
            if fix_context:
                print(f"Fixes to apply: {fix_context}")
            print(f"{'='*50}")
            print("\nManual mode: implement this phase, then press Enter to validate...")
            try:
                input()
            except EOFError:
                print("\nERROR: --manual mode requires an interactive stdin. "
                      "Drop --manual to run autonomously via the primary validator.",
                      file=sys.stderr)
                sys.exit(3)
            return ImplementationResult(ok=True, summary="implemented by operator")
    else:
        print(f"Implementer: {primary.name}  ·  Reviewers: "
              f"{', '.join(v.name for v in pool.reviewers()) or '(none)'}")
        print(f"Timeout: {cfg.timeout}s  ·  Mode: autonomous (use --manual for human-in-loop)")
        print()

        def implementation_fn(phase, fix_context=None):
            from .executor import ImplementationResult
            from .validators import _is_flag_error

            phase_blurb = _phase_to_prompt(phase, doc, fix_context)
            print(f"\n>>> Phase {phase.index}: {phase.name}")
            print(f">>> Delegating to {primary.name} ({cfg.timeout}s timeout)...")

            try:
                output = primary.generate(phase_blurb, timeout=cfg.timeout)
            except NotImplementedError:
                return ImplementationResult(
                    ok=False,
                    summary=(
                        f"{primary.name} does not support autonomous implementation "
                        f"via .generate() in v0.4.1. Re-run with --manual or pick "
                        f"a different primary (claude, opencode, gemini-cli, codex, aider)."
                    ),
                )
            except Exception as e:
                # v0.4.1: detect flag-surface errors from the generate() path
                # and route through the SelfHealer if LOPE_SELF_HEAL=1. Same
                # detection logic as the validate() path (_infra_error), now
                # extended to cover implementation failures too.
                err_msg = f"{type(e).__name__}: {e}"
                if _is_flag_error(err_msg):
                    healed = _try_self_heal_from_generate(
                        primary, err_msg, pool, cfg.timeout,
                    )
                    if healed:
                        print(f">>> self-heal succeeded, retrying phase "
                              f"{phase.index} with learned adapter")
                        try:
                            output = primary.generate(phase_blurb, timeout=cfg.timeout)
                        except Exception as e2:
                            return ImplementationResult(
                                ok=False,
                                summary=(
                                    f"{primary.name} still failing after self-heal: "
                                    f"{type(e2).__name__}: {e2}"
                                ),
                            )
                    else:
                        return ImplementationResult(
                            ok=False,
                            summary=(
                                f"{primary.name} failed with a flag-surface error "
                                f"(upstream CLI likely renamed a flag):\n  {err_msg[:400]}\n"
                                f"Set LOPE_SELF_HEAL=1 and re-run to attempt "
                                f"automatic adapter repair."
                            ),
                        )
                else:
                    return ImplementationResult(
                        ok=False,
                        summary=f"{primary.name} subprocess failed: {err_msg[:400]}",
                    )

            # The primary writes files directly via its own tools. What comes
            # back in stdout is a free-form summary — pass it through to the
            # validators so they can see what the primary *claims* it did,
            # alongside the actual file diffs they'll read themselves.
            summary = (output or "").strip()[:2000]
            if not summary:
                summary = f"{primary.name} completed phase {phase.index} (no stdout summary)"
            print(f">>> {primary.name} returned {len(output or '')} chars")
            return ImplementationResult(ok=True, summary=summary)

    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=implementation_fn,
        max_rounds_per_phase=3,
    )
    report = executor.run(doc)

    auditor = Auditor()
    print(f"\n{auditor.scorecard(report)}")

    if report.ok and report.sprint_doc.phases:
        print("\nAll phases passed!")
        from .logo import mascot
        print()
        print(mascot("shipped. noticed."))
    else:
        print(f"\nEscalation: {report.error}")
        sys.exit(1)


def _try_self_heal_from_generate(primary, err_msg: str, pool, timeout: int):
    """v0.4.1: route generate()-path flag errors through SelfHealer.

    The validator.generate() path bypasses _infra_error (which is
    validate()-path only), so without this helper the v0.4.0 self-heal
    never fires when a CLI flag break happens during implementation.

    Returns a LearnedAdapter on success, None otherwise. Never raises.
    """
    from .healer import SelfHealer

    # Need at least one reviewer that is NOT the failing CLI
    reviewers = [v for v in pool.reviewers() if v.name != primary.name]
    if not reviewers:
        print(f">>> self-heal skipped: no reviewer available "
              f"(pool has only {primary.name})")
        return None

    healer = SelfHealer()
    if not healer.should_attempt(primary.name, reviewer_available=True):
        print(f">>> self-heal skipped: set LOPE_SELF_HEAL=1 to enable "
              f"automatic adapter repair on flag breaks")
        return None

    # Reconstruct the failing argv as best we can — the exception message
    # doesn't carry it cleanly, so we pass a placeholder. The healer
    # prompts the reviewer with stderr + help output, which is usually
    # enough for the reviewer to propose a corrected invocation without
    # needing the exact old argv.
    binary = getattr(primary, "_binary", primary.name)
    old_argv = [binary, "<unknown>", "{prompt}"]

    print(f">>> flag-surface error detected, attempting self-heal...")
    print(f">>> reviewer: {reviewers[0].name}  ·  target: {primary.name}")
    return healer.attempt(
        cli_name=primary.name,
        cli_binary=binary,
        old_argv=old_argv,
        stderr=err_msg,
        reviewer=reviewers[0],
    )


def _phase_to_prompt(phase, doc, fix_context=None) -> str:
    """Build the implementer prompt for a sprint phase.

    The prompt tells the primary CLI what phase we're on, what the files
    and tests should look like, the sprint context, and any validator
    fix instructions from a previous NEEDS_FIX round.
    """
    parts = [
        f"You are implementing phase {phase.index} of a lope sprint.",
        f"Sprint: {doc.title}",
        "",
        f"## Phase {phase.index}: {phase.name}",
        "",
        f"Goal: {phase.goal}",
        "",
    ]
    if phase.artifacts:
        parts.append("## Files / Artifacts / Deliverables to produce or modify")
        parts.extend(f"- {a}" for a in phase.artifacts)
        parts.append("")
    if phase.checks:
        parts.append("## Tests / Checks / Success Metrics")
        parts.extend(f"- {c}" for c in phase.checks)
        parts.append("")
    if fix_context:
        parts.append("## Fixes to apply from prior validator review")
        # fix_context is List[str] from executor.py (phase.verdict.required_fixes),
        # but we also tolerate a plain string for future/test callers.
        if isinstance(fix_context, (list, tuple)):
            for fix in fix_context:
                parts.append(f"- {fix}")
        else:
            parts.append(str(fix_context))
        parts.append("")
    parts.append(
        "Implement the phase completely. Write the files directly using "
        "your own filesystem tools. When you are done, return a short "
        "(1-3 sentence) summary of what you changed — the validator "
        "ensemble will read the actual file diffs, so the summary is "
        "just for humans skimming the run log."
    )
    return "\n".join(parts)


def _cmd_audit(args):
    doc = SprintDoc.from_markdown(
        Path(args.sprint_doc).read_text(), path=args.sprint_doc
    )
    # Load existing verdicts if available. Accept pool-override flags even
    # though audit doesn't currently re-run validators — the args shape stays
    # consistent so _ensure_config accepts them.
    auditor = Auditor()
    from .models import ExecutionReport
    report = ExecutionReport(sprint_doc=doc)
    print(auditor.scorecard(report))

    if not args.no_journal:
        journal_path = auditor.write_journal(report)
        print(f"\nJournal written to: {journal_path}")


# ─── ask / review — sprint-free fan-out commands ──────────────────────
#
# Both commands share one primitive: take a raw prompt, dispatch `.generate()`
# to every available validator in parallel, collect raw text responses. No
# VERDICT block parsing, no phase retries, no majority vote. The user gets
# N perspectives; synthesis is their job (or a future `--synth` flag).

def _fanout_generate(pool, prompt, timeout):
    """Parallel .generate() across every available validator in pool.

    Returns a list of (validator_name, answer_text, error_message) tuples,
    ordered by thread completion (fastest first). Never raises — errors
    are surfaced per-validator so one slow/broken CLI doesn't blank the run.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    validators = (
        getattr(pool, "_validators", None)
        or getattr(pool, "_ordered", None)
        or []
    )
    available = [v for v in validators if v.available()]
    if not available:
        return []
    out = []
    with ThreadPoolExecutor(max_workers=min(len(available), 5)) as ex:
        futures = {ex.submit(v.generate, prompt, timeout): v for v in available}
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                text = fut.result()
                out.append((v.name, text or "", None))
            except Exception as e:
                out.append((v.name, "", str(e)))
    return out


def _render_fanout(label, results, machine_json=False):
    """Format fan-out results for stdout. Human-readable by default."""
    from .output import fanout_payload, print_json
    from .redaction import redact_text

    if machine_json:
        print_json(fanout_payload(label, results))
        return
    for name, answer, error in results:
        print(f"\n━━━ {name} ━━━")
        if error:
            print(f"[ERROR] {redact_text(error)}")
        elif answer.strip():
            print(redact_text(answer).rstrip())
        else:
            print("[empty response]")
    print()


def _cmd_ask(args):
    """Fan out one question to every validator, print N answers."""
    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    prompt = args.question
    if args.context:
        prompt = f"{args.context}\n\n{prompt}"

    preview = prompt[:100].replace("\n", " ")
    if not args.json:
        print(f"\nLope ask: {preview}{'...' if len(prompt) > 100 else ''}")
        print(f"Validators: {', '.join(validator_names)}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)
    _render_fanout("answer", results, machine_json=args.json)


def _cmd_review(args):
    """Read a file, fan out a review prompt, print N critiques.

    Default behavior is the v0.6 raw fan-out: one section per validator. With
    ``--consensus`` (or any non-text ``--format``) the request flows through
    :mod:`lope.review` instead, which dedupes and consensus-ranks findings.
    """
    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"Cannot read {file_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Resolve the structured-mode decision once, then route. Specifying
    # any non-text ``--format`` implies structured because text is the
    # only renderer that has a meaningful raw-mode equivalent.
    output_format = getattr(args, "output_format", "text") or "text"
    structured_mode = bool(getattr(args, "consensus", False)) or output_format != "text"

    if structured_mode:
        _cmd_review_consensus(args, file_path, content, output_format)
        return

    focus = args.focus.strip() or (
        "Review this file. Identify bugs, code-smells, design issues, "
        "and concrete improvements. Be specific with line references."
    )
    prompt = (
        f"{focus}\n\n"
        f"File: {file_path}\n"
        f"```\n{content}\n```\n\n"
        "Return your review as plain prose. No VERDICT block needed."
    )

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    if not args.json:
        print(f"\nLope review: {file_path}  ({len(content)} chars)")
        print(f"Validators: {', '.join(validator_names)}")
        print(f"Focus: {focus[:80]}{'...' if len(focus) > 80 else ''}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)
    _render_fanout("review", results, machine_json=args.json)


def _cmd_review_consensus(args, file_path, content, output_format):
    """Run a consensus review and print the chosen format.

    Tests should not reach this helper directly; they exercise
    :func:`lope.review.run_consensus_review` and :func:`render_report`
    against monkeypatched fan-out functions instead.
    """
    from .review import render_report, run_consensus_review

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    # ``--json`` is a long-standing ergonomic flag. In structured mode treat
    # it as ``--format json`` unless the user explicitly chose another format.
    fmt = output_format
    if fmt == "text" and getattr(args, "json", False):
        fmt = "json"

    is_machine = fmt in {"json", "sarif"}

    if not is_machine:
        focus_preview = (args.focus or "").strip()
        if focus_preview:
            focus_label = focus_preview[:80] + ("..." if len(focus_preview) > 80 else "")
        else:
            focus_label = "(default)"
        print(f"\nLope consensus review: {file_path}  ({len(content)} chars)")
        print(f"Validators: {', '.join(validator_names) or '—'}")
        print(f"Focus: {focus_label}")
        print(f"Format: {fmt}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    report = run_consensus_review(
        target=str(file_path),
        content=content,
        focus=args.focus,
        validators=validator_names,
        pool=pool,
        timeout=cfg.timeout,
        similarity=getattr(args, "similarity", 0.85),
        min_consensus=getattr(args, "min_consensus", 0.0),
    )

    if not report.raw_results and not report.errors:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)

    rendered = render_report(
        report,
        output_format=fmt,
        include_raw=getattr(args, "include_raw", False),
    )
    print(rendered, end="" if rendered.endswith("\n") else "\n")


# ─── vote ─────────────────────────────────────────────────────────────
#
# Every validator sees the IDENTICAL option list embedded in the prompt.
# Each is asked to reply with EXACTLY ONE option label — no prose, no
# re-labelling. We parse the first matching label from stdout and tally.
# Addresses "option drift" flagged by pi during design review.


def _parse_vote(raw_answer, options):
    """Match the first option label that appears in `raw_answer`.

    Case-insensitive. Labels are matched as whole tokens (bounded by
    non-word chars) so 'A' doesn't match inside 'ALGORITHM'. Returns
    the canonical label from `options` (preserves original case), or
    None if no match is found.
    """
    import re as _re
    text = raw_answer.strip()
    # Try each option, longest first — so 'yes' doesn't eat 'yesterday'
    # when the user's options include 'yesterday'.
    for opt in sorted(options, key=len, reverse=True):
        pattern = r"(?<![A-Za-z0-9_])" + _re.escape(opt) + r"(?![A-Za-z0-9_])"
        if _re.search(pattern, text, _re.IGNORECASE):
            return opt
    return None


def _cmd_vote(args):
    """Each validator picks one of --options; tally and print winner."""
    options = [o.strip() for o in args.options.split(",") if o.strip()]
    if len(options) < 2:
        print("--options needs at least 2 comma-separated labels", file=sys.stderr)
        sys.exit(2)
    if len(set(o.lower() for o in options)) != len(options):
        print("--options labels must be unique (case-insensitive)", file=sys.stderr)
        sys.exit(2)

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    # Strict voter prompt — option list is a single block, reply shape pinned.
    prompt = (
        f"{args.prompt}\n\n"
        f"Options (pick EXACTLY one):\n"
        + "\n".join(f"  - {o}" for o in options)
        + "\n\nReply with ONLY the option label. No explanation. No prose. "
        "Just one of: " + ", ".join(options)
    )

    if not args.json:
        print(f"\nLope vote: {args.prompt[:80]}{'...' if len(args.prompt) > 80 else ''}")
        print(f"Options: {', '.join(options)}")
        print(f"Validators: {', '.join(validator_names)}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)

    # Tally
    picks = []  # list of (validator_name, chosen_option_or_None, raw_answer, error)
    tally = {opt: 0 for opt in options}
    unparseable = 0
    errored = 0
    for name, answer, error in results:
        if error:
            errored += 1
            picks.append((name, None, answer, error))
            continue
        chosen = _parse_vote(answer, options)
        if chosen is None:
            unparseable += 1
        else:
            tally[chosen] += 1
        picks.append((name, chosen, answer, None))

    if args.json:
        import json as _j
        print(_j.dumps({
            "prompt": args.prompt,
            "options": options,
            "tally": tally,
            "winner": _vote_winner(tally),
            "picks": [
                {"validator": n, "chose": c, "raw": r, "error": e}
                for n, c, r, e in picks
            ],
            "unparseable": unparseable,
            "errored": errored,
        }, indent=2))
        return

    # Human: per-voter first, then tally summary, then winner.
    for name, chosen, raw, error in picks:
        print(f"━━━ {name} ━━━")
        if error:
            print(f"[ERROR] {error}")
        elif chosen:
            print(f"  chose: {chosen}")
        else:
            preview = raw.strip().replace("\n", " ")[:120]
            print(f"  [UNPARSEABLE — no option label found] {preview}")
    print()
    print("Tally:")
    for opt in options:
        bar = "█" * tally[opt]
        print(f"  {opt:<20} {tally[opt]:>2}  {bar}")
    if errored or unparseable:
        print(f"  (errored: {errored}, unparseable: {unparseable})")
    winner = _vote_winner(tally)
    print()
    if winner:
        print(f"Winner: {winner}")
    else:
        print("No winner — tie or no votes. See tally above.")


def _vote_winner(tally):
    """Return the option with the strictly-highest count, or None on a tie."""
    if not tally:
        return None
    max_count = max(tally.values())
    if max_count == 0:
        return None
    winners = [opt for opt, c in tally.items() if c == max_count]
    return winners[0] if len(winners) == 1 else None


# ─── compare ──────────────────────────────────────────────────────────
#
# Two files + explicit --criteria → each validator picks A or B (a vote
# with options {A, B}). Addresses "criteria opacity" by making criteria
# mandatory in the prompt, never model-invented.


def _cmd_compare(args):
    """Each validator picks the better of two files given explicit criteria."""
    file_a = Path(args.file_a)
    file_b = Path(args.file_b)
    for label, f in [("A", file_a), ("B", file_b)]:
        if not f.is_file():
            print(f"File {label} not found: {f}", file=sys.stderr)
            sys.exit(1)
    try:
        content_a = file_a.read_text(encoding="utf-8", errors="replace")
        content_b = file_b.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"Cannot read file: {e}", file=sys.stderr)
        sys.exit(1)

    criteria = args.criteria.strip() or "correctness and clarity"
    prompt = (
        f"Compare two files and pick the better one against explicit criteria: {criteria}.\n\n"
        f"━━━ File A ({file_a}) ━━━\n```\n{content_a}\n```\n\n"
        f"━━━ File B ({file_b}) ━━━\n```\n{content_b}\n```\n\n"
        "Reply with ONLY the letter A or B. No explanation. No prose. "
        "Just: A or B."
    )

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    if not args.json:
        print(f"\nLope compare:")
        print(f"  A: {file_a}  ({len(content_a)} chars)")
        print(f"  B: {file_b}  ({len(content_b)} chars)")
        print(f"  Criteria: {criteria}")
        print(f"  Validators: {', '.join(validator_names)}")
        print(f"  Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)

    tally = {"A": 0, "B": 0}
    picks = []
    errored = unparseable = 0
    for name, answer, error in results:
        if error:
            errored += 1
            picks.append((name, None, answer, error))
            continue
        chosen = _parse_vote(answer, ["A", "B"])
        if chosen is None:
            unparseable += 1
        else:
            tally[chosen] += 1
        picks.append((name, chosen, answer, None))

    if args.json:
        import json as _j
        print(_j.dumps({
            "file_a": str(file_a),
            "file_b": str(file_b),
            "criteria": criteria,
            "tally": tally,
            "winner": _vote_winner(tally),
            "picks": [
                {"validator": n, "chose": c, "raw": r, "error": e}
                for n, c, r, e in picks
            ],
        }, indent=2))
        return

    for name, chosen, raw, error in picks:
        print(f"━━━ {name} ━━━")
        if error:
            print(f"[ERROR] {error}")
        elif chosen:
            print(f"  chose: {chosen}")
        else:
            preview = raw.strip().replace("\n", " ")[:120]
            print(f"  [UNPARSEABLE] {preview}")
    print()
    print(f"Tally:  A={tally['A']}  B={tally['B']}")
    if errored or unparseable:
        print(f"  (errored: {errored}, unparseable: {unparseable})")
    winner = _vote_winner(tally)
    print()
    if winner == "A":
        print(f"Winner: A  ({file_a})")
    elif winner == "B":
        print(f"Winner: B  ({file_b})")
    else:
        print("No winner — tie.")


# ─── pipe ─────────────────────────────────────────────────────────────
#
# Read stdin → fan out → per-validator stdout sections. Default is
# fire-and-forget (errors surface per-section, exit 0). --require-all
# makes any error an exit 1. Addresses "partial failure semantics"
# from pi's design review.


def _cmd_pipe(args):
    """Read stdin as the prompt; fan out; print per-validator answers."""
    if sys.stdin.isatty():
        print("lope pipe: no stdin detected (run with a pipe, e.g. `echo 'Q' | lope pipe`)",
              file=sys.stderr)
        sys.exit(2)
    prompt = sys.stdin.read()
    if not prompt.strip():
        print("lope pipe: stdin was empty", file=sys.stderr)
        sys.exit(2)

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    if not args.json:
        preview = prompt.strip()[:80].replace("\n", " ")
        print(f"\nLope pipe: {preview}{'...' if len(prompt) > 80 else ''}")
        print(f"Validators: {', '.join(validator_names)}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)
    _render_fanout("answer", results, machine_json=args.json)

    # Partial-failure semantics
    any_error = any(e for _, _, e in results)
    if any_error and args.require_all:
        sys.exit(1)


# ─── team: grandma-friendly validator management ───────────────


# Hardcoded validator names that cannot be shadowed by custom providers.
_HARDCODED_VALIDATOR_NAMES = frozenset({"claude", "opencode", "gemini", "codex", "aider"})


def _cmd_team(args):
    """Dispatch for `lope team {list,add,remove,test}`."""
    from .config import LopeCfg

    sub_cmd = getattr(args, "team_cmd", None) or "list"
    cfg_path = default_path()
    cfg = load_config(cfg_path)
    if cfg is None:
        cfg = LopeCfg(validators=[], primary="", timeout=480, parallel=True, providers=[])

    if sub_cmd == "list":
        _team_list(cfg)
    elif sub_cmd == "add":
        _team_add(args, cfg, cfg_path)
    elif sub_cmd == "remove":
        _team_remove(args, cfg, cfg_path)
    elif sub_cmd == "test":
        _team_test(args, cfg)
    else:
        _team_list(cfg)


def _team_list(cfg):
    """Render the current team: active validators (with source tag) + disabled providers."""
    from .logo import box

    print()
    print(box())
    print("\nLope — Your Validator Team")
    print("-" * 40)

    if not cfg.validators:
        print("\n  (no active validators yet — add one with `lope team add <name> ...`)")
    else:
        print(f"\nActive ({len(cfg.validators)}):")
        for name in cfg.validators:
            marker = " ★ primary" if name == cfg.primary else ""
            source = _team_classify_source(name, cfg)
            print(f"  {name:<22} {source}{marker}")

    custom_disabled = [p for p in cfg.providers if p.get("name") not in cfg.validators]
    if custom_disabled:
        print(f"\nDisabled providers ({len(custom_disabled)}):")
        for entry in custom_disabled:
            print(f"  {entry.get('name', '?'):<22} ({entry.get('type', '?')})")

    print()
    print("Add a teammate:")
    print("  lope team add <name> --cmd \"binary --flag {prompt}\"")
    print("  lope team add <name> --url URL --model MODEL --key-env OPENAI_API_KEY")
    print()


def _team_classify_source(name: str, cfg) -> str:
    """Return a human-readable source tag for a validator name."""
    if name in _HARDCODED_VALIDATOR_NAMES:
        return "(built-in)"
    custom = next((p for p in cfg.providers if p.get("name") == name), None)
    if custom is not None:
        return f"(custom {custom.get('type', '?')})"
    try:
        from .cli_discovery import KNOWN_CLIS
        if any(c.name == name and getattr(c, "generic_command", None) for c in KNOWN_CLIS):
            return "(auto)"
    except Exception:
        pass
    return "(?)"


def _team_add(args, cfg, cfg_path):
    """Build a provider dict from CLI flags, validate, upsert, save."""
    from .config import save
    from .generic_validators import _validate_provider_config, ConfigError

    name = args.name.strip()
    if not name:
        print("ERROR: name cannot be empty", file=sys.stderr)
        sys.exit(2)
    if name in _HARDCODED_VALIDATOR_NAMES:
        print(
            f"ERROR: {name!r} is a built-in validator — pick a different name "
            f"(you already have {name} if its CLI is on PATH).",
            file=sys.stderr,
        )
        sys.exit(2)
    if any(ch in name for ch in " \t\n,;|"):
        print(f"ERROR: name must not contain whitespace or separators", file=sys.stderr)
        sys.exit(2)

    existing = next((p for p in cfg.providers if p.get("name") == name), None)
    if existing and not args.force:
        print(
            f"ERROR: provider {name!r} already exists — use --force to overwrite "
            f"or `lope team remove {name}` first.",
            file=sys.stderr,
        )
        sys.exit(2)

    mode_flags = [bool(args.from_curl), bool(args.url), bool(args.cmd)]
    if sum(mode_flags) > 1:
        print(
            "ERROR: --from-curl, --url, and --cmd are mutually exclusive — pick one.",
            file=sys.stderr,
        )
        sys.exit(2)
    if args.from_curl and args.body_json:
        print(
            "ERROR: --from-curl already sets the body from the pasted curl; "
            "--body-json would override it. Drop one.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.from_curl:
        entry = _team_build_entry_from_curl(name, args)
    elif args.url:
        entry = _team_build_http_entry(name, args)
    elif args.cmd:
        entry = _team_build_subprocess_entry(name, args)
    else:
        print(
            "ERROR: provide one of --cmd (subprocess), --url (HTTP), "
            "or --from-curl (paste a curl command).\n"
            "Examples:\n"
            "  lope team add my-ollama --cmd \"ollama run qwen3:8b {prompt}\"\n"
            "  lope team add openclaw --url http://10.42.42.1:18080/v1/chat/completions"
            " --model openclaw --key-env OPENAI_API_KEY\n"
            "  lope team add openai --from-curl \"curl https://api.openai.com/v1/chat/completions"
            " -H 'Authorization: Bearer \\${OPENAI_API_KEY}'"
            " -H 'Content-Type: application/json'"
            " -d '{\\\"model\\\":\\\"gpt-4o-mini\\\",\\\"messages\\\":"
            "[{\\\"role\\\":\\\"user\\\",\\\"content\\\":\\\"hi\\\"}]}'\"",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        _validate_provider_config(entry)
    except ConfigError as e:
        print(f"ERROR: invalid config: {e}", file=sys.stderr)
        sys.exit(2)

    cfg.providers = [p for p in cfg.providers if p.get("name") != name]
    cfg.providers.append(entry)

    if not args.disabled:
        if name not in cfg.validators:
            cfg.validators.append(name)
        # If this is the first validator ever, promote to primary automatically.
        if not cfg.primary and cfg.validators:
            cfg.primary = name

    if args.primary:
        if args.disabled:
            print(
                "ERROR: cannot set --primary and --disabled together "
                "(disabled validators can't be primary).",
                file=sys.stderr,
            )
            sys.exit(2)
        cfg.primary = name

    save(cfg, cfg_path)

    status = (
        "added to team" if not args.disabled
        else "saved (disabled — enable with `lope team add` again without --disabled)"
    )
    kind = entry["type"]
    print(f"[OK] {name} {status} ({kind}).")
    if cfg.validators:
        print(f"     Active validators ({len(cfg.validators)}): {', '.join(cfg.validators)}")
    if cfg.primary:
        print(f"     Primary: {cfg.primary}")
    print(f"     Smoke-test now: lope team test {name}")


def _team_build_subprocess_entry(name: str, args):
    """Assemble a subprocess provider dict from --cmd / --stdin / --wrap / --timeout."""
    import shlex

    try:
        tokens = shlex.split(args.cmd)
    except ValueError as e:
        print(f"ERROR: could not parse --cmd (unclosed quote?): {e}", file=sys.stderr)
        sys.exit(2)
    if not tokens:
        print("ERROR: --cmd must have at least one token (the binary name)", file=sys.stderr)
        sys.exit(2)
    if not args.stdin and "{prompt}" not in " ".join(tokens):
        # Auto-append {prompt} so casual invocations like
        # `--cmd "mybin --json"` still feed the prompt as the final arg.
        tokens.append("{prompt}")

    entry: Dict[str, Any] = {
        "name": name,
        "type": "subprocess",
        "command": tokens,
    }
    if args.stdin:
        entry["stdin"] = True
    if args.wrap:
        entry["prompt_wrapper"] = args.wrap
    if args.timeout:
        entry["timeout"] = args.timeout
    return entry


def _team_build_entry_from_curl(name: str, args):
    """Parse args.from_curl and convert it to a provider dict.

    Exits 2 with a helpful message on any CurlParseError so the user sees the
    fix they need (swap literal key → ${VAR}, put {prompt} in body, etc.).
    """
    from .curl_parser import (
        CurlParseError,
        curl_to_provider_entry,
        parse_curl,
    )

    try:
        parsed = parse_curl(args.from_curl)
        return curl_to_provider_entry(
            name,
            parsed,
            key_env=args.key_env,
            response_path=args.response_path,
            wrap=args.wrap,
            timeout=args.timeout,
        )
    except CurlParseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)


def _team_build_http_entry(name: str, args):
    """Assemble an HTTP provider dict. OpenAI-compatible shape unless --body-json override."""
    if not args.url.startswith(("http://", "https://")):
        print("ERROR: --url must start with http:// or https://", file=sys.stderr)
        sys.exit(2)

    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if args.key_env:
        env_var = args.key_env.strip()
        if not env_var:
            print("ERROR: --key-env cannot be empty", file=sys.stderr)
            sys.exit(2)
        if not env_var.replace("_", "").isalnum():
            print(f"ERROR: --key-env {env_var!r} must be a valid env var name (A-Z, 0-9, _)",
                  file=sys.stderr)
            sys.exit(2)
        headers[args.key_header] = f"{args.key_prefix}${{{env_var}}}"

    if args.body_json:
        try:
            body = json.loads(args.body_json)
        except json.JSONDecodeError as e:
            print(f"ERROR: --body-json is not valid JSON: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        if not args.model:
            print(
                "ERROR: HTTP provider needs --model (for OpenAI-compatible body), "
                "or use --body-json to supply a custom shape.",
                file=sys.stderr,
            )
            sys.exit(2)
        body = {
            "model": args.model,
            "messages": [{"role": "user", "content": "{prompt}"}],
        }

    entry: Dict[str, Any] = {
        "name": name,
        "type": "http",
        "url": args.url,
        "headers": headers,
        "body": body,
        "response_path": args.response_path or "choices.0.message.content",
    }
    if args.wrap:
        entry["prompt_wrapper"] = args.wrap
    if args.timeout:
        entry["timeout"] = args.timeout
    return entry


def _team_remove(args, cfg, cfg_path):
    """Drop a teammate from providers + validators. Unset primary if it pointed there."""
    from .config import save

    name = args.name.strip()
    if not name:
        print("ERROR: name required", file=sys.stderr)
        sys.exit(2)

    providers_before = len(cfg.providers)
    cfg.providers = [p for p in cfg.providers if p.get("name") != name]
    removed_provider = len(cfg.providers) < providers_before

    was_validator = name in cfg.validators
    if was_validator:
        cfg.validators = [v for v in cfg.validators if v != name]

    was_primary = cfg.primary == name
    if was_primary:
        cfg.primary = cfg.validators[0] if cfg.validators else ""

    if not removed_provider and not was_validator:
        print(
            f"ERROR: no teammate named {name!r} found in providers or validators.\n"
            f"       Run `lope team list` to see who's on the team.",
            file=sys.stderr,
        )
        sys.exit(1)

    save(cfg, cfg_path)

    bits = []
    if removed_provider:
        bits.append("custom provider config")
    if was_validator:
        bits.append("active validators")
    if was_primary:
        bits.append("primary role")
    print(f"[OK] removed {name} from {', '.join(bits)}.")
    if cfg.validators:
        print(f"     Active validators ({len(cfg.validators)}): {', '.join(cfg.validators)}")
        if cfg.primary:
            print(f"     Primary: {cfg.primary}")
    else:
        print(f"     No validators remaining — add one with `lope team add`.")


def _team_test(args, cfg):
    """Send one prompt to a named validator via generate(); print the raw response."""
    from .generic_validators import build_provider, ConfigError

    name = args.name.strip()
    if not name:
        print("ERROR: name required", file=sys.stderr)
        sys.exit(2)

    # Lookup order mirrors build_validator_pool: custom providers first, then
    # hardcoded + auto via the pool builder (which knows how to instantiate them).
    entry = next((p for p in cfg.providers if p.get("name") == name), None)
    if entry is not None:
        try:
            validator = build_provider(entry)
        except ConfigError as e:
            print(f"ERROR: cannot build {name!r}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        try:
            pool = build_validator_pool(cfg)
        except Exception as e:
            print(f"ERROR: could not build validator pool: {e}", file=sys.stderr)
            sys.exit(1)
        validator = next(
            (v for v in getattr(pool, "validators", []) if getattr(v, "name", None) == name),
            None,
        )
        if validator is None:
            print(
                f"ERROR: {name!r} is not on the team.\n"
                f"       Active: {', '.join(cfg.validators) or '(none)'}.\n"
                f"       Run `lope team list` to see the full roster.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"[test] {name} ← {args.prompt!r}")
    try:
        out = validator.generate(args.prompt, timeout=args.timeout)
    except AttributeError:
        print(
            f"ERROR: {name!r} does not support generate() (validate-only validator). "
            f"Use `lope ask` to fan out across validators instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {name!r} failed: {e}", file=sys.stderr)
        sys.exit(1)

    text = (out or "").strip()
    if not text:
        print(f"[warn] {name} returned empty output", file=sys.stderr)
        sys.exit(1)
    print("-" * 60)
    print(text)
    print("-" * 60)
    print(f"[OK] {name} responded ({len(text)} chars).")


if __name__ == "__main__":
    main()
