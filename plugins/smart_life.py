"""
Nano Plugin: Smart Life
IoT via REST webhooks + RAG over local documents (PDFs).
"""

import logging
from pathlib import Path
from typing import Any

import httpx

from core.memory import get_memory
from core.safe_http import public_async_client

logger = logging.getLogger("nano.plugins.smart_life")


# ─── IoT / Smart Home ─────────────────────────────────────────────────────────

async def send_iot_command(device: str, action: str,
                            value: Any = None, webhook_url: str | None = None) -> dict:
    """Envia comando REST para dispositivo IoT (luzes, tomadas, etc.)."""
    if not webhook_url:
        return {
            "error": "webhook_url não configurado. Adiciona o URL do teu hub IoT em settings.yaml",
            "hint":  "Compatível com Home Assistant, n8n, Make, Zapier ou qualquer REST API."
        }

    payload = {"device": device, "action": action}
    if value is not None:
        payload["value"] = value

    try:
        async with public_async_client(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            return {
                "success":    True,
                "device":     device,
                "action":     action,
                "status_code": resp.status_code,
                "response":   resp.text[:500],
            }
    except httpx.ConnectError:
        return {"error": f"Não consigo alcançar o hub IoT em {webhook_url}"}
    except httpx.HTTPStatusError as exc:
        return {"error": f"IoT respondeu com erro {exc.response.status_code}"}
    except Exception as exc:
        return {"error": f"Falha IoT: {exc}"}


async def phone_notification(message: str, webhook_url: str | None = None) -> dict:
    """Envia notificação para o telemóvel via webhook (Pushover, ntfy, etc.)."""
    if not webhook_url:
        return {"error": "webhook_url não configurado para notificações mobile."}
    try:
        async with public_async_client(timeout=10) as client:
            resp = await client.post(webhook_url, json={"message": message, "title": "Nano"})
            return {"success": resp.status_code < 400, "message": message}
    except Exception as exc:
        return {"error": f"Falha ao enviar notificação: {exc}"}


# ─── RAG sobre PDFs locais ────────────────────────────────────────────────────

def index_pdf(file_path: str) -> dict:
    """Indexa um PDF para pesquisa local (RAG)."""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"Ficheiro não encontrado: {file_path}"}

    try:
        # Extrai texto do PDF
        try:
            import pypdf
            reader = pypdf.PdfReader(str(path))
            text   = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            try:
                import pdfplumber
                with pdfplumber.open(str(path)) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            except ImportError:
                return {"error": "Instala 'pypdf' ou 'pdfplumber': pip install pypdf"}

        if not text.strip():
            return {"error": f"PDF sem texto extraível: {path.name}"}

        mem = get_memory()
        success = mem.index_document(
            doc_id   = path.stem,
            text     = text,
            metadata = {"filename": path.name, "path": str(path)},
        )

        return {
            "success":   success,
            "file":      path.name,
            "chars":     len(text),
            "pages":     text.count("\n\n"),
            "message":   f"'{path.name}' indexado! Agora posso responder perguntas sobre ele.",
        }

    except Exception as exc:
        return {"error": f"Falha ao indexar PDF: {exc}"}


def search_documents(query: str, n_results: int = 5) -> dict:
    """Pesquisa nos documentos indexados (RAG local, SQLite FTS5)."""
    try:
        results = get_memory().search_documents(query, n_results)
        if not results:
            return {"message": "Não encontrei documentos relevantes. Já indexaste algum PDF?",
                    "results": []}
        return {"query": query, "results": results, "count": len(results)}
    except Exception as exc:
        return {"error": f"Falha na pesquisa RAG: {exc}"}


def list_indexed_documents() -> dict:
    """Lista todos os documentos indexados no RAG."""
    try:
        docs = get_memory().list_documents()
    except Exception as exc:
        return {"error": str(exc)}
    return {"docs": docs, "count": len(docs)}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "iot_command",
            "description": "Envia comando para dispositivo IoT (luzes, tomadas, etc.) via webhook REST.",
            "parameters": {"type": "object", "required": ["device","action"], "properties": {
                "device":      {"type": "string", "description": "Nome do dispositivo (ex: 'luz_sala')"},
                "action":      {"type": "string", "description": "Acção (ex: 'on', 'off', 'dim')"},
                "value":       {"type": "number",  "description": "Valor opcional (ex: brilho 50)"},
                "webhook_url": {"type": "string",  "description": "URL do hub IoT"},
            }},
        }},
        {"type": "function", "function": {
            "name": "phone_notify",
            "description": "Envia notificação push para o telemóvel do utilizador.",
            "parameters": {"type": "object", "required": ["message"], "properties": {
                "message":     {"type": "string"},
                "webhook_url": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "rag_index_pdf",
            "description": "Indexa um PDF local para que o Nano possa responder perguntas sobre ele.",
            "parameters": {"type": "object", "required": ["file_path"], "properties": {
                "file_path": {"type": "string", "description": "Caminho completo para o PDF"},
            }},
        }},
        {"type": "function", "function": {
            "name": "rag_search",
            "description": "Pesquisa semanticamente nos documentos indexados. Usa quando o utilizador pergunta sobre os seus PDFs ou documentos.",
            "parameters": {"type": "object", "required": ["query"], "properties": {
                "query":     {"type": "string"},
                "n_results": {"type": "integer", "default": 5},
            }},
        }},
        {"type": "function", "function": {
            "name": "rag_list_docs",
            "description": "Lista todos os documentos indexados disponíveis para RAG.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]


TOOL_HANDLERS: dict = {
    "iot_command":   lambda a: send_iot_command(**a),
    "phone_notify":  lambda a: phone_notification(**a),
    "rag_index_pdf": lambda a: index_pdf(**a),
    "rag_search":    lambda a: search_documents(**a),
    "rag_list_docs": lambda _: list_indexed_documents(),
}
