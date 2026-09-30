# Recipes

Task-shaped examples. They use one subjects file, kept on the client side:

```bash
cat > subjects.json <<'EOF'
{"subjects": {
  "einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
               "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"},
  "bob": {"name": "Bob", "date": "1985-06-01", "time": "09:30",
          "lat": 45.07, "lng": 7.69, "tz": "Europe/Rome"}
}}
EOF
curl -s "$KERYKEION_API_URL/" | jq -r '.version'
```

## A chart for a language model

`"f": "xml"` is the compact context shape (~5 KB instead of ~35 KB):

```bash
jq '. + {s: "einstein", f: "xml"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -d @- | head -3
```

## Save an SVG, and fail loudly on an error

`--fail` makes curl exit non-zero on an HTTP error instead of saving the JSON
error as `chart.svg`:

```bash
jq '. + {s: "einstein", S: "bob", f: "svg", theme: "dark"}' subjects.json \
  | curl -sS --fail "$KERYKEION_API_URL/synastry" -d @- -o synastry.svg
grep -c "<svg" synastry.svg
```

## Status, exit code and warnings of one request

```bash
jq '. + {s: "einstein", f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/natal" -d @- -D headers.txt -o chart.json
grep -i -E "^HTTP|x-kerykeion" headers.txt
jq -r '.sun.sign' chart.json
```

## Handle an error

Branch on the status (or on `exit_code` in the body), not on the message:

```bash
# gate: expect-error
status=$(curl -s "$KERYKEION_API_URL/natal" \
  -d '{"s": "nobody"}' -o error.json -w '%{http_code}')
echo "HTTP $status, exit $(jq -r '.exit_code' error.json): $(jq -r '.error' error.json)"
```

## Many subjects in a loop

One request per subject; the API has no batch endpoint, and each request is
small:

```bash
for name in einstein bob; do
  jq --arg n "$name" '{subjects: {($n): .subjects[$n]}, s: $n, f: "json"}' subjects.json \
    | curl -s "$KERYKEION_API_URL/natal" -d @- \
    | jq -r '"\(.name): Sun \(.sun.sign), Moon \(.moon.sign), Asc \(.ascendant.sign)"'
done
```

## This year's transits as events

```bash
jq '. + {s: "einstein", from: "2025-01-01", to: "2025-03-01", step_type: "days", events: true, f: "json"}' subjects.json \
  | curl -s "$KERYKEION_API_URL/transits" -d @- \
  | jq -r '.events[0:3][] | "\(.p1_name) \(.aspect) \(.p2_name)"'
```

## From Python

The same request with the standard library; no kerykeion install needed:

```python
import json, os, urllib.request

body = {
    "subjects": {"einstein": {"name": "Albert Einstein", "date": "1879-03-14", "time": "11:30",
                              "lat": 48.4011, "lng": 9.9876, "tz": "Europe/Berlin"}},
    "s": "einstein",
    "f": "json",
}
request = urllib.request.Request(
    os.environ["KERYKEION_API_URL"] + "/natal",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=35) as response:
        chart = json.load(response)
        print(chart["sun"]["sign"], response.headers.get("X-Kerykeion-Warnings", ""))
except urllib.error.HTTPError as error:
    detail = json.load(error)  # {"exit_code": ..., "error": ...}
    raise SystemExit(f"HTTP {error.code}: {detail}")
```
