# Contributing to lope

Thanks for helping improve lope. This is a small, fast-moving project; the bar
is "green CI, clear diff, no behavior surprises."

## Dev setup

```bash
git clone https://github.com/traylinx/lope
cd lope
python -m pip install -e ".[dev]"   # ruff, mypy, pytest, pytest-asyncio
```

## Before you open a PR

Run the same gates CI runs (`.github/workflows/ci.yml`):

```bash
ruff check .                        # lint — must be clean (blocking)
python -m compileall -q lope tests  # syntax
python -m pytest -q                 # tests — must be green
mypy lope                           # type check — advisory, non-blocking
bash scripts/check-version.sh       # version strings in sync across all 6 files
```

## Conventions

- **Lint/format:** ruff, `line-length = 100`, select set pinned in
  `pyproject.toml`. Don't broaden the select set in an unrelated PR.
- **Types:** mypy is advisory today — fix genuinely-wrong types you touch, but
  full annotation coverage is not required.
- **Commits:** small, focused, imperative subject lines.
- **Versioning:** lope keeps its version string in 6 files in sync. Use
  `scripts/bump-version.sh <new>` and confirm `scripts/check-version.sh` passes
  before tagging.

## Security issues

Do **not** use the public issue tracker for vulnerabilities — see
[`SECURITY.md`](SECURITY.md).
