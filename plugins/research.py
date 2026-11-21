"""
Nano Plugin: deep research with a report and its sources.

"Investiga a fundo ...", "pesquisa com fontes ...", "faz-me um relatório
sobre ...": NANO searches several ways and several places, reads the pages,
cross-checks them and hands the model the evidence to write a report with
[n] citations. The list of sources under the report is added by NANO itself
(core/brain.py), from the pages really read.

The work lives in core/research.py. This tool is read-only (``browser.read``):
it runs without a card, like any web search, and its output is fenced as
external content before the model sees it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import research


async def deep_research(arguments: dict) -> dict:
    args = arguments or {}
    return await research.run(str(args.get("question") or ""), args.get("angles"))


def get_tools() -> list[dict]:
    return [{"type": "function", "function": {
        "name": "deep_research",
        "description": (
            "Investigação profunda: pesquisa a mesma pergunta de vários ângulos, em vários sítios "
            "(DuckDuckGo/Bing e Wikipédia), lê até 8 páginas, cruza o que dizem e devolve as provas "
            "numeradas [1], [2]... Usa quando o utilizador pede para investigar, pesquisar a fundo, "
            "comparar fontes, um relatório ou uma resposta com fontes (ex.: 'investiga se vale a "
            "pena a RTX 4060', 'research the best budget laptop'). Para uma pergunta rápida usa "
            "web_search. Depois escreve o relatório citando [n] após cada facto; o NANO junta a "
            "lista de fontes sozinho."),
        "parameters": {"type": "object", "required": ["question"], "properties": {
            "question": {"type": "string",
                         "description": "A pergunta a investigar, completa, numa frase."},
            "angles": {"type": "array", "maxItems": 3, "items": {"type": "string"},
                       "description": ("Até 3 pesquisas diferentes que ajudam a responder "
                                       "(outras palavras, em inglês, com 'preço', 'review', "
                                       "'vs', o ano...). Curtas, como se escrevesse num motor "
                                       "de pesquisa.")},
        }},
    }}]


TOOL_HANDLERS: dict = {
    "deep_research": deep_research,
}
