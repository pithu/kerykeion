---
name: kerykeion-api
description: >-
  Call kerykeion over HTTP: the kerykeion astrology CLI as a REST API
  (AWS Lambda). Use this skill WHENEVER astrology
  must be computed through the kerykeion REST API, KERYKEION_API_URL
  is set, or the task says kerykeion plus API, REST, HTTP, GET URL,
  endpoint, curl, webhook, Lambda or "without installing Python": natal /
  synastry / transit / composite / return / progression charts and SVG wheels;
  aspects, dominants, moon phase, relationship score; profections, firdaria,
  zodiacal releasing, horary, directions, solar arc, astrocartography, fixed
  stars; eclipses, lunations, ingresses, stations, void-of-course Moon, sun
  times, planetary hours, mundane aspects; ephemeris and transit series; the
  `call` dispatcher over the public API. Covers the path + JSON-body request
  shape, inline subjects (the API is stateless), HTTP status vs. CLI exit
  codes, response limits and the traps that silently produce a wrong chart.
  For a local shell use `kerykeion-cli`; for Python use `kerykeion`.
license: AGPL-3.0
---

# Driving Kerykeion over HTTP

Verified against **kerykeion v6** and the handler in `aws/handler/kerykeion_api.py`.

The API is the `kerykeion` command line behind HTTP: **the URL path is the
command, the JSON body holds the flags**. Anything the CLI can compute, the
API can too, with the same flag names and values.

**Accuracy rule for you:** never invent a command, flag or value. The API
describes itself: `GET /` lists every command with its flags, `GET /<command>`
returns its help, and `POST /info/literals` lists every accepted value. Read
them instead of guessing.

## Setup

One environment variable, printed by `aws/deploy.sh` when the API is
deployed. The API is open: no key, no header.

```bash
# gate: skip
export KERYKEION_API_URL=https://abc123.execute-api.eu-central-1.amazonaws.com/v1
```

```bash
curl -s "$KERYKEION_API_URL/"
curl -s "$KERYKEION_API_URL/natal"
```

## The request in one example

```bash
curl -s "$KERYKEION_API_URL/natal" -d '{
  "subjects": {"einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
                            "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"}},
  "s": "einstein",
  "f": "json"
}'
```

- `POST /natal` runs `kerykeion natal`; `POST /sky/eclipses` runs
  `kerykeion sky eclipses`.
- Body keys become flags: `"s"` → `-s`, `"to_date"` → `--to-date`. `true`
  is a bare switch, a list repeats the flag, `"args"` are positionals.
- **The API is stateless.** Nothing is stored between requests. Send every
  subject in `"subjects"` on every request; it is a profile for that request
  only, so `-s`/`-S` work as on the command line.

### GET: when all you can do is fetch a URL

The same commands run from a plain URL, for agents that cannot send a body.
The query string maps like the body; `p1_<field>` and `p2_<field>` describe
up to two people, bound to `-s` and `-S`:

```bash
curl -sG "$KERYKEION_API_URL/natal" \
  --data-urlencode "p1_name=Albert Einstein" --data-urlencode "p1_date=1879-03-14" \
  --data-urlencode "p1_time=11:30" --data-urlencode "p1_lat=48.4011" \
  --data-urlencode "p1_lng=9.9876" --data-urlencode "p1_tz=Europe/Berlin" \
  --data-urlencode "f=xml" | head -3
```

That is `…/natal?p1_name=Albert%20Einstein&p1_date=1879-03-14&p1_time=11:30&…&f=xml`.
A GET without a query string returns the command's help instead.

Full mapping rules and the subject fields:
[references/request-mapping.md](references/request-mapping.md).

## The rules that keep results usable

1. **Check the HTTP status first.** 200 means the body is the payload; anything
   else is an error, as `{"exit_code", "error"}` for JSON. Branch on the status
   or on `exit_code`, not on the message.
2. **Pass `"f"` explicitly.** Without it the answer is an HTML page of tables,
   made for agents that fetch URLs. A program wants `"f": "json"` (the full
   model, ~35 KB for a natal chart); `"f": "xml"` is the same chart in ~5 KB,
   the shape to read into a context window; `"f": "yaml"` is the JSON as YAML.
3. **Warnings are not in a JSON body** unless you ask: they arrive in the
   `X-Kerykeion-Warnings` header. `"envelope": true` puts them, with
   provenance, into the JSON itself; use it when you cannot read headers.

## HTTP status

| Status | CLI exit | Meaning | What to do |
|---|---|---|---|
| 200 | 0 | success | — |
| 400 | 2, 4 | unknown command or flag, invalid input, refused option | fix the field the `error` names |
| 413 | 8 | sampling ceiling, or response over 6 MB | narrow the range, widen the step, use `"f": "xml"` |
| 422 | 5, 6, 9 | kerykeion rejected it; date outside the ephemeris | astrological/domain error; read `error` |
| 429 | — | the API's rate limit (shared by all callers) | wait a few seconds and retry |
| 500 | 1 | unexpected error | a bug; report it |
| 502 | 7 | network (GeoNames) | give coordinates instead of a city |
| 504 | — | over 29 s | split the request |

Response headers, formats and limits:
[references/responses-and-errors.md](references/responses-and-errors.md).

## Capability routing

| You want | Path | Reference |
|---|---|---|
| natal, synastry, transit, composite, return, progression | `/natal`, `/synastry`, … | [endpoints.md](references/endpoints.md) |
| an SVG with a theme, a language, a variant | any chart path, `"f": "svg"` | [rendering.md](references/rendering.md) |
| aspects, dominants, moon phase, relationship score | `/aspects`, `/dominants`, `/moon`, `/relationship-score` | [endpoints.md](references/endpoints.md) |
| profections, firdaria, ZR, horary, directions, solar arc, ACG, relocation | `/technique/<sub>` | [endpoints.md](references/endpoints.md) |
| eclipses, lunations, ingresses, stations, VoC, hours, sun times, mundane, phenomena, occultations | `/sky/<sub>` | [endpoints.md](references/endpoints.md) |
| a time series of positions, or transits over a range | `/ephemeris`, `/transits` | [endpoints.md](references/endpoints.md) |
| something with no curated command | `/call` | [call-dispatcher.md](references/call-dispatcher.md) |
| the valid values for a flag | `/info/<sub>` | [request-mapping.md](references/request-mapping.md) |
| task-shaped examples (save an SVG, batch, Python) | — | [recipes.md](references/recipes.md) |

## Top traps

These produce a **wrong chart silently** or a confusing failure.

- **A subject without `lat`, `lng` and `tz` is looked up online** (GeoNames), which
  can fail (502) or pick the wrong town. Always send all three, and
  `"offline": true` for inline natal flags. Sending `city` **and** coordinates is
  400: one request, one place, never a silent pick between the two.
- **`tz` is an IANA zone** (`Europe/Berlin`), not an offset. A wrong zone moves
  every house and the Ascendant by hours.
- **House-system letters are case-sensitive.** `i` and `I` are different
  systems. In a subject recipe use `houses_system_identifier`; on a command use
  `"houses": "placidus"` (a name is safer than a letter).
- **A relocated `transit`/`return` needs `lat`, `lng` and `tz` together** (or
  `city`); two of three is 400.
- **Dates outside the deployed ephemeris are 422.** The default image covers
  1550–2650; one built with `EPHEMERIS_TIER=base` only 1850–2150.
- **`sky/voc` ranges are UTC** unless you pass `"tz"`.
- **`transits` with `"refine": true` needs `"events": true`**, and a long series
  hits the sampling ceiling (413) before computing anything.
- **`-o/--output`, `subject save` and any value that names a server path are
  refused** (400). The payload is the response body; save it on your side.
- **`degree_indicators`, `aspect_icons` and `external_view` only apply to
  `"style": "classic"`.** Under the default modern style they do nothing, and
  only `external_view` warns about it.

## Never invent values

```bash
curl -s "$KERYKEION_API_URL/info/literals" -d '{}'
curl -s "$KERYKEION_API_URL/info/houses" -d '{}'
curl -s "$KERYKEION_API_URL/info/points" -d '{}'
curl -s "$KERYKEION_API_URL/info/methods" -d '{}'
```

## Reference index

- [references/request-mapping.md](references/request-mapping.md): body → flags,
  `subjects`, `files`, `args`, `/run`, the catalog.
- [references/responses-and-errors.md](references/responses-and-errors.md):
  content types, headers, `envelope`, status codes, limits.
- [references/endpoints.md](references/endpoints.md): every command path and
  what it needs.
- [references/rendering.md](references/rendering.md): SVG appearance and text
  report options.
- [references/call-dispatcher.md](references/call-dispatcher.md): reaching any
  public factory through `/call`.
- [references/recipes.md](references/recipes.md): task-shaped examples with
  curl, jq and Python.

For the same commands in a local shell, use the **`kerykeion-cli`** skill; for
Python against the library, the **`kerykeion`** skill.
