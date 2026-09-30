#!/usr/bin/env bash
# Call the Lambda image running locally (the runtime interface emulator on port 9000)
# the way API Gateway would, and print the HTTP response.
#
#   docker run --rm -d --name kapi --read-only --tmpfs /tmp -p 9000:8080 kerykeion-api:dev
#   aws/local-invoke.sh GET /
#   aws/local-invoke.sh GET /natal
#   aws/local-invoke.sh POST /natal '{"subjects":{"ada":{"name":"Ada","date":"1815-12-10","time":"18:00","lat":51.5,"lng":-0.12,"tz":"Europe/London"}},"s":"ada","f":"xml"}'
#   aws/local-invoke.sh -v POST /sky/eclipses '{"start_year":2027,"count":2}'     # -v: status and headers on stderr
#
# The response body goes to stdout, so it can be piped (| jq) or redirected (> chart.svg).
set -euo pipefail

VERBOSE=0
if [ "${1:-}" = "-v" ]; then VERBOSE=1; shift; fi
[ $# -ge 2 ] || { sed -n '2,12p' "$0" >&2; exit 2; }

METHOD=$1 PATH_=$2 BODY=${3:-} URL=${LAMBDA_URL:-http://localhost:9000/2015-03-31/functions/function/invocations}

python3 - "$METHOD" "$PATH_" "$BODY" "$URL" "$VERBOSE" <<'EOF'
import json, sys, urllib.request

method, path, body, url, verbose = sys.argv[1:]
if body:
    try:  # fail here, with a clear message, rather than inside the Lambda
        json.loads(body)
    except json.JSONDecodeError as exc:
        sys.exit(f"the body is not valid JSON: {exc}")
event = {"httpMethod": method.upper(), "path": path, "body": body or None}
request = urllib.request.Request(url, json.dumps(event).encode(), {"Content-Type": "application/json"})
try:
    response = json.loads(urllib.request.urlopen(request).read())
except OSError as exc:
    sys.exit(f"cannot reach the local Lambda at {url} ({exc}); is the container running?")
if "statusCode" not in response:
    sys.exit(f"the Lambda failed: {json.dumps(response)}")
if verbose == "1":
    print(f"HTTP {response['statusCode']}", file=sys.stderr)
    for key, value in response.get("headers", {}).items():
        print(f"{key}: {value}", file=sys.stderr)
sys.stdout.write(response["body"])
if not response["body"].endswith("\n"):
    sys.stdout.write("\n")
sys.exit(0 if response["statusCode"] < 400 else 1)
EOF
