from __future__ import annotations

from lope.request_plan import (
    PlanAction,
    SemanticUnit,
    pack_units,
    plan_request,
    semantic_units,
)


VALIDATORS = [
    {"name": "claude", "transport": "stdin"},
    {"name": "codex", "transport": "stdin"},
    {"name": "gemini", "transport": "argv"},
]


def test_direct_plan_profiles_utf8_bytes_and_labels_token_estimate_approximate():
    plan = plan_request("ä\nabc", mode="ask", validators=VALIDATORS)
    payload = plan.to_dict()
    assert plan.action == PlanAction.DIRECT
    assert payload["input"]["utf8_bytes"] == 6
    assert payload["input"]["estimated_tokens"] == 2
    assert "approximate" in payload["input"]["estimated_tokens_label"]
    assert payload["planned_calls"] == 3
    assert payload["maximum_concurrency"] == 3


def test_transport_specific_cap_forces_chunk_in_auto_mode():
    validators = [{"name": "tiny", "transport": "argv", "max_prompt_bytes": 8192}]
    plan = plan_request(
        "x" * 9000,
        mode="ask",
        validators=validators,
        max_chunk_bytes=2048,
    )
    assert plan.action == PlanAction.CHUNK
    assert len(plan.chunks) == 5
    assert all(chunk.profile.utf8_bytes <= 2048 for chunk in plan.chunks)


def test_chunk_content_reserves_space_for_executor_wrapper():
    validators = [{"name": "tiny", "transport": "argv", "max_prompt_bytes": 5000}]
    plan = plan_request(
        "x" * 5000,
        mode="ask",
        validators=validators,
        policy="chunk",
        max_chunk_bytes=5000,
    )
    assert plan.action == PlanAction.CHUNK
    assert all(chunk.profile.utf8_bytes <= 904 for chunk in plan.chunks)
    assert plan.forecast_input_bytes == sum(
        chunk.profile.utf8_bytes + 4096 for chunk in plan.chunks
    )


def test_transport_safety_is_reported_per_validator():
    plan = plan_request(
        "x" * 5000,
        mode="ask",
        validators=[
            {"name": "tiny", "transport": "argv", "max_prompt_bytes": 4096},
            {"name": "large", "transport": "stdin", "max_prompt_bytes": 100_000},
        ],
    )
    safety = {item.validator: item.direct_safe for item in plan.transports}
    assert safety == {"tiny": False, "large": True}


def test_forced_direct_rejects_oversize_before_launch():
    plan = plan_request(
        "x" * 200_000,
        mode="ask",
        validators=VALIDATORS,
        policy="direct",
    )
    assert plan.action == PlanAction.REJECT
    assert "direct ceiling" in plan.reason
    assert "--request-policy chunk" in plan.mitigation


def test_single_200kb_line_never_produces_over_limit_chunk():
    plan = plan_request(
        "é" * 100_000,
        mode="review",
        validators=VALIDATORS,
        policy="chunk",
        max_chunk_bytes=32 * 1024,
    )
    assert plan.action == PlanAction.CHUNK
    assert len(plan.chunks) > 1
    assert all(chunk.profile.utf8_bytes <= 32 * 1024 for chunk in plan.chunks)
    assert all("�" not in chunk.content for chunk in plan.chunks)


def test_markdown_headings_respect_fenced_block_structure():
    text = (
        "# One\nintro\n\n"
        "```python\n# not a markdown heading\ndef x():\n    pass\n```\n\n"
        "## Two\noutro\n"
    )
    units = semantic_units(text, source_label="doc.md")
    assert [unit.kind for unit in units] == ["markdown", "markdown"]
    assert "# not a markdown heading" in units[0].content
    assert "## Two" in units[1].content
    assert "".join(unit.content for unit in units) == text


def test_python_top_level_declarations_are_semantic_units():
    text = "import os\n\nVALUE = 1\n\ndef alpha():\n    return 1\n\nclass Beta:\n    pass\n"
    units = semantic_units(text, source_label="sample.py")
    assert len(units) >= 4
    assert all(unit.kind == "python" for unit in units)
    assert "".join(unit.content for unit in units) == text


def test_38_small_diff_hunks_pack_into_few_bounded_requests():
    pieces = []
    for index in range(38):
        pieces.append(
            f"diff --git a/f{index}.py b/f{index}.py\n"
            f"--- a/f{index}.py\n+++ b/f{index}.py\n"
            "@@ -1,1 +1,2 @@\n-old\n+new\n+line\n"
        )
    text = "".join(pieces)
    plan = plan_request(
        text,
        mode="review",
        validators=VALIDATORS,
        policy="chunk",
        kind="diff",
        max_chunk_bytes=16 * 1024,
    )
    assert plan.action == PlanAction.CHUNK
    assert len(plan.chunks) < 10
    assert plan.planned_calls == len(plan.chunks) * 3


def test_default_33_chunk_plan_rejects_with_exact_override_and_zero_ambiguity():
    plan = plan_request(
        "x" * 330,
        mode="ask",
        validators=VALIDATORS,
        policy="chunk",
        max_chunk_bytes=10,
        max_chunks=32,
        max_calls=200,
    )
    assert plan.action == PlanAction.REJECT
    assert plan.required_chunks == 33
    assert plan.required_calls == 99
    assert "--max-chunks 33" in plan.mitigation
    assert "--max-calls 99" in plan.mitigation


def test_plan_is_byte_deterministic():
    text = "# A\n" + ("paragraph\n\n" * 1000)
    kwargs = dict(
        mode="pipe",
        validators=VALIDATORS,
        policy="chunk",
        max_chunk_bytes=1024,
    )
    first = plan_request(text, **kwargs).to_dict()
    second = plan_request(text, **kwargs).to_dict()
    assert first == second


def test_overlap_stays_bounded_and_dedupes_exact_duplicate_units():
    duplicate = SemanticUnit("a lines 1-1", "same\n", 1, 1, "text")
    chunks = pack_units(
        [duplicate, duplicate, SemanticUnit("b", "z" * 20, 2, 2, "text")],
        max_bytes=20,
        overlap_lines=1,
    )
    assert all(chunk.profile.utf8_bytes <= 20 for chunk in chunks)
    assert len({(chunk.digest, chunk.labels) for chunk in chunks}) == len(chunks)


def test_non_lossless_mode_rejects_oversize_context():
    plan = plan_request(
        "evidence" * 20_000,
        mode="negotiate",
        validators=VALIDATORS,
        allow_chunk=False,
    )
    assert plan.action == PlanAction.REJECT
    assert "cannot losslessly shape" in plan.reason


def test_call_and_input_forecasts_fail_before_admission():
    call_limited = plan_request(
        "x" * 20_000,
        mode="review",
        validators=VALIDATORS,
        policy="chunk",
        max_chunk_bytes=1000,
        max_calls=10,
    )
    assert call_limited.action == PlanAction.REJECT
    assert "calls" in call_limited.reason

    byte_limited = plan_request(
        "x" * 5000,
        mode="ask",
        validators=VALIDATORS,
        max_input_bytes=1000,
    )
    assert byte_limited.action == PlanAction.REJECT
    assert "input bytes" in byte_limited.reason
