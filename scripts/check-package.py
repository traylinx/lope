#!/usr/bin/env python3
"""Qualify a wheel outside the checkout, offline, with isolated operator/host homes.

No native AI providers are invoked. This checks artifact installation, host file
layouts, preservation, maintenance, and an optional previous-wheel migration.
"""
import argparse
import email
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def run(argv, cwd, env):
    result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv}\n{result.stdout}")
    return result.stdout


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def inspect(wheel, sdist):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata = email.message_from_bytes(archive.read(next(
            n for n in names if n.endswith(".dist-info/METADATA"))))
        check(metadata["Name"] == "lope-agent", "wrong project metadata")
        assets = {n.removeprefix("lope/_assets/"): archive.read(n)
                  for n in names if n.startswith("lope/_assets/") and not n.endswith("/")}
        for required in ("install", "docs/reference.md", "docs/samples.md", "README.md",
                         "CHANGELOG.md", "skills/lope/SKILL.md"):
            check(required in assets, f"missing wheel resource {required}")
        skills = [n for n in assets if n.startswith("skills/") and n.endswith("/SKILL.md")]
        check(len(skills) >= 17, f"incomplete skill bundle: {len(skills)}")
        for name in names:
            check(not any(p in name.split("/") for p in
                          ("lope-runs", ".env", "config.json", "memory.db", "latency.json")),
                  f"private runtime file in wheel: {name}")
    if sdist:
        with tarfile.open(sdist) as archive:
            members = {n.split("/", 1)[1]: n for n in archive.getnames() if "/" in n}
            for name, content in assets.items():
                check(name in members, f"sdist missing {name}")
                check(archive.extractfile(members[name]).read() == content,
                      f"sdist/wheel resource drift: {name}")
    return metadata["Version"], assets


def check_layouts(home, assets):
    count = 0
    for name, content in assets.items():
        rel = Path(name)
        targets = []
        if name.startswith("skills/") and rel.name == "SKILL.md":
            skill = rel.parts[1]
            targets = [home / p / skill / "SKILL.md" for p in
                       (".claude/skills", ".codex/skills", ".vibe/skills", ".qwen/skills",
                        ".agents/skills")]
            targets.append(home / ".cursor/agents" / (skill + ".md"))
        elif name.startswith("commands/lope/") and rel.suffix == ".toml":
            targets = [home / ".gemini/commands/lope" / rel.name]
        elif name.startswith("commands/opencode/") and rel.suffix == ".md":
            targets = [home / ".config/opencode/commands" / rel.name]
        for target in targets:
            check(target.read_bytes() == content, f"host content mismatch: {target}")
            count += 1
    check(count > 100, "host layouts incomplete")
    return count


def qualify(wheel, version, assets, baseline):
    with tempfile.TemporaryDirectory(prefix="lope-package-") as temp:
        root = Path(temp)
        home = root / "operator's isolated home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LOPE_", "PYTHON", "PIP_", "UV_"))}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_STATE_HOME=str(home / ".local/state"),
                   XDG_CACHE_HOME=str(home / ".cache"), LOPE_HOME=str(home / ".lope"),
                   LOPE_INSTALL_HEADROOM="0", LOPE_LATENCY="off", PIP_NO_INDEX="1",
                   PIP_FIND_LINKS=str(wheel.parent), PIP_CONFIG_FILE=os.devnull,
                   PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1")
        venv = root / "venv"
        run([sys.executable, "-m", "venv", venv], root, env)
        python = venv / "bin/python"
        cli = venv / "bin/lope"
        if baseline:
            run([python, "-m", "pip", "install", "--no-deps", baseline], root, env)
        run([python, "-m", "pip", "install", "--no-deps", "--upgrade", wheel], root, env)
        location = run([python, "-c", "import lope; print(lope.__file__)"], root, env).strip()
        check(str(venv) in location, f"import leaked to source tree: {location}")
        check(version in run([cli, "version"], root, env), "wrong installed version")
        check(run([cli, "docs"], root, env).strip() ==
              assets["docs/reference.md"].decode().strip(), "wrong installed docs")
        run([cli, "status"], root, env)
        for name in (".vibe", ".qwen", ".pi", ".lope"):
            (home / name).mkdir(exist_ok=True)
        config = home / ".lope/config.json"
        config.write_text('{"validators": [], "preservation_sentinel": "unchanged"}\n')
        config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        # Preserve directory, regular file, dangling link, and old OpenCode path.
        existing = home / ".codex/skills/lope"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("custom operator skill\n")
        flat = home / ".cursor/agents/lope.md"
        flat.parent.mkdir(parents=True)
        flat.write_text("custom flat skill\n")
        dangling = home / ".claude/skills/lope"
        dangling.parent.mkdir(parents=True)
        dangling.symlink_to(home / "missing-target")
        stale = home / ".config/opencode/command/lope.md"
        stale.parent.mkdir(parents=True)
        stale.write_text("old custom wrapper\n")
        backup = root / "first-backup"
        first_env = dict(env, LOPE_INSTALL_BACKUP_DIR=str(backup))
        output = run([cli, "install", "--host", "all"], root, first_env)
        check("OK — lope v" + version in output, "engine import verification failed")
        check("PYTHONPATH=" not in output, "wheel suggested source-checkout alias")
        paths = check_layouts(home, assets)
        check((backup / ".codex/skills/lope/SKILL.md").read_text() ==
              "custom operator skill\n", "directory backup missing")
        check((backup / ".cursor/agents/lope.md").read_text() == "custom flat skill\n",
              "file backup missing")
        check(os.readlink(backup / ".claude/skills/lope") == str(home / "missing-target"),
              "dangling link backup missing")
        check((backup / ".config/opencode/command/lope.md").read_text() ==
              "old custom wrapper\n", "old OpenCode wrapper not preserved")
        run([cli, "install", "--host", "all"], root, env)
        check_layouts(home, assets)
        # Exercise the real pip updater; index disabled, only this wheel available.
        update = run([cli, "update", "--method", "pip", "--host", "codex"], root, env)
        check("host skills refreshed" in update, "pip update did not refresh integrations")
        check_layouts(home, assets)
        check(hashlib.sha256(config.read_bytes()).hexdigest() == config_hash,
              "operator config changed")
        run([python, "-m", "pip", "check"], root, env)
        return {"version": version, "host_layouts": 8, "host_files_checked": paths,
                "outside_checkout": True, "offline_update": True,
                "operator_config_preserved": True, "host_backups_verified": True,
                "previous_wheel_upgrade": bool(baseline), "native_ai_hosts_executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--sdist", type=Path)
    parser.add_argument("--baseline-wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    version, assets = inspect(wheel, args.sdist)
    print(json.dumps(qualify(wheel, version, assets, args.baseline_wheel), indent=2))


if __name__ == "__main__":
    main()
