"""Chamadas à API do Claude: leitura de notas fiscais, interpretação de
mensagens e respostas a perguntas sobre as finanças."""

from __future__ import annotations

import base64
from datetime import date
from typing import Literal, Optional

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from .db import Cartao

MODELO = "claude-opus-4-8"

CATEGORIAS = [
    "alimentação",
    "mercado",
    "transporte",
    "saúde",
    "moradia",
    "contas",
    "lazer",
    "educação",
    "vestuário",
    "outros",
]

_client: Optional[AsyncAnthropic] = None


def _cliente() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic()
    return _client


class GastoExtraido(BaseModel):
    valor_total: Optional[float] = None
    data_compra: Optional[str] = None  # YYYY-MM-DD
    estabelecimento: Optional[str] = None
    categoria: str = "outros"
    forma_pagamento: Optional[str] = None
    observacoes: Optional[str] = None


class Interpretacao(BaseModel):
    intencao: Literal["registrar_gasto", "pergunta", "outro"]
    gasto: Optional[GastoExtraido] = None


def _contexto_cartoes(cartoes: list[Cartao]) -> str:
    if not cartoes:
        return "Nenhum cartão ou conta cadastrado."
    linhas = []
    for c in cartoes:
        venc = f", vence dia {c.dia_vencimento}" if c.dia_vencimento else ""
        linhas.append(f"- {c.nome} ({c.tipo}{venc})")
    return "\n".join(linhas)


def _instrucoes_extracao(cartoes: list[Cartao], hoje: date) -> str:
    return (
        "Você extrai dados de gastos para um controle financeiro pessoal "
        "brasileiro. Valores em reais (BRL). Hoje é "
        f"{hoje.isoformat()}.\n"
        f"Categorias válidas: {', '.join(CATEGORIAS)}.\n"
        "Cartões e contas cadastrados pelo usuário:\n"
        f"{_contexto_cartoes(cartoes)}\n"
        "Regras:\n"
        "- valor_total: o valor TOTAL pago, em reais (ex.: 45.9).\n"
        "- data_compra: data da compra no formato YYYY-MM-DD; se não houver, "
        "deixe nulo.\n"
        "- forma_pagamento: se a legenda ou a nota indicarem o cartão/conta, "
        "use exatamente o nome cadastrado correspondente; caso contrário, "
        "copie o que foi informado (ex.: 'pix', 'dinheiro').\n"
        "- A legenda do usuário tem prioridade sobre o que está impresso na nota."
    )


async def extrair_de_foto(
    imagem: bytes,
    media_type: str,
    legenda: Optional[str],
    cartoes: list[Cartao],
    hoje: date,
) -> GastoExtraido:
    """Lê uma foto de nota fiscal/comprovante e extrai os dados do gasto."""
    conteudo: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(imagem).decode("ascii"),
            },
        },
        {
            "type": "text",
            "text": (
                "Extraia os dados desta nota fiscal ou comprovante.\n"
                + (f"Legenda enviada pelo usuário: {legenda}" if legenda else "O usuário não enviou legenda.")
            ),
        },
    ]
    resposta = await _cliente().messages.parse(
        model=MODELO,
        max_tokens=2048,
        system=_instrucoes_extracao(cartoes, hoje),
        messages=[{"role": "user", "content": conteudo}],
        output_format=GastoExtraido,
    )
    return resposta.parsed_output


async def interpretar_texto(
    texto: str,
    cartoes: list[Cartao],
    hoje: date,
) -> Interpretacao:
    """Decide se uma mensagem de texto registra um gasto ou faz uma pergunta."""
    resposta = await _cliente().messages.parse(
        model=MODELO,
        max_tokens=2048,
        system=(
            _instrucoes_extracao(cartoes, hoje)
            + "\nClassifique a mensagem do usuário:\n"
            "- 'registrar_gasto': ele relata um gasto que fez (ex.: 'gastei 50 "
            "no mercado no nubank', 'paguei 120 de luz'). Preencha o campo "
            "gasto. Se não houver data, use a de hoje.\n"
            "- 'pergunta': ele pergunta algo sobre seus gastos, contas ou "
            "vencimentos.\n"
            "- 'outro': qualquer outra coisa (saudações, etc.)."
        ),
        messages=[{"role": "user", "content": texto}],
        output_format=Interpretacao,
    )
    return resposta.parsed_output


async def responder_pergunta(pergunta: str, contexto_financeiro: str) -> str:
    """Responde uma pergunta livre usando o retrato atual das finanças."""
    resposta = await _cliente().messages.create(
        model=MODELO,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=(
            "Você é um assistente financeiro pessoal no Telegram. Responda em "
            "português do Brasil, de forma direta e amigável, usando APENAS os "
            "dados abaixo. Valores em reais. Se os dados não permitirem "
            "responder, diga o que falta.\n\n"
            "=== DADOS FINANCEIROS DO USUÁRIO ===\n"
            f"{contexto_financeiro}"
        ),
        messages=[{"role": "user", "content": pergunta}],
    )
    return "".join(b.text for b in resposta.content if b.type == "text").strip()


async def redigir_resumo(dados: str, periodo: str) -> str:
    """Transforma as estatísticas calculadas em um resumo amigável com dicas."""
    resposta = await _cliente().messages.create(
        model=MODELO,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=(
            "Você é um assistente financeiro pessoal no Telegram. Escreva em "
            "português do Brasil. Com base APENAS nos dados fornecidos, redija "
            f"um resumo {periodo} curto e organizado com:\n"
            "1. Total gasto no período e comparação com a média;\n"
            "2. Maiores categorias e cartões/contas;\n"
            "3. Onde dá para economizar (valores concretos, ex.: 'reduzindo X "
            "você economizaria R$ Y');\n"
            "4. Próximos vencimentos, se houver.\n"
            "Use no máximo ~15 linhas, com emojis com moderação. Não invente "
            "números que não estejam nos dados."
        ),
        messages=[{"role": "user", "content": dados}],
    )
    return "".join(b.text for b in resposta.content if b.type == "text").strip()
