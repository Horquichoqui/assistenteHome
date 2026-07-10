"""Backend de IA: Anthropic (Claude). Melhor qualidade de leitura; pago."""

from __future__ import annotations

from typing import Optional, TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

MODELO = "claude-opus-4-8"

T = TypeVar("T", bound=BaseModel)

_client: Optional[AsyncAnthropic] = None


def _cliente() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


async def estruturado(
    system: str,
    texto: str,
    modelo: type[T],
    imagem: Optional[bytes] = None,
    media_type: Optional[str] = None,
) -> T:
    import base64

    conteudo: list[dict] = []
    if imagem is not None:
        conteudo.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type or "image/jpeg",
                    "data": base64.standard_b64encode(imagem).decode("ascii"),
                },
            }
        )
    conteudo.append({"type": "text", "text": texto})

    resposta = await _cliente().messages.parse(
        model=MODELO,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": conteudo}],
        output_format=modelo,
    )
    return resposta.parsed_output


async def gerar_texto(system: str, mensagem: str) -> str:
    resposta = await _cliente().messages.create(
        model=MODELO,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=system,
        messages=[{"role": "user", "content": mensagem}],
    )
    return "".join(b.text for b in resposta.content if b.type == "text").strip()
