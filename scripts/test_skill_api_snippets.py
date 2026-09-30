#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Execute every ``bash`` block in the REST API agent skill against the Lambda handler.

The sibling of ``test_skill_cli_snippets.py`` for ``skills/kerykeion-api``. Its
examples are ``curl`` calls against a deployed API, which a test cannot reach,
so ``curl`` is replaced on ``PATH`` by a stand-in that hands the request to
``aws/handler/kerykeion_api.py`` in-process, exactly as API Gateway would, and
prints the response like curl. Everything else in a block (``jq``, pipes,
shell variables) runs for real.

A block fails when bash fails, when it prints a bare ``null`` (a jq path that
names a missing field), or when any request in it gets an HTTP status of 400 or
more, unless the block says ``# gate: expect-error`` (an example of an error).
Blocks that cannot run here (``export`` of real credentials, installation)
start with ``# gate: skip``.

Blocks of one page run in order in one sandbox directory, so a page's setup
block serves the examples below it; pages never share state.

Usage:
    python scripts/test_skill_api_snippets.py [--timeout SECONDS]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT_ROOT / "skills" / "kerykeion-api"
HANDLER_DIR = PROJECT_ROOT / "aws" / "handler"
SKIP_MARKER = "# gate: skip"
ERROR_MARKER = "# gate: expect-error"
API_URL = "https://api.example.test/v1"

BASH_BLOCK = re.compile(r"```(?:bash|console|sh)\n(.*?)```", re.DOTALL)

# The curl stand-in. It supports the options the skill uses and refuses any
# other, so an example cannot pass on an option the real curl would read
# differently.
FAKE_CURL = r'''#!{python}
import json, os, sys, urllib.parse
sys.path.insert(0, {handler_dir!r})

args, method, body, headers, data = sys.argv[1:], None, None, {{}}, []
url = out_file = dump = write_out = None
include = fail = fail_with_body = get = False
# Split bundled short switches (-sG, -sSf) the way curl reads them.
def _split(arg):
    bundled = arg.startswith("-") and not arg.startswith("--") and len(arg) > 2 and set(arg[1:]) <= set("sSGLfi")
    return [f"-{{c}}" for c in arg[1:]] if bundled else [arg]
args = [part for arg in args for part in _split(arg)]
i = 0
def value():
    global i
    i += 1
    if i >= len(args):
        sys.exit(f"curl (test stand-in): {{args[i - 1]}} needs a value")
    return args[i]
while i < len(args):
    a = args[i]
    if a in ("-s", "-S", "--silent", "--show-error", "-L", "--location"):
        pass
    elif a in ("-H", "--header"):
        k, _, v = value().partition(":")
        headers[k.strip().lower()] = v.strip()
    elif a in ("-d", "--data", "--data-raw", "--data-binary"):
        body = value()
        if body == "@-":
            body = sys.stdin.read()
        elif body.startswith("@"):
            body = open(body[1:], encoding="utf-8").read()
        data.append(body)
    elif a == "--data-urlencode":
        k, sep, v = value().partition("=")
        data.append(f"{{k}}={{urllib.parse.quote(v, safe='')}}" if sep else urllib.parse.quote(k, safe=""))
        body = "&".join(data)
    elif a in ("-G", "--get"):
        get = True
    elif a in ("-X", "--request"):
        method = value()
    elif a in ("-i", "--include"):
        include = True
    elif a in ("-D", "--dump-header"):
        dump = value()
    elif a in ("-o", "--output"):
        out_file = value()
    elif a in ("-f", "--fail"):
        fail = True
    elif a == "--fail-with-body":
        fail_with_body = True
    elif a in ("-w", "--write-out"):
        write_out = value()
    elif a.startswith("-"):
        sys.exit(f"curl (test stand-in): unsupported option {{a}}")
    else:
        url = a
    i += 1

base = os.environ["KERYKEION_API_URL"].rstrip("/")
if not url or not url.startswith(base):
    sys.exit(f"curl (test stand-in): {{url!r}} is not under $KERYKEION_API_URL")
path, _, query = url[len(base):].partition("?")
path = path or "/"
if get:  # -G: the data goes into the query string, as with the real curl
    query = "&".join(q for q in [query, *data] if q)
    body = None
method = (method or ("POST" if body is not None else "GET")).upper()
multi = urllib.parse.parse_qs(query, keep_blank_values=True) if query else {{}}

import kerykeion_api
response = kerykeion_api.handler({{
    "httpMethod": method,
    "path": path,
    "body": body,
    "queryStringParameters": {{k: v[-1] for k, v in multi.items()}} or None,
    "multiValueQueryStringParameters": multi or None,
}}, None)

status = response["statusCode"]
with open(os.environ["KERYKEION_FAKE_CURL_LOG"], "a", encoding="utf-8") as log:
    log.write(f"{{status}} {{method}} {{path}}\n")

head = f"HTTP/2 {{status}}\r\n" + "".join(f"{{k.lower()}}: {{v}}\r\n" for k, v in response["headers"].items()) + "\r\n"
if dump == "-":
    sys.stdout.write(head)
elif dump:
    open(dump, "w", encoding="utf-8").write(head)
if fail and status >= 400:
    sys.stderr.write(f"curl: (22) The requested URL returned error: {{status}}\n")
    sys.exit(22)
payload = (head if include else "") + response["body"]
if out_file:
    open(out_file, "w", encoding="utf-8").write(payload)
else:
    sys.stdout.write(payload)
if write_out:
    sys.stdout.write(write_out.replace("%{{http_code}}", str(status)).replace("\\n", "\n"))
if fail_with_body and status >= 400:
    sys.exit(22)
'''


def extract_blocks(text: str) -> list[str]:
    return [match.group(1) for match in BASH_BLOCK.finditer(text)]


def install_fake_curl(bindir: Path) -> None:
    script = bindir / "curl"
    script.write_text(FAKE_CURL.format(python=sys.executable, handler_dir=str(HANDLER_DIR)), encoding="utf-8")
    script.chmod(0o755)


def run_block(code: str, *, workdir: Path, bindir: Path, timeout: float) -> tuple[bool, str]:
    """Run one block inside the page's sandbox; return (ok, detail)."""
    log = workdir / ".curl.log"
    log.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "KERYKEION_API_URL": API_URL,
        "KERYKEION_FAKE_CURL_LOG": str(log),
        "XDG_CONFIG_HOME": str(workdir / "config"),
    }
    try:
        completed = subprocess.run(
            ["bash", "-euo", "pipefail", "-c", code],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:g}s"
    requests = log.read_text(encoding="utf-8").splitlines()
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        detail = "\n      ".join(tail[-6:]) if tail else "(no output)"
        return False, f"exit {completed.returncode}\n      {detail}"
    if not requests:
        return False, "made no request; mark illustrative blocks with '# gate: skip'"
    failed = [line for line in requests if int(line.split()[0]) >= 400]
    if failed and ERROR_MARKER not in code:
        return False, "request failed: " + "; ".join(failed)
    if any(line.strip() == "null" for line in completed.stdout.splitlines()):
        return False, "prints a bare `null`: a jq path names a field the payload does not have"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-block timeout in seconds (default: 120).")
    args = parser.parse_args()

    for tool in ("bash", "jq"):
        if shutil.which(tool) is None:
            print(f"{tool} not found; cannot verify the API skill's examples.", file=sys.stderr)
            return 1
    if not SKILL_DIR.is_dir():
        print(f"missing skill directory: {SKILL_DIR}", file=sys.stderr)
        return 1

    total = skipped = failed = 0
    with tempfile.TemporaryDirectory(prefix="kerykeion-fake-curl-") as bin_tmp:
        bindir = Path(bin_tmp)
        install_fake_curl(bindir)
        for path in sorted(SKILL_DIR.rglob("*.md")):
            blocks = extract_blocks(path.read_text(encoding="utf-8"))
            if not blocks:
                continue
            print(f"\n📝 {path.relative_to(PROJECT_ROOT)} ({len(blocks)} block(s))")
            with tempfile.TemporaryDirectory(prefix="kerykeion-api-skill-") as tmp:
                for index, code in enumerate(blocks, start=1):
                    total += 1
                    if code.lstrip().startswith(SKIP_MARKER):
                        skipped += 1
                        print(f"  ⏭  Block {index}: skipped ({SKIP_MARKER})")
                        continue
                    ok, detail = run_block(code, workdir=Path(tmp), bindir=bindir, timeout=args.timeout)
                    if ok:
                        print(f"  ✅ Block {index}: OK")
                    else:
                        failed += 1
                        print(f"  ❌ Block {index}: {detail}")

    ran = total - skipped
    print(f"\n📊 Results: {ran - failed}/{ran} runnable block(s) passed ({skipped} skipped, {total} total)")
    if failed:
        print("🚨 The API skill documents requests that do not work.")
        return 1
    if ran == 0:
        print(f"🚨 No runnable bash block was found (or every one is marked '{SKIP_MARKER}'). Refusing to report success.")
        return 1
    print("🎉 Every runnable block in the API skill works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
