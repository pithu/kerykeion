# Kerykeion CLI als AWS-Lambda-REST-API + Skill `kerykeion-api`

## Context
Die `kerykeion`-CLI (`cli/kerykeion_cli`, ~50 Kommandos) soll als REST-API auf AWS laufen (Lambda + API Gateway, per CloudFormation deployt), damit Agents/Skripte Astrologie-Berechnungen ohne lokale Installation per HTTP abrufen können. Danach kommt ein Skill `skills/kerykeion-api/`, analog zu `skills/kerykeion-cli/`.

Getroffene Entscheidungen: **stateless** (kein dauerhafter Profilspeicher), **API-Key**-Auth, **lokaler Build mit Docker + AWS CLI**, REST-Stil **Pfad = Kommando, JSON-Body = Flags**.

## Kernidee
Die ~50 Kommandos werden nicht nachgebaut. Der Lambda-Handler übersetzt den HTTP-Request in eine argv-Liste und ruft den bestehenden Dispatcher `kerykeion_cli.app.run(argv)` auf (`cli/kerykeion_cli/app.py:71`). stdout/stderr werden in Buffer umgeleitet. Neue CLI-Kommandos sind damit automatisch in der API verfügbar.

Wichtige Befunde aus dem Code:
- `kerykeion_cli.main()` darf **nicht** verwendet werden: `errors.handle_uncaught` macht `os.dup2(devnull, stdout)` (`cli/kerykeion_cli/errors.py:144`) und ruft exit auf. Das würde die Logs eines warmen Lambda-Containers dauerhaft kaputtmachen. Der Handler ruft stattdessen `run()` auf, fängt Exceptions selbst ab und nutzt `errors.classify(exc)` sowie `errors._clean_message(exc)` für Exit-Code und Fehlermeldung.
- argparse-Fehler werfen `SystemExit(2)` und `--help` wirft `SystemExit(0)`. Beides wird abgefangen.
- `synastry`, `transit`, `composite`, `return` und die Techniken brauchen **gespeicherte** Profile (`_stored_subject`, `cli/kerykeion_cli/commands/_shared.py:29`). Stateless heißt deshalb: Der Request bringt die Subjects inline mit. Der Handler schreibt sie pro Request in ein temporäres `XDG_CONFIG_HOME` unter `/tmp` (Format `ProfileInput`, `cli/kerykeion_cli/profiles.py:60`, über die bestehenden Schreibfunktionen der profiles.py) und löscht das Verzeichnis danach wieder. Dauerhaft wird nichts gespeichert.
- Das libephemeris-Paket bringt nur die Ephemeriden-Stufe „base“ mit (`base_core.leb2`, 1850–2150). Ältere Geburtsdaten (z.B. 1815) scheitern damit. kerykeion läuft im „sealed“-Modus und lädt zur Laufzeit nichts nach. Deshalb legt der Image-Build die Stufe „medium“ (1550–2650, ~90 MB) über `libephemeris.download.download_leb2_for_tier` in `LIBEPHEMERIS_DATA_DIR` ab. Umgesetzt wird das Lambda als **Container-Image**, nicht als ZIP.

## Neue Dateien (Top-Level-Ordner `aws/`)
```
aws/
  Dockerfile            # public.ecr.aws/lambda/python:3.13 (arm64); pip install ./ ./cli; medium-Ephemeriden einbacken
  handler/kerykeion_api.py   # Lambda-Handler
  template.yaml         # CloudFormation
  deploy.sh             # ECR-Repo sicherstellen → docker build/push → cloudformation deploy → URL + Key ausgeben
  README.md             # Voraussetzungen (Docker/OrbStack, AWS CLI, aws configure), Deploy, Kosten, Löschen
tests/aws/test_handler.py    # Unit-Tests des Handlers, laufen lokal ohne AWS
```

### Handler (`aws/handler/kerykeion_api.py`)
- **Routing** (API Gateway Proxy, `/{proxy+}`):
  - `GET /` gibt den Kommando-Katalog als JSON zurück (aus `app.build_parser()` introspektiert: Kommandos, Gruppen, Flags mit Hilfetexten). Er dient Agents als Quelle, damit sie keine Flags erfinden.
  - `GET /<cmd>[/<sub>]` gibt den Hilfetext des Kommandos zurück (`--help`, abgefangenes SystemExit(0)).
  - `POST /<cmd>[/<sub>]` führt das Kommando aus, z.B. `POST /natal`, `POST /sky/eclipses`, `POST /technique/profections`, `POST /info/literals`, `POST /call`.
  - `POST /run` mit `{"argv": [...]}` ist der Rohzugang für Sonderfälle.
- **Body → argv** (generisch):
  ```json
  { "subjects": { "ada": {"name":"Ada","date":"1815-12-10","time":"18:00","lat":51.5,"lng":-0.13,"tz_str":"Europe/London"} },
    "args": ["positional"],
    "s": "ada", "f": "json", "houses": "placidus", "points": ["Sun","Moon"], "offline": true, "envelope": true }
  ```
  Regeln: Schlüssel mit 1 Zeichen wird `-k`, sonst `--key` (mit `_`→`-`). `true` wird ein bloßes Flag, `false` wird `--no-key` (falls vorhanden) oder weggelassen. Eine Liste wird zum wiederholten Flag. `args` wird an Positionalparameter angehängt. `subjects` wird ins temporäre Profil-Store geschrieben.
- **Gesperrt** (Antwort 400): `-o/--output` (kein Dateisystem) und schreibende `subject`-Unterkommandos (save/delete/…, die exakten Namen stehen in `commands/subject.py` `COMMANDS`). `--traceback` wird nur geloggt, nicht zurückgegeben.
- **Standardformat json**: Env `KERYKEION_CLI_FORMAT=json`, siehe `rendering.resolve_format`.
- **Antwort**: Content-Type richtet sich nach `f`: json→`application/json`, svg→`image/svg+xml`, xml→`application/xml`, text→`text/plain`. stderr (Warnungen) kommt gekürzt in den Header `X-Kerykeion-Warnings`. Empfohlen wird `envelope: true`. Der Exit-Code kommt in `X-Kerykeion-Exit-Code`.
- **Exit-Code → HTTP-Status**: 0→200, 2/4→400, 5/6/9→422, 8→413, 7→502, 1→500. Der Fehlerbody ist `{"exit_code", "error"}`.

### Dockerfile
- Base `public.ecr.aws/lambda/python:3.12`, Plattform `linux/arm64` (günstiger, passt zu Apple Silicon).
- `pip install /src /src/cli` aus dem Repo (Build-Kontext = Repo-Root, mit `.dockerignore` für `.venv`, `site`, `docs`, `tests`, `*.json`).
- `ENV LIBEPHEMERIS_DATA_DIR=/opt/libephemeris`, `LIBEPHEMERIS_PRECISION=<tier>` und `HOME=/tmp`. Der Build-Arg `EPHEMERIS_TIER` ist standardmäßig `medium`; mit `base` wird nichts heruntergeladen. Bereits verifiziert: Der Container läuft mit `--read-only` und nur `/tmp` beschreibbar, und es gibt keine Laufzeit-Downloads.
- `CMD ["kerykeion_api.handler"]`.

### CloudFormation (`aws/template.yaml`)
Parameter: `ImageUri`, `StageName` (Standard `v1`), `ThrottleRate`/`Burst`, `MonthlyQuota`.
Ressourcen:
- Lambda-Rolle mit `AWSLambdaBasicExecutionRole`. `AWS::Logs::LogGroup` mit 14 Tagen Aufbewahrung.
- `AWS::Lambda::Function`: PackageType Image, arm64, 2048 MB, Timeout 29 s, Env `KERYKEION_CLI_FORMAT=json`.
- `AWS::ApiGateway::RestApi`, Root-Methode und `{proxy+}`-Ressource mit `ANY`-Methode, `ApiKeyRequired: true`, `AWS_PROXY`-Integration. `BinaryMediaTypes` ist nicht nötig, weil SVG Text ist.
- `AWS::Lambda::Permission` für API Gateway, dazu Deployment und Stage.
- `AWS::ApiGateway::ApiKey`, `UsagePlan` (Throttle + Quota) und `UsagePlanKey`.
- Outputs: `ApiUrl`, `ApiKeyId`.

### `aws/deploy.sh`
Ablauf: `aws ecr describe-repositories || create-repository`, dann `docker buildx build --platform linux/arm64 -f aws/Dockerfile -t <repo>:<git-sha> .`, dann ECR-Login und Push, dann `aws cloudformation deploy --capabilities CAPABILITY_IAM --parameter-overrides ImageUri=…`. Am Ende werden URL und Key-Wert ausgegeben (`aws apigateway get-api-key --include-value`). Region und Stack-Name kommen aus Env mit Defaults (`AWS_REGION`, `STACK_NAME=kerykeion-api`).

## Skill `skills/kerykeion-api/`
Aufbau wie bei `skills/kerykeion-cli/` (SKILL.md mit Frontmatter, README.md, LICENSE, references/):
- **SKILL.md**:
  - Trigger: kerykeion + REST/HTTP/API/curl/Lambda.
  - Setup über `KERYKEION_API_URL` und `KERYKEION_API_KEY` (Header `x-api-key`).
  - Die drei Regeln: Exit-Code bzw. HTTP-Status prüfen; `f` explizit setzen; Subjects inline mitschicken.
  - Tabelle HTTP-Status ↔ Exit-Code.
  - Tabelle „Capability routing“ mit denselben Kommandos wie im CLI-Skill.
  - Top-Traps: die CLI-Traps übernehmen (Groß-/Kleinschreibung der Häuser, lat/lng/tz vollständig, UTC-Bereiche usw.) und API-spezifische ergänzen: kein `-o`, keine persistenten Profile, 29 s Timeout, 6 MB Antwortlimit (lange Serien eingrenzen bzw. `f: xml`), `offline: true` empfehlen.
  - „Never invent values“: `GET /` und `POST /info/literals`.
- **references/**:
  - `endpoints.md`: Kommandobaum als Endpunkte, abgeleitet aus `skills/kerykeion-cli/references/commands.md`.
  - `request-mapping.md`: Body→Flags-Regeln, `subjects`, `args`, `/run`.
  - `errors.md`
  - `recipes.md`: curl- und jq-Beispiele (natal, synastry mit zwei inline Subjects, SVG speichern, Transite, `call --explain`).
  - `rendering.md` wird aus dem CLI-Skill übernommen bzw. angepasst.
- Querverweise auf die Skills `kerykeion-cli` und `kerykeion`.

## Verifikation
1. `uv run pytest tests/aws` testet die Handler-Unit-Tests (Proxy-Events → Status/Headers/Body): natal mit inline Subject, synastry mit zwei Subjects, SVG-Content-Type, Usage-Error→400, gesperrtes `-o`→400, `GET /`-Katalog. Anschließend soll `/tmp` sauber sein.
2. Lokal im Container: `docker build` und `docker run -p 9000:8080`, Aufruf über den Lambda-RIE mit `curl localhost:9000/2015-03-31/functions/function/invocations -d @event.json`. Dabei prüfen, dass kein Ephemeriden-Download stattfindet.
3. `aws cloudformation validate-template` bzw. `cfn-lint aws/template.yaml`.
4. Deploy mit `aws/deploy.sh`, dann Smoke-Tests per curl gegen die Stage-URL: ohne Key→403; `POST /natal` (json/svg/xml); `POST /sky/eclipses`; `GET /`.
5. Skill-Beispiele aus `recipes.md` gegen die echte API ausführen.

Voraussetzung auf deinem Mac: Docker (Docker Desktop oder OrbStack), AWS CLI v2 und `aws configure` mit deinem Account.
