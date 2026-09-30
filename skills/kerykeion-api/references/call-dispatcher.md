# `/call`: reaching any public factory

The curated paths cover the common work. `/call` covers **everything else**: it
dispatches to any name in `kerykeion.__all__`, so no library capability is out
of reach over HTTP. The target is the positional argument, `"args": [...]`.

## Safety model

The target must be a public name in `kerykeion.__all__`, split on one `.`.
Private members are refused, models and exceptions are not dispatchable, and
lookup never runs a descriptor. On top of that the API refuses any parameter
value that names a server path.

## Discovering targets

```bash
curl -s "$KERYKEION_API_URL/call" \
  -d '{"list": true, "f": "json"}' | jq -r '.[0:3][] | .owner'
curl -s "$KERYKEION_API_URL/call" \
  -d '{"args": ["ProfectionsFactory.from_subject"], "explain": true, "f": "json"}' | jq -r '.[].name'
```

`explain` classifies every parameter, which is what tells you how to pass it:

| class | meaning | how to pass it |
|---|---|---|
| `cli` | a scalar, enum, list or date | `"param": ["name=value"]` |
| `subject` | an `AstrologicalSubjectModel` | `"s"` (and `"S"` for a second), with `subjects` |
| `json-only` | a mapping or a nested model | `"param": ["name={\"k\": 1}"]`, or a file from `files` for a model |
| `unsupported` | a Protocol or similar | not reachable; use a curated path |

## Binding subjects and parameters

```bash
cat > subjects.json <<'EOF'
{"subjects": {"einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
                           "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"}}}
EOF
jq '. + {args: ["DominantsFactory.from_subject"], s: "einstein", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/call" -d @- | jq -r '.method'
jq '. + {args: ["MidpointFactory.compute"], s: "einstein", param: ["active_points=Sun,Moon"], f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/call" -d @- | jq 'length'
```

`param` is a list of `name=value` strings; each value is coerced by the
parameter's annotation:

- scalars: `int`, `float`, `bool` (`true/yes/1`, `false/no/0`), `str`
- `none` / `null` → `None` for a nullable parameter
- `datetime` / `date`: ISO
- `Literal`: membership, case-checked, with a suggestion on a near miss
- lists: a JSON array, or comma-separated text for scalar elements
- mappings: JSON, e.g. `"custom_weights={\"Sun\": 1.5}"`
- a Pydantic model: the name of a JSON file sent under `files`

An unknown `param` name is 400, so a typo cannot silently run the factory
with defaults.

## Two shapes of factory

Most targets are static or classmethods. Some need construction first
(`PlanetaryReturnFactory`, `TransitsTimeRangeFactory`,
`RelationshipScoreFactory`, `HouseComparisonFactory`,
`CompositeSubjectFactory`, `EphemerisDataFactory`, `HeliacalFactory`,
`OccultationFactory`); their constructor and method parameters share one flat
namespace, and `explain` shows the union.

## When to prefer a curated path

`/call` has no per-parameter help and no domain validation. If a curated path
exists for the job, it gives better errors and a shorter request. `/call` is
for the tail: a method with no command, or a parameter a command does not
expose.
