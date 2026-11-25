"""Chained tasks inside a conversation: plans, routines, "tenta outra vez".

This is the part of the Brain that turns "faz estas cinco coisas" into five
checked steps, and it lives in its own module so core/brain.py stays about
talking to models. ``Brain`` inherits :class:`TaskChains`.

WHAT IT ADDS TO A TURN
----------------------
* **Plans NANO runs itself.** When the model's ``task_plan`` gives every step
  its arguments, NANO runs the plan through core/task_runner.py instead of
  trusting the model to keep a chain of calls together. The model gets back
  what really happened, step by step, and only has to summarise it.
* **Routines by voice.** ``routine_run`` returns a saved routine's steps and
  NANO runs them the same way; ``routine_save`` with ``from_last_actions``
  saves what NANO did in the previous request ("guarda isto como rotina").
* **"Tenta outra vez".** Right after a plan or routine with steps that did
  not work, the person can say "tenta outra vez" / "faz o que faltou" and
  NANO runs only those steps again, without a model.
* **An honest ending.** When a plan was carried out by the model, NANO says
  which planned steps never happened; when NANO ran it, NANO adds the steps
  that did not work. The model is never the only witness of its own success.
* **Questions only when they matter.** A plan of safe steps runs without
  "Avanço?". It asks once when a step is one the guardrails always ask about,
  or when this request already read outside content.

Every step still goes through ``Brain._run_tool``: guardrails, policy,
permission, the per-turn duplicate ledger and the executor.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

from core import task_runner
from core.i18n import tr

logger = logging.getLogger("nano.brain")

#: Tools whose call is followed by NANO running steps (see _run_calls).
STEP_RUNNERS = frozenset({"task_plan", "routine_run"})
#: How long after a run "tenta outra vez" still means "that run".
RETRY_WINDOW_SECONDS = 15 * 60
#: What the person says to run the steps that did not work again (normalised
#: with core.protocols.normalise_phrase, which drops "ei nano", "por favor"...).
RETRY_PHRASES = frozenset({
    "tenta outra vez", "tenta de novo", "tenta novamente", "volta a tentar", "tenta la outra vez",
    "tenta outra vez o que falhou", "repete o que falhou", "faz o que falhou", "faz o que faltou",
    "faz o resto", "acaba o resto", "acaba o que faltou", "termina o que faltou",
    "continua o plano", "continua a tarefa", "continua a rotina", "tenta outra vez a rotina",
    "try again", "retry", "do the rest", "finish the rest", "try that again",
    "retry what failed", "continue the plan",
})


def _plain_retry(text: str) -> str:
    from core import protocols

    return protocols.normalise_phrase(text)


_RETRY_KEYS = None


def is_retry_phrase(text: str) -> bool:
    global _RETRY_KEYS
    if _RETRY_KEYS is None:
        _RETRY_KEYS = {_plain_retry(phrase) for phrase in RETRY_PHRASES}
    said = _plain_retry(text)
    return bool(said) and len(said) <= 60 and said in _RETRY_KEYS


class TaskChains:
    """Mixin for Brain. Uses ``_run_tool``, ``tool_executor``, ``guardrails``."""

    # ------------------------------------------------------------ turn state

    def _begin_task_turn(self) -> None:
        """Called at the start of every turn, before anything else runs."""
        self._turn_counter = getattr(self, "_turn_counter", 0) + 1
        previous = getattr(self, "_turn_actions", None) or []
        steps = self._routine_steps_from(previous)
        if steps:
            # What the LAST request did, for "guarda isto como rotina".
            self._last_actions = steps
        self._turn_actions = []
        self._turn_plan = None
        self._turn_runs = []

    def _note_action(self, name: str, args: dict, result: dict) -> None:
        """Remember a successful call of this turn, in order."""
        actions = getattr(self, "_turn_actions", None)
        if actions is None:
            actions = self._turn_actions = []
        if len(actions) < 40:
            actions.append({"tool": name, "args": dict(args), "output": result.get("output")})

    @staticmethod
    def _routine_steps_from(actions: list[dict]) -> list[dict]:
        """The successful actions that can be steps of a routine, cleaned."""
        from core import protocols

        steps: list[dict] = []
        for action in actions:
            if action.get("tool") not in protocols.ROUTINE_TOOLS:
                continue
            args = dict(action.get("args") or {})
            output = action.get("output") if isinstance(action.get("output"), dict) else {}
            if "window_id" in args and not args.get("query"):
                # A window handle dies with the window: name it instead.
                window = output.get("window") if isinstance(output.get("window"), dict) else {}
                name = str(window.get("process") or "").removesuffix(".exe") or window.get("title")
                if not name:
                    continue
                args = {k: v for k, v in args.items() if k != "window_id"}
                args["query"] = name
            if action["tool"] == "pc_app_launch" and isinstance(output.get("app"), dict):
                args = {"name": output["app"].get("name") or args.get("name")}
            try:
                step = protocols.validate_action({"tool": action["tool"], "args": args})
            except protocols.ProtocolError:
                continue
            if step not in steps:
                steps.append(step)
        return steps[:protocols.MAX_ACTIONS]

    # -------------------------------------------------------- tool hooks

    def _prepare_task_call(self, name: str, args: dict) -> dict | None:
        """Adjust or refuse a call before guardrails. None = go ahead."""
        if name == "routine_save" and args.get("from_last_actions") and not args.get("steps"):
            # This request's own actions first ("abre X e Y e guarda isto como
            # rotina"), else the previous request's ("guarda isto como rotina").
            steps = self._routine_steps_from(getattr(self, "_turn_actions", None) or []) \
                or getattr(self, "_last_actions", None)
            if not steps:
                return {"ok": False, "success": False, "status": "nothing_to_save",
                        "error": tr("Não fiz nenhuma ação que dê para guardar como rotina. "
                                    "Diz-me os passos que queres.")}
            args["steps"] = [{"tool": s["tool"], "args": s["args"], "label": s["label"]} for s in steps]
            args.pop("from_last_actions", None)
        return None

    def _plan_needs_question(self, args: dict) -> bool:
        """Whether showing this plan must wait for the person's yes."""
        from plugins import task_plan as task_plan_plugin  # noqa: PLC0415

        if self._turn_saw_untrusted():
            return True
        plan = task_plan_plugin.validate(args)
        if not plan["ok"]:
            return True
        from core.guardrails import SENSITIVE_TOOLS  # noqa: PLC0415

        for step in plan["steps"]:
            step_args = step.get("args")
            if step_args is None and step["tool"] in SENSITIVE_TOOLS:
                return True
            if self.guardrails.requires_confirmation(step["tool"], step_args or {}):
                return True
        return False

    def _plan_step_refusal(self, plan: dict) -> dict | None:
        """A step whose arguments the tool's own schema rejects, for the model to fix."""
        from core import schema_validation  # noqa: PLC0415

        registry = getattr(self.tool_executor, "registry", None)
        if not isinstance(registry, dict):
            return None
        for step in plan["steps"]:
            if "args" not in step:
                continue
            entry = registry.get(step["tool"])
            schema = entry.get("input_schema") if isinstance(entry, dict) else None
            if not isinstance(schema, dict):
                continue
            try:
                step["args"] = schema_validation.normalize_arguments(schema, step["args"])
            except schema_validation.SchemaValidationError as exc:
                return {"ok": False, "success": False, "status": "invalid_plan",
                        "error": tr("O passo “{label}” tem argumentos inválidos: {reason}",
                                    label=step["label"], reason=str(exc)[:160])}
        return None

    # ------------------------------------------------------------ running

    async def _call_tool(self, name: str, args: dict) -> dict:
        """One step, through the same gate as a model's call."""
        return await self._run_tool({"function": {"name": name, "arguments": dict(args)}})

    def _should_stop(self) -> bool:
        manager = getattr(self.tool_executor, "permission_manager", None)
        check = getattr(manager, "is_emergency_stopped", None)
        try:
            return bool(check()) if callable(check) else False
        except Exception:
            return False

    def _retry_allowed(self, tool: str) -> bool:
        policy = getattr(self.tool_executor, "get_retry_policy", None)
        try:
            return not callable(policy) or str(policy(tool)) != "NOT_SAFE_TO_RETRY"
        except Exception:
            return False

    async def _probe(self, name: str, args: dict) -> Any:
        """Read-only window listing, outside the ledger (it is polled)."""
        if name != "pc_window_list" or self.tool_executor is None:
            return {"success": False, "status": "refused"}
        return await self.tool_executor.execute_tool_async(name, args)

    async def _stream_task(self, start: Callable[[Callable[[str], None]], Any],
                           holder: dict) -> AsyncIterator[str]:
        """Run ``start(status)`` while yielding its status lines as they come.

        The result lands in ``holder["result"]``. A consumer that stops
        listening cancels the run between steps.
        """
        queue: asyncio.Queue[str] = asyncio.Queue()
        task = asyncio.ensure_future(start(queue.put_nowait))
        getter: asyncio.Future | None = None
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _pending = await asyncio.wait({task, getter},
                                                    return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    yield f"_thinking_:{getter.result()}"
                    continue
                getter.cancel()
                break
            while not queue.empty():
                yield f"_thinking_:{queue.get_nowait()}"
            holder["result"] = task.result()
        finally:
            if getter is not None and not getter.done():
                getter.cancel()
            if not task.done():
                task.cancel()

    async def _run_steps_streaming(self, steps: list[dict], holder: dict) -> AsyncIterator[str]:
        async def start(status):
            return await task_runner.run_steps(
                steps, self._call_tool, probe=self._probe, status=status,
                should_stop=self._should_stop, retry_allowed=self._retry_allowed)

        async for line in self._stream_task(start, holder):
            yield line

    def _remember_run(self, name: str, steps: list[dict], run: dict, kind: str) -> None:
        record = {"name": name, "steps": steps, "run": run, "kind": kind,
                  "at": time.monotonic(), "turn": getattr(self, "_turn_counter", 0)}
        self._last_plan_run = record
        runs = getattr(self, "_turn_runs", None)
        if runs is None:
            runs = self._turn_runs = []
        runs.append(record)

    async def _run_calls(self, calls: list[dict], holder: dict) -> AsyncIterator[str]:
        """Run one round of tool calls; results land in ``holder["results"]``.

        The usual case is unchanged: every call at once through ``_run_tool``.
        A round with a plan or a routine to run is taken one call at a time,
        so NANO can run the steps and show each one as it goes.
        """
        names = [self._call_name(call) for call in calls]
        if not any(name in STEP_RUNNERS for name in names):
            holder["results"] = await asyncio.gather(*(self._run_tool(c) for c in calls),
                                                     return_exceptions=True)
            return
        results: list[Any] = []
        covered: set[str] = set()
        for call, name in zip(calls, names):
            if self._step_key(name, self._call_args(call)) in covered:
                # The model also called a step the plan already ran AND that
                # worked: the plan's result stands, nothing runs twice. Only
                # the same call counts ("volume 20" is not "volume 40"), and a
                # step that failed is the model's to try again.
                results.append({"success": True, "status": "covered_by_plan",
                                "output": {"ok": True, "message": tr("Já foi feito como passo do plano.")}})
                continue
            try:
                result = await self._run_tool(call)
            except Exception as exc:  # pragma: no cover - _run_tool catches its own
                results.append(exc)
                continue
            if name in STEP_RUNNERS and task_runner.succeeded(result):
                inner = {}
                async for line in self._run_runner_result(name, call, result, inner):
                    yield line
                if inner.get("steps"):
                    covered.update(self._step_key(step["tool"], step.get("args") or {})
                                   for step, outcome in zip(inner["steps"], inner.get("outcomes") or [])
                                   if isinstance(outcome, dict) and outcome.get("ok"))
                result = inner.get("result", result)
            results.append(result)
        holder["results"] = results

    def _step_key(self, tool: str, args: dict) -> str:
        """One identity for "this tool with these arguments", as the turn
        ledger sees it, with app and window names compared loosely
        ("notion" is the "Notion" the plan opened)."""
        effective = self._effective_arguments(tool, dict(args or {}))
        loose = {key: (task_runner.fold(value) if key in ("name", "app", "query") and isinstance(value, str)
                       else value) for key, value in effective.items()}
        return self._call_fingerprint(tool, loose)

    def _plan_approves(self, name: str, args: dict) -> bool:
        """Whether the plan the person approved this turn covers this call.

        Each approved step covers ONE call: of that tool with those arguments
        when the plan gave them, or of that tool once when the model fills
        the arguments in itself. A second call, or one with other arguments
        (a page read mid-turn steering a "type" after an approved "click"),
        is asked about like any other."""
        approvals = getattr(self, "_turn_plan_tools", None)
        if not approvals:
            return False
        key = None
        for index, approval in enumerate(approvals):
            if approval.get("tool") != name:
                continue
            if approval.get("key") is not None:
                key = key or self._step_key(name, args)
                if approval["key"] != key:
                    continue
            del approvals[index]
            return True
        return False

    def _plan_approvals(self, args: dict) -> list[dict]:
        out = []
        for step in args.get("steps") or []:
            if not isinstance(step, dict) or not step.get("tool"):
                continue
            tool = str(step["tool"])
            step_args = step.get("args")
            out.append({"tool": tool,
                        "key": self._step_key(tool, step_args) if isinstance(step_args, dict) and step_args
                        else None})
        return out

    @staticmethod
    def _call_name(call: Any) -> str:
        if isinstance(call, dict):
            return str((call.get("function") or {}).get("name") or "")
        return str(getattr(getattr(call, "function", None), "name", "") or "")

    @staticmethod
    def _call_args(call: Any) -> dict:
        fn = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
        raw = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    async def _run_runner_result(self, name: str, call: Any, result: dict,
                                 inner: dict) -> AsyncIterator[str]:
        """After task_plan / routine_run said yes: run the steps, if NANO can."""
        output = result.get("output") if isinstance(result.get("output"), dict) else result
        if name == "task_plan":
            from plugins import task_plan as task_plan_plugin  # noqa: PLC0415

            plan = task_plan_plugin.validate(self._call_args(call))
            self._turn_plan = plan if plan.get("ok") else None
            if not task_plan_plugin.runnable(plan):
                return
            refusal = self._plan_step_refusal(plan)
            if refusal is not None:
                inner["result"] = refusal
                self._turn_plan = None
                return
            title, steps, closing = plan["goal"], plan["steps"], ""
        else:
            steps = [s for s in output.get("steps") or [] if isinstance(s, dict) and s.get("tool")]
            title, closing = str(output.get("name") or ""), str(output.get("message") or "")
            if not steps:
                inner["result"] = {"success": True, "status": "routine_done",
                                   "output": {"ok": True, "message": closing or tr("{name}: feito.", name=title)}}
                return
        holder: dict = {}
        async for line in self._run_steps_streaming(steps, holder):
            yield line
        run = holder.get("result") or {"ok": False, "steps": [], "done": 0, "total": len(steps)}
        self._remember_run(title, steps, run, name)
        inner["steps"] = steps
        inner["outcomes"] = run.get("steps") or []
        inner["result"] = self._run_result_for_model(name, title, run, closing)

    @staticmethod
    def _run_result_for_model(kind: str, title: str, run: dict, closing: str = "") -> dict:
        steps = [{"label": o["label"], "status": o["status"],
                  **({"why": o["message"]} if o.get("message") and o["status"] != "ok" else {})}
                 for o in run.get("steps") or []]
        return {
            "success": True,
            "status": "plan_executed" if kind == "task_plan" else "routine_executed",
            "output": {
                "ok": bool(run.get("ok")), "executed_by_nano": True, "name": title,
                "done": run.get("done", 0), "total": run.get("total", 0), "steps": steps,
                **({"closing_line": closing} if closing else {}),
                "instructions": ("O NANO já executou e verificou estes passos; não os voltes a chamar. "
                                 "Responde numa ou duas frases curtas sobre o que ficou feito"
                                 + (" e usa 'closing_line'" if closing else "")
                                 + ". Não listes o que falhou: o NANO junta isso no fim."),
            },
        }

    # ------------------------------------------------------------- ending

    def _plan_report_note(self) -> str | None:
        """NANO's own line at the end of a turn that had a plan or routine."""
        runs = getattr(self, "_turn_runs", None) or []
        lines = [task_runner.short_report(record["run"]) for record in runs]
        lines = [line for line in lines if line]
        if lines:
            lines.append(tr("Diz “tenta outra vez” para eu repetir só isso."))
            return "\n\n" + " ".join(lines)
        plan = getattr(self, "_turn_plan", None)
        if plan and not runs:
            missing = self._plan_gaps(plan)
            if missing:
                return "\n\n" + tr("Do plano, ficou por fazer: {steps}.", steps="; ".join(missing))
        if runs and not getattr(self, "_routine_hint_shown", False):
            record = runs[-1]
            if record["kind"] == "task_plan" and record["run"].get("ok") \
                    and len(self._routine_steps_from(
                        [{"tool": s["tool"], "args": s.get("args") or {}} for s in record["steps"]])) >= 3:
                self._routine_hint_shown = True
                return "\n\n" + tr("Se quiseres repetir isto com uma frase, diz “guarda isto como rotina”.")
        return None

    def _plan_gaps(self, plan: dict) -> list[str]:
        """Steps of a model-driven plan whose tool never succeeded this turn."""
        done = [action["tool"] for action in getattr(self, "_turn_actions", None) or []]
        missing = []
        for step in plan.get("steps") or []:
            if step["tool"] in done:
                done.remove(step["tool"])
            else:
                missing.append(step["label"])
        return missing

    # ------------------------------------------------------ "tenta outra vez"

    def _retry_candidate(self, user_message: str) -> dict | None:
        record = getattr(self, "_last_plan_run", None)
        if not record or not is_retry_phrase(user_message):
            return None
        if record.get("turn") != getattr(self, "_turn_counter", 0) - 1:
            return None          # something else was said since: not "that run"
        if time.monotonic() - record.get("at", 0) > RETRY_WINDOW_SECONDS:
            return None
        outcomes = record["run"].get("steps") or []
        again = [step for step, outcome in zip(record["steps"], outcomes) if not outcome.get("ok")]
        return {"name": record["name"], "steps": again, "kind": record["kind"]} if again else None

    async def _direct_retry(self, retry: dict) -> AsyncIterator[str]:
        """Run again only the steps that did not work, without a model."""
        self.last_provider_used = None
        self.last_metadata = {
            "task": "retry", "tier": None, "mode": getattr(self, "provider_mode", "AUTO"),
            "provider": None, "model": None, "fallback_used": False,
            "tools_offered": 0, "direct_action": "retry", "provider_attempts": [],
        }
        if self.tool_executor is None:
            answer = tr("Execução indisponível: falta a autoridade central.")
        else:
            holder: dict = {}
            async for line in self._run_steps_streaming(retry["steps"], holder):
                yield line
            run = holder.get("result") or {"ok": False, "steps": []}
            self._remember_run(retry["name"], retry["steps"], run, retry["kind"])
            report = task_runner.short_report(run)
            if run.get("ok"):
                answer = tr("Feito: desta vez correu tudo ({count} de {count}).", count=run.get("done", 0))
            elif run.get("done"):
                answer = tr("Fiz {done} de {total}.", done=run["done"], total=run.get("total", 0)) + " " + report
            else:
                answer = report or tr("Continuou a não dar.")
        self.conversation.append({"role": "assistant", "content": answer})
        yield answer


__all__ = ["RETRY_PHRASES", "STEP_RUNNERS", "TaskChains", "is_retry_phrase"]
