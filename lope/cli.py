"""Lope CLI — autonomous sprint runner with multi-CLI validation."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    def _add_synth_flags(p):
        # v0.7 synthesis pass: roll N answers into one executive summary via
        # the primary. ``--anonymous`` strips validator identity from the
        # synthesis prompt so the synthesizer can't bias on model name.
        p.add_argument("--synth", action="store_true",
                       help="Run a synthesis pass on the fan-out (executive summary)")
        p.add_argument("--anonymous", dest="anonymous", action="store_true",
                       help="Strip validator names from synthesis input (Response A/B/C labels)")

    def _add_brain_flags(p):
        # v0.7 Makakoo bridge: optional Brain context-in / log-out. Public
        # Lope must work outside Makakoo, so these flags only activate the
        # bridge — they never silently auto-detect or auto-log.
        p.add_argument("--brain-context", dest="brain_context", default=None,
                       metavar="QUERY",
                       help="Pull Makakoo Brain context for QUERY and prepend to validator prompts")
        p.add_argument("--brain-budget", dest="brain_budget", type=int, default=1200,
                       help="Approximate token budget for brain context (default: 1200)")
        p.add_argument("--brain-log", dest="brain_log", action="store_true",
                       help="Append a bullet to today's Makakoo Brain journal after the run")

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
    _add_brain_flags(neg)

    # execute
    exe = sub.add_parser("execute", help="Run sprint phases with validator-in-the-loop")
    exe.add_argument("sprint_doc", help="Path to sprint doc markdown")
    exe.add_argument("--phase", type=int, default=None, help="Run specific phase only")
    exe.add_argument("--manual", action="store_true",
                     help="Human-in-the-loop mode: wait for Enter between phases "
                          "(legacy pre-v0.4.0 behavior). Default is autonomous "
                          "via primary validator's generate() method.")
    exe.add_argument("--gates", action="store_true",
                     help="Run objective evidence gates before/after each phase (opt-in)")
    exe.add_argument("--gate-config", dest="gate_config", default=None,
                     help="Path to .lope/rules.json gate config")
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
    _add_synth_flags(ask)
    _add_brain_flags(ask)

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
    rev.add_argument("--remember", action="store_true",
                     help="Store consensus findings in the persistent Lope memory (v0.7)")
    rev.add_argument("--divide", choices=["files", "hunks"], default=None,
                     help="Divide the target before review: walk a directory of files or split a unified-diff into hunks")
    rev.add_argument("--roles", default=None,
                     help="Comma-separated role lenses (e.g. 'security,performance,tests'); "
                          "round-robin-assigned to validators")
    _add_pool_flags(rev)
    _add_synth_flags(rev)
    _add_brain_flags(rev)

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
    _add_synth_flags(vote)

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
    _add_synth_flags(comp)

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
    _add_synth_flags(pp)
    _add_brain_flags(pp)

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

    # deliberate — Agent-Order-style council for ADR / PRD / RFC / build-vs-buy
    # / migration-plan / incident-review. Structured adversarial reasoning,
    # NOT code execution. Optional `--brain-context` pulls Makakoo Brain
    # background; output dir layout matches `lope-runs/<timestamp>-<template>/`.
    delib = sub.add_parser(
        "deliberate",
        help="Run a council deliberation (ADR/PRD/RFC/build-vs-buy/migration/incident)",
    )
    delib.add_argument(
        "template",
        choices=["adr", "prd", "rfc", "build-vs-buy", "migration-plan", "incident-review"],
        help="Deliberation template",
    )
    delib.add_argument("scenario", help="Path to scenario file (or '-' for stdin)")
    delib.add_argument("--depth", default="standard",
                       choices=["quick", "standard", "deep"],
                       help="Council depth (default: standard)")
    delib.add_argument("--out", default=None,
                       help="Output directory (default: lope-runs/<timestamp>-<template>/)")
    delib.add_argument("--no-anonymize", dest="anonymize", action="store_false",
                       help="Disable anonymized critique (validator names will leak)")
    delib.set_defaults(anonymize=True)
    delib.add_argument("--minority-report", dest="minority_report", action="store_true",
                       help="Force minority report output even when council is unanimous (default: always emitted)")
    delib.add_argument("--human-questions", default="never",
                       choices=["never", "blocking", "always"],
                       help="When to surface clarifying questions to the human (default: never)")
    delib.add_argument("--json", action="store_true",
                       help="Emit a JSON summary instead of human-readable text")
    _add_pool_flags(delib)
    _add_brain_flags(delib)

    # memory — persistent finding store. Subcommands: stats / search / file
    # / hotspots / forget. Default subcommand is `stats`.
    mem = sub.add_parser(
        "memory",
        help="Query and manage the persistent Lope finding memory",
    )
    mem_sub = mem.add_subparsers(dest="memory_cmd")
    mem_stats = mem_sub.add_parser("stats", help="Show aggregate memory statistics")
    mem_stats.add_argument("--json", action="store_true", help="Emit JSON")

    mem_search = mem_sub.add_parser(
        "search", help="LIKE-search stored findings by message"
    )
    mem_search.add_argument("query", help="Substring to match against message")
    mem_search.add_argument("--min-score", dest="min_score", type=float, default=0.0,
                            help="Minimum consensus_score (default: 0.0)")
    mem_search.add_argument("--limit", type=int, default=20,
                            help="Maximum rows to return (default: 20)")
    mem_search.add_argument("--json", action="store_true", help="Emit JSON")

    mem_file = mem_sub.add_parser(
        "file", help="Show stored findings for a specific file"
    )
    mem_file.add_argument("path", help="File path to look up")
    mem_file.add_argument("--limit", type=int, default=50,
                          help="Maximum rows to return (default: 50)")
    mem_file.add_argument("--json", action="store_true", help="Emit JSON")

    mem_hot = mem_sub.add_parser(
        "hotspots", help="Files with the most stored findings recently"
    )
    mem_hot.add_argument("--days", type=int, default=30,
                         help="Window length in days (default: 30)")
    mem_hot.add_argument("--limit", type=int, default=10,
                         help="Maximum rows to return (default: 10)")
    mem_hot.add_argument("--json", action="store_true", help="Emit JSON")

    mem_forget = mem_sub.add_parser(
        "forget", help="Remove findings by --hash or --file"
    )
    mem_forget.add_argument("--hash", default=None,
                            help="Finding hash to remove")
    mem_forget.add_argument("--file", default=None,
                            help="File path; removes every finding stored for it")

    mem_gates = mem_sub.add_parser("gates", help="Show recent objective gate sessions")
    mem_gates.add_argument("--limit", type=int, default=20, help="Maximum rows to return")
    mem_gates.add_argument("--json", action="store_true", help="Emit JSON")

    gate = sub.add_parser("gate", help="Save or compare objective evidence gate baselines")
    gate_sub = gate.add_subparsers(dest="gate_cmd")
    gate_save = gate_sub.add_parser("save", help="Run gates and save baseline")
    gate_save.add_argument("--config", default=None, help="Path to .lope/rules.json")
    gate_save.add_argument("--baseline", default=None, help="Baseline file path")
    gate_save.add_argument("--timeout", type=int, default=480, help="Default per-gate timeout")
    gate_save.add_argument("--json", action="store_true", help="Emit JSON")
    gate_save.add_argument("--remember", action="store_true", help="Persist run in Lope memory")
    gate_check = gate_sub.add_parser("check", help="Run gates and compare with baseline")
    gate_check.add_argument("--config", default=None, help="Path to .lope/rules.json")
    gate_check.add_argument("--baseline", default=None, help="Baseline file path")
    gate_check.add_argument("--timeout", type=int, default=480, help="Default per-gate timeout")
    gate_check.add_argument("--json", action="store_true", help="Emit JSON")
    gate_check.add_argument("--remember", action="store_true", help="Persist run in Lope memory")

    chk = sub.add_parser("check", help="Run objective evidence gates without a baseline")
    chk.add_argument("--config", default=None, help="Path to .lope/rules.json")
    chk.add_argument("--timeout", type=int, default=480, help="Default per-gate timeout")
    chk.add_argument("--json", action="store_true", help="Emit JSON")
    chk.add_argument("--remember", action="store_true", help="Persist run in Lope memory")

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

    if args.command == "memory":
        _cmd_memory(args)
        return

    if args.command == "gate":
        _cmd_gate(args)
        return

    if args.command == "check":
        _cmd_check(args)
        return

    if args.command == "deliberate":
        _cmd_deliberate(args)
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




def _cmd_gate(args):
    from .gates import (
        GateConfigError, build_run, compare_results, default_baseline_path,
        load_baseline, load_gate_specs, run_gates, save_baseline,
    )
    import time as _time
    sub_cmd = getattr(args, 'gate_cmd', None) or 'check'
    started = _time.perf_counter_ns()
    try:
        specs, config_path = load_gate_specs(args.config)
        baseline = Path(args.baseline).expanduser() if args.baseline else default_baseline_path()
        results = run_gates(specs, default_timeout=args.timeout)
        comparisons = []
        if sub_cmd == 'save':
            save_baseline(results, baseline)
        elif specs:
            before = load_baseline(baseline)
            comparisons = compare_results(specs, before, results)
        else:
            comparisons = []
        run = build_run(sub_cmd, specs, config_path, baseline, results, comparisons, started)
    except GateConfigError as exc:
        print(f"lope gate: {exc}", file=sys.stderr)
        sys.exit(2)
    if getattr(args, 'remember', False):
        _remember_gate_run(run, task=f"gate {sub_cmd}")
    _print_gate_run(run, json_mode=getattr(args, 'json', False))
    sys.exit(0 if run.passed else 1)


def _cmd_check(args):
    from .gates import GateConfigError, build_run, default_baseline_path, load_gate_specs, run_gates
    import time as _time
    started = _time.perf_counter_ns()
    try:
        specs, config_path = load_gate_specs(args.config)
        baseline = default_baseline_path()
        results = run_gates(specs, default_timeout=args.timeout)
        run = build_run('check', specs, config_path, baseline, results, [], started)
    except GateConfigError as exc:
        print(f"lope check: {exc}", file=sys.stderr)
        sys.exit(2)
    if getattr(args, 'remember', False):
        _remember_gate_run(run, task='check')
    _print_gate_run(run, json_mode=getattr(args, 'json', False))
    sys.exit(0 if run.passed else 1)


def _print_gate_run(run, json_mode=False):
    if json_mode:
        import json as _j
        print(_j.dumps(run.to_dict(), indent=2, sort_keys=True))
        return
    print(f"\nLope {run.mode}: {'PASS' if run.passed else 'FAIL'}")
    print("-" * 40)
    if run.comparisons:
        for c in run.comparisons:
            status = 'PASS' if c.passed else 'FAIL'
            req = '' if c.required else ' (optional)'
            if c.before and c.before.value is not None and c.after.value is not None:
                print(f"  {status:<4} {c.name}{req}: {c.before.value:.4g} -> {c.after.value:.4g}  {c.reason}")
            else:
                print(f"  {status:<4} {c.name}{req}: {c.reason}")
    else:
        for r in run.results:
            status = 'PASS' if r.ok else 'FAIL'
            req = '' if r.required else ' (optional)'
            value = '' if r.value is None else f" value={r.value:.4g}"
            reason = '' if r.ok else f" — {r.error or 'failed'}"
            print(f"  {status:<4} {r.name}{req}{value}{reason}")
    print(f"Baseline: {run.baseline_path}")
    print()


def _remember_gate_run(run, task=''):
    from .memory import open_memory
    store = open_memory()
    if store is None:
        return
    failed = len(run.blocking_failures())
    store.store_gate_session(
        task=task,
        mode=run.mode,
        baseline_path=run.baseline_path,
        passed=run.passed,
        gate_count=len(run.results),
        failed_count=failed,
        payload=run.to_dict(),
    )


def _make_execute_gate_runner(args, timeout):
    from .gates import (
        GateConfigError, build_run, compare_results, default_baseline_path,
        load_baseline, load_gate_specs, run_gates, save_baseline,
    )
    import time as _time
    try:
        specs, config_path = load_gate_specs(getattr(args, 'gate_config', None))
    except GateConfigError as exc:
        print(f"lope execute --gates: {exc}", file=sys.stderr)
        sys.exit(2)
    baseline = default_baseline_path()
    if specs:
        initial = run_gates(specs, default_timeout=timeout)
        save_baseline(initial, baseline)
        print(f"Objective gate baseline saved: {baseline}")
    else:
        print("Objective gates enabled, but no gates configured.")
    def _runner(phase=None, attempt=1):
        started = _time.perf_counter_ns()
        results = run_gates(specs, default_timeout=timeout)
        comparisons = []
        if specs:
            try:
                before = load_baseline(baseline)
                comparisons = compare_results(specs, before, results)
            except GateConfigError:
                comparisons = []
        run = build_run('execute', specs, config_path, baseline, results, comparisons, started)
        _remember_gate_run(run, task=f"execute phase {getattr(phase, 'index', '?')} attempt {attempt}")
        failures = run.blocking_failures()
        if failures:
            print(f"Objective gates: FAIL ({len(failures)} blocking)")
        else:
            print("Objective gates: PASS")
        return run
    return _runner


def _cmd_deliberate(args):
    """Dispatch ``lope deliberate <template> <scenario>``."""

    from .deliberation import (
        DeliberationRun,
        default_output_dir,
        get_template,
        run_deliberation,
    )

    spec = get_template(args.template)

    # Scenario intake: path or '-' for stdin.
    if args.scenario == "-":
        if sys.stdin.isatty():
            print("lope deliberate: '-' requires piped stdin", file=sys.stderr)
            sys.exit(2)
        scenario = sys.stdin.read()
    else:
        scenario_path = Path(args.scenario)
        if not scenario_path.is_file():
            print(f"Scenario file not found: {scenario_path}", file=sys.stderr)
            sys.exit(1)
        try:
            scenario = scenario_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"Cannot read {scenario_path}: {e}", file=sys.stderr)
            sys.exit(1)

    if not scenario.strip():
        print("lope deliberate: scenario is empty", file=sys.stderr)
        sys.exit(2)

    # Optional Brain context — fail-clear outside Makakoo, otherwise prepend.
    brain_block = _maybe_brain_context_block(args)
    if brain_block:
        scenario = f"{brain_block}\n{scenario}"

    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()
    if not validator_names:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)

    primary = pool.primary_validator()
    primary_name = primary.name

    # EnsemblePool exposes `_validators`; ValidatorPool exposes `_ordered`.
    pool_validators = (
        getattr(pool, "_validators", None)
        or getattr(pool, "_ordered", None)
        or []
    )
    name_to_validator = {v.name: v for v in pool_validators}
    if primary_name not in name_to_validator:
        name_to_validator[primary_name] = primary

    # Build a single ``generate`` callable that dispatches to the right
    # validator. Failures propagate as plain RuntimeErrors so the orchestrator
    # records them in the trace; the council protocol does not treat one
    # validator's flake as a fatal session error.
    def _generate(name: str, prompt: str, timeout: int) -> str:
        validator = name_to_validator.get(name)
        if validator is None:
            return f"[validator {name} unavailable]"
        try:
            return validator.generate(prompt, timeout=timeout)
        except NotImplementedError as exc:
            return f"[validator {name} cannot generate: {exc}]"
        except Exception as exc:  # pragma: no cover — defensive
            return f"[validator {name} errored: {type(exc).__name__}: {exc}]"

    out_dir = Path(args.out) if args.out else default_output_dir(spec)

    if not args.json:
        print(f"\nLope deliberate: {spec.title}")
        print(f"Council: {', '.join(validator_names)}")
        print(f"Primary: {primary_name}")
        print(f"Depth: {args.depth}  ·  Anonymous: {args.anonymize}")
        print(f"Output: {out_dir}\n")

    run = run_deliberation(
        template=spec,
        scenario=scenario,
        validators=validator_names,
        primary=primary_name,
        generate=_generate,
        depth=args.depth,
        timeout=cfg.timeout,
        anonymous=args.anonymize,
        output_dir=out_dir,
    )

    # Optional brain log: drop a one-liner pointing at the run directory.
    brain_ack = _maybe_emit_brain_log(
        args,
        journal_text=(
            f"[[Lope]] deliberated {spec.name} from `{args.scenario}` → "
            f"`{out_dir}`. Council: {len(run.validators)}. "
            f"Rubric: "
            f"{sum(1 for r in run.rubric if r.status == 'PASS')} PASS, "
            f"{sum(1 for r in run.rubric if r.status == 'NEEDS_FIX')} NEEDS_FIX. "
            "[[Makakoo OS]]"
        ),
    )

    if args.json:
        import json as _j
        payload = run.to_dict()
        if brain_ack:
            payload["brain"] = {"note": brain_ack}
        print(_j.dumps(payload, indent=2, sort_keys=True))
        return

    rubric_pass = sum(1 for r in run.rubric if r.status == "PASS")
    rubric_fix = sum(1 for r in run.rubric if r.status == "NEEDS_FIX")

    print(f"Synthesis: {out_dir / 'final' / 'report.md'}")
    print(f"Minority report: {out_dir / 'final' / 'minority-report.md'}")
    print(f"Decision log: {out_dir / 'final' / 'decision-log.md'}")
    print(f"Trace: {out_dir / 'trace.jsonl'}")
    print(f"Rubric: {rubric_pass} PASS · {rubric_fix} NEEDS_FIX")

    if brain_ack:
        print(brain_ack)


def _cmd_memory(args):
    """Dispatch ``lope memory {stats|search|file|hotspots|forget}``."""
    from .memory import open_memory

    sub_cmd = getattr(args, "memory_cmd", None) or "stats"
    store = open_memory()
    if store is None:
        print(
            "Lope memory is disabled (LOPE_MEMORY=off). "
            "Unset the variable or set LOPE_MEMORY= to re-enable.",
            file=sys.stderr,
        )
        sys.exit(1)

    if sub_cmd == "stats":
        _memory_stats(store, args)
    elif sub_cmd == "search":
        _memory_search(store, args)
    elif sub_cmd == "file":
        _memory_file(store, args)
    elif sub_cmd == "hotspots":
        _memory_hotspots(store, args)
    elif sub_cmd == "forget":
        _memory_forget(store, args)
    elif sub_cmd == "gates":
        _memory_gates(store, args)
    else:
        _memory_stats(store, args)


def _memory_stats(store, args):
    payload = store.stats()
    if getattr(args, "json", False):
        import json as _j
        print(_j.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"\nLope memory at {payload['db_path']}")
    print("-" * 40)
    print(f"  Findings (total)      {payload['total_findings']:>5}")
    print(f"  Findings (recurring)  {payload['recurring_findings']:>5}")
    print(f"  Findings (confirmed)  {payload['confirmed_findings']:>5}")
    print(f"  Files flagged         {payload['flagged_files']:>5}")
    print(f"  Sessions stored       {payload['total_sessions']:>5}")
    if 'total_gate_sessions' in payload:
        print(f"  Gate sessions         {payload['total_gate_sessions']:>5}")
    if payload["last_session_at"]:
        print(f"  Last session          {payload['last_session_at']}")
    print()


def _memory_search(store, args):
    rows = store.search_findings(args.query, min_score=args.min_score, limit=args.limit)
    if getattr(args, "json", False):
        import json as _j
        print(_j.dumps([r.to_dict() for r in rows], indent=2, sort_keys=True))
        return
    if not rows:
        print(f"No stored findings matching {args.query!r}.")
        return
    print(f"\nLope memory: {len(rows)} match(es) for {args.query!r}")
    print("-" * 60)
    for r in rows:
        location = r.file or ""
        if r.line is not None:
            location += f":{r.line}"
        print(
            f"  [{r.severity.upper():<8}] {r.consensus_level:<11} "
            f"score {r.consensus_score:.2f}  seen {r.seen_count}x  "
            f"{location}"
        )
        print(f"    {r.message[:120]}")
    print()


def _memory_file(store, args):
    rows = store.findings_for_file(args.path, limit=args.limit)
    if getattr(args, "json", False):
        import json as _j
        print(_j.dumps([r.to_dict() for r in rows], indent=2, sort_keys=True))
        return
    if not rows:
        print(f"No stored findings for {args.path}.")
        return
    print(f"\nLope memory for {args.path}: {len(rows)} finding(s)")
    print("-" * 60)
    for r in rows:
        line = f":{r.line}" if r.line is not None else ""
        print(
            f"  [{r.severity.upper():<8}] score {r.consensus_score:.2f}  "
            f"seen {r.seen_count}x  {r.file}{line}"
        )
        print(f"    hash: {r.hash}  level: {r.consensus_level}")
        print(f"    {r.message[:120]}")
    print()


def _memory_hotspots(store, args):
    rows = store.hotspots(days=args.days, limit=args.limit)
    if getattr(args, "json", False):
        import json as _j
        print(_j.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        print(f"No hotspots in the last {args.days} day(s).")
        return
    print(f"\nLope hotspots (last {args.days} days)")
    print("-" * 60)
    for row in rows:
        print(
            f"  {row['finding_count']:>3} findings  "
            f"{row['detection_count']:>3} detections  "
            f"avg {row['avg_score']:.2f}   {row['file']}"
        )
    print()


def _memory_gates(store, args):
    rows = store.gate_sessions(limit=args.limit)
    if getattr(args, "json", False):
        import json as _j
        print(_j.dumps(rows, indent=2, sort_keys=True))
        return
    if not rows:
        print("No stored gate sessions.")
        return
    print(f"\nLope gate sessions: {len(rows)}")
    print("-" * 60)
    for r in rows:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  #{r['id']} {status:<4} {r['mode']:<6} gates={r['gate_count']} failed={r['failed_count']} {r['created_at']}")
    print()


def _memory_forget(store, args):
    if not (args.hash or args.file):
        print("memory forget: pass --hash or --file", file=sys.stderr)
        sys.exit(2)
    removed = store.forget(hash=args.hash, file=args.file)
    target = f"hash={args.hash}" if args.hash else f"file={args.file}"
    print(f"Removed {removed} finding(s) for {target}")


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

    # Brain context: prepend to the goal context so every drafter +
    # reviewer round sees the same advisory background. We feed it via
    # ``context`` rather than ``goal`` so the sprint doc title stays clean.
    augmented_context = args.context or ""
    brain_block = _maybe_brain_context_block(args)
    if brain_block:
        if augmented_context.strip():
            augmented_context = f"{brain_block}\n{augmented_context}"
        else:
            augmented_context = brain_block

    negotiator = Negotiator(
        llm_call=llm_call,
        validator_pool=pool,
        max_rounds=args.max_rounds,
        domain=args.domain,
    )
    try:
        result = negotiator.converge(args.goal, augmented_context)
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

    # Brain log: drop a one-liner about the negotiated sprint into the
    # journal so the team has a paper trail of what Lope produced.
    if isinstance(result, SprintDoc):
        _print_brain_log_ack(
            args,
            machine_json=False,
            journal_text=(
                f"[[Lope]] negotiated sprint `{out_path}` for goal: "
                f"{args.goal[:120]}. Rounds: {len(negotiator.rounds)}. "
                "[[Makakoo OS]]"
            ),
        )


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

    gate_runner = _make_execute_gate_runner(args, cfg.timeout) if getattr(args, "gates", False) else None
    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=implementation_fn,
        max_rounds_per_phase=3,
        timeout_seconds=cfg.timeout,
        gate_runner=gate_runner,
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


def _build_report_via_divided_files(
    args, target_path, validator_names, pool, cfg, brain_context_block
):
    """Walk a file tree (or single file) and produce one merged consensus report.

    Each chunk goes through :func:`run_consensus_review` independently;
    the chunk-level reports are then collapsed into a single report by
    concatenating raw findings, dedup-merging globally, and rescoring
    against the same validator roster.
    """
    from .divide import split_files
    from .review import ReviewReport, run_consensus_review
    from .findings import merge_findings, score_consensus

    similarity = getattr(args, "similarity", 0.85)
    min_consensus = getattr(args, "min_consensus", 0.0)

    chunks, skipped = split_files(target_path)
    if skipped and not args.json:
        print(f"Skipped {len(skipped)} non-reviewable file(s):")
        for entry in skipped[:10]:
            print(f"  - {entry.path}: {entry.reason}")
        if len(skipped) > 10:
            print(f"  ... and {len(skipped) - 10} more")
        print()

    if not chunks:
        print("No reviewable files found.", file=sys.stderr)
        sys.exit(1)

    chunk_reports = []
    aggregated_findings = []
    aggregated_errors = []
    raw_results_combined = []
    parse_methods_combined: Dict[str, str] = {}

    for chunk in chunks:
        # Progress goes to stderr so machine-readable formats (json,
        # sarif, markdown-pr) keep stdout clean for downstream parsers.
        print(f"  → reviewing {chunk.label}", file=sys.stderr)
        chunk_report = run_consensus_review(
            target=chunk.label,
            content=chunk.content,
            focus=args.focus,
            validators=validator_names,
            pool=pool,
            timeout=cfg.timeout,
            similarity=similarity,
            min_consensus=0.0,  # filter at the global level once we merge
            brain_context_block=brain_context_block,
            source_label=chunk.path,
        )
        chunk_reports.append(chunk_report)
        aggregated_findings.extend(chunk_report.findings)
        aggregated_errors.extend(chunk_report.errors)
        raw_results_combined.extend(chunk_report.raw_results)
        for name, method in chunk_report.parse_methods.items():
            parse_methods_combined.setdefault(name, method)

    merged = merge_findings(aggregated_findings, similarity_threshold=similarity)
    scored = score_consensus(merged, validator_names)
    if min_consensus > 0:
        scored = [s for s in scored if s.consensus_score >= min_consensus]

    return ReviewReport(
        target=f"{target_path} ({len(chunks)} chunk(s))",
        focus=args.focus or "(default)",
        validators=list(validator_names),
        raw_results=raw_results_combined,
        parse_methods=parse_methods_combined,
        findings=aggregated_findings,
        merged=merged,
        scored=scored,
        errors=aggregated_errors,
        raw_count=len(aggregated_findings),
        merged_count=len(merged),
        fallback=len(aggregated_findings) == 0,
    )


def _build_report_via_divided_hunks(
    args, target_path, content, validator_names, pool, cfg, brain_context_block
):
    """Parse a unified diff into hunks and review each one independently."""
    from .divide import split_diff_hunks
    from .review import ReviewReport, run_consensus_review
    from .findings import merge_findings, score_consensus

    similarity = getattr(args, "similarity", 0.85)
    min_consensus = getattr(args, "min_consensus", 0.0)

    hunks = split_diff_hunks(content)
    if not hunks:
        print(
            f"--divide hunks: no diff hunks parsed from {target_path}.",
            file=sys.stderr,
        )
        sys.exit(2)

    aggregated_findings = []
    aggregated_errors = []
    raw_results_combined = []
    parse_methods_combined: Dict[str, str] = {}

    for hunk in hunks:
        # Progress goes to stderr so machine-readable formats (json,
        # sarif, markdown-pr) keep stdout clean for downstream parsers.
        print(f"  → reviewing {hunk.label}", file=sys.stderr)
        chunk_report = run_consensus_review(
            target=hunk.label,
            content=hunk.content,
            focus=args.focus,
            validators=validator_names,
            pool=pool,
            timeout=cfg.timeout,
            similarity=similarity,
            min_consensus=0.0,
            brain_context_block=brain_context_block,
            source_label=hunk.path,
        )
        # Re-anchor any findings without explicit line numbers to the
        # hunk's new-line range so SARIF / merge views point at the
        # new file rather than the in-hunk offset.
        for finding in chunk_report.findings:
            if finding.line is None:
                finding.line = hunk.new_start
        aggregated_findings.extend(chunk_report.findings)
        aggregated_errors.extend(chunk_report.errors)
        raw_results_combined.extend(chunk_report.raw_results)
        for name, method in chunk_report.parse_methods.items():
            parse_methods_combined.setdefault(name, method)

    merged = merge_findings(aggregated_findings, similarity_threshold=similarity)
    scored = score_consensus(merged, validator_names)
    if min_consensus > 0:
        scored = [s for s in scored if s.consensus_score >= min_consensus]

    return ReviewReport(
        target=f"{target_path} ({len(hunks)} hunk(s))",
        focus=args.focus or "(default)",
        validators=list(validator_names),
        raw_results=raw_results_combined,
        parse_methods=parse_methods_combined,
        findings=aggregated_findings,
        merged=merged,
        scored=scored,
        errors=aggregated_errors,
        raw_count=len(aggregated_findings),
        merged_count=len(merged),
        fallback=len(aggregated_findings) == 0,
    )


def _build_report_via_roles(
    args, target_path, content, validator_names, pool, cfg,
    brain_context_block, roles_spec,
):
    """Single target, per-validator role-tinted prompts, one merged report."""
    from .divide import assign_roles, build_role_prompt, parse_roles
    from .findings import merge_findings, score_consensus
    from .review import (
        ReviewInput,
        ReviewReport,
        build_review_prompt,
        parse_responses,
    )
    from .redaction import redact_text

    roles = parse_roles(roles_spec)
    if not roles:
        print(
            f"lope review --roles: no usable role names in {roles_spec!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    role_assignments = assign_roles(validator_names, roles)

    base_prompt = build_review_prompt(
        ReviewInput(
            target=str(target_path),
            content=content,
            focus=args.focus,
            source_label=str(target_path),
        ),
        brain_context_block=brain_context_block,
    )

    similarity = getattr(args, "similarity", 0.85)
    min_consensus = getattr(args, "min_consensus", 0.0)

    # EnsemblePool exposes `_validators`; ValidatorPool exposes `_ordered`.
    pool_validators = (
        getattr(pool, "_validators", None)
        or getattr(pool, "_ordered", None)
        or []
    )
    name_to_validator = {v.name: v for v in pool_validators}
    raw: List[Tuple[str, str, Optional[str]]] = []
    for v_name in validator_names:
        validator = name_to_validator.get(v_name)
        role = role_assignments.get(v_name)
        if validator is None or role is None:
            raw.append((v_name, "", "validator unavailable"))
            continue
        prompt = build_role_prompt(role, base_prompt)
        try:
            text = validator.generate(prompt, timeout=cfg.timeout)
            raw.append((v_name, text or "", None))
        except Exception as exc:
            raw.append((v_name, "", str(exc)))

    raw_results = [
        {
            "validator": name,
            "answer": redact_text(answer or "").rstrip(),
            "error": redact_text(error).strip() if error else None,
            "role": role_assignments[name].name if name in role_assignments else None,
        }
        for name, answer, error in raw
    ]

    findings, parse_results, errors = parse_responses(raw, source_file=str(target_path))
    # Stamp the role onto each finding's category so consensus output
    # surfaces the lens the validator was wearing when it produced the
    # finding. We only set it when the validator did not already pick
    # a category, to honor explicit signals.
    for f in findings:
        if not f.category:
            role = role_assignments.get(f.validator)
            if role is not None:
                f.category = role.name

    merged = merge_findings(findings, similarity_threshold=similarity)
    scored = score_consensus(merged, validator_names)
    if min_consensus > 0:
        scored = [s for s in scored if s.consensus_score >= min_consensus]

    parse_methods = {n: r.method for n, r in parse_results.items()}

    return ReviewReport(
        target=f"{target_path} (roles: {', '.join(r.name for r in roles)})",
        focus=args.focus or "(default)",
        validators=list(validator_names),
        raw_results=raw_results,
        parse_methods=parse_methods,
        findings=findings,
        merged=merged,
        scored=scored,
        errors=errors,
        raw_count=len(findings),
        merged_count=len(merged),
        fallback=len(findings) == 0,
    )


def _build_review_brain_journal_text(*, file_path, report, memory_summary) -> str:
    """Compose the canonical journal bullet for `lope review --brain-log`.

    Picks the highest-severity / highest-consensus finding as ``Top``,
    falls back gracefully when nothing parsed, and quotes the Lope
    memory hash when ``--remember`` was paired with ``--brain-log``.
    """
    from .makakoo_bridge import format_review_journal_line

    confirmed_count = sum(
        1 for f in report.scored if f.consensus_level.value == "confirmed"
    )
    top_finding = None
    if report.scored:
        head = report.scored[0]
        agreement = f"{head.agreement_count}/{head.total_validators} validators"
        top_finding = {
            "file": head.file,
            "line": head.line,
            "agreement": agreement,
            "score": head.consensus_score,
            "message": head.message,
        }
    memory_hash = None
    if memory_summary and memory_summary.get("recurring_hashes"):
        memory_hash = memory_summary["recurring_hashes"][0]
    elif memory_summary and report.scored:
        memory_hash = report.scored[0].hash if report.scored else None

    return format_review_journal_line(
        target_path=str(file_path),
        merged_count=report.merged_count,
        confirmed_count=confirmed_count,
        top_finding=top_finding,
        memory_hash=memory_hash,
    )


def _maybe_brain_context_block(args) -> Optional[str]:
    """Return a Makakoo Brain context block (already redacted + marker-wrapped).

    Used by structured commands (``review --consensus``) that build
    their prompts internally and need to pass the block in rather than
    receive a pre-built string. Outside Makakoo this exits 2 just like
    :func:`_maybe_apply_brain_context`.
    """

    query = getattr(args, "brain_context", None)
    if not query:
        return None

    from .makakoo_bridge import (
        BrainQueryError,
        MakakooNotDetected,
        build_context_block,
        query_brain,
    )

    budget = int(getattr(args, "brain_budget", 1200) or 1200)
    try:
        body = query_brain(query, budget_tokens=budget)
    except (MakakooNotDetected, BrainQueryError) as exc:
        print(f"--brain-context: {exc}", file=sys.stderr)
        sys.exit(2)
    return build_context_block(query, body)


def _maybe_apply_brain_context(args, prompt: str) -> str:
    """If ``--brain-context`` is set, prepend a Makakoo Brain block to ``prompt``.

    Outside Makakoo, this prints a clear error to stderr and exits 2 so
    the user knows their request was honored but the bridge isn't
    available. Inside Makakoo, the context is fetched once and rendered
    by :func:`build_context_block` so the markers can't be doubled.
    """

    query = getattr(args, "brain_context", None)
    if not query:
        return prompt

    from .makakoo_bridge import (
        BrainQueryError,
        MakakooNotDetected,
        build_context_block,
        query_brain,
    )

    budget = int(getattr(args, "brain_budget", 1200) or 1200)
    try:
        body = query_brain(query, budget_tokens=budget)
    except MakakooNotDetected as exc:
        print(f"--brain-context: {exc}", file=sys.stderr)
        sys.exit(2)
    except BrainQueryError as exc:
        print(f"--brain-context: {exc}", file=sys.stderr)
        sys.exit(2)

    block = build_context_block(query, body)
    return f"{block}\n{prompt}"


def _print_brain_log_ack(args, *, journal_text: str, machine_json: bool = False) -> None:
    """Single helper for raw-mode commands: emit + ack the brain log line.

    For machine-readable output, the ack is suppressed (the JSON
    consumer can parse no extra noise). Human modes get a one-line
    footer either confirming the path or surfacing the bridge error.
    """
    ack = _maybe_emit_brain_log(args, journal_text=journal_text)
    if ack and not machine_json:
        print(ack)


def _maybe_emit_brain_log(args, *, journal_text: str) -> Optional[str]:
    """If ``--brain-log`` is set, append ``journal_text`` to today's journal.

    Returns a one-line ack ("Brain journal: <path>") on success, the
    error message on failure, or ``None`` when the flag wasn't passed.
    Failures never exit the process — Brain logging is post-hoc and
    must not undo the work the user already paid for.
    """

    if not getattr(args, "brain_log", False):
        return None

    from .makakoo_bridge import (
        MakakooBridgeError,
        write_brain_journal,
    )

    try:
        path = write_brain_journal(journal_text)
    except MakakooBridgeError as exc:
        return f"--brain-log: {exc}"
    except Exception as exc:  # pragma: no cover — defensive
        return f"--brain-log: unexpected error: {type(exc).__name__}"
    return f"Brain journal: {path}"


def _maybe_synthesize(args, pool, results, *, task, structured_findings=None, timeout=None):
    """Run a synthesis pass when ``--synth`` is set; otherwise return None.

    Returns a :class:`lope.synthesis.SynthesisResult` so callers can decide
    whether to surface success or fall back to the raw fan-out. The pool's
    primary is used as the synthesizer; if no primary is available the
    result carries a fail-soft error message.
    """
    if not getattr(args, "synth", False):
        return None
    from .synthesis import build_synthesis_prompt, run_synthesis

    primary = None
    if pool is not None:
        try:
            primary = pool.primary_validator()
        except Exception:
            primary = None

    prompt = build_synthesis_prompt(
        task=task,
        responses=results,
        structured_findings=structured_findings,
        anonymous=getattr(args, "anonymous", False),
    )
    return run_synthesis(primary, prompt, timeout or 240)


def _render_fanout_with_synth(label, results, synth, machine_json=False):
    """Combined renderer that prints fan-out + synthesis or JSON-bundles them.

    ``synth`` is the optional :class:`SynthesisResult` from
    :func:`_maybe_synthesize`. When ``machine_json`` is set the function
    prints a single JSON envelope ``{responses: [...], synthesis: ...}``
    so machine consumers get one parseable artifact.
    """
    from .output import fanout_payload, print_json
    from .synthesis import format_synthesis

    if machine_json:
        payload = {
            "responses": fanout_payload(label, results),
        }
        if synth is not None:
            payload["synthesis"] = {
                "ok": synth.ok,
                "primary": synth.primary,
                "text": format_synthesis(synth, machine_json=True) if synth.ok else "",
                "error": synth.error if not synth.ok else "",
            }
        print_json(payload)
        return

    _render_fanout(label, results, machine_json=False)
    if synth is not None:
        print(format_synthesis(synth, machine_json=False))


def _cmd_ask(args):
    """Fan out one question to every validator, print N answers."""
    cfg, pool = _ensure_config(args)
    validator_names = [v.name for v in getattr(pool, "_validators", [])] or pool.names()

    prompt = args.question
    if args.context:
        prompt = f"{args.context}\n\n{prompt}"
    prompt = _maybe_apply_brain_context(args, prompt)

    preview = prompt[:100].replace("\n", " ")
    if not args.json:
        print(f"\nLope ask: {preview}{'...' if len(prompt) > 100 else ''}")
        print(f"Validators: {', '.join(validator_names)}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    results = _fanout_generate(pool, prompt, cfg.timeout)
    if not results:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)
    synth = _maybe_synthesize(args, pool, results, task=prompt, timeout=cfg.timeout)
    _render_fanout_with_synth("answer", results, synth, machine_json=args.json)
    _print_brain_log_ack(args, machine_json=args.json,
                         journal_text=f"[[Lope]] ask: {args.question[:120]} [[Makakoo OS]]")


def _cmd_review(args):
    """Read a file, fan out a review prompt, print N critiques.

    Default behavior is the v0.6 raw fan-out: one section per validator. With
    ``--consensus`` (or any non-text ``--format``) the request flows through
    :mod:`lope.review` instead, which dedupes and consensus-ranks findings.
    """
    file_path = Path(args.file)
    divide_mode = getattr(args, "divide", None)

    # ``--divide files`` lets the target be a directory; everything else
    # still requires a regular file.
    if divide_mode == "files":
        if not file_path.exists():
            print(f"Path not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        content = ""  # divided path reads each chunk on its own
    else:
        if not file_path.is_file():
            print(f"File not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            print(f"Cannot read {file_path}: {e}", file=sys.stderr)
            sys.exit(1)

    # Resolve the structured-mode decision once, then route. Specifying
    # any non-text ``--format``, ``--divide``, or ``--roles`` implies
    # structured because the raw renderer has no equivalent of those.
    output_format = getattr(args, "output_format", "text") or "text"
    structured_mode = (
        bool(getattr(args, "consensus", False))
        or output_format != "text"
        or bool(divide_mode)
        or bool(getattr(args, "roles", None))
    )

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
    prompt = _maybe_apply_brain_context(args, prompt)

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
    synth_task = f"Review of {file_path} — focus: {focus}"
    synth = _maybe_synthesize(args, pool, results, task=synth_task, timeout=cfg.timeout)
    _render_fanout_with_synth("review", results, synth, machine_json=args.json)
    _print_brain_log_ack(
        args,
        machine_json=args.json,
        journal_text=(
            f"[[Lope]] review of `{file_path}` — focus: {focus[:80]}. "
            "[[Makakoo OS]]"
        ),
    )


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

    # Reject incompatible mode combinations before printing any header so
    # the user sees the error first, not after a misleading announcement.
    divide_mode = getattr(args, "divide", None)
    roles_spec = getattr(args, "roles", None)
    if divide_mode and roles_spec:
        print(
            "lope review: --divide and --roles cannot be combined; "
            "pick one (combination behavior is reserved for a future phase).",
            file=sys.stderr,
        )
        sys.exit(2)

    if not is_machine:
        focus_preview = (args.focus or "").strip()
        if focus_preview:
            focus_label = focus_preview[:80] + ("..." if len(focus_preview) > 80 else "")
        else:
            focus_label = "(default)"
        mode_label = (
            f"divide={divide_mode}" if divide_mode
            else (f"roles={roles_spec}" if roles_spec else "single")
        )
        print(f"\nLope consensus review: {file_path}  ({len(content)} chars)")
        print(f"Validators: {', '.join(validator_names) or '—'}")
        print(f"Focus: {focus_label}")
        print(f"Format: {fmt}  ·  Mode: {mode_label}")
        print(f"Timeout: {cfg.timeout}s per validator\n")

    brain_context_block = _maybe_brain_context_block(args)

    if divide_mode == "files":
        report = _build_report_via_divided_files(
            args, file_path, validator_names, pool, cfg, brain_context_block
        )
    elif divide_mode == "hunks":
        report = _build_report_via_divided_hunks(
            args, file_path, content, validator_names, pool, cfg, brain_context_block
        )
    elif roles_spec:
        report = _build_report_via_roles(
            args, file_path, content, validator_names, pool, cfg, brain_context_block, roles_spec
        )
    else:
        report = run_consensus_review(
            target=str(file_path),
            content=content,
            focus=args.focus,
            validators=validator_names,
            pool=pool,
            timeout=cfg.timeout,
            similarity=getattr(args, "similarity", 0.85),
            min_consensus=getattr(args, "min_consensus", 0.0),
            brain_context_block=brain_context_block,
        )

    if not report.raw_results and not report.errors:
        print("No validators available. Run: lope status", file=sys.stderr)
        sys.exit(1)

    # --remember: persist consensus findings to the local SQLite memory.
    # Honors LOPE_MEMORY=off (visible no-op) and LOPE_MEMORY_DB override.
    memory_message = None
    memory_summary = None
    if getattr(args, "remember", False):
        from .memory import open_memory

        store = open_memory()
        if store is None:
            memory_message = (
                "Memory disabled (LOPE_MEMORY=off); --remember was a no-op."
            )
        else:
            session_id, stored = store.store_review_session(
                task=f"lope review {file_path}",
                focus=args.focus or "",
                target_path=str(file_path),
                validators=validator_names,
                findings=report.scored,
                duration_ms=0,
            )
            recurring = [r for r in stored if r.seen_count > 1]
            memory_summary = {
                "session_id": session_id,
                "stored_count": len(stored),
                "recurring_count": len(recurring),
                "recurring_hashes": [r.hash for r in recurring],
                "db_path": str(store.db_path),
            }
            memory_message = (
                f"Memory: stored {len(stored)} finding(s) "
                f"({len(recurring)} recurring) → {store.db_path}"
            )

    # Synthesis on the consensus path: feed merged findings (not raw spam)
    # to the primary, then either append the synthesis block in human modes
    # or attach it under ``synthesis`` in JSON / SARIF properties.
    synth = None
    if getattr(args, "synth", False):
        from .synthesis import build_synthesis_prompt, run_synthesis

        # In structured mode the synthesizer should see merged findings
        # rather than the full transcripts, unless the caller explicitly
        # asks for raw inclusion.
        include_raw = getattr(args, "include_raw", False)
        responses_for_synth = [
            (r["validator"], r.get("answer") or "", r.get("error"))
            for r in report.raw_results
        ] if include_raw else [
            (r["validator"], "", r.get("error"))
            for r in report.raw_results
        ]
        primary = None
        try:
            primary = pool.primary_validator()
        except Exception:
            primary = None
        synth_task = (
            f"Consensus review of {file_path} — "
            f"{report.merged_count} merged findings from "
            f"{len(report.validators)} validators."
        )
        synth_prompt = build_synthesis_prompt(
            task=synth_task,
            responses=responses_for_synth,
            structured_findings=report.scored,
            anonymous=getattr(args, "anonymous", False),
        )
        synth = run_synthesis(primary, synth_prompt, cfg.timeout)

    rendered = render_report(
        report,
        output_format=fmt,
        include_raw=getattr(args, "include_raw", False),
    )

    brain_log_text = _build_review_brain_journal_text(
        file_path=file_path,
        report=report,
        memory_summary=memory_summary,
    )
    brain_ack = _maybe_emit_brain_log(args, journal_text=brain_log_text)

    # SARIF stays spec-compliant: synthesis goes to stderr and so does the
    # ``--remember`` ack so the JSON payload stays a clean SARIF run.
    if fmt == "sarif":
        print(rendered, end="" if rendered.endswith("\n") else "\n")
        if synth is not None:
            from .synthesis import format_synthesis as _fmt_synth
            print(_fmt_synth(synth, machine_json=False), file=sys.stderr)
        if memory_message:
            print(memory_message, file=sys.stderr)
        if brain_ack:
            print(brain_ack, file=sys.stderr)
        return

    if fmt == "json":
        import json as _j
        try:
            payload = _j.loads(rendered)
        except _j.JSONDecodeError:
            payload = {"raw": rendered}
        if synth is not None:
            from .synthesis import format_synthesis as _fmt_synth
            payload["synthesis"] = {
                "ok": synth.ok,
                "primary": synth.primary,
                "text": _fmt_synth(synth, machine_json=True) if synth.ok else "",
                "error": synth.error if not synth.ok else "",
            }
        if memory_summary is not None:
            payload["memory"] = memory_summary
        elif memory_message:
            payload["memory"] = {"disabled": True, "note": memory_message}
        if brain_ack:
            payload["brain"] = {"note": brain_ack}
        print(_j.dumps(payload, indent=2, sort_keys=True))
        return

    # Human / markdown / markdown-pr — append synthesis + memory footer.
    print(rendered, end="" if rendered.endswith("\n") else "\n")
    if synth is not None:
        from .synthesis import format_synthesis as _fmt_synth
        print(_fmt_synth(synth, machine_json=False))
    if memory_message:
        print(memory_message)
    if brain_ack:
        print(brain_ack)


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

    synth = _maybe_synthesize(
        args,
        pool,
        results,
        task=(
            f"Vote on: {args.prompt}\n"
            f"Options: {', '.join(options)}\n"
            f"Tally: " + ", ".join(f"{o}={tally[o]}" for o in options)
        ),
        timeout=cfg.timeout,
    )

    if args.json:
        import json as _j
        payload = {
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
        }
        if synth is not None:
            from .synthesis import format_synthesis as _fmt_synth
            payload["synthesis"] = {
                "ok": synth.ok,
                "primary": synth.primary,
                "text": _fmt_synth(synth, machine_json=True) if synth.ok else "",
                "error": synth.error if not synth.ok else "",
            }
        print(_j.dumps(payload, indent=2))
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

    if synth is not None:
        from .synthesis import format_synthesis as _fmt_synth
        print()
        print(_fmt_synth(synth, machine_json=False))


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

    synth = _maybe_synthesize(
        args,
        pool,
        results,
        task=(
            f"Compare {file_a} (A) vs {file_b} (B) against criteria: {criteria}\n"
            f"Tally: A={tally['A']}  B={tally['B']}"
        ),
        timeout=cfg.timeout,
    )

    if args.json:
        import json as _j
        payload = {
            "file_a": str(file_a),
            "file_b": str(file_b),
            "criteria": criteria,
            "tally": tally,
            "winner": _vote_winner(tally),
            "picks": [
                {"validator": n, "chose": c, "raw": r, "error": e}
                for n, c, r, e in picks
            ],
        }
        if synth is not None:
            from .synthesis import format_synthesis as _fmt_synth
            payload["synthesis"] = {
                "ok": synth.ok,
                "primary": synth.primary,
                "text": _fmt_synth(synth, machine_json=True) if synth.ok else "",
                "error": synth.error if not synth.ok else "",
            }
        print(_j.dumps(payload, indent=2))
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

    if synth is not None:
        from .synthesis import format_synthesis as _fmt_synth
        print()
        print(_fmt_synth(synth, machine_json=False))


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
    prompt = _maybe_apply_brain_context(args, prompt)

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
    synth = _maybe_synthesize(args, pool, results, task=prompt, timeout=cfg.timeout)
    _render_fanout_with_synth("answer", results, synth, machine_json=args.json)
    _print_brain_log_ack(
        args,
        machine_json=args.json,
        journal_text=(
            "[[Lope]] pipe answer (stdin → fan-out): "
            f"{prompt.strip().splitlines()[0][:120]}. [[Makakoo OS]]"
        ),
    )

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
