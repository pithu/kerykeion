# Rendering: SVG appearance and text reports

These keys work on every chart path: `/natal`, `/now`, `/synastry`,
`/transit`, `/composite`, `/return`, `/progression` and
`/technique/relocate`. Ask for the SVG with `"f": "svg"`; the response body is
the SVG document (`Content-Type: image/svg+xml`), so save it on your side.

```bash
cat > subjects.json <<'EOF'
{"subjects": {"einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
                           "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"}}}
EOF
jq '. + {s: "einstein", f: "svg"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d @- -o einstein.svg
head -c 5 einstein.svg
```

## SVG appearance

| Key | Values / effect |
|---|---|
| `theme` | `classic`, `dark`, `black-and-white` (`/info/methods` lists the current set) |
| `chart_language` | `EN FR PT IT CN ES RU TR DE HI` |
| `style` | `classic` or `modern` (default) |
| `custom_title` | replaces the title line |
| `padding` | outer padding in SVG units |
| `transparent_background` | `true` omits the background rectangle |
| `auto_size` | `true`/`false`: fit the viewBox to the content (default on) |
| `zodiac_ring` | `true`/`false`: the zodiac background ring (default on) |
| `diurnality` | `true`/`false`: day/night sect marking (default on) |
| `house_position_comparison` | `true`/`false`, dual wheels only (default on) |
| `cusp_position_comparison` | `true`, dual wheels only |
| `aspect_grid_type` | `list` or `table`, dual wheels only |
| `svg_variant` | `full` (default), `wheel`, `aspect-grid` |

```bash
jq '. + {s: "einstein", f: "svg", theme: "dark", chart_language: "DE", svg_variant: "wheel"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d @- -o dark-wheel.svg
jq '. + {s: "einstein", f: "svg", transparent_background: true, zodiac_ring: false}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d @- -o plain.svg
grep -c "<svg" dark-wheel.svg plain.svg
```

Values are case-insensitive, and an unknown one is 400 listing the valid set.

`external_view`, `degree_indicators` and `aspect_icons` apply to
`"style": "classic"` only; under the default modern style they do nothing.
Only `external_view` says so (in `X-Kerykeion-Warnings`); the other two are
ignored silently, so send `"style": "classic"` with them.

## Structural settings: `chart_settings`

Palettes, point tables, aspect tables and language packs are too large for
keys. `chart_settings` names a JSON file; send its content under `files`:

```bash
jq '. + {s: "einstein", f: "svg",
         files: {"palette.json": {colors_settings: {paper_0: "#101010"}}},
         chart_settings: "palette.json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d @- | grep -c "#101010"
```

The sections are `colors_settings`, `celestial_points_settings`,
`aspects_settings` and `language_pack`. Mapping sections are merged over the
library defaults, so one colour is enough; list sections (points, aspects)
replace the default list.

## Text reports

`"f": "text"` returns the ASCII report (`text/plain`):

```bash
jq '. + {s: "einstein", f: "text"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d @- | head -5
```

On reports with an aspects section (the chart paths), `no_aspects: true` drops
it and `max_aspects: N` keeps the N tightest.
