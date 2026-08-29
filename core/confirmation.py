"""What the approval dialog says, and why it says that much.

"O Nano pretende executar 'pc.window.close' sobre 'window:786686'. Confirmas?"
is not consent. It names a capability the person has never heard of and a
handle that means nothing to them, and the only thing they can really do with
it is press Yes.

So a confirmation is built from three things they can actually judge:

    ACTION   FECHAR JANELA
    TARGET   Discord — #chat-dos-adm
    SCOPE    Esta janela

and, where knowing the size of the decision changes the decision, a PREVIEW:
how many windows a batch close will affect and what they are called, or how
many files are inside the folder being recycled. "Fecha tudo do Discord" is a
different request at one window than at nine, and the person approving it is
entitled to know which one they are agreeing to before they agree.

Everything here is READ-ONLY and best-effort. It runs before the action is
authorised, so it must never change anything, and a preview that cannot be
produced simply is not shown -- a failure to describe the target must never
become a failure to ask.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("nano.confirmation")

#: capability -> the headline a person reads first. Deliberately verbs in
#: capitals: the eye lands on WHAT WILL HAPPEN before anything else.
ACTION_LABELS: dict[str, str] = {
    "pc.window.close": "FECHAR JANELA",
    "pc.window.batch_close": "FECHAR TODAS AS JANELAS",
    "pc.screen.capture": "CAPTURAR O ECRÃ",
    "pc.clipboard.read": "LER A ÁREA DE TRANSFERÊNCIA",
    "pc.clipboard.write": "COPIAR PARA A ÁREA DE TRANSFERÊNCIA",
    "pc.clipboard.clear": "LIMPAR A ÁREA DE TRANSFERÊNCIA",
    "pc.input.type": "ESCREVER NUMA APLICAÇÃO",
    "pc.input.key_destructive": "CARREGAR NUMA TECLA QUE APAGA",
    "pc.folder.create": "CRIAR PASTA",
    "pc.file.create": "CRIAR FICHEIRO",
    "pc.file.copy": "COPIAR FICHEIRO",
    "pc.file.move": "MOVER FICHEIRO",
    "pc.file.rename": "MUDAR O NOME",
    "pc.file.recycle": "MOVER PARA A RECICLAGEM",
    "pc.folder.recycle": "MOVER PASTA PARA A RECICLAGEM",
    "pc.session.lock": "BLOQUEAR A SESSÃO",
    "pc.power.sleep": "SUSPENDER O COMPUTADOR",
    "pc.power.restart": "REINICIAR O COMPUTADOR",
    "pc.power.shutdown": "DESLIGAR O COMPUTADOR",
    "pc.session.logoff": "TERMINAR A SESSÃO DO WINDOWS",
    "filesystem.write": "ESCREVER FICHEIRO",
    "filesystem.delete": "APAGAR FICHEIRO",
    # "shell.execute": "EXECUTAR COMANDO" was here. A capability that can never
    # be confirmed must not own an approval headline -- the label is what a
    # card would have said, and there is no card. See core/capabilities.py.
    "browser.submit": "SUBMETER FORMULÁRIO",
    "external.send": "ENVIAR PARA FORA DO COMPUTADOR",
    "process.start": "INICIAR PROCESSO",
    "process.kill": "TERMINAR PROCESSO",
    "credential.write": "ALTERAR CREDENCIAIS",
    "financial.transaction": "TRANSAÇÃO FINANCEIRA",
}

#: The scope line: what the approval reaches, in the person's terms.
SCOPE_LABELS: dict[str, str] = {
    "pc.window.close": "Apenas esta janela",
    "pc.window.batch_close": "Todas as janelas desta aplicação",
    "pc.screen.capture": "Tudo o que estiver visível no ecrã",
    "pc.clipboard.read": "O que copiaste por último",
    "pc.clipboard.write": "Substitui o que tens copiado",
    "pc.clipboard.clear": "Apaga o que tens copiado",
    "pc.input.type": "Escreve como se fosses tu, nesta janela",
    "pc.input.key_destructive": "Nesta janela",
    "pc.folder.create": "Este caminho",
    "pc.file.create": "Este caminho",
    "pc.file.copy": "Origem e destino",
    "pc.file.move": "Origem e destino",
    "pc.file.rename": "Este ficheiro",
    "pc.file.recycle": "Vai para a Reciclagem; podes recuperar",
    "pc.folder.recycle": "Vai para a Reciclagem, com todo o conteúdo",
    "pc.session.lock": "Sessão do Windows",
    "pc.power.sleep": "Todo o computador",
    "pc.power.restart": "Todo o computador, e tudo o que estiver aberto",
    "pc.power.shutdown": "Todo o computador, e tudo o que estiver aberto",
    "pc.session.logoff": "Sessão do Windows, e tudo o que estiver aberto",
}

def _window_phrase(value: str) -> str:
    """"window:12345" or "window:Bloco" -> something a person recognises."""
    rest = value[len("window:"):] if value.startswith("window:") else value
    rest = rest.strip()
    if not rest or rest == "*":
        return "Janela em primeiro plano"
    return rest if not rest.isdigit() else f"Janela #{rest}"


#: Target prefixes, in the order they are tried, mapped to a human phrasing.
_TARGET_PREFIXES = (
    ("recycle:", lambda rest: rest),
    ("create:", lambda rest: rest),
    ("file:", lambda rest: rest.replace(" -> ", "  →  ")),
    ("folder:", lambda rest: f"Pasta {rest}"),
    ("windows:", lambda rest: f"Aplicação {rest.split(':', 1)[0]}"),
    ("window:", _window_phrase),
    ("app:", lambda rest: rest),
    ("apps:", lambda _rest: "Aplicações abertas"),
    ("clipboard:", lambda _rest: "Área de transferência"),
    ("input:type:", lambda rest: _window_phrase(rest.split(":#", 1)[0])),
    ("input:key:", lambda rest: _window_phrase(rest.split(":", 1)[-1])),
    ("input:hotkey:", lambda rest: _window_phrase(rest.split(":", 1)[-1])),
    ("pointer:scroll:", lambda rest: _window_phrase(rest)),
    ("screen:", lambda rest: "Ecrã completo" if rest == "desktop" else _window_phrase(rest)),
    ("settings:", lambda rest: f"Definições: {rest}"),
    ("search:", lambda rest: rest.split(":", 1)[-1]),
    ("power:", lambda _rest: "Sessão do Windows"),
    ("session:", lambda _rest: "Sessão do Windows"),
    ("system:", lambda rest: f"Estado do sistema ({rest})"),
    ("display:", lambda rest: f"Monitor {rest}"),
    ("volume:", lambda _rest: "Volume do sistema"),
    ("media:", lambda rest: f"Reprodução ({rest})"),
    ("query:", lambda rest: f"“{rest}”"),
)


def humanise_target(target) -> str:
    """A permission target, rendered for a person rather than for a log line."""
    text = str(target or "").strip()
    if not text:
        return "—"
    for prefix, render in _TARGET_PREFIXES:
        if text.startswith(prefix):
            try:
                return render(text[len(prefix):]) or text
            except Exception:
                return text
    return text


def _preview(capability: str, args: dict) -> dict:
    """Extra facts that change the size of the decision. Read-only, best-effort.

    Only two cases need it, and both are cases where the same sentence can mean
    a small thing or a large one:

    * a batch close, where "todas as janelas do Discord" is one window or nine;
    * recycling a folder, where the name says nothing about what is inside.

    Any failure here is swallowed. Not being able to DESCRIBE a target must
    never turn into not ASKING about it.
    """
    try:
        if capability == "pc.window.batch_close":
            from core.pc_control import windows

            group = windows.resolve_group(str(args.get("app") or ""))
            return {"count": len(group),
                    "items": [w["title"] for w in group[:10]],
                    "note": f"{len(group)} janela(s) serão fechadas."}

        if capability in {"pc.file.recycle", "pc.folder.recycle"}:
            from core.pc_control import fileops

            item = fileops.preview_recycle(str(args.get("path") or ""))
            preview = {"items": [item.get("path") or ""]}
            contains = item.get("contains")
            if contains is not None:
                preview["count"] = contains
                preview["note"] = f"A pasta contém {contains} item(ns)."
            elif item.get("size_bytes") is not None:
                preview["note"] = f"{item['size_bytes']} bytes."
            return preview
    except Exception:
        logger.debug("could not preview %s", capability, exc_info=True)
    return {}


def describe(capability: str, args: dict | None = None, *,
             risk: str | None = None, target: str | None = None) -> dict:
    """The structured content of one approval card.

    Returns action / target / scope / risk plus an optional preview. The caller
    renders it; nothing here decides anything.
    """
    args = dict(args or {})
    resolved_target = target or args.get("_pc_target") or args.get("target") \
        or args.get("path") or args.get("url") or args.get("command")

    return {
        "capability": capability,
        "action": ACTION_LABELS.get(capability, _fallback_action(capability)),
        "target": humanise_target(resolved_target),
        "raw_target": str(resolved_target) if resolved_target else None,
        "scope": SCOPE_LABELS.get(capability, "Esta execução"),
        "risk": str(risk or "").upper() or None,
        "preview": _preview(capability, args),
    }


def _fallback_action(capability: str) -> str:
    """A readable headline for a capability with no entry in the table.

    Never "Permitir ação?": an unlisted capability still gets a sentence naming
    what it is, so a new tool that nobody labelled is verbose rather than
    opaque.
    """
    parts = [part for part in str(capability or "").replace("_", ".").split(".") if part]
    if not parts:
        return "AÇÃO DESCONHECIDA"
    return " ".join(parts[1:] or parts).upper()


def message(capability: str, args: dict | None = None) -> str:
    """One line for clients that can only show a string."""
    described = describe(capability, args)
    return f"{described['action']} — {described['target']}"


__all__ = ["ACTION_LABELS", "SCOPE_LABELS", "describe", "humanise_target", "message"]
