"""The Worker's routing layer -- the edge equivalent of aircrew/server.py.

Everything below the routing is the same code that runs locally: the same
tools, the same engine, the same claim gate. That is deliberate. A deployment
that reimplements the engine in another language is a second engine to keep
correct, and the point of this build is that there is exactly one.

Two things differ at the edge, and both are named rather than papered over:

  * There is no filesystem, so the dataset arrives as a bundled module.
  * The chat does not run here at all. The agent loop is synchronous and a
    Worker cannot block on an outbound request, so /api/chat says so plainly
    instead of failing on the first click. What deploys is the half that
    proves the claim: every panel in the workspace is computed by this engine
    and needs no model.
"""

import json

from js import Response
from pyodide.ffi import to_js

import dataset_bundle
from aircrew import data as _data

# Before anything constructs a Dataset. Tools() does, at import time.
_data.BUNDLED = dataset_bundle.tables()

from aircrew.tools import SCHEMAS, Tools, dispatch  # noqa: E402  (after BUNDLED)

_tools = Tools()


def _json(obj, status=200):
    return Response.new(
        json.dumps(obj, default=str),
        to_js(
            {"status": status,
             "headers": {"content-type": "application/json; charset=utf-8"}},
            dict_converter=__import__("js").Object.fromEntries,
        ),
    )




async def on_fetch(request, env):
    url = request.url
    path = url.split("://", 1)[-1].split("/", 1)[-1].split("?")[0]
    path = "/" + path if not path.startswith("/") else path

    if request.method == "GET":
        if path in ("/", "/index.html"):
            return await env.ASSETS.fetch(request)

        if path == "/api/health":
            return _json({
                "engine": "ok",
                "crew": len(_tools.ds.crew),
                "flights": len(_tools.ds.flights),
                "pairings": len(_tools.ds.pairings),
                "model": False,
                "model_name": None,
                "snapshot": _tools.ds.snapshot_utc.strftime("%Y-%m-%d %H:%MZ"),
                "model_error":
                    "no model on this deployment; every figure in the "
                    "workspace is computed by the engine and needs none",
            })

        if path == "/api/tools":
            return _json(SCHEMAS)

        if path == "/api/prompt":
            from aircrew.agent import SYSTEM_PROMPT

            dates = _tools.ds.schedule_dates
            return _json({
                "model": None,
                "system_prompt": SYSTEM_PROMPT.format(
                    schedule_from=dates[0],
                    schedule_to=dates[-1],
                    snapshot=_tools.ds.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    year=dates[0][:4],
                ),
            })

        return await env.ASSETS.fetch(request)

    if request.method != "POST":
        return _json({"error": "not found"}, 404)

    try:
        payload = json.loads(await request.text() or "{}")
    except Exception:
        return _json({"error": "bad JSON body"}, 400)

    if path == "/api/tool":
        name = payload.get("name")
        if not name:
            return _json({"error": "name required"}, 400)
        try:
            return _json(dispatch(_tools, name, payload.get("arguments") or {}))
        except Exception as exc:  # a tool must never take the isolate down
            return _json({"error": "tool raised", "detail": str(exc)}, 500)

    if path == "/api/reset":
        # Nothing to reset: the conversation lives in the browser here.
        return _json({"ok": True})

    if path == "/api/chat":
        # Not "not configured yet" -- not possible here, and worth saying so
        # rather than shipping a button that fails on the first click.
        #
        # `Agent.ask` is synchronous: it calls `_post`, which blocks on
        # urllib. A Worker has no sockets and no way to block on a promise, so
        # the loop cannot run at the edge until `ask` and `_post` are async all
        # the way down. That refactor is worth doing; it is not worth doing
        # untested the night before a demo. See worker/README.md.
        return _json({
            "error": "the chat is not available on this deployment",
            "detail": "The agent loop is synchronous and a Worker cannot block "
                      "on an outbound request; the loop has to be made async "
                      "before it can run here.",
            "hint": "Every panel in the workspace is computed by the engine "
                    "over /api/tool and works without a model. For the chat, "
                    "run the local server: python -m aircrew.server",
        }, 503)

    return _json({"error": "not found"}, 404)
