"""The agent loop: an OpenAI-compatible model with tool calling.

Target is `gpt-5.6-luna`. The scaffolding here is deliberately thin -- the
model is capable, and every extra rule is a thing that can be wrong. What is
here has a reason:

- tool summaries steer, because a result that looks answer-shaped is where an
  invented figure comes from;
- the claim gate runs on every final turn, because that is the product's whole
  premise and it must be a mechanism rather than an instruction;
- there is no push-back heuristic for a model that announces a tool instead of
  calling it. That belongs in the "add only if measured" pile.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import grounding
from .tools import OPENAI_TOOLS, Tools, dispatch, renumber

DEFAULT_MODEL = os.environ.get("AIRCREW_MODEL", "gpt-5.6-luna")
DEFAULT_BASE_URL = os.environ.get("AIRCREW_BASE_URL", "https://api.openai.com/v1")
DEFAULT_TIMEOUT = float(os.environ.get("AIRCREW_TIMEOUT", "180"))


class ModelError(RuntimeError):
    """The model could not be reached, or answered with something unusable.

    Raised rather than exited: a server thread must be able to turn this into a
    502 for one turn, not take the process down with it.
    """


def _call_of(tc: dict) -> tuple[str, dict]:
    """Name and arguments out of one raw tool_call.

    Arguments arrive as a JSON string the model wrote, so it can be malformed
    or absent. An empty dict is the right recovery: the tool then reports what
    it needs, and the model reads that and tries again.
    """
    fn = tc.get("function") or {}
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        args = {}
    return fn.get("name") or "", args if isinstance(args, dict) else {}

SYSTEM_PROMPT = """\
You are the Crew Ops Advisor for an airline crew-control desk. The controller \
is working under time pressure and will act on what you say.

THE OPERATING WINDOW

The schedule covers {schedule_from} to {schedule_to}, and the duty snapshot is \
{snapshot}. A controller who says "17 Sep" means {year}-09-17. Never supply a \
different year, and never ask which year they meant -- there is only one. If a \
date is genuinely outside the window the tool will tell you so rather than \
returning an empty result.

WHAT YOU DO AND WHAT THE ENGINE DOES

Deterministic Python computes every figure, every legality verdict and every \
cost. You choose which question to ask it, you resolve what the controller \
meant, and you explain the result. You never calculate.

You may think out loud and propose. Saying "C-2210 might work if we position \
them from DEL" is useful. But a proposal is not an answer: before it reaches \
the controller, call the tool that settles it -- `check_assignment` for \
legality, `resolve_cover` for cost and ranking, `validate` for a statement you \
want checked -- and report what came back, including when it refutes you.

WRITING FIGURES

Every number, cost, count and verdict in your reply must come from a tool \
result in this conversation. Prefer to write it as a placeholder:

    {{claim:c7}}

which is replaced with that claim's validated text. Claim ids are in each tool \
result's `claims` list. If you type a figure directly it is checked against the \
tool results, and a figure that is not there stops your reply from being sent.

If you do not have a figure, say so and call the tool. Never estimate, never \
carry a number over from an earlier answer, and never round one for readability.

A placeholder mid-sentence is replaced with the bare figure, so write the label \
yourself and let the placeholder supply the value:

    good:  Three flights are uncovered on day 1: {{claim:c1}}.
    bad:   Three flights are uncovered on day 1: 3 flights uncovered on day 1: DX412 ...

A cost placeholder already carries the currency, so do not write INR in front of one: "at {{claim:c4}}", never "at INR {{claim:c4}}".

HOW TO WRITE IT

You are talking to a crew controller, not to a developer. Never name a tool, a \
function, a field or a claim id in your reply: they mean nothing to the person \
reading and they make a plain answer look like a debug log. Say "I have not \
priced the options yet", not "call resolve_cover".

When a result establishes the impact but not the plan, end with one short offer \
of the obvious next step, phrased as a question: "Shall I rank the cover options \
and price them?" If the controller agrees, run it immediately in that next turn \
without asking again.

WHAT YOU ADD

The engine cannot resolve ambiguity, and that is your job:
- Pin down what the controller meant -- which date "tomorrow" is, which pairing \
"the DXA captain" is on -- and say what you assumed.
- Say which of two verdicts was asked for. `check_assignment` returns both \
"does this breach a rule" and "can we call this person out"; they have \
different answers and the controller asked one of them.
- Call out ties. When several options cost the same, cost has stopped deciding \
and the choice is the controller's, usually on reachability. Say that.
- Read the `missing` list on every result. It tells you what that result does \
not establish. An impact result has no costs in it; do not supply one.
- When a follow-up changes who is available, pass `exclude_crew` and let the \
engine re-rank. Do not read the next row off the previous ranking -- it is a \
different question and usually has a different answer.

STYLE

The workspace beside you already shows the ranked options, the timeline and the \
exclusions. Do not re-list what is on screen. Give the recommendation and the \
one reason that decided it, then stop. Two or three sentences is usually right. \
Carry the plain-English constraint with any rule id you mention: "RULE-REST-04 \
(minimum 12h rest)", never the bare id.
"""


@dataclass
class Turn:
    reply: str
    tool_calls: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    grounding: grounding.Grounding | None = None
    corrected: bool = False


class Agent:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str | None = None,
        tools: Tools | None = None,
        max_rounds: int = 8,
    ):
        self.model = model
        self.tools = tools or Tools()
        self.max_rounds = max_rounds
        dates = self.tools.ds.schedule_dates
        prompt = SYSTEM_PROMPT.format(
            schedule_from=dates[0],
            schedule_to=dates[-1],
            snapshot=self.tools.ds.snapshot_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            year=dates[0][:4],
        )
        self.messages: list[dict] = [{"role": "system", "content": prompt}]
        self.timeout = DEFAULT_TIMEOUT
        self.max_retries = 2
        self._base_url = base_url
        self._api_key = api_key or os.environ.get("AIRCREW_API_KEY") or os.environ.get(
            "OPENAI_API_KEY"
        )

    # ------------------------------------------------------------------
    # Transport. Any OpenAI-compatible /chat/completions endpoint, over the
    # standard library, because that is all this loop ever asks for: one
    # completion with tools, no streaming and no vendor extensions. A package
    # for that would be a dependency the engine, the CLI and the workspace do
    # not have, and the README promises clone-and-run.
    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url.rstrip('/')}{path}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                # 4xx other than rate-limiting will not improve on a retry.
                if exc.code not in (408, 429) and exc.code < 500:
                    raise ModelError(f"HTTP {exc.code} from the model endpoint: {detail}")
                last = ModelError(f"HTTP {exc.code} from the model endpoint: {detail}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = ModelError(f"cannot reach the model at {self._base_url}: {exc}")
            if attempt < self.max_retries:
                time.sleep(1.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _complete(self, tool_choice: str = "auto") -> dict:
        """One completion. Returns the raw assistant message as a dict."""
        data = self._post(
            "/chat/completions",
            {
                "model": self.model,
                "messages": self.messages,
                "tools": OPENAI_TOOLS,
                "tool_choice": tool_choice,
            },
        )
        choices = data.get("choices")
        if not choices:
            raise ModelError(
                f"no choices in the model response: {json.dumps(data)[:300]}"
            )
        msg = choices[0].get("message") or {}
        # Drop nulls before the message goes back into the history: some
        # gateways reject a replayed `"content": null` alongside tool_calls.
        return {k: v for k, v in msg.items() if v is not None}

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Forget the conversation, keep the system prompt.

        History is what makes "C-3310 is also sick, now what?" work without a
        pairing id, so it is never trimmed mid-conversation. It does have to be
        droppable on request, or "start again" is a lie.
        """
        del self.messages[1:]

    # ------------------------------------------------------------------
    def ask(self, question: str) -> Turn:
        """One controller question, through as many tool rounds as it needs."""
        self.messages.append({"role": "user", "content": question})
        results: list[dict] = []
        calls: list[dict] = []

        for _ in range(self.max_rounds):
            msg = self._complete("auto")
            self.messages.append(msg)

            if not msg.get("tool_calls"):
                return self._finish(msg.get("content") or "", calls, results)

            for tc in msg["tool_calls"]:
                name, args = _call_of(tc)
                result = dispatch(self.tools, name, args)
                calls.append({"name": name, "arguments": args})
                results.append(result)
                renumber(results)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(result, default=str, ensure_ascii=False),
                    }
                )

        return self._finish(
            "I ran out of tool rounds before reaching an answer. The workspace "
            "shows what was computed so far.",
            calls,
            results,
        )

    # ------------------------------------------------------------------
    def _finish(self, reply: str, calls: list[dict], results: list[dict]) -> Turn:
        g = grounding.check(reply, results)
        if g.ok:
            return Turn(g.rendered, calls, results, g)

        # One corrective turn, but not one corrective *round*. The correction
        # is usually "call the tool that computes this", and a tier-3 question
        # needs several calls to answer -- ranking each affected pairing, say.
        # Allowing exactly one round and then forcing text produced the worst
        # outcome available: "I cannot run the required recovery computation in
        # this turn", on a question the engine can answer completely.
        self.messages.append({"role": "user", "content": g.corrective_prompt()})
        for remaining in range(self.max_rounds - 1, -1, -1):
            msg = self._complete("auto" if remaining else "none")
            self.messages.append(msg)
            if not msg.get("tool_calls"):
                break
            for tc in msg["tool_calls"]:
                name, args = _call_of(tc)
                result = dispatch(self.tools, name, args)
                calls.append({"name": name, "arguments": args})
                results.append(result)
                renumber(results)
                self.messages.append(
                    {"role": "tool", "tool_call_id": tc.get("id"),
                     "content": json.dumps(result, default=str, ensure_ascii=False)}
                )

        g2 = grounding.check(msg.get("content") or "", results)
        if g2.ok:
            return Turn(g2.rendered, calls, results, g2, corrected=True)
        if not g2.blocking:
            # The second draft is true; it just still reads like a debug log.
            # Withholding a correct recovery plan over wording would be a far
            # worse failure than printing a tool name.
            return Turn(g2.rendered, calls, results, g2, corrected=True)
        return Turn(
            "I could not ground every figure in that answer against a computed "
            "result, so I am not stating it. The workspace shows what the engine "
            "did compute.",
            calls,
            results,
            g2,
            corrected=True,
        )
