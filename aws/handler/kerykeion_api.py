# -*- coding: utf-8 -*-
"""The ``kerykeion`` command line behind an API Gateway REST proxy (AWS Lambda).

Every CLI command is reachable without being re-declared here: the request is
turned into an argv list and handed to :func:`kerykeion_cli.app.run`, the same
dispatcher the console script uses. New CLI commands therefore appear in the
API on their own.

    GET  /                     the command catalog (commands, flags, help), JSON
    GET  /<cmd>[/<sub>]        without a query string: the command's --help, text
    GET  /<cmd>[/<sub>]?…      run it; the query string carries the flags
    POST /<cmd>[/<sub>]        run it; the JSON body carries the flags
    POST /run                  {"argv": [...]} for anything the body mapping cannot say

GET exists for agents that can only fetch a URL (no body, no headers): the
query string maps like the body, a repeated key is a list, ``true``/``false``
set switches, and ``p1_<field>``/``p2_<field>`` describe up to two subjects,
bound to ``-s``/``-S`` unless those are given.

Body → argv: ``"s": "ada"`` → ``-s ada``; ``"houses": "placidus"`` →
``--houses placidus``; ``true`` → a bare flag; ``false`` → ``--no-x`` where the
command has one; a list → the flag repeated. ``args`` are positionals.
``subjects`` are profiles written for this request only, so ``-s``/``-S`` work
without a persistent store; ``files`` are JSON inputs (``call --param``).

Stateless by design: each request gets its own directory under ``/tmp`` (the
profile store, the working directory) and it is deleted afterwards.

``kerykeion_cli.main`` is deliberately not used: on a broken pipe its error
boundary points fd 1 at /dev/null, which in a warm Lambda container would
silence the function's logs for every later invocation.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Optional

os.environ.setdefault("KERYKEION_CLI_FORMAT", "json")

from kerykeion_cli import app, errors  # noqa: E402 — after the format default

logger = logging.getLogger("kerykeion_api")
logger.setLevel(logging.INFO)

# Body keys that are not flags.
RESERVED_KEYS = frozenset({"subjects", "args", "files"})
# Options that would touch the filesystem outside the request.
BLOCKED_OPTIONS = frozenset({"-o", "--output"})
# Commands that make no sense without a persistent store.
BLOCKED_COMMANDS = {("subject", "save"): "profiles are not stored; send them in the request's \"subjects\" object"}
# Exit code → HTTP status.
HTTP_STATUS = {0: 200, 2: 400, 4: 400, 5: 422, 6: 422, 7: 502, 8: 413, 9: 422, 130: 503}
CONTENT_TYPES = {
    "json": "application/json; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "xml": "application/xml; charset=utf-8",
    "text": "text/plain; charset=utf-8",
}
# Lambda's synchronous response limit is 6 MB, and the proxy envelope takes a share of it.
MAX_BODY_BYTES = 5_500_000
MAX_HEADER_CHARS = 4000
_NAME = re.compile(r"[A-Za-z0-9_.\- ]+")


class RequestError(ValueError):
    """A request the API refuses before the CLI sees it (HTTP 400)."""


# ── parser introspection ─────────────────────────────────────────────────────

_parser: Optional[argparse.ArgumentParser] = None


def _root_parser() -> argparse.ArgumentParser:
    global _parser
    if _parser is None:
        _parser = app.build_parser()
    return _parser


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def command_parser(parts: list[str]) -> argparse.ArgumentParser:
    """The parser of ``kerykeion <parts…>``, or :class:`RequestError` naming what exists."""
    parser = _root_parser()
    for depth, part in enumerate(parts):
        choices = _subcommands(parser)
        if part not in choices:
            where = " ".join(parts[:depth]) or "the root"
            raise RequestError(f"unknown command {'/'.join(parts[: depth + 1])!r}; {where} offers: {', '.join(sorted(choices))}")
        parser = choices[part]
    if _subcommands(parser):
        raise RequestError(f"{'/'.join(parts)!r} is a group; choose one of: {', '.join(sorted(_subcommands(parser)))}")
    return parser


def _describe(parser: argparse.ArgumentParser) -> dict[str, Any]:
    flags, positionals = [], []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction) or BLOCKED_OPTIONS & set(action.option_strings):
            continue
        if not action.option_strings:
            positionals.append({"name": action.dest, "required": action.nargs != "?", "help": action.help})
            continue
        longest = max(action.option_strings, key=len)
        if isinstance(action, argparse.BooleanOptionalAction):
            kind = "bool"
            longest = action.option_strings[0]  # --x, not its --no-x
        elif action.nargs == 0:
            kind = "flag"
        elif isinstance(action, argparse._AppendAction):
            kind = "list"
        else:
            kind = "value"
        flags.append({"key": longest.lstrip("-").replace("-", "_"), "names": list(action.option_strings), "kind": kind, "help": action.help})
    return {"summary": (parser.description or "").splitlines()[0] if parser.description else "", "positionals": positionals, "flags": flags}


def catalog() -> dict[str, Any]:
    """Every runnable command, keyed by its API path."""
    commands: dict[str, Any] = {}

    def walk(parser: argparse.ArgumentParser, prefix: list[str]) -> None:
        for name, sub in _subcommands(parser).items():
            path = prefix + [name]
            if _subcommands(sub):
                walk(sub, path)
            elif tuple(path) not in BLOCKED_COMMANDS:
                commands["/".join(path)] = _describe(sub)

    walk(_root_parser(), [])
    return {"version": app.__version__, "commands": commands}


# ── request → argv ───────────────────────────────────────────────────────────


def _option_index(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """The command's options, minus --help and those the API refuses."""
    return {
        name: action
        for action in parser._actions
        if not isinstance(action, argparse._HelpAction) and not BLOCKED_OPTIONS & set(action.option_strings)
        for name in action.option_strings
    }


def _flag_for(key: str, options: dict[str, argparse.Action]) -> str:
    """``"s"`` → ``-s``, ``"subject2"`` → ``--subject2``; the key may also be spelled with its dashes."""
    if key.startswith("-"):
        candidates = [key]
    else:
        candidates = [f"-{key}"] if len(key) == 1 else [f"--{key.replace('_', '-')}", f"--{key}"]
    for flag in candidates:
        if flag in options:
            return flag
    raise RequestError(f"unknown flag {key!r}; accepted: {', '.join(sorted(options))}")


def _scalar(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def build_argv(parts: list[str], body: dict[str, Any]) -> list[str]:
    """The argv for ``POST /<parts>`` with *body*; flags are checked against the command's own parser."""
    if tuple(parts) in BLOCKED_COMMANDS:
        raise RequestError(f"{'/'.join(parts)} is not available over the API: {BLOCKED_COMMANDS[tuple(parts)]}")
    options = _option_index(command_parser(parts))
    args = body.get("args", [])
    if not isinstance(args, list):
        raise RequestError('"args" must be a list of positional values')
    argv = list(parts) + [_scalar(a) for a in args]
    for key, value in body.items():
        if key in RESERVED_KEYS or value is None:
            continue
        flag = _flag_for(key, options)
        action = options[flag]
        if isinstance(action, argparse.BooleanOptionalAction):
            if not isinstance(value, bool):
                raise RequestError(f"{key!r} is a switch; pass true or false")
            argv.append(flag if value else "--no-" + max(action.option_strings, key=len)[2:].removeprefix("no-"))
        elif action.nargs == 0:
            if not isinstance(value, bool):
                raise RequestError(f"{key!r} is a switch; pass true or false")
            if value:
                argv.append(flag)
        elif isinstance(value, list):
            if not isinstance(action, argparse._AppendAction):
                raise RequestError(f"{key!r} takes a single value, not a list")
            for item in value:
                argv += [flag, _scalar(item)]
        else:
            argv += [flag, _scalar(value)]
    return argv


def _check_argv(argv: list[str], workdir: Path) -> None:
    """Refuse file output, and any value naming a path outside this request's directory."""
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise RequestError('"argv" must be a list of strings')
    for token in argv:
        if token in BLOCKED_OPTIONS or any(token.startswith(o + "=") for o in BLOCKED_OPTIONS if o.startswith("--")):
            raise RequestError("-o/--output is not available over the API; the payload is the response body")
        if token.startswith("-o") and len(token) > 2 and not token.startswith("--"):
            raise RequestError("-o/--output is not available over the API; the payload is the response body")
    commands = [a for a in argv if not a.startswith("-")][:2]
    if tuple(commands) in BLOCKED_COMMANDS:
        raise RequestError(f"{'/'.join(commands)} is not available over the API: {BLOCKED_COMMANDS[tuple(commands)]}")
    root = str(workdir.resolve())
    for token in argv:
        for value in {token, token.partition("=")[2]}:
            if not value:
                continue
            if value.startswith(("/", "~", "\\")) or ".." in re.split(r"[/\\]", value):
                raise RequestError(f"{value!r} names a path on the server; send inputs in \"subjects\" or \"files\"")
            try:
                resolved = os.path.realpath(os.path.expanduser(value))
                exists = os.path.exists(resolved)
            except (OSError, ValueError):
                continue
            if exists and resolved != root and not resolved.startswith(root + os.sep):
                raise RequestError(f"{value!r} names a path on the server; send inputs in \"subjects\" or \"files\"")


def _write_inputs(body: dict[str, Any], workdir: Path) -> None:
    """Write the request's ``subjects`` into a private profile store and ``files`` into the working directory."""
    from kerykeion_cli import profiles

    subjects = body.get("subjects") or {}
    files = body.get("files") or {}
    if not isinstance(subjects, dict) or not isinstance(files, dict):
        raise RequestError('"subjects" and "files" must be objects keyed by name')
    for name, recipe in subjects.items():
        if not isinstance(recipe, dict):
            raise RequestError(f"subject {name!r} must be an object of profile fields")
        recipe = dict(recipe)
        if "tz" in recipe and "tz_str" not in recipe:  # the CLI flag's spelling
            recipe["tz_str"] = recipe.pop("tz")
        try:
            path = profiles.profile_path(name)
            profile = profiles.Profile(name=name, input=profiles.ProfileInput(**recipe))
        except (ValueError, TypeError) as exc:
            raise RequestError(f"subject {name!r}: {errors._clean_message(exc)}") from None
        profiles.save(path, profile)
    for name, content in files.items():
        if not _NAME.fullmatch(name) or name != name.strip(" .") or not name.endswith(".json"):
            raise RequestError(f"file name {name!r}: use a plain name ending in .json")
        (workdir / name).write_text(json.dumps(content), encoding="utf-8")


# ── query string → body (GET) ────────────────────────────────────────────────

_SUBJECT_KEY = re.compile(r"(p[1-9])_(\w+)")
_LIST_FIELDS = frozenset({"active_points", "active_fixed_stars"})
_TRUE, _FALSE = frozenset({"true", "1", "yes", "on", ""}), frozenset({"false", "0", "no", "off"})


def _switch(key: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise RequestError(f"{key!r} is a switch; pass true or false, not {value!r}")


def query_body(parts: list[str], params: dict[str, list[str]]) -> dict[str, Any]:
    """The body equivalent of a GET query string, typed by the command's own parser."""
    options = _option_index(command_parser(parts))
    body: dict[str, Any] = {}
    subjects: dict[str, dict[str, Any]] = {}
    for key, values in params.items():
        if key in ("subjects", "files"):
            raise RequestError(f"{key!r} is not available in a query string; use p1_<field>/p2_<field>, or POST")
        match = _SUBJECT_KEY.fullmatch(key)
        if match:
            name, field = match.groups()
            value = values[-1]
            subjects.setdefault(name, {})[field] = [v.strip() for v in value.split(",") if v.strip()] if field in _LIST_FIELDS else value
            continue
        if key == "args":
            body["args"] = values
            continue
        action = options.get(_flag_for(key, options))
        if isinstance(action, argparse.BooleanOptionalAction) or (action is not None and action.nargs == 0):
            body[key] = _switch(key, values[-1])
        elif isinstance(action, argparse._AppendAction):
            body[key] = values
        else:
            if len(values) > 1:
                raise RequestError(f"{key!r} takes a single value; it appears {len(values)} times")
            body[key] = values[0]
    if subjects:
        body["subjects"] = subjects
        for name, flag in (("p1", "s"), ("p2", "S")):
            if name in subjects and flag not in body and not ({"subject", "subject2"} & body.keys()):
                if f"-{flag}" not in options:
                    raise RequestError(f"{'/'.join(parts)} takes no {'second ' if flag == 'S' else ''}subject; drop the {name}_ fields")
                body[flag] = name
    return body


def _query_params(event: dict[str, Any]) -> dict[str, list[str]]:
    multi = event.get("multiValueQueryStringParameters")
    if multi:
        return {key: list(values) for key, values in multi.items()}
    return {key: [value] for key, value in (event.get("queryStringParameters") or {}).items()}


# ── execution ────────────────────────────────────────────────────────────────


def execute(argv: list[str]) -> tuple[int, str, str]:
    """Run the CLI on *argv*; ``(exit code, stdout, stderr)``. Never raises, never exits.

    The Lambda runtime's root log handler writes to stdout, so while the CLI
    runs the root logger is pointed at the captured stderr: a library warning
    belongs in ``X-Kerykeion-Warnings``, not in front of the JSON payload.
    """
    out, err = io.StringIO(), io.StringIO()
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    capture = logging.StreamHandler(err)
    capture.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.handlers = [capture]
    unexpected: Optional[str] = None
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = app.run(argv)
            except SystemExit as exc:  # argparse (2, or 0 for --help), exit 9
                if isinstance(exc.code, int) or exc.code is None:
                    code = exc.code or 0
                else:
                    err.write(f"{exc.code}\n")
                    code = 1
            except BaseException as exc:  # noqa: BLE001 — the CLI's own boundary, minus the exit
                code = int(errors.classify(exc))
                if code == errors.ExitCode.UNEXPECTED:
                    unexpected = "".join(traceback.format_exception(exc))
                err.write(f"kerykeion: error: {errors._clean_message(exc)}\n")
    finally:
        root.handlers = saved_handlers
    if unexpected:  # after the handlers are back, so it reaches the function's logs
        logger.error("unexpected error for %s\n%s", argv[:2], unexpected)
    return int(code), out.getvalue(), err.getvalue()


def _format_of(argv: list[str]) -> str:
    for index, token in enumerate(argv):
        if token in ("-f", "--format") and index + 1 < len(argv):
            return argv[index + 1]
        if token.startswith("--format="):
            return token.split("=", 1)[1]
        if token.startswith("-f") and len(token) > 2 and not token.startswith("--"):
            return token[2:]
    return os.environ.get("KERYKEION_CLI_FORMAT", "json")


def _header_safe(text: str) -> str:
    flat = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    flat = flat.encode("ascii", "backslashreplace").decode("ascii")
    return flat[:MAX_HEADER_CHARS]


def _response(status: int, body: str, content_type: str, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": content_type, **(headers or {})},
        "body": body,
        "isBase64Encoded": False,
    }


def _json_response(status: int, payload: Any, headers: Optional[dict[str, str]] = None) -> dict[str, Any]:
    return _response(status, json.dumps(payload, indent=2), CONTENT_TYPES["json"], headers)


def _error(status: int, message: str, exit_code: Optional[int] = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    return _json_response(status, payload)


def run_request(argv: list[str], body: dict[str, Any], help_request: bool = False) -> dict[str, Any]:
    """Run *argv* inside a fresh request directory and shape the HTTP response."""
    workdir = Path(tempfile.mkdtemp(prefix="kerykeion-"))
    previous_cwd = os.getcwd()
    previous_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(workdir / "config")
    started = time.monotonic()
    try:
        os.chdir(workdir)
        _write_inputs(body, workdir)
        _check_argv(argv, workdir)
        code, stdout, stderr = execute(argv)
    except RequestError as exc:
        return _error(400, str(exc), exit_code=4)
    finally:
        os.chdir(previous_cwd)
        if previous_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = previous_xdg
        shutil.rmtree(workdir, ignore_errors=True)
    logger.info(json.dumps({"command": [a for a in argv if not a.startswith("-")][:2], "exit_code": code, "ms": round((time.monotonic() - started) * 1000)}))

    headers = {"X-Kerykeion-Exit-Code": str(code)}
    if code != 0:
        return _json_response(HTTP_STATUS.get(code, 500), {"exit_code": code, "error": stderr.strip() or stdout.strip()}, headers)
    if stderr.strip():
        headers["X-Kerykeion-Warnings"] = _header_safe(stderr)
    fmt = "text" if help_request else _format_of(argv)
    if len(stdout.encode("utf-8")) > MAX_BODY_BYTES:
        return _error(413, "the response exceeds Lambda's 6 MB limit; narrow the range, widen the step or use \"f\": \"xml\"", exit_code=8)
    return _response(200, stdout, CONTENT_TYPES.get(fmt, CONTENT_TYPES["text"]), headers)


# ── Lambda entry point ───────────────────────────────────────────────────────


def _body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body")
    if not raw:
        return {}
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RequestError(f"the body is not valid JSON: {exc}") from None
    if not isinstance(body, dict):
        raise RequestError("the body must be a JSON object")
    return body


def _path_parts(event: dict[str, Any]) -> list[str]:
    proxy = (event.get("pathParameters") or {}).get("proxy")
    path = proxy if proxy is not None else event.get("path", "/")
    return [part for part in path.strip("/").split("/") if part]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """API Gateway (REST, proxy integration) → the CLI."""
    method = (event.get("httpMethod") or "GET").upper()
    try:
        parts = _path_parts(event)
        if method == "GET":
            if not parts:
                return _json_response(200, catalog())
            params = _query_params(event)
            if not params:
                command_parser(parts)
                return run_request(parts + ["--help"], {}, help_request=True)
            if parts == ["run"]:
                raise RequestError("/run takes POST; run a command with GET /<command>?<flags>")
            body = query_body(parts, params)
            return run_request(build_argv(parts, body), body)
        if method != "POST":
            return _error(405, "use GET or POST")
        body = _body(event)
        if parts == ["run"]:
            argv = body.get("argv")
            if not isinstance(argv, list) or not argv:
                raise RequestError('POST /run needs {"argv": ["natal", "-s", "ada", ...]}')
            return run_request([_scalar(a) for a in argv], body)
        if not parts:
            raise RequestError("POST to a command path, e.g. /natal or /sky/eclipses; GET / lists them")
        return run_request(build_argv(parts, body), body)
    except RequestError as exc:
        return _error(400, str(exc), exit_code=4)
    except Exception:  # noqa: BLE001 — never let API Gateway see a raw 502
        logger.exception("unhandled error")
        return _error(500, "internal error", exit_code=1)
