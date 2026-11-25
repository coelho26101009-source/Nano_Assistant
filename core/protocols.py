"""Protocols: the person's own routines, started by saying a phrase.

THE IDEA
--------
In the films Tony says "House Party Protocol" and JARVIS does a dozen things at
once. A NANO protocol is the same idea, made by the person: a name, one or more
phrases that start it ("modo estudo", "boa noite", "modo jogo"), and a list of
ordinary PC actions: close apps, open apps and sites, set the volume, pause
the music, open a Windows Settings page, lock the PC, and a sentence NANO says
when it is done.

HOW IT STAYS SAFE
-----------------
A protocol is DATA, in ``protocols.json`` in the data directory, and compiles
to the same plan of narrow PC Control tools as a work mode
(core/work_modes.py), validated with the same rules: app NAMES only (never a
path or an interpreter), http/https sites only, volume 0-100, allow-listed
Settings pages. Running it sends each step through ``ToolExecutor``, so
policy, permissions and confirmations apply exactly as if the person had asked
for each step: closing apps and locking the PC still ask every time.

HOW A PHRASE STARTS IT
----------------------
:func:`match` compares the WHOLE message with the protocol's phrases, after
removing accents, punctuation, the wake phrase ("ei nano, …") and a polite
verb ("ativa", "liga", "protocolo", "start", "run"…). "boa noite" starts the
"Boa noite" protocol; "boa noite, amanhã acordo às 7" is an ordinary message.
The Brain runs a matched protocol directly, without asking a model, the same
way a clear reminder request is handled (core/reminder_intent.py).

ROUTINES MADE BY VOICE (``actions``)
------------------------------------
Besides the fixed fields the window edits, a protocol can carry ``actions``:
an ordered list of steps ``{tool, args, label}`` -- "abre o Notion, encosta o
Chrome à esquerda, põe o volume a 30 e lembra-me daqui a 50 minutos de fazer
pausa". The person makes these by talking to NANO ("guarda isto como rotina
setup de estudo", plugins/routines.py). Only the tools in ``ROUTINE_TOOLS``
can be steps, and each one has its own argument check here (app NAMES, never
paths; http/https only; allow-listed Settings pages; bounded numbers). Typing,
the clipboard, moving or deleting files and power off/restart are not on the
list: a routine replays without anyone reading it first, so it only holds
actions that are safe to repeat. Running it goes through core/task_runner.py,
which checks every step and retries what is worth retrying.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import unicodedata
import uuid
from pathlib import Path
from typing import Any

from core import work_modes
from core.i18n import tr

logger = logging.getLogger("nano.protocols")

MAX_PROTOCOLS = 30
MAX_PHRASES = 5
MAX_PHRASE = 60
PROTOCOL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
MEDIA_ACTIONS = ("play_pause", "stop", "next", "previous")
MAX_ACTIONS = 12

#: Words in front of a phrase that only say "do it".
_LEADING = (
    "ei nano", "hey nano", "ola nano", "ok nano", "okay nano", "hi nano", "nano",
    "ativa o protocolo", "ativar o protocolo", "ativa protocolo", "inicia o protocolo",
    "ativa a rotina", "corre a rotina", "faz a rotina", "inicia a rotina", "rotina",
    "prepara me o meu", "prepara me a minha", "prepara me o", "prepara me a", "prepara me",
    "prepara o meu", "prepara a minha", "prepara o", "prepara a", "prepara",
    "poe me o meu", "poe o meu", "poe a minha", "o meu", "a minha",
    "protocolo", "ativa o", "ativa a", "ativa", "ativar", "liga o", "liga a", "liga",
    "inicia o", "inicia a", "inicia", "comeca o", "comeca", "poe o", "poe", "entra em",
    "activate the protocol", "activate protocol", "start the protocol", "run the protocol",
    "run the routine", "start the routine", "routine", "set up my", "setup my",
    "prepare my", "get my", "prepare", "my",
    "protocol", "activate", "start", "run", "begin", "switch to", "go to",
    "por favor", "please",
)
_TRAILING = ("por favor", "please", "nano", "agora", "now", "protocol", "protocolo",
             "rotina", "routine", "ready", "pronto", "pronta")


def _plain(text: Any) -> str:
    decomposed = unicodedata.normalize("NFD", str(text or "").casefold())
    bare = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", bare).split())


def normalise_phrase(text: Any) -> str:
    """The comparable core of a spoken or typed phrase."""
    plain = _plain(text)
    changed = True
    while changed and plain:
        changed = False
        for lead in _LEADING:
            if plain == lead:
                break
            if plain.startswith(lead + " "):
                plain = plain[len(lead) + 1:]
                changed = True
                break
        for trail in _TRAILING:
            if plain.endswith(" " + trail):
                plain = plain[: -len(trail) - 1]
                changed = True
                break
    return plain


class ProtocolError(ValueError):
    """A protocol that cannot be saved. The message is user-facing."""


def _clip(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _phrases(raw: Any, name: str) -> list[str]:
    values = raw if isinstance(raw, list) else [raw] if raw else []
    out: list[str] = []
    for value in values[:MAX_PHRASES]:
        phrase = _clip(value, MAX_PHRASE)
        if phrase and normalise_phrase(phrase) and phrase not in out:
            out.append(phrase)
    if not out and name:
        out.append(name)
    if not out:
        raise ProtocolError(tr("Um protocolo precisa de pelo menos uma frase para o ativar."))
    return out


# ---------------------------------------------------------------------------
# Steps a routine may hold (``actions``)
# ---------------------------------------------------------------------------

def _text(args: dict, key: str, limit: int, *, required: bool = True) -> str:
    value = _clip(args.get(key), limit)
    if required and not value:
        raise ProtocolError(tr("Falta '{key}' num passo da rotina.", key=key))
    return value


def _level(args: dict, key: str = "level") -> int:
    value = args.get(key)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ProtocolError(tr("'{key}' tem de ser um número inteiro entre 0 e 100.", key=key))
    return value


def _monitor(args: dict, *, required: bool) -> dict:
    value = args.get("monitor")
    if value in (None, ""):
        if required:
            raise ProtocolError(tr("Falta o número do monitor."))
        return {}
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 16:
        raise ProtocolError(tr("O monitor tem de ser um número entre 1 e 16."))
    return {"monitor": value}


def _app(args: dict, key: str) -> str:
    try:
        return work_modes._app_name(args.get(key))
    except work_modes.ModeError as exc:
        raise ProtocolError(str(exc)) from exc


def _window_query(args: dict) -> str:
    # A window_id from pc_window_list is only valid until that window closes,
    # so a saved routine names the window by its app or title instead.
    query = _text(args, "query", 60, required=False) or _text(args, "app", 60, required=False)
    if not query:
        raise ProtocolError(tr("Falta '{key}' num passo da rotina.", key="query"))
    return query


def _a_launch(args):
    name = _app(args, "name")
    return {"name": name}, tr("Abrir {name}", name=name)


def _a_switch(args):
    name = _app(args, "name")
    return {"name": name}, tr("Mudar para {name}", name=name)


def _a_close(args):
    name = _app(args, "app")
    return {"app": name}, tr("Fechar {name}", name=name)


def _a_batch_state(args):
    name = _app(args, "app")
    state = str(args.get("state") or "").strip()
    if state not in ("minimize", "restore"):
        raise ProtocolError(tr("'state' tem de ser 'minimize' ou 'restore'."))
    label = tr("Minimizar {name}", name=name) if state == "minimize" else tr("Restaurar {name}", name=name)
    return {"app": name, "state": state}, label


_SNAP_LABELS = {
    "left": "à esquerda", "right": "à direita", "top": "em cima", "bottom": "em baixo",
    "top_left": "no canto de cima à esquerda", "top_right": "no canto de cima à direita",
    "bottom_left": "no canto de baixo à esquerda", "bottom_right": "no canto de baixo à direita",
}


def _a_snap(args):
    from core.pc_control import geometry

    query = _window_query(args)
    position = str(args.get("position") or "").strip()
    if position not in geometry.SNAP_MODES:
        raise ProtocolError(tr("Posição de janela desconhecida."))
    return ({"query": query, "position": position, **_monitor(args, required=False)},
            tr("Pôr {name} {where}", name=query, where=tr(_SNAP_LABELS.get(position, position))))


def _window_simple(verb: str):
    def check(args):
        query = _window_query(args)
        return {"query": query}, tr(verb, name=query)
    return check


def _a_move_monitor(args):
    query = _window_query(args)
    monitor = _monitor(args, required=True)
    return ({"query": query, **monitor},
            tr("Mover {name} para o monitor {number}", name=query, number=monitor["monitor"]))


def _a_volume(args):
    level = _level(args)
    return {"level": level}, tr("Volume a {level}%", level=level)


def _a_media(args):
    action = str(args.get("action") or "").strip()
    if action not in MEDIA_ACTIONS:
        raise ProtocolError(tr("Ação de música desconhecida."))
    label = {"play_pause": tr("Pausar ou retomar a música"), "stop": tr("Parar a música"),
             "next": tr("Faixa seguinte"), "previous": tr("Faixa anterior")}[action]
    return {"action": action}, label


def _a_url(args):
    from core.pc_control import web as pc_web
    from core.pc_control.results import PCControlError

    try:
        url = pc_web.normalise_url(args.get("url"))
    except PCControlError as exc:
        raise ProtocolError(tr("Endereço recusado: {reason}", reason=exc.message)) from exc
    return {"url": url}, tr("Abrir {name}", name=url)


def _a_search(args):
    query = _text(args, "query", 200)
    return {"query": query}, tr("Pesquisar “{query}”", query=query)


def _a_settings(args):
    from core.pc_control import settings as pc_settings
    from core.pc_control.results import PCControlError

    try:
        key, _uri, label = pc_settings.resolve_section(args.get("section"))
    except PCControlError as exc:
        raise ProtocolError(exc.message) from exc
    return {"section": key}, tr("Abrir as definições de {section}", section=label)


def _a_folder(args):
    folder = _text(args, "folder", 60, required=False)
    if folder:
        return {"folder": folder}, tr("Abrir a pasta {name}", name=folder)
    path = _text(args, "path", 260)
    return {"path": path}, tr("Abrir a pasta {name}", name=path)


def _a_file(args):
    path = _text(args, "path", 260)
    return {"path": path}, tr("Abrir {name}", name=path.replace("\\", "/").rsplit("/", 1)[-1])


def _a_brightness(args):
    level = _level(args)
    return {"level": level, **_monitor(args, required=False)}, tr("Brilho a {level}%", level=level)


def _a_lock(_args):
    return {}, tr("Bloquear o PC")


def _a_mute(_args):
    return {}, tr("Tirar o som")


def _a_unmute(_args):
    return {}, tr("Voltar a pôr o som")


def _a_reminder(args):
    text = _text(args, "text", 120)
    when = _text(args, "when", 60)
    out = {"text": text, "when": when}
    repeat = str(args.get("repeat") or "").strip()
    if repeat:
        if repeat not in ("daily", "weekdays", "weekly", "monthly"):
            raise ProtocolError(tr("Repetição desconhecida."))
        out["repeat"] = repeat
    return out, tr("Lembrete: {text} ({when})", text=text, when=when)


#: tool -> argument check. A closed list: anything else cannot be a step.
ROUTINE_TOOLS: dict[str, Any] = {
    "pc_app_launch": _a_launch,
    "pc_app_switch": _a_switch,
    "pc_window_batch_close": _a_close,
    "pc_window_batch_state": _a_batch_state,
    "pc_window_snap": _a_snap,
    "pc_window_maximize": _window_simple("Maximizar {name}"),
    "pc_window_minimize": _window_simple("Minimizar {name}"),
    "pc_window_restore": _window_simple("Restaurar {name}"),
    "pc_window_focus": _window_simple("Trazer {name} para a frente"),
    "pc_window_center": _window_simple("Centrar {name}"),
    "pc_window_move_monitor": _a_move_monitor,
    "pc_volume_set": _a_volume,
    "pc_volume_mute": _a_mute,
    "pc_volume_unmute": _a_unmute,
    "pc_media_control": _a_media,
    "pc_web_open_url": _a_url,
    "pc_web_search": _a_search,
    "pc_settings_open": _a_settings,
    "pc_folder_open": _a_folder,
    "pc_file_open": _a_file,
    "pc_display_set_brightness": _a_brightness,
    "pc_session_lock": _a_lock,
    "set_reminder": _a_reminder,
}
#: Steps that always ask before they run (the executor asks; this is for the window).
ASKING_TOOLS = frozenset({"pc_window_batch_close", "pc_session_lock"})


def validate_action(raw: Any) -> dict:
    """One clean step ``{tool, args, label[, asks]}``. Raises ProtocolError."""
    if not isinstance(raw, dict):
        raise ProtocolError(tr("Cada passo da rotina precisa de 'tool' e 'args'."))
    tool = str(raw.get("tool") or "").strip()
    check = ROUTINE_TOOLS.get(tool)
    if check is None:
        raise ProtocolError(tr("“{tool}” não pode ser um passo de uma rotina.", tool=_clip(tool, 40)))
    args = raw.get("args") if raw.get("args") is not None else raw.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ProtocolError(tr("Os argumentos de um passo têm de ser um objeto."))
    clean, default_label = check(args)
    label = _clip(raw.get("label"), 120) or default_label
    step = {"tool": tool, "args": clean, "label": label}
    if tool in ASKING_TOOLS:
        step["asks"] = True
    return step


def validate_actions(raw: Any) -> list[dict]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ProtocolError(tr("'{key}' tem de ser uma lista.", key="actions"))
    if len(raw) > MAX_ACTIONS:
        raise ProtocolError(tr("Uma rotina pode ter no máximo {count} passos.", count=MAX_ACTIONS))
    return [validate_action(item) for item in raw]


def validate(raw: Any) -> dict:
    """A clean, storable protocol from what the window sent. Raises ProtocolError."""
    if not isinstance(raw, dict):
        raise ProtocolError(tr("O protocolo não tem o formato esperado."))
    name = _clip(raw.get("name"), 60)
    if not name:
        raise ProtocolError(tr("Dá um nome ao protocolo."))
    pid = str(raw.get("id") or "").strip().casefold()
    if not PROTOCOL_ID.match(pid):
        pid = (re.sub(r"[^a-z0-9]+", "-", _plain(name)).strip("-")[:30] or "protocolo") \
            + "-" + uuid.uuid4().hex[:6]
    media = str(raw.get("media") or "").strip()
    if media and media not in MEDIA_ACTIONS:
        raise ProtocolError(tr("Ação de música desconhecida."))
    volume = raw.get("volume")
    if volume in ("", None):
        volume = None
    clean = {
        "id": pid,
        "name": name,
        "phrases": _phrases(raw.get("phrases"), name),
        "close_apps": [_clip(a, 60) for a in (raw.get("close_apps") or []) if _clip(a, 60)],
        "open_apps": [_clip(a, 60) for a in (raw.get("open_apps") or []) if _clip(a, 60)],
        "open_urls": [_clip(u, 300) for u in (raw.get("open_urls") or []) if _clip(u, 300)],
        "volume": volume,
        "media": media,
        "settings_section": _clip(raw.get("settings_section"), 40),
        "lock": raw.get("lock") is True,
        "message": _clip(raw.get("message"), 160),
        "actions": validate_actions(raw.get("actions")),
    }
    for key in ("close_apps", "open_apps", "open_urls"):
        if not isinstance(raw.get(key) or [], list):
            raise ProtocolError(tr("'{key}' tem de ser uma lista.", key=key))
    compile_protocol(clean)        # every value checked with the work-mode rules
    return clean


def compile_protocol(protocol: dict) -> dict:
    """The plan of narrow tool calls, like a work mode, plus music and lock."""
    base = {key: protocol.get(key) for key in
            ("name", "close_apps", "open_apps", "open_urls", "volume", "settings_section", "message")
            if protocol.get(key) not in (None, "", [])}
    steps: list[dict] = []
    if any(key in base for key in ("close_apps", "open_apps", "open_urls", "volume", "settings_section")):
        try:
            steps = work_modes.compile_mode(protocol.get("id") or "protocol", base)["steps"]
        except work_modes.ModeError as exc:
            raise ProtocolError(str(exc)) from exc
    media = protocol.get("media")
    if media:
        label = {"play_pause": tr("Pausar ou retomar a música"), "stop": tr("Parar a música"),
                 "next": tr("Faixa seguinte"), "previous": tr("Faixa anterior")}[media]
        steps.append({"tool": "pc_media_control", "args": {"action": media}, "label": label})
    for action in protocol.get("actions") or []:
        steps.append(validate_action(action))
    if protocol.get("lock"):
        steps.append({"tool": "pc_session_lock", "args": {}, "label": tr("Bloquear o PC"),
                      "asks": True})
    if not steps and not protocol.get("message"):
        raise ProtocolError(tr("Este protocolo não faz nada. Junta pelo menos uma ação."))
    return {"id": protocol.get("id"), "name": protocol.get("name"),
            "message": protocol.get("message") or "", "steps": steps,
            "phrases": list(protocol.get("phrases") or [])}


#: Starting points offered in the window (not created until the person saves
#: one), in each interface language: a phrase must be what the person says.
TEMPLATES: tuple[dict, ...] = (
    {"key": "study", "close_apps": ["discord", "steam"], "open_apps": [], "open_urls": [],
     "volume": 20, "media": "", "settings_section": "notifications", "lock": False,
     "pt-PT": {"name": "Modo estudo", "phrases": ["modo estudo", "vou estudar"],
               "message": "Modo estudo ativo. Bom estudo!"},
     "en": {"name": "Study mode", "phrases": ["study mode", "time to study"],
            "message": "Study mode on. Good luck!"}},
    {"key": "goodnight", "close_apps": [], "open_apps": [], "open_urls": [], "volume": 10,
     "media": "stop", "settings_section": "", "lock": True,
     "pt-PT": {"name": "Boa noite", "phrases": ["boa noite", "vou dormir"],
               "message": "Boa noite. Dorme bem."},
     "en": {"name": "Good night", "phrases": ["good night", "going to bed"],
            "message": "Good night. Sleep well."}},
    {"key": "gaming", "close_apps": [], "open_apps": ["Steam", "Discord"], "open_urls": [],
     "volume": 60, "media": "", "settings_section": "", "lock": False,
     "pt-PT": {"name": "Modo jogo", "phrases": ["modo jogo", "bora jogar"],
               "message": "Modo jogo ativo. Boa sorte!"},
     "en": {"name": "Gaming mode", "phrases": ["gaming mode", "let's play"],
            "message": "Gaming mode on. Have fun!"}},
)


def templates(language: str | None = None) -> list[dict]:
    if language is None:
        from core import i18n

        language = i18n.current()
    lang = "pt-PT" if language == "pt-PT" else "en"
    out = []
    for template in TEMPLATES:
        item = {k: v for k, v in template.items() if k not in ("pt-PT", "en")}
        item.update(template[lang])
        item["phrases"] = list(item["phrases"])
        out.append(item)
    return out


class ProtocolStore:
    """The person's protocols, in one JSON file. Thread-safe."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> list[dict]:
        if not self.path or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("protocols.json is unreadable; showing no protocols")
            return []
        items = data.get("protocols") if isinstance(data, dict) else None
        out = []
        for item in items or []:
            try:
                out.append(validate(item))
            except ProtocolError:
                logger.warning("A stored protocol is invalid and was skipped")
        return out[:MAX_PROTOCOLS]

    def _write(self, protocols: list[dict]) -> None:
        if not self.path:
            return
        from core.atomic_storage import write_bytes

        payload = json.dumps({"version": 1, "protocols": protocols}, ensure_ascii=False, indent=2)
        write_bytes(self.path, payload.encode("utf-8"))

    def list(self) -> list[dict]:
        with self._lock:
            return self._read()

    def describe(self) -> list[dict]:
        """Every protocol with its compiled steps, for the window."""
        out = []
        for protocol in self.list():
            plan = compile_protocol(protocol)
            out.append({**protocol, "steps": plan["steps"]})
        return out

    def get(self, protocol_id: str) -> dict | None:
        wanted = str(protocol_id or "").strip().casefold()
        return next((p for p in self.list() if p["id"] == wanted), None)

    def save(self, raw: Any) -> dict:
        with self._lock:
            protocol = validate(raw)
            items = self._read()
            # A protocol's NAME starts it too (match), so names and phrases
            # share one space: two protocols never answer the same words.
            taken = {normalise_phrase(ph): p["name"] for p in items if p["id"] != protocol["id"]
                     for ph in [*p["phrases"], p["name"]]}
            for phrase in [*protocol["phrases"], protocol["name"]]:
                owner = taken.get(normalise_phrase(phrase))
                if owner:
                    raise ProtocolError(tr("A frase “{phrase}” já ativa o protocolo “{name}”.",
                                           phrase=phrase, name=owner))
            replaced = False
            for index, item in enumerate(items):
                if item["id"] == protocol["id"]:
                    items[index] = protocol
                    replaced = True
            if not replaced:
                if len(items) >= MAX_PROTOCOLS:
                    raise ProtocolError(tr("Já tens {count} protocolos, o máximo.", count=MAX_PROTOCOLS))
                items.append(protocol)
            self._write(items)
            return protocol

    def delete(self, protocol_id: str) -> bool:
        with self._lock:
            items = self._read()
            kept = [p for p in items if p["id"] != str(protocol_id or "").strip().casefold()]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True

    def forget(self) -> None:
        """Remove the file ("Apagar todos os dados")."""
        with self._lock:
            try:
                if self.path and self.path.exists():
                    self.path.unlink()
            except OSError:
                logger.debug("Could not delete protocols.json", exc_info=True)

    def match(self, text: str) -> dict | None:
        """The protocol whose phrase IS this message, or None."""
        said = normalise_phrase(text)
        if not said or len(said) > 80:
            return None
        for protocol in self.list():
            for phrase in protocol["phrases"]:
                if normalise_phrase(phrase) == said:
                    return protocol
            if normalise_phrase(protocol["name"]) == said:
                return protocol
        return None


async def run(protocol: dict, executor: Any, *, call: Any = None, status: Any = None) -> dict:
    """Run one protocol's plan. Same result shape as a work mode.

    ``call`` is the gate each step goes through: the executor by default (the
    Modos page), ``Brain._run_tool`` when the person said the phrase.
    """
    plan = compile_protocol(protocol)
    if not plan["steps"]:
        message = plan["message"] or tr("{name}: feito.", name=plan["name"])
        return {"ok": True, "id": plan["id"], "name": plan["name"], "message": message,
                "done": 0, "skipped": 0, "total": 0, "steps": [], "protocol": True}
    result = await work_modes.run_plan(plan, executor, call=call, status=status)
    result["protocol"] = True
    result["plan_steps"] = plan["steps"]
    return result


def spoken_summary(result: dict) -> str:
    """What NANO answers after running a protocol: its sentence, and what failed."""
    lines = [str(result.get("message") or "")]
    from core import task_runner

    report = task_runner.short_report(result)
    if report:
        lines.append(report)
    return "\n".join(line for line in lines if line)


def _default_store() -> ProtocolStore:
    try:
        from core.app_paths import DATA_DIR

        return ProtocolStore(DATA_DIR / "protocols.json")
    except Exception:  # pragma: no cover - the data directory always resolves in the app
        return ProtocolStore(None)


#: The app's store, in the data directory ("Apagar todos os dados" removes it).
STORE = _default_store()


__all__ = ["ASKING_TOOLS", "MAX_ACTIONS", "ROUTINE_TOOLS", "STORE", "ProtocolError",
           "ProtocolStore", "TEMPLATES", "compile_protocol", "normalise_phrase", "run",
           "spoken_summary", "templates", "validate", "validate_action", "validate_actions"]
