"""Tests for ``lope.divide`` — file walker, diff hunk parser, role lens.

All tests run against ``tmp_path`` so the user's real tree is never
touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lope.divide import (
    DEFAULT_MAX_CHARS,
    FileChunk,
    HunkChunk,
    ROLE_LENSES,
    RoleLens,
    SkippedFile,
    assign_roles,
    build_role_prompt,
    get_role,
    list_roles,
    parse_roles,
    split_diff_hunks,
    split_files,
)


# ---------------------------------------------------------------------------
# split_files — single file
# ---------------------------------------------------------------------------


def test_split_single_file_returns_single_chunk(tmp_path):
    f = tmp_path / "auth.py"
    f.write_text("def login():\n    return True\n")
    chunks, skipped = split_files(f)
    assert skipped == []
    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, FileChunk)
    assert chunk.start_line == 1
    assert chunk.end_line == 2
    assert chunk.chunk_total == 1
    assert chunk.label == str(f)


def test_split_single_file_handles_empty_file(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text("")
    chunks, _ = split_files(f)
    assert len(chunks) == 1
    assert chunks[0].content == ""


def test_split_single_file_chunks_when_oversized(tmp_path):
    f = tmp_path / "big.py"
    body = "\n".join(f"line {i}" for i in range(2000))  # ~14 KB
    f.write_text(body)
    chunks, _ = split_files(f, max_chars=2000)
    assert len(chunks) > 1
    # Line ranges are contiguous and cover the original.
    assert chunks[0].start_line == 1
    last = chunks[-1]
    total_lines = sum(c.end_line - c.start_line + 1 for c in chunks)
    assert total_lines == 2000
    # Every chunk except possibly the last fits under the budget.
    for c in chunks[:-1]:
        assert len(c.content) <= 2000 + 200  # mild slack — we never split mid-line


def test_chunk_label_includes_line_range_when_split(tmp_path):
    f = tmp_path / "long.py"
    f.write_text("\n".join(f"line {i}" for i in range(1000)))
    chunks, _ = split_files(f, max_chars=500)
    multi = chunks[1]
    assert "chunk" in multi.label
    assert f"lines {multi.start_line}-{multi.end_line}" in multi.label


# ---------------------------------------------------------------------------
# split_files — directory walking
# ---------------------------------------------------------------------------


def test_split_directory_returns_files_in_sorted_order(tmp_path):
    (tmp_path / "b.py").write_text("b = 2")
    (tmp_path / "a.py").write_text("a = 1")
    (tmp_path / "c.py").write_text("c = 3")
    chunks, _ = split_files(tmp_path)
    paths = [c.path for c in chunks]
    assert paths == ["a.py", "b.py", "c.py"]


def test_split_directory_skips_known_dot_dirs(tmp_path):
    (tmp_path / "a.py").write_text("a = 1")
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main")
    node_modules = tmp_path / "node_modules"
    node_modules.mkdir()
    (node_modules / "junk.js").write_text("// noise")
    chunks, _ = split_files(tmp_path)
    paths = [c.path for c in chunks]
    assert paths == ["a.py"]


def test_split_directory_skips_binary_extensions(tmp_path):
    (tmp_path / "ok.py").write_text("ok")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00")
    chunks, skipped = split_files(tmp_path)
    paths = [c.path for c in chunks]
    skipped_paths = {s.path: s.reason for s in skipped}
    assert paths == ["ok.py"]
    assert skipped_paths.get("logo.png") == "binary"


def test_split_directory_detects_binary_by_nul_byte(tmp_path):
    (tmp_path / "weird.txt").write_bytes(b"hello\x00world")
    chunks, skipped = split_files(tmp_path)
    assert chunks == []
    assert any(s.reason == "binary" for s in skipped)


def test_split_directory_skips_oversized_files(tmp_path):
    (tmp_path / "small.py").write_text("ok")
    big = tmp_path / "huge.py"
    big.write_bytes(b"x" * 1024)
    chunks, skipped = split_files(tmp_path, max_file_bytes=512)
    paths = [c.path for c in chunks]
    too_large = [s for s in skipped if s.reason == "too_large"]
    assert paths == ["small.py"]
    assert too_large and too_large[0].path == "huge.py"


def test_split_directory_does_not_follow_external_symlinks(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "a.py").write_text("a")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("nope")
    link = inside / "link"
    link.symlink_to(outside)
    chunks, _ = split_files(inside)
    paths = [c.path for c in chunks]
    assert "a.py" in paths
    assert all("secret.py" not in p for p in paths)


# ---------------------------------------------------------------------------
# split_diff_hunks
# ---------------------------------------------------------------------------


_SAMPLE_DIFF = """\
diff --git a/auth.py b/auth.py
index 1234..5678 100644
--- a/auth.py
+++ b/auth.py
@@ -10,5 +10,7 @@ def login():
     ...context...
+    new line
+    another line
     ...context...
diff --git a/api.py b/api.py
index aaaa..bbbb 100644
--- a/api.py
+++ b/api.py
@@ -1,3 +1,4 @@
 def root():
+    new
     return ok
"""


def test_split_diff_hunks_parses_two_files():
    hunks = split_diff_hunks(_SAMPLE_DIFF)
    assert len(hunks) == 2
    auth, api = hunks
    assert auth.path == "auth.py"
    assert auth.new_start == 10
    assert auth.new_lines == 7
    assert auth.new_end == 16
    assert api.path == "api.py"
    assert api.new_start == 1
    assert api.new_lines == 4


def test_split_diff_hunks_returns_empty_on_blank_input():
    assert split_diff_hunks("") == []
    assert split_diff_hunks("   \n\n") == []


def test_split_diff_hunks_handles_multiple_hunks_in_same_file():
    diff = """\
diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1,3 +1,4 @@
 ctx
+added
 ctx
@@ -50,2 +51,3 @@
 ctx
+second
"""
    hunks = split_diff_hunks(diff)
    assert len(hunks) == 2
    assert hunks[0].path == "a.py"
    assert hunks[0].hunk_index == 0
    assert hunks[1].hunk_index == 1
    assert hunks[1].new_start == 51


def test_hunk_label_carries_new_line_range():
    hunks = split_diff_hunks(_SAMPLE_DIFF)
    assert "auth.py (hunk 1: new lines 10-16)" == hunks[0].label


def test_hunk_chunk_to_dict_round_trips_basic_fields():
    hunks = split_diff_hunks(_SAMPLE_DIFF)
    payload = hunks[0].to_dict()
    assert payload["path"] == "auth.py"
    assert payload["new_start"] == 10
    assert payload["hunk_index"] == 0


# ---------------------------------------------------------------------------
# Role lens
# ---------------------------------------------------------------------------


def test_role_catalog_contains_all_v07_lenses():
    names = set(list_roles())
    assert {
        "security",
        "correctness",
        "performance",
        "tests",
        "api-design",
        "docs",
        "ops",
        "ux",
    } <= names


def test_get_role_is_case_insensitive():
    assert get_role("SECURITY").name == "security"
    assert get_role("Api-Design").name == "api-design"


def test_get_role_raises_on_unknown_name():
    with pytest.raises(KeyError):
        get_role("not-a-real-role")


def test_parse_roles_parses_comma_separated_spec():
    roles = parse_roles("security, performance ,tests")
    names = [r.name for r in roles]
    assert names == ["security", "performance", "tests"]


def test_parse_roles_dedupes_input():
    # ``perf`` is an alias for ``performance``; the dedupe step works on
    # the canonical lens name so duplicates in either spelling collapse.
    roles = parse_roles("security,security,perf,security")
    names = [r.name for r in roles]
    assert names == ["security", "performance"]
    # Accepts both spellings interchangeably.
    via_alias = parse_roles("perf, performance")
    assert [r.name for r in via_alias] == ["performance"]


def test_parse_roles_returns_empty_for_empty_input():
    assert parse_roles("") == []


def test_assign_roles_round_robins_across_validators():
    roles = parse_roles("security,performance")
    mapping = assign_roles(["claude", "gemini", "codex", "kimi"], roles)
    assert mapping["claude"].name == "security"
    assert mapping["gemini"].name == "performance"
    assert mapping["codex"].name == "security"
    assert mapping["kimi"].name == "performance"


def test_assign_roles_is_deterministic():
    roles = parse_roles("security,tests,perf")
    a = assign_roles(["x", "y", "z"], roles)
    b = assign_roles(["x", "y", "z"], roles)
    assert {k: v.name for k, v in a.items()} == {k: v.name for k, v in b.items()}


def test_assign_roles_rejects_empty_inputs():
    with pytest.raises(ValueError):
        assign_roles([], parse_roles("security"))
    with pytest.raises(ValueError):
        assign_roles(["claude"], [])


def test_build_role_prompt_prepends_lens_prefix():
    role = get_role("security")
    out = build_role_prompt(role, "Review this file")
    assert out.startswith(role.prompt_prefix.strip())
    assert "Review this file" in out
    assert "[security]" in out  # tag instruction is inside the prefix


def test_build_role_prompt_redacts_base_prompt():
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    out = build_role_prompt(get_role("security"), f"check {secret}")
    assert "abcdefghijklmnop" not in out
    assert "Bearer <redacted>" in out


def test_build_role_prompt_redacts_custom_role_prefix():
    # Regression: the docstring promises both the prefix and the base
    # prompt pass through redaction, so a user-supplied role with a
    # secret-shaped prefix must not leak.
    secret = "Bearer abcdefghijklmnopqrstuvwxyz123456"
    rogue = RoleLens(
        name="rogue",
        title="Rogue",
        description="Carries a secret in its prefix.",
        prompt_prefix=f"Trust me, here is the token: {secret}",
    )
    out = build_role_prompt(rogue, "review this")
    assert "abcdefghijklmnop" not in out
    assert "Bearer <redacted>" in out


def test_split_files_symlink_containment_uses_original_root(tmp_path):
    # Regression: symlink containment must be checked against the
    # top-level tree the user asked us to walk, not the subdirectory the
    # walker is currently inside. A symlink at depth 2 pointing to a
    # sibling directory inside the same tree should be followed.
    root = tmp_path / "tree"
    root.mkdir()
    a = root / "a"
    a.mkdir()
    (a / "alpha.py").write_text("alpha")
    b = root / "b"
    b.mkdir()
    (b / "bravo.py").write_text("bravo")
    # Symlink inside `a/` pointing to `b/` — both inside the requested tree.
    (a / "to_b").symlink_to(b)

    chunks, _ = split_files(root)
    paths = {c.path for c in chunks}
    # The original files reachable directly are present.
    assert any("alpha.py" in p for p in paths)
    assert any("bravo.py" in p for p in paths)


def test_split_files_symlink_outside_original_root_still_blocked(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    (inside / "ok.py").write_text("ok")
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "secret.py").write_text("nope")
    nested = inside / "nested"
    nested.mkdir()
    # A deep symlink that points outside the original tree must still
    # be rejected even though it sits two levels down.
    (nested / "escape").symlink_to(sibling)

    chunks, _ = split_files(inside)
    paths = {c.path for c in chunks}
    assert any("ok.py" in p for p in paths)
    assert all("secret.py" not in p for p in paths)
