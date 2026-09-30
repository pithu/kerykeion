"""The Lambda handler in ``aws/handler``: API Gateway proxy events in, HTTP responses out.

No AWS is involved; the handler is called in-process exactly as the Lambda
runtime would call it. Dates stay inside the base ephemeris tier (1850-2150),
the only one a plain checkout is guaranteed to have.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "aws" / "handler"))

import kerykeion_api  # noqa: E402

pytestmark = pytest.mark.cli

BOB = {"name": "Bob", "date": "1990-07-15", "time": "10:30", "lat": 41.9, "lng": 12.5, "tz": "Europe/Rome"}
ALICE = {"name": "Alice", "date": "1985-03-02", "time": "08:15", "lat": 48.14, "lng": 11.58, "tz_str": "Europe/Berlin"}


def call(method: str, path: str, body: object = None) -> dict:
    """A POST/GET with a JSON body. It asks for JSON unless the body names a format: the default (html) has its own tests."""
    if isinstance(body, dict) and not {"f", "format"} & body.keys():
        body = {**body, "f": "json"}
    event = {"httpMethod": method, "path": path, "body": None if body is None else json.dumps(body)}
    return kerykeion_api.handler(event, None)


def payload(response: dict) -> dict:
    return json.loads(response["body"])


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "outer-config"))
    monkeypatch.setenv("KERYKEION_CLI_FORMAT", "json")


# ── running commands ─────────────────────────────────────────────────────────


def test_natal_with_an_inline_subject():
    response = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "offline": True})
    assert response["statusCode"] == 200, response["body"]
    assert response["headers"]["Content-Type"].startswith("application/json")
    assert response["headers"]["X-Kerykeion-Exit-Code"] == "0"
    chart = payload(response)
    assert chart["name"] == "Bob"
    assert chart["sun"]["sign"] == "Can"


def test_synastry_binds_two_inline_subjects_and_renders_svg():
    response = call("POST", "/synastry", {"subjects": {"bob": BOB, "alice": ALICE}, "s": "bob", "S": "alice", "f": "svg"})
    assert response["statusCode"] == 200, response["body"]
    assert response["headers"]["Content-Type"].startswith("image/svg+xml")
    assert "<svg" in response["body"]


def test_xml_format_and_long_flag_names():
    response = call("POST", "/natal", {"subjects": {"bob": BOB}, "subject": "bob", "format": "xml"})
    assert response["statusCode"] == 200, response["body"]
    assert response["headers"]["Content-Type"].startswith("text/plain")  # text/*: every fetch tool reads it
    assert response["headers"]["X-Kerykeion-Format"] == "xml"
    assert response["body"].lstrip().startswith("<")


def test_envelope_carries_the_payload_in_band():
    response = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "envelope": True})
    assert response["statusCode"] == 200, response["body"]
    assert payload(response)["data"]["name"] == "Bob"


def test_nested_command_path():
    response = call("POST", "/info/houses", {})
    assert response["statusCode"] == 200, response["body"]


def test_raw_argv_route():
    response = call("POST", "/run", {"argv": ["natal", "-s", "bob", "-f", "json"], "subjects": {"bob": BOB}})
    assert response["statusCode"] == 200, response["body"]
    assert payload(response)["name"] == "Bob"


def test_list_values_repeat_the_flag():
    argv = kerykeion_api.build_argv(["natal"], {"s": "bob", "with": ["dignities", "lunar_phase"]})
    assert argv[argv.index("--with") + 1] == "dignities"
    assert argv.count("--with") == 2


def test_a_list_for_a_single_value_flag_is_400():
    assert call("POST", "/natal", {"points": ["all", "v5"]})["statusCode"] == 400


def test_false_boolean_optional_becomes_no_flag():
    parser = kerykeion_api.command_parser(["natal"])
    switches = [a for a in parser._actions if a.__class__.__name__ == "BooleanOptionalAction"]
    if not switches:
        pytest.skip("natal has no --x/--no-x switch in this version")
    key = switches[0].option_strings[0].lstrip("-")
    argv = kerykeion_api.build_argv(["natal"], {key: False})
    assert f"--no-{key}" in argv


# ── the state stays per request ──────────────────────────────────────────────


def test_subjects_do_not_outlive_their_request():
    assert call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob"})["statusCode"] == 200
    response = call("POST", "/natal", {"s": "bob"})
    assert response["statusCode"] == 400
    assert payload(response)["exit_code"] == 4


def test_request_directories_are_removed_and_the_environment_restored(monkeypatch, tmp_path):
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))  # private to this test: xdist workers share the real one
    cwd, xdg = os.getcwd(), os.environ["XDG_CONFIG_HOME"]
    call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob"})
    assert list(scratch.iterdir()) == []
    assert os.getcwd() == cwd
    assert os.environ["XDG_CONFIG_HOME"] == xdg


class _StdoutAtEmit(logging.Handler):
    """Like the Lambda runtime's root handler: it writes to whatever ``sys.stdout`` is at emit time."""

    def emit(self, record):
        sys.stdout.write(f"[{record.levelname}]\t{record.getMessage()}\n")


def test_library_log_records_stay_out_of_the_payload():
    """A library warning must reach the warnings header, not the front of the JSON body."""
    root = logging.getLogger()
    lambda_like = _StdoutAtEmit()
    root.addHandler(lambda_like)
    try:
        before = root.handlers[:]
        response = call(
            "POST",
            "/transits",
            {"subjects": {"bob": BOB}, "s": "bob", "from": "2025-01-01", "to": "2025-01-10", "step_type": "days", "events": True},
        )
        assert root.handlers == before  # the runtime's handlers are back after the request
    finally:
        root.removeHandler(lambda_like)
    assert response["statusCode"] == 200, response["body"]
    payload(response)  # valid JSON: nothing was written in front of it
    assert "sampling step" in response["headers"]["X-Kerykeion-Warnings"]


# ── errors map to HTTP ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path, body",
    [
        ("/natal", {"bogus": 1}),  # unknown flag
        ("/nope", {}),  # unknown command
        ("/sky", {}),  # a group, not a command
        ("/natal", {"offline": "yes"}),  # a switch needs a boolean
        ("/run", {"argv": []}),
    ],
)
def test_bad_requests_are_400(path, body):
    response = call("POST", path, body)
    assert response["statusCode"] == 400
    assert payload(response)["exit_code"] == 4


def test_invalid_json_body_is_400():
    response = kerykeion_api.handler({"httpMethod": "POST", "path": "/natal", "body": "{not json"}, None)
    assert response["statusCode"] == 400


def test_cli_input_error_keeps_its_exit_code():
    response = call("POST", "/natal", {"subjects": {"bob": {**BOB, "tz": "Mars/Olympus"}}, "s": "bob"})
    assert response["statusCode"] in (400, 422)
    assert response["headers"]["X-Kerykeion-Exit-Code"] in ("4", "5")
    assert payload(response)["error"]


def test_unsupported_method_is_405():
    assert call("DELETE", "/natal")["statusCode"] == 405


# ── what the API refuses ─────────────────────────────────────────────────────


@pytest.mark.parametrize("argv", [["natal", "-s", "bob", "-o", "x.svg"], ["natal", "--output=x.svg"], ["natal", "-ox.svg"]])
def test_file_output_is_refused(argv):
    response = call("POST", "/run", {"argv": argv, "subjects": {"bob": BOB}})
    assert response["statusCode"] == 400
    assert "output" in response["body"]  # an html page: /run's format comes from its argv, which names none


def test_output_is_not_even_a_known_flag():
    assert call("POST", "/natal", {"o": "x.svg"})["statusCode"] == 400


@pytest.mark.parametrize("spec", ["/etc/passwd", "../../etc/passwd", "~", "/proc/self/environ"])
def test_server_paths_are_refused(spec):
    response = call("POST", "/natal", {"s": spec})
    assert response["statusCode"] == 400
    assert "path on the server" in payload(response)["error"]


def test_call_params_cannot_name_server_files():
    response = call("POST", "/run", {"argv": ["call", "DominantsFactory.from_subject", "--param", "x=/etc/hosts"]})
    assert response["statusCode"] == 400


def test_subject_save_is_refused():
    assert call("POST", "/subject/save", {"name": "x"})["statusCode"] == 400
    assert call("POST", "/run", {"argv": ["subject", "save", "x"]})["statusCode"] == 400


@pytest.mark.parametrize("name", ["../evil", "a/b", ".hidden"])
def test_subject_names_are_restricted(name):
    assert call("POST", "/natal", {"subjects": {name: BOB}, "s": "bob"})["statusCode"] == 400


def test_file_names_are_restricted():
    assert call("POST", "/natal", {"files": {"../x.json": {}}})["statusCode"] == 400


# ── discovery ────────────────────────────────────────────────────────────────


def test_catalog_lists_every_command_but_the_refused_ones():
    response = call("GET", "/")
    assert response["statusCode"] == 200
    commands = payload(response)["commands"]
    assert {"natal", "synastry", "sky/eclipses", "technique/profections", "call"} <= set(commands)
    assert "subject/save" not in commands
    natal_keys = {flag["key"] for flag in commands["natal"]["flags"]}
    assert "subject" in natal_keys and "format" in natal_keys
    assert "output" not in natal_keys and "help" not in natal_keys


def test_get_a_command_returns_its_help():
    response = call("GET", "/natal")
    assert response["statusCode"] == 200
    assert response["headers"]["Content-Type"].startswith("text/plain")
    assert "usage: kerykeion natal" in response["body"]


def test_get_an_unknown_command_is_400():
    assert call("GET", "/nope")["statusCode"] == 400


def test_proxy_path_parameter_wins_over_the_raw_path():
    event = {"httpMethod": "GET", "path": "/v1/natal", "pathParameters": {"proxy": "natal"}}
    assert kerykeion_api.handler(event, None)["statusCode"] == 200


# ── GET with a query string (for agents that can only fetch a URL) ───────────


def get(path: str, **params: object) -> dict:
    """A GET as API Gateway delivers it: every value a string, repeated keys in the multi-value map."""
    multi = {key: [str(v) for v in value] if isinstance(value, list) else [str(value)] for key, value in params.items()}
    event = {
        "httpMethod": "GET",
        "path": path,
        "queryStringParameters": {key: values[-1] for key, values in multi.items()} or None,
        "multiValueQueryStringParameters": multi or None,
    }
    return kerykeion_api.handler(event, None)


def as_query(prefix: str, subject: dict) -> dict:
    return {f"{prefix}_{key}": value for key, value in subject.items()}


def test_get_natal_with_p1_fields():
    response = get("/natal", **as_query("p1", BOB), f="json")
    assert response["statusCode"] == 200, response["body"]
    assert payload(response)["name"] == "Bob"


def test_get_synastry_binds_p1_and_p2():
    response = get("/synastry", **as_query("p1", BOB), **as_query("p2", ALICE), f="xml")
    assert response["statusCode"] == 200, response["body"]
    assert "Bob" in response["body"] and "Alice" in response["body"]


def test_get_with_inline_natal_flags_and_a_switch():
    response = get("/natal", name="X", date="1990-01-01", time="12:00", lat="45", lng="9", tz="Europe/Rome", offline="true")
    assert response["statusCode"] == 200, response["body"]


def test_get_repeated_key_is_a_list():
    body = kerykeion_api.query_body(["natal"], {"with": ["dignities", "lunar_phase"]})
    assert body["with"] == ["dignities", "lunar_phase"]


def test_get_false_switch_becomes_no_flag():
    body = kerykeion_api.query_body(["natal"], {"zodiac_ring": ["false"]})
    assert "--no-zodiac-ring" in kerykeion_api.build_argv(["natal"], body)


def test_get_without_query_is_still_help():
    response = get("/natal")
    assert response["headers"]["Content-Type"].startswith("text/plain")


def test_get_without_multi_value_map():
    event = {"httpMethod": "GET", "path": "/natal", "queryStringParameters": {**as_query("p1", BOB)}}
    assert kerykeion_api.handler(event, None)["statusCode"] == 200


def test_explicit_s_wins_over_p1():
    body = kerykeion_api.query_body(["natal"], {"p1_name": ["A"], "p9_name": ["B"], "s": ["p9"]})
    assert body["s"] == "p9"


@pytest.mark.parametrize(
    "params",
    [
        {**as_query("p1", BOB), "offline": "maybe"},  # a switch needs a boolean
        {**as_query("p1", BOB), **as_query("p2", ALICE)},  # natal has no second subject
        {"f": ["json", "xml"]},  # a single-value flag given twice
        {"subjects": "{}"},  # POST only
        {"bogus": "1"},
    ],
)
def test_bad_get_requests_are_400(params):
    response = get("/natal", **params)
    assert response["statusCode"] == 400, response["body"]


def test_run_is_post_only():
    assert get("/run", argv="natal")["statusCode"] == 400


# ── response formats ─────────────────────────────────────────────────────────


def test_html_is_the_default_with_a_planets_table():
    response = kerykeion_api.handler({"httpMethod": "POST", "path": "/natal", "body": json.dumps({"subjects": {"bob": BOB}, "s": "bob"})}, None)
    assert response["statusCode"] == 200, response["body"]
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert response["headers"]["X-Kerykeion-Format"] == "html"
    body = response["body"]
    assert body.startswith("<!doctype html>") and "<title>kerykeion natal: Bob</title>" in body
    assert "<h3>planets</h3><table>" in body and "<td>Sun</td>" in body


def test_get_defaults_to_html_too():
    response = get("/natal", **as_query("p1", BOB))
    assert response["headers"]["Content-Type"].startswith("text/html")


def test_html_falls_back_to_json_tables_without_an_xml_view():
    response = get("/sky/eclipses", start_year=2027, count=1)
    assert response["statusCode"] == 200, response["body"]
    assert response["headers"]["Content-Type"].startswith("text/html")
    assert "<h3>solar eclipses</h3>" in response["body"] and "annular" in response["body"]


def test_html_carries_the_warnings_in_the_page():
    response = get("/transits", **as_query("p1", BOB), **{"from": "2025-01-01", "to": "2025-01-10", "step_type": "days", "events": "true"})
    assert response["statusCode"] == 200, response["body"]
    assert "<h2>Warnings</h2>" in response["body"] and "sampling step" in response["body"]


def test_yaml_is_the_json_payload():
    import yaml

    as_json = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "f": "json"})
    as_yaml = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "f": "yaml"})
    assert as_yaml["headers"]["Content-Type"].startswith("text/plain")
    assert as_yaml["headers"]["X-Kerykeion-Format"] == "yaml"
    assert yaml.safe_load(as_yaml["body"]) == payload(as_json)


@pytest.mark.parametrize("fmt", ["yaml", "html"])
def test_envelope_works_with_derived_formats(fmt):
    response = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "f": fmt, "envelope": True})
    assert response["statusCode"] == 200, response["body"]
    assert "Bob" in response["body"] and "warnings" in response["body"]


def test_run_takes_a_derived_format():
    response = call("POST", "/run", {"argv": ["natal", "-s", "bob", "-f", "yaml"], "subjects": {"bob": BOB}})
    assert response["statusCode"] == 200, response["body"]
    assert response["body"].startswith("name: Bob")


def test_unknown_format_is_400():
    response = call("POST", "/natal", {"subjects": {"bob": BOB}, "s": "bob", "f": "pdf"})
    assert response["statusCode"] == 400
    assert "html, json, xml, yaml" in payload(response)["error"]


def test_errors_are_html_pages_when_html_is_asked_for():
    cli_error = get("/natal", p1_date="1400-01-01", p1_time="12:00", p1_lat=45, p1_lng=9, p1_tz="Europe/Rome")
    assert cli_error["statusCode"] == 422
    assert cli_error["headers"]["Content-Type"].startswith("text/html") and "exit code" in cli_error["body"]
    early_error = get("/natal", **as_query("p1", BOB), offline="maybe")  # refused before the CLI runs
    assert early_error["statusCode"] == 400
    assert early_error["headers"]["Content-Type"].startswith("text/html")


def test_help_stays_plain_text():
    assert get("/natal")["headers"]["Content-Type"].startswith("text/plain")


def test_xml_to_html_groups_same_tag_leaves_into_one_table():
    rendered = kerykeion_api.xml_to_html('<chart name="X"><planets><point name="Sun" sign="Ari"/><point name="Moon" sign="Tau"/></planets></chart>')
    assert rendered.count("<table>") == 1
    assert "<th>name</th><th>sign</th>" in rendered and "<td>Moon</td><td>Tau</td>" in rendered


def test_json_to_html_escapes_values():
    assert "&lt;script&gt;" in kerykeion_api.json_to_html({"name": "<script>"})
