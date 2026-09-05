# Deploying to Cloudflare Workers

What deploys is the workspace: the rules engine, the ten tools, and the page
that draws them. Every panel — the ranked cover options, the duty timeline, the
exclusions with the rule that stopped each candidate — is computed by the same
Python that runs locally, over `/api/tool`.

The chat does not deploy. That is a decision, not an omission; see below.

## Deploy

```bash
python worker/build.py                 # assemble worker/dist
cd worker
npx wrangler deploy
```

`build.py` copies `aircrew/` in unchanged, writes the nine dataset tables into
`dist/dataset_bundle.py`, and copies `web/index.html` into `dist/public/`.
Rebuild before every deploy — `dist/` is generated and gitignored, and
deploying a stale bundle is the failure that guards against.

First deploy also needs `npx wrangler login`.

## Check it

```bash
curl https://vista-crew.<subdomain>.workers.dev/api/health
curl -X POST https://vista-crew.<subdomain>.workers.dev/api/tool \
  -H 'content-type: application/json' \
  -d '{"name":"resolve_cover","arguments":{"pairing_id":"P-2291","vacated_by":"C-1042"}}'
```

Health should report 150 crew, 147 flights, 39 pairings. The tool call should
recommend C-3310 at INR 18,500, which is the published answer key.

## Why the chat is not here

`Agent.ask` is synchronous. It calls `_post`, which blocks on `urllib`. A
Worker has no sockets and no way to block on a promise, so the loop cannot run
at the edge until `ask` and `_post` are async the whole way down.

That refactor is small and worth doing — transport is already isolated in one
method for exactly this reason — but it changes the component that is hardest
to test, and it was not worth doing untested before a demo. `/api/chat` returns
503 with that explanation rather than a button that fails on first click.

There is a second obstacle behind it: the model endpoint this build uses is a
private Tailscale address, which a Worker cannot reach whatever the code does.
Enabling the chat at the edge needs a publicly reachable OpenAI-compatible
endpoint, its URL and key set as Worker secrets:

```bash
npx wrangler secret put AIRCREW_BASE_URL
npx wrangler secret put AIRCREW_API_KEY
```

For the chat today, run the local server, which is unchanged:

```bash
python -m aircrew.server --port 8768
```

## What was checked

The bundled dataset produces byte-identical tool output to the file-backed one,
and the full grading run passes against it with no filesystem at all:

```
ENGINE: 36/36 pass (0 fail, 0 TODO), 2 GEN not counted
SCENARIO CHECKS: 19/19
```

`tests/test_agent_loop.py::test_the_engine_runs_with_no_filesystem` keeps the
two paths from drifting.

## Notes on the runtime

- `compatibility_flags = ["python_workers"]` is required; Python Workers run on
  Pyodide.
- `run_worker_first = ["/api/*"]` in `wrangler.toml` matters. Without it the
  static-asset router answers `/api/*` with a 404 before the Worker is reached.
- Cold start parses 747 KB of bundled JSON on top of Pyodide's own startup. The
  parse happens once per isolate, at import, so the first request after an idle
  period is the slow one. If that becomes a problem, `duty_clocks.json` is
  384 KB of it and only some fields are read.
- `aircrew/server.py`, `cli.py`, `scoreboard.py` and `replay.py` are left out of
  the bundle. The Worker replaces the first and does not run the rest.
