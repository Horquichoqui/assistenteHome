"""Backend de IA: Google Gemini via REST. Tem camada gratuita oficial
(chave em https://aistudio.google.com/), suficiente para uso pessoal."""

from __future__ import annotations

import base64
import json
import os
from typing import Optional, TypeVar

import httpx
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent"


def _modelo() -> str:
    return os.environ.get("GEMINI_MODEL") or "gemini-flash-latest"


def limpar_json(texto: str) -> str:
    """Remove cercas de código (```json ... ```) que o modelo às vezes inclui."""
    texto = texto.strip()
    if texto.startswith("```"):
        primeira_quebra = texto.find("\n")
        if primeira_quebra != -1:
            texto = texto[primeira_quebra + 1 :]
        if texto.rstrip().endswith("```"):
            texto = texto.rstrip()[:-3]
    return texto.strip()


async def _chamar(body: dict) -> str:
    chave = os.environ.get("GEMINI_API_KEY", "")
    if not chave:
        raise RuntimeError("GEMINI_API_KEY não configurada")
    async with httpx.AsyncClient(timeout=120) as cliente:
        resposta = await cliente.post(
            _URL.format(modelo=_modelo()),
            headers={"x-goog-api-key": chave},
            json=body,
        )
        resposta.raise_for_status()
    dados = resposta.json()
    partes = dados["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in partes)


async def estruturado(
    system: str,
    texto: str,
    modelo: type[T],
    imagem: Optional[bytes] = None,
    media_type: Optional[str] = None,
) -> T:
    parts: list[dict] = []
    if imagem is not None:
        parts.append(
            {
                "inlineData": {
                    "mimeType": media_type or "image/jpeg",
                    "data": base64.standard_b64encode(imagem).decode("ascii"),
                }
            }
        )
    parts.append({"text": texto})

    schema = json.dumps(modelo.model_json_schema(), ensure_ascii=False)
    body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": system
                    + "\n\nResponda SOMENTE com um objeto JSON válido que siga "
                    "este JSON Schema (sem comentários, sem texto extra):\n"
                    + schema
                }
            ]
        },
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    resposta = await _chamar(body)
    return modelo.model_validate_json(limpar_json(resposta))


async def gerar_texto(system: str, mensagem: str) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": mensagem}]}],
    }
    return (await _chamar(body)).strip()
