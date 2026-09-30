"""The Lambda handler in ``aws/handler``: API Gateway proxy events in, HTTP responses out.

No AWS is involved; the handler is called in-process exactly as the Lambda
runtime would call it. Dates stay inside the base ephemeris tier (1850-2150),
the only one a plain checkout is guaranteed to have.
"""

from __future__ import annotations

import json
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
    assert response["headers"]["Content-Type"].startswith("application/xml")
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
    assert "output" in payload(response)["error"]


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
