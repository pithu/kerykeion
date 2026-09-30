# Kerykeion REST API Agent Skill

A cross-platform [Agent Skill](https://agentskills.io/) that teaches AI coding
agents to use **kerykeion over HTTP**: the kerykeion command line deployed as a
REST API on AWS Lambda (see [`aws/`](../../aws)), for agents that cannot or
should not install Python.

Works with any skills-aware agent: Claude Code, Cursor, Codex, Copilot,
Gemini CLI, Windsurf, Cline, and others.

## What it covers

- the request shape: the URL path is the command, the JSON body (or the query
  string, for agents that can only fetch a URL) holds the flags;
- inline subjects, because the API is stateless and stores nothing;
- `files` for flags that expect a JSON file, and `/run` for a raw command line;
- HTTP status codes and how they map to the CLI's exit codes;
- response formats, the warnings header, `envelope`, and the API's limits;
- `/call`, the guarded dispatcher over every public factory;
- the traps that silently produce a wrong chart.

For the same commands in a local shell, use the sibling
[`kerykeion-cli`](../kerykeion-cli) skill; for Python, [`kerykeion`](../kerykeion).

## Install

The skill lives in the [kerykeion repository](https://github.com/g-battaglia/kerykeion).

```bash
# gate: skip
git clone --branch main --depth 1 https://github.com/g-battaglia/kerykeion.git
cd kerykeion

# Claude Code
cp -r skills/kerykeion-api /path/to/your-project/.claude/skills/kerykeion-api

# Codex
cp -r skills/kerykeion-api /path/to/your-project/.codex/skills/kerykeion-api

# Cursor, Windsurf, Cline and others: copy into the agent's skills directory.
```

The skill needs a deployed API. `aws/deploy.sh` deploys one and prints the
variable the skill reads:

```bash
# gate: skip
export KERYKEION_API_URL=https://abc123.execute-api.eu-central-1.amazonaws.com/v1
```

## Contents

```
SKILL.md                               the router: rules, status codes, traps, capability table
references/request-mapping.md          routes, body → flags, subjects, files, /run
references/responses-and-errors.md     formats, headers, envelope, status codes, limits
references/endpoints.md                every command path and what it needs
references/rendering.md                SVG appearance and text reports
references/call-dispatcher.md          reaching any public factory
references/recipes.md                  task-shaped examples with curl, jq and Python
```

Every `bash` example is executed against the Lambda handler by
`scripts/test_skill_api_snippets.py` (`poe skill:api:smoke`).

## License

AGPL-3.0, the same as kerykeion. The full text is vendored in `LICENSE`.
