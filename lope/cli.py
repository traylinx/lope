"""Lope CLI — autonomous sprint runner with multi-CLI validation."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

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
            "Any AI CLI implements, any AI CLI validates. Supports 12 built-in CLIs "
            "(claude, opencode, gemini, codex, vibe, aider, ollama, goose, interpreter, "
            "llama-cpp, gh-copilot, amazon-q) plus infinite custom providers via JSON. "
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

    # negotiate
    neg = sub.add_parser("negotiate", help="Draft a sprint doc via multi-round validation")
    neg.add_argument("goal", help="Sprint goal description")
    neg.add_argument("--out", default=None, help="Output path for sprint doc")
    neg.add_argument("--max-rounds", type=int, default=3)
    neg.add_argument("--context", default="", help="Additional context")
    neg.add_argument("--domain", default="engineering",
                     choices=["engineering", "business", "research"],
                     help="Domain: engineering (default), business, or research")

    # execute
    exe = sub.add_parser("execute", help="Run sprint phases with validator-in-the-loop")
    exe.add_argument("sprint_doc", help="Path to sprint doc markdown")
    exe.add_argument("--phase", type=int, default=None, help="Run specific phase only")

    # audit
    aud = sub.add_parser("audit", help="Generate scorecard from sprint results")
    aud.add_argument("sprint_doc", help="Path to sprint doc markdown")
    aud.add_argument("--no-journal", action="store_true", help="Skip journal write")

    # status
    sub.add_parser("status", help="Show available validators and config")

    # configure
    sub.add_parser("configure", help="Interactive validator picker")

    # install
    inst = sub.add_parser("install", help="Install lope skills into CLI hosts")
    inst.add_argument("--host", default="all", help="Target host (claude, codex, gemini, opencode, cursor, all)")

    # version
    sub.add_parser("version", help="Show version")

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
        _cmd_negotiate(args)
        return

    if args.command == "execute":
        _cmd_execute(args)
        return

    if args.command == "audit":
        _cmd_audit(args)
        return


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


def _ensure_config():
    """Load or create config. Returns (cfg, pool)."""
    cfg = load_config(default_path())
    if cfg is None:
        available = discover()
        if not available:
            print("No AI CLIs detected. Install at least one of: claude, opencode, gemini, codex, aider")
            sys.exit(1)
        if is_interactive():
            cfg = run_selector(available)
            save_config(cfg, default_path())
        else:
            defs = defaults(available)
            cfg = LopeCfg(
                validators=[c.name for c in defs],
                primary=defs[0].name if defs else "",
                timeout=480,
                parallel=True,
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
    cfg, pool = _ensure_config()

    # Drafter = the primary validator in the pool. Lope's core premise:
    # any CLI implements, any CLI validates. So drafting a proposal is
    # just the primary CLI implementing; reviewers then vote on it.
    # No separate hosted LLM endpoint required.
    primary = pool.primary_validator()
    timeout = int(os.environ.get("LOPE_TIMEOUT", "480"))
    print(f"Drafter: {primary.name}  ·  Reviewers: {', '.join(v.name for v in pool.reviewers()) or '(none — need at least 2 validators for real ensemble review)'}")
    print()

    def llm_call(system: str, user: str) -> str:
        combined = f"{system}\n\n{user}"
        try:
            return primary.generate(combined, timeout=timeout)
        except NotImplementedError as e:
            # Primary validator doesn't support drafting yet.
            # Optional fallback: LOPE_LLM_URL if user opted in.
            llm_url = os.environ.get("LOPE_LLM_URL")
            if not llm_url:
                raise RuntimeError(
                    f"{primary.name} does not support drafting, and no "
                    f"LOPE_LLM_URL fallback is set.\n"
                    f"  Fix: pick a different primary in ~/.lope/config.json "
                    f"(claude, opencode, gemini-cli, codex, aider support drafting),\n"
                    f"       or set LOPE_LLM_URL / LOPE_LLM_API_KEY to a hosted endpoint."
                ) from None
            return _http_llm_fallback(system, user, llm_url)

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
    print(f"\nLope execute: {doc.title} ({len(doc.phases)} phases)\n")
    cfg, pool = _ensure_config()

    def implementation_fn(phase, fix_context=None):
        # In standalone mode, print what needs to be done and wait
        from .executor import ImplementationResult
        print(f"\n{'='*50}")
        print(f"Phase {phase.index}: {phase.name}")
        print(f"Goal: {phase.goal}")
        if fix_context:
            print(f"Fixes to apply: {fix_context}")
        print(f"{'='*50}")
        print("\nImplement this phase, then press Enter to validate...")
        input()
        return ImplementationResult(ok=True, summary="implemented by operator")

    executor = PhaseExecutor(
        validator_pool=pool,
        implementation_fn=implementation_fn,
        max_rounds_per_phase=3,
    )
    report = executor.run(doc)

    auditor = Auditor()
    print(f"\n{auditor.scorecard(report)}")

    if report.ok:
        print("\nAll phases passed!")
        from .logo import mascot
        print()
        print(mascot("shipped. noticed."))
    else:
        print(f"\nEscalation: {report.error}")
        sys.exit(1)


def _cmd_audit(args):
    doc = SprintDoc.from_markdown(
        Path(args.sprint_doc).read_text(), path=args.sprint_doc
    )
    # Load existing verdicts if available
    auditor = Auditor()
    from .models import ExecutionReport
    report = ExecutionReport(sprint_doc=doc)
    print(auditor.scorecard(report))

    if not args.no_journal:
        journal_path = auditor.write_journal(report)
        print(f"\nJournal written to: {journal_path}")


if __name__ == "__main__":
    main()
