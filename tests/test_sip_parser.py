import pytest

from local_engine.kernel.sip_parser import parse_sip


def parse(raw):
    return parse_sip(raw, "tester", "task_1")


def assert_contract(sip):
    assert set(sip) == {
        "type", "skill", "task_id", "confidence", "assumptions", "unknowns", "risks", "warnings", "dependencies", "artifacts", "findings", "recommendations", "decisions", "body", "failure_type"
    }
    assert sip["skill"] == "tester"
    assert sip["task_id"] == "task_1"
    assert isinstance(sip["confidence"], float)
    for name in ("assumptions", "unknowns", "risks", "warnings", "dependencies", "artifacts", "findings", "recommendations", "decisions"):
        assert isinstance(sip[name], list)
    assert isinstance(sip["body"], str)


def test_pure_yaml():
    sip = parse("type: plan\nconfidence: 0.75\nbody: hello\n")
    assert sip["type"] == "plan"
    assert sip["confidence"] == 0.75
    assert sip["body"] == "hello"
    assert_contract(sip)


def test_pure_json():
    sip = parse('{"type": "review", "confidence": 1, "body": "ok"}')
    assert sip["type"] == "review"
    assert sip["confidence"] == 1.0
    assert_contract(sip)


def test_fenced_yaml():
    sip = parse("```yaml\ntype: test\nbody: fenced\n```")
    assert sip["type"] == "test"
    assert sip["body"] == "fenced"


def test_fenced_json():
    sip = parse('```json\n{"type":"patch", "body":"diff"}\n```')
    assert sip["type"] == "patch"
    assert sip["body"] == "diff"


def test_generic_fenced_block():
    sip = parse("```\ntype: analysis\nbody: generic\n```")
    assert sip["type"] == "analysis"
    assert sip["body"] == "generic"


def test_arbitrarily_named_fenced_block():
    sip = parse("```output\ntype: analysis\nbody: labelled\n```")
    assert sip["type"] == "analysis"
    assert sip["body"] == "labelled"


def test_natural_language_then_yaml_fragment():
    sip = parse("Here is the answer.\n\ntype: plan\nbody: useful\n")
    assert sip["type"] == "plan"
    assert sip["body"] == "useful"


def test_natural_language_then_json_object():
    sip = parse('Here is the answer. {"type": "plan", "body": "useful"} trailing')
    assert sip["type"] == "plan"
    assert sip["body"] == "useful"


def test_invalid_free_text_is_unstructured_and_preserved():
    raw = "I could not follow the required schema, but here are useful notes."
    sip = parse(raw)
    assert sip["type"] == "unstructured"
    assert sip["body"] == raw
    assert "not valid SIP" in sip["unknowns"][0]


def test_empty_output_is_error():
    sip = parse("  \n")
    assert sip["type"] == "error"
    assert sip["body"] == ""
    assert "empty output" in sip["unknowns"][0]


def test_missing_fields_get_defaults():
    sip = parse("type: review\n")
    assert sip["type"] == "review"
    assert sip["confidence"] == 0.0
    assert sip["assumptions"] == []
    assert "type: review" in sip["body"]
    assert_contract(sip)


def test_wrong_field_types_are_normalized():
    sip = parse("""type: plan
confidence: not-a-number
assumptions: assumption
unknowns: unknown
risks: risk
dependencies: task_a
artifacts: report.md
body: done
""")
    assert sip["confidence"] == 0.0
    assert sip["assumptions"] == ["assumption"]
    assert sip["dependencies"] == ["task_a"]
    assert sip["artifacts"] == ["report.md"]


@pytest.mark.parametrize("body", ["nested: value\nitems:\n  - one\n", "- one\n- two\n"])
def test_body_dict_or_list_is_serialized_to_yaml(body):
    raw = "type: plan\nbody:\n  " + body.replace("\n", "\n  ").rstrip() + "\n"
    sip = parse(raw)
    assert isinstance(sip["body"], str)
    assert sip["body"].strip()


def test_invalid_confidence_string_defaults_to_zero():
    sip = parse("type: plan\nconfidence: invalid\nbody: works\n")
    assert sip["confidence"] == 0.0
