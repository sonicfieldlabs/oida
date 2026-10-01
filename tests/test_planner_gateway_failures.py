"""The planner gateway says why the worker refused, from a fixed list (Phase 3, 24 Sept 2026)."""

from oida.reasoning.local.gateway import failure_category


def test_known_refusals_are_named_and_nothing_else_is_quoted(tmp_path):
    log = tmp_path / "error.log"
    log.write_text("Traceback ...\nValueError: Input exceeds the validated 8192-token planning context\n")
    assert failure_category(log) == "prompt exceeds the validated 8192-token context"
    log.write_text("jsonschema.exceptions.ValidationError: 'PRIVATE CANARY' is not one of ['stop']\n")
    assert failure_category(log) == "the plan did not match the offered schema"
    log.write_text("RuntimeError: PRIVATE CANARY\n")
    assert failure_category(log) == "unknown"
    assert failure_category(tmp_path / "missing.log") == "unknown (no worker log)"
