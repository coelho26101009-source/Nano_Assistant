"""
Nano Plugin: look through the phone's camera (with the person's yes on the phone).

One tool, ``phone_camera_look``. It can only ASK the paired phone for one
photo: the phone shows a card, and only the person's tap there opens the
camera. See core/phone_camera.py for the whole consent and privacy model.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import phone_camera


def phone_camera_look(question: str = "") -> dict:
    return phone_camera.look(question)


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "phone_camera_look",
            "description": (
                "Pede ao telemóvel do utilizador para mostrar UMA foto pela câmara e diz o que "
                "lá está. Usa quando o utilizador pede para veres algo à frente dele (\"o que é "
                "isto?\", \"olha para isto pelo telemóvel\"). O telemóvel mostra um pedido e só "
                "o utilizador, no telemóvel, abre a câmara e tira a foto; nada é gravado. "
                "Se ele recusar, não insistas."),
            "parameters": {"type": "object", "properties": {
                "question": {"type": "string",
                             "description": "O que queres saber sobre o que a câmara vê, numa frase curta."},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "phone_camera_look": lambda a: phone_camera_look(**{k: v for k, v in (a or {}).items() if k == "question"}),
}
