# The endpoints

Every CLI command is a path: `kerykeion sky eclipses` is `POST /sky/eclipses`.
`GET /` lists them all with their flags; `GET /<path>` returns one command's
help. The flag keys below are the body keys (see
[request-mapping.md](request-mapping.md)).

The examples share one subjects file, sent with each request, which is how a
client of this stateless API works in practice: keep the subjects on your side
and merge the flags in.

```bash
cat > subjects.json <<'EOF'
{"subjects": {
  "einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
               "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"},
  "bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30",
          "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}
}}
EOF
jq '. + {s: "einstein", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -d @- | jq -r '.sun.sign'
```

## Charts

| Path | Needs | Notes |
|---|---|---|
| `/natal` | `s`, or the inline subject flags | the only command with the full inline flag set |
| `/now` | a place (`lat`, `lng`, `tz`) | a chart for the current moment |
| `/synastry` | `s` and `S` | dual wheel |
| `/transit` | `s` | now at the natal place by default; `to_date` **and** `to_time` for a moment |
| `/composite` | `s` and `S` | midpoint composite |
| `/return` | `s`, `year` | `type`: `Solar` or `Lunar` |
| `/progression` | `s`, `target_year` | secondary progression |

```bash
jq '. + {s: "einstein", S: "bob", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/synastry" -d @- | jq '.aspects | length'
jq '. + {s: "einstein", to_date: "2025-06-01", to_time: "12:00", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/transit" -d @- | jq -r '.chart_type'
jq '. + {s: "einstein", type: "Solar", year: 2025, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/return" -d @- | jq -r '.chart_type'
jq '. + {s: "einstein", target_year: 2026, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/progression" -d @- | jq -r '.chart_type'
curl -s "$KERYKEION_API_URL/now" \
  -d '{"lat": 52.52, "lng": 13.405, "tz": "Europe/Berlin", "offline": true, "f": "json"}' | jq -r '.moon.sign'
```

A **relocated** transit or return needs `lat`, `lng` and `tz` together; two of
the three is 400, because the natal timezone at new coordinates is a multi-hour
error in the houses and Ascendant.

## Analyses

```bash
jq '. + {s: "einstein", S: "bob", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/aspects" -d @- | jq '.aspects | length'
jq '. + {s: "einstein", declinations: true, orb: 1.0, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/aspects" -d @- | jq 'length'
jq '. + {s: "einstein", method: "almuten_figuris", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/dominants" -d @- | jq 'keys | length'
jq '. + {s: "einstein", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/moon" -d @- | jq -r '.moon.phase_name'
jq '. + {s: "einstein", S: "bob", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/relationship-score" -d @- | jq -r '.score_description'
```

`aspects` takes names or `name:orb` pairs as a list:
`"aspects": ["trine:6", "square"]`. Declination aspects use a single `orb`
instead and refuse `aspects`.

## `/technique/<sub>`: analytical techniques on a subject

`profections`, `firdaria`, `zr`, `receptions`, `horary`, `midpoints`,
`directions`, `acg`, `heliacal`, `nodes`, `relocate`, `house-comparison`,
`solar-arc`, `fixed-stars`.

```bash
jq '. + {s: "einstein", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/technique/profections" -d @- | jq -r '.current.house'
jq '. + {s: "einstein", lot: "fortune", levels: 2, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/technique/zr" -d @- | jq 'keys | length'
jq '. + {s: "einstein", target_year: 2026, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/technique/solar-arc" -d @- | jq 'keys | length'
jq '. + {s: "einstein", orb: 1.5, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/technique/fixed-stars" -d @- | jq 'length'
jq '. + {s: "einstein", S: "bob", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/technique/house-comparison" -d @- | jq 'keys | length'
```

Enum-style values (`lot`, `rate`, `method`, `type`) are case-insensitive;
`POST /info/methods` lists what each accepts.

## `/sky/<sub>`: events, with or without a subject

Moment commands (`sun-times`, `hours`) need a place: `s`, or `lat`/`lng`/`tz`.
Range commands (`lunations`, `ingresses`, `stations`, `mundane`) need `from` and
`to` and no place. `voc` does both; `eclipses` takes a year and an optional
place; `phenomena` and `occultations` take `s`.

```bash
jq '. + {s: "einstein", from: "2025-06-01", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/sky/sun-times" -d @- | jq -r '.sunrise'
jq '. + {s: "einstein", from: "2025-06-01T12:00", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/sky/hours" -d @- | jq -r '.day_ruler'
curl -s "$KERYKEION_API_URL/sky/lunations" \
  -d '{"from": "2025-01-01", "to": "2025-03-01", "f": "json"}' | jq '.lunations | length'
curl -s "$KERYKEION_API_URL/sky/ingresses" \
  -d '{"from": "2025-01-01", "to": "2025-06-01", "periods": true, "f": "json"}' | jq '.periods | length'
curl -s "$KERYKEION_API_URL/sky/stations" \
  -d '{"from": "2025-01-01", "to": "2025-06-01", "f": "json"}' | jq 'length'
curl -s "$KERYKEION_API_URL/sky/eclipses" \
  -d '{"start_year": 2027, "count": 2, "f": "json"}' | jq -r '.solar_eclipses[0].datestamp'
curl -s "$KERYKEION_API_URL/sky/voc" \
  -d '{"from": "2025-01-01", "to": "2025-01-10", "tz": "UTC", "f": "json"}' | jq '.windows | length'
jq '. + {s: "einstein", planet: "Venus", count: 2, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/sky/occultations" -d @- | jq 'length'
```

`periods: true` on `ingresses` and `stations` reports spans instead of events.
`voc` ranges are UTC unless `tz` is given. `occultations` searches forward from
the subject's moment and requires `planet`: the Moon is the occulter.

## Time series

```bash
curl -s "$KERYKEION_API_URL/ephemeris" \
  -d '{"from": "2025-01-01", "to": "2025-01-05", "step_type": "days", "step": 1,
       "lat": 45.0, "lng": 9.0, "tz": "Europe/Rome", "f": "json"}' | jq 'length'
jq '. + {s: "einstein", from: "2025-01-01", to: "2025-02-01", step_type: "days", events: true, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/transits" -d @- | jq '.events | length'
```

Both refuse a request over the sampling ceiling (413) before computing
anything; `no_limit: true` lifts the ceiling but not the 29 s and 6 MB limits of
the API, so prefer a coarser `step`. `events: true` collapses the series into
applying → exact → separating events; `refine: true` requires it.

## Subjects, info, diagnostics

| Path | Does |
|---|---|
| `/subject/show`, `/subject/verify` | the request's own subject (`"args": ["name"]`) |
| `/subject/list` | the names in the request's `subjects` |
| `/info/literals`, `/info/points`, `/info/stars`, `/info/houses`, `/info/methods` | the valid values |
| `/status` | the deployed version, backend and ephemeris coverage |
| `/call` | any public factory: [call-dispatcher.md](call-dispatcher.md) |

```bash
curl -s "$KERYKEION_API_URL/status" -d '{"f": "json"}' | jq -r '.kerykeion_version'
```
