# Responses, errors and limits

## A successful response

The body is exactly what the CLI prints on stdout; `Content-Type` follows `"f"`:

| `"f"` | Content-Type | Body |
|---|---|---|
| `json` (default) | `application/json` | the library's full model |
| `xml` | `application/xml` | `to_context()`: the same chart in ~5 KB, for a context window |
| `svg` | `image/svg+xml` | the chart; only chart commands produce one |
| `text` | `text/plain` | the ASCII report |

Headers:

| Header | Content |
|---|---|
| `X-Kerykeion-Exit-Code` | the CLI exit code (`0` on success) |
| `X-Kerykeion-Warnings` | the CLI's stderr on one line (lines joined by ` \| `), up to 4000 characters; absent when there were none |

Warnings are ephemeris precision notes and house-system fallbacks (a polar
latitude). They do not change the status. Read them with `curl -i` or `-D -`.

### `"envelope": true`

When a client cannot read headers, the envelope puts provenance and the
warnings into the JSON body. It needs JSON output:

```bash
curl -s "$KERYKEION_API_URL/natal" -d '{
  "subjects": {"bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30", "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}},
  "s": "bob", "f": "json", "envelope": true
}' | jq '{kerykeion, warnings, sun: .data.sun.sign}'
```

`"warnings_as_errors"` is not available as a body key (it is a global CLI
flag); use `/run` with `"argv": ["--warnings-as-errors", "natal", ...]` to
turn any warning into a 422.

## An error response

Every error is JSON, whatever `"f"` asked for:

```json
{"exit_code": 4, "error": "kerykeion: error: No profile named 'ada' and no such file."}
```

| Status | `exit_code` | Meaning |
|---|---|---|
| 400 | 2 | the command line did not parse (the `error` includes the usage) |
| 400 | 4 | invalid input, an unknown flag or command, a refused option, bad JSON |
| 413 | 8 | the series exceeds the sampling ceiling, or the response exceeds 6 MB |
| 422 | 5 | kerykeion rejected the request (astrological/domain error) |
| 422 | 6 | ephemeris problem: the date is outside the deployed data |
| 422 | 9 | a warning with `--warnings-as-errors` |
| 500 | 1 | unexpected error (a bug) |
| 502 | 7 | network error (GeoNames unreachable) |

Returned by API Gateway, before kerykeion runs, with its own body
(`{"message": ...}`): 429 (the rate limit, shared by all callers), 504 (the
request took longer than 29 s).

## Limits

| Limit | Value | When you hit it |
|---|---|---|
| request time | 29 s (API Gateway) | a long range or fine step: split it |
| response size | 6 MB (Lambda) | 413; narrow the range, widen the step or use `"f": "xml"` |
| rate | default 10 requests/minute, burst 10, for all callers together | 429; wait a few seconds and retry |
| concurrency | default 15 requests computed at once | rarely reached; the rate limit comes first |
| ephemeris | 1550–2650 (default image) | 422 with exit 6 |

A cold start (the first request after a pause) takes a few seconds; after that a
chart takes tens of milliseconds.
