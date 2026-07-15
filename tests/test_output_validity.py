import pytest

from lope.output_validity import classify_output


@pytest.mark.parametrize("value", ["", "   ", "{}", '{"tool_calls":[{"name":"read"}]}', "<tool_call name='read'>"])
def test_non_substantive_outputs(value):
    assert classify_output(value).substantive is False


@pytest.mark.parametrize("value", [
    "CLEAN",
    "Review finding: auth.py:4 is unsafe",
    '{"choices":[{"message":{"content":"real answer"}}]}',
    '{"answer":"real answer"}',
    "Code mentions tool_calls but this is a legitimate review finding.",
])
def test_substantive_outputs(value):
    assert classify_output(value).substantive is True
