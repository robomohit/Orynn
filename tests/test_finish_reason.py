"""The finish action's answer must always be clean prose. Some models wrap it
as JSON (reason='{"reason":"…"}'), which used to render as literal JSON in the
capsule answer card. _clean_finish_reason unwraps that."""
from app.tools import _clean_finish_reason


def test_plain_reason_passes_through():
    assert _clean_finish_reason({"reason": "The result is 441."}) == "The result is 441."


def test_json_wrapped_reason_is_unwrapped():
    wrapped = {"reason": '{"reason":"Displayed result is 432"}'}
    assert _clean_finish_reason(wrapped) == "Displayed result is 432"


def test_nested_dict_reason_is_unwrapped():
    assert _clean_finish_reason({"reason": {"reason": "nested answer"}}) == "nested answer"


def test_alternate_keys():
    assert _clean_finish_reason({"answer": "via answer"}) == "via answer"
    assert _clean_finish_reason({"text": "via text"}) == "via text"
    assert _clean_finish_reason({"summary": "via summary"}) == "via summary"


def test_empty_falls_back():
    assert _clean_finish_reason({"reason": ""}) == "Task marked complete by agent."
    assert _clean_finish_reason({}) == "Task marked complete by agent."


def test_does_not_break_legit_json_looking_prose():
    # A normal sentence that merely mentions braces shouldn't be mangled.
    s = "I created {config} with the right keys."
    assert _clean_finish_reason({"reason": s}) == s


def test_deeply_nested_stops_safely():
    deep = {"reason": '{"reason": "deep value", "text": "ignored"}'}
    assert _clean_finish_reason(deep) == "deep value"
