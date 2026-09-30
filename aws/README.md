# kerykeion REST API on AWS Lambda

The `kerykeion` command line as an HTTP API: an AWS Lambda container image
behind an API Gateway REST API, secured with an API key. Every CLI command is
an endpoint; the handler hands the request to the CLI's own dispatcher, so a
command added to the CLI appears in the API without changes here.

| File | What it is |
|---|---|
| `handler/kerykeion_api.py` | The Lambda handler (request → argv → `kerykeion_cli.app.run`) |
| `Dockerfile` | The Lambda image; build context is the repository root |
| `template.yaml` | CloudFormation: Lambda, REST API, API key, usage plan, logs |
| `deploy.sh` | Build → push to ECR → deploy the stack → print URL and key |
| `local-invoke.sh` | Call the image running locally as API Gateway would |

Tests: `tests/aws/test_handler.py` (no AWS needed).

## The API

| Request | Result |
|---|---|
| `GET /` | the command catalog: every command, its flags and help (JSON) |
| `GET /<cmd>[/<sub>]` | the command's `--help` (text) |
| `POST /<cmd>[/<sub>]` | runs the command; the JSON body carries the flags |
| `POST /run` | `{"argv": ["natal", "-s", "ada"]}`, the raw command line |

Body → command line: `"s": "ada"` → `-s ada`, `"houses": "placidus"` →
`--houses placidus`, `true` → a bare switch, `false` → `--no-x` where the
command has one, a list → the flag repeated, `"args": [...]` → positionals.

The API is **stateless**. Send subjects inline under `"subjects"`; they exist
for that one request, so `-s`/`-S` work as on the command line:

```bash
curl -s "$KERYKEION_API_URL/synastry" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "subjects": {
    "ada": {"name": "Ada", "date": "1815-12-10", "time": "18:00", "lat": 51.5074, "lng": -0.1278, "tz": "Europe/London"},
    "bob": {"name": "Bob", "date": "1990-07-15", "time": "10:30", "lat": 41.9, "lng": 12.5, "tz": "Europe/Rome"}
  },
  "s": "ada", "S": "bob", "f": "svg"
}' > synastry.svg
```

The response body is the CLI's stdout: `Content-Type` follows `f`
(json/svg/xml/text; JSON by default). Warnings go to the `X-Kerykeion-Warnings`
header (or into the body with `"envelope": true`), and the CLI exit code to
`X-Kerykeion-Exit-Code`.

| Exit code | HTTP | Meaning |
|---|---|---|
| 0 | 200 | success |
| 2, 4 | 400 | bad request: unknown flag or command, invalid input |
| 5, 6, 9 | 422 | kerykeion rejected it (e.g. a date outside the ephemeris) |
| 8 | 413 | sampling ceiling, or the response exceeds Lambda's 6 MB |
| 7 | 502 | network (GeoNames); prefer coordinates over `city` |
| 1 | 500 | unexpected error (a bug; see the logs) |

Refused with 400: `-o/--output`, `subject save`, and any value that names a
path on the server.

Limits: 29 s per request (API Gateway), 6 MB per response (Lambda), the usage
plan's rate and monthly quota (template parameters).

## Try it locally

Docker only; no AWS account needed.

```bash
docker buildx build --platform linux/arm64 -f aws/Dockerfile -t kerykeion-api:dev --load .
docker run --rm -d --name kapi --read-only --tmpfs /tmp -p 9000:8080 kerykeion-api:dev

aws/local-invoke.sh GET / | jq '.commands | keys'
aws/local-invoke.sh -v POST /natal '{"subjects":{"ada":{"name":"Ada","date":"1815-12-10","time":"18:00","lat":51.5,"lng":-0.12,"tz":"Europe/London"}},"s":"ada","f":"xml"}'

docker stop kapi
```

`--read-only --tmpfs /tmp` mirrors Lambda, where only `/tmp` is writable.

## Deploy

Prerequisites: Docker (Docker Desktop, OrbStack or Rancher Desktop), the AWS
CLI v2 and working credentials (`aws sts get-caller-identity` must succeed).

```bash
export AWS_PROFILE=kerykeion     # optional
export AWS_REGION=eu-central-1   # optional, defaults to the profile's region
aws/deploy.sh
```

The script creates the ECR repository on first run, builds and pushes the
image tagged with the git commit, deploys the `kerykeion-api` stack and prints
`KERYKEION_API_URL` and `KERYKEION_API_KEY`. Run it again to ship a new
version. `STACK_NAME` changes the name; `EPHEMERIS_TIER=base` builds a smaller
image that only covers 1850–2150.

Remove everything:

```bash
aws/deploy.sh --destroy
```

## Ephemeris data

The kerykeion wheel bundles only the *base* tier (1850–2150), and kerykeion
runs libephemeris in sealed mode, so it never downloads at runtime. The image
therefore bakes the *medium* tier (1550–2650, ~90 MB) in at build time;
`--build-arg EPHEMERIS_TIER=base` skips it.

## Costs

With light use this stays within or near the AWS free tier. The costs are
Lambda (per request and GB-second; a warm chart takes ~20 ms), API Gateway (per
million requests), ECR storage (~0.5 GB per image; the repository keeps the last
10) and CloudWatch Logs (kept 14 days).
