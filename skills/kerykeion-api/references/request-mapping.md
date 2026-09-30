# Requests: path, body and subjects

## Routes

| Request | Does |
|---|---|
| `GET /` | the catalog: every command path with its flags, their kind and help (JSON) |
| `GET /<cmd>[/<sub>]` | the command's help text, exactly as `kerykeion <cmd> --help` |
| `POST /<cmd>[/<sub>]` | runs the command; the JSON body carries its flags |
| `POST /run` | `{"argv": [...]}`, a raw command line for anything the body cannot say |

A group path alone (`/sky`, `/technique`) is 400 and names its commands.

```bash
curl -s "$KERYKEION_API_URL/" -H "x-api-key: $KERYKEION_API_KEY" | jq '.commands | keys'
curl -s "$KERYKEION_API_URL/" -H "x-api-key: $KERYKEION_API_KEY" | jq '.commands["sky/eclipses"].flags[] | {key, kind}'
curl -s "$KERYKEION_API_URL/sky/eclipses" -H "x-api-key: $KERYKEION_API_KEY"
```

In the catalog, each flag has a `key` (the body key to use), its CLI `names`,
and a `kind`: `value` (one value), `list` (a JSON array, the flag repeated),
`flag` (`true` to set it) or `bool` (`true`/`false`, i.e. `--x`/`--no-x`).

## Body → command line

| Body | Command line |
|---|---|
| `"s": "ada"` | `-s ada` (a one-letter key is a short flag) |
| `"to_date": "2025-06-01"` | `--to-date 2025-06-01` (`_` becomes `-`) |
| `"offline": true` | `--offline` |
| `"zodiac_ring": false` | `--no-zodiac-ring` (a `bool` flag) |
| `"planets": ["Sun", "Moon"]` | `--planets Sun --planets Moon` (a `list` flag) |
| `"args": ["ada"]` | a positional argument |

Keys may also be spelled as the flag itself (`"--to-date"`). An unknown key is
400, listing every key the command accepts, so a typo never runs a command with
defaults. A list for a single-value flag, or a non-boolean for a switch, is 400
too.

Reserved keys that are not flags: `subjects`, `files`, `args`.

## Subjects: the API stores nothing

Each request runs in a fresh, empty directory that is deleted afterwards.
Profiles from an earlier request do not exist. Send them every time:

```bash
curl -s "$KERYKEION_API_URL/synastry" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "subjects": {
    "einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
                 "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"},
    "bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30",
            "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}
  },
  "s": "einstein", "S": "bob", "f": "xml"
}'
```

`"subjects"` maps a profile name to its recipe: the same fields as a profile
file saved by the CLI (`input` section). Unknown fields are 400.

| Field | Value |
|---|---|
| `name` | display name |
| `date` | `YYYY-MM-DD` (a negative year is BCE) |
| `time` | `HH:MM` or `HH:MM:SS` |
| `lat`, `lng` | decimal degrees; south and west are negative |
| `tz` (or `tz_str`) | IANA zone, e.g. `Europe/Berlin` |
| `city`, `nation` | a place to look up online **instead of** `lat`/`lng`/`tz`; both together is 400 |
| `online` | `false` never touches the network |
| `iso_utc_time`, `mode` | a UTC instant instead of `date`+`time` (`mode: "iso_utc"`) |
| `altitude`, `is_dst` | metres; the DST fold for an ambiguous local time |
| `zodiac_type`, `sidereal_mode` | `Tropical`/`Sidereal`, e.g. `LAHIRI` |
| `houses_system_identifier` | a house letter, e.g. `P`, `W` (case-sensitive) |
| `perspective_type` | e.g. `Apparent Geocentric`, `Heliocentric` |
| `active_points`, `active_fixed_stars` | lists of names |
| `calculate_lunar_phase`, `calculate_dignities`, `calculate_nakshatra`, `calculate_gauquelin`, `calculate_nutation`, `calculate_local_space` | booleans |
| `custom_ayanamsa_t0`, `custom_ayanamsa_ayan_t0` | a custom ayanamsa |
| `extra` | other factory parameters, as the CLI's `--set` would store them |

A sidereal, whole-sign subject:

```bash
curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "subjects": {"sid": {"name": "Sid", "date": "1990-03-21", "time": "06:00", "lat": 19.07, "lng": 72.88,
                       "tz": "Asia/Kolkata", "zodiac_type": "Sidereal", "sidereal_mode": "LAHIRI",
                       "houses_system_identifier": "W"}},
  "s": "sid", "f": "xml"
}'
```

Profile names use ASCII letters, digits, spaces, `_`, `-` and `.`.

`subject show`, `subject verify` and `subject list` work on the request's own
subjects. `verify` is the cheap check that a recipe builds:

```bash
curl -s "$KERYKEION_API_URL/subject/verify" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "subjects": {"bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30", "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}},
  "args": ["bob"]
}'
```

`subject save` is refused (400); there is nowhere to save to.

### `natal` without `subjects`

`natal` (and `now`) also take the subject as flags, like the CLI:

```bash
curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "name": "Bob", "date": "1985-06-01", "time": "09:30", "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome",
  "offline": true, "f": "xml"
}'
```

Inline flags override a profile's fields, so `"s"` plus `"lat"`/`"lng"`/`"tz"`
re-reads a subject at another place.

## Files: JSON inputs a flag expects as a path

Some flags take a JSON file (`chart_settings`, a model parameter of `call`).
Send the content under `"files"` and name it in the flag; the file exists in
the request's working directory only:

```bash
curl -s "$KERYKEION_API_URL/natal" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "subjects": {"bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30", "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}},
  "s": "bob", "f": "svg",
  "files": {"palette.json": {"colors_settings": {"paper_0": "#101010"}}},
  "chart_settings": "palette.json"
}'
```

File names are plain names ending in `.json`. Any value that points at a path
on the server (`/…`, `~…`, `..`) is 400.

## `POST /run`

The whole command line as a list, for the rare case the body mapping cannot
express. `subjects` and `files` work the same way:

```bash
curl -s "$KERYKEION_API_URL/run" -H "x-api-key: $KERYKEION_API_KEY" -d '{
  "argv": ["aspects", "-s", "bob", "--aspects", "trine:6,square", "-f", "json"],
  "subjects": {"bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30", "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}}
}'
```

`-o/--output` is refused here too.
