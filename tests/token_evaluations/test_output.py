"""Provider-free prospective output-contract controls; no native sessions."""
import copy

import pytest

from scripts.token_evaluation import native
from scripts.token_evaluation.output import (
    JSON_INSTRUCTION,
    OUTPUT_CONTRACT,
    instruction,
    structurally_compliant,
)
from scripts.token_evaluation.protocol import CONTRACT
from scripts.token_evaluation.quality import review_policy


def output(format="json"):
    return {"contract": OUTPUT_CONTRACT, "format": format,
            "answer_policy": "last_agent_message"}


@pytest.mark.parametrize("text", [
    '{"facts": {}, "sources": []}',
    ' \n {"sources": null, "facts": null} \t',
    '{"facts": 7, "sources": "a.py:1", "extra": true}',
    '{"facts": ["é 🐜"], "sources": {"a.py": [1, 3]}}',
    '{"facts": ' + "9" * 5000 + ', "sources": 1e9999}',
])
def test_json_structure_does_not_add_value_or_citation_types(text):
    assert structurally_compliant(text, output())


@pytest.mark.parametrize("text", [
    '', 'null', 'true', '42', '"facts"', '[]',
    '{"facts": {"sources": []}}', '{"sources": []}',
    '{"Facts": {}, "Sources": []}',
    '```json\n{"facts":{},"sources":[]}\n```',
    'Answer: {"facts":{},"sources":[]}',
    '{"facts":{},"sources":[]} trailing',
    '{"facts":{},"sources":[]} {"facts":{},"sources":[]}',
    '{"facts":{},"sources":[],}',
    '{"facts":NaN,"sources":[]}',
    '{"facts":{},"sources":Infinity}',
    '{"facts":{},"sources":-Infinity}',
])
def test_json_structure_rejects_invalid_json_or_missing_top_level_keys(text):
    assert not structurally_compliant(text, output())


@pytest.mark.parametrize("text", ["", "plain prose", "```anything```", '{"facts":{}}'])
def test_free_text_has_no_new_structure_requirement(text):
    assert structurally_compliant(text, output("free_text"))
    assert instruction(output("free_text")) == ""


@pytest.mark.parametrize("guidance", ["", "Configured project research guidance."])
@pytest.mark.parametrize("phase", ["learn", "reuse"])
def test_both_arms_and_phases_receive_identical_shared_json_instruction(guidance, phase):
    protocol = {"contract": CONTRACT, "quality_review": review_policy(), "output": output(), "research": {"max_calls": 6},
                "track": "controlled"}
    task = {"phase": phase, "question": "What is the timeout?"}
    prompt = native._prompt(protocol, task, guidance)
    assert prompt.count(JSON_INSTRUCTION) == 1
    assert prompt.index(JSON_INSTRUCTION) < prompt.index("Question: ")
    assert instruction(protocol["output"]) == JSON_INSTRUCTION
    unchanged = copy.deepcopy(protocol)
    unchanged["output"]["format"] = "free_text"
    assert JSON_INSTRUCTION not in native._prompt(unchanged, task, guidance)


@pytest.mark.parametrize("change", [{"contract": None}, {"contract": "unknown"},
                                    {"format": None}, {"answer_policy": "other"}])
def test_instruction_and_checker_fail_closed_on_unbound_output(change):
    spec = {**output(), **change}
    for operation in [instruction, lambda value: structurally_compliant('{}', value)]:
        with pytest.raises(ValueError):
            operation(spec)


def test_parser_recursion_is_a_failure_instead_of_losing_the_report(monkeypatch):
    from scripts.token_evaluation import output as shared_output

    def exhausted(*args, **kwargs):
        raise RecursionError()

    monkeypatch.setattr(shared_output.json, "loads", exhausted)
    text = '{"facts":{},"sources":[]}'
    assert not structurally_compliant(text, output())
