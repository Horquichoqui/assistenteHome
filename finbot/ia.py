"""Camada de IA: prompts e escolha do provedor (Claude ou Gemini).

O provedor é escolhido pelas variáveis de ambiente:
- IA_PROVIDER=claude|gemini força um provedor específico;
- caso contrário, usa o Claude se ANTHROPIC_API_KEY existir, senão o Gemini
  (que tem camada gratuita) se GEMINI_API_KEY existir.
"""

from __future__ import annotations

import os
from datetime import date
from types import ModuleType
from typing import Literal, Optional

from pydantic import BaseModel

from . import ia_claude, ia_gemini
from .db import Cartao

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


def _backend() -> ModuleType:
    escolha = os.environ.get("IA_PROVIDER", "").strip().lower()
    if escolha in ("claude", "anthropic"):
        return ia_claude
    if escolha == "gemini":
        return ia_gemini
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ia_claude
    if os.environ.get("GEMINI_API_KEY"):
        return ia_gemini
    raise RuntimeError(
        "Nenhuma chave de IA configurada (ANTHROPIC_API_KEY ou GEMINI_API_KEY)."
    )


class GastoExtraido(BaseModel):
    valor_total: Optional[float] = None
    data_compra: Optional[str] = None  # YYYY-MM-DD
    estabelecimento: Optional[str] = None
    categoria: str = "outros"
    forma_pagamento: Optional[str] = None
    observacoes: Optional[str] = None
    responsavel: Optional[str] = None  # nome de uma pessoa cadastrada, ou "combinado"
    parcelas: Optional[int] = None  # número de parcelas, se mencionado (ex.: "10x")


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


def _instrucoes_extracao(
    cartoes: list[Cartao], hoje: date, pessoas: list[str]
) -> str:
    if pessoas:
        bloco_pessoas = (
            f"Pessoas cadastradas: {', '.join(pessoas)}.\n"
            "- responsavel: se a mensagem indicar que o gasto foi de uma "
            "dessas pessoas específicas (ex.: 'a Maria Eduarda gastou...'), "
            "preencha com o nome exato dela. Se indicar que foi conjunto "
            "('nosso', 'dividimos', 'juntos', 'da casa'), preencha com "
            "'combinado'. Se não houver indicação, deixe nulo (será "
            "assumido como de quem enviou a mensagem).\n"
        )
    else:
        bloco_pessoas = ""
    return (
        "Você extrai dados de gastos para um controle financeiro pessoal "
        "brasileiro. Valores em reais (BRL). Hoje é "
        f"{hoje.isoformat()}.\n"
        f"Categorias válidas: {', '.join(CATEGORIAS)}.\n"
        "Cartões e contas cadastrados pelo usuário:\n"
        f"{_contexto_cartoes(cartoes)}\n"
        f"{bloco_pessoas}"
        "Regras:\n"
        "- valor_total: o valor TOTAL pago, em reais (ex.: 45.9). Se a "
        "compra foi parcelada, é o valor total da compra, não o da parcela.\n"
        "- data_compra: data da compra no formato YYYY-MM-DD; se não houver, "
        "deixe nulo.\n"
        "- forma_pagamento: se a legenda ou a nota indicarem o cartão/conta, "
        "use exatamente o nome cadastrado correspondente; caso contrário, "
        "copie o que foi informado (ex.: 'pix', 'dinheiro').\n"
        "- parcelas: número de parcelas, se mencionado (ex.: '10x', 'em 3 "
        "vezes'); caso contrário, deixe nulo.\n"
        "- A legenda do usuário tem prioridade sobre o que está impresso na nota."
    )


async def extrair_de_foto(
    imagem: bytes,
    media_type: str,
    legenda: Optional[str],
    cartoes: list[Cartao],
    hoje: date,
    pessoas: list[str] = (),
) -> GastoExtraido:
    """Lê uma foto de nota fiscal/comprovante e extrai os dados do gasto."""
    texto = "Extraia os dados desta nota fiscal ou comprovante.\n" + (
        f"Legenda enviada pelo usuário: {legenda}"
        if legenda
        else "O usuário não enviou legenda."
    )
    return await _backend().estruturado(
        system=_instrucoes_extracao(cartoes, hoje, list(pessoas)),
        texto=texto,
        modelo=GastoExtraido,
        imagem=imagem,
        media_type=media_type,
    )


async def interpretar_texto(
    texto: str,
    cartoes: list[Cartao],
    hoje: date,
    pessoas: list[str] = (),
) -> Interpretacao:
    """Decide se uma mensagem de texto registra um gasto ou faz uma pergunta."""
    system = (
        _instrucoes_extracao(cartoes, hoje, list(pessoas))
        + "\nClassifique a mensagem do usuário:\n"
        "- 'registrar_gasto': ele relata um gasto que fez (ex.: 'gastei 50 "
        "no mercado no nubank', 'paguei 120 de luz'). Preencha o campo "
        "gasto. Se não houver data, use a de hoje.\n"
        "- 'pergunta': ele pergunta algo sobre seus gastos, contas ou "
        "vencimentos.\n"
        "- 'outro': qualquer outra coisa (saudações, etc.)."
    )
    return await _backend().estruturado(
        system=system, texto=texto, modelo=Interpretacao
    )


async def responder_pergunta(pergunta: str, contexto_financeiro: str) -> str:
    """Responde uma pergunta livre usando o retrato atual das finanças."""
    system = (
        "Você é um assistente financeiro pessoal no Telegram. Responda em "
        "português do Brasil, de forma direta e amigável, usando APENAS os "
        "dados abaixo. Valores em reais. Se os dados não permitirem "
        "responder, diga o que falta.\n\n"
        "=== DADOS FINANCEIROS DO USUÁRIO ===\n"
        f"{contexto_financeiro}"
    )
    return await _backend().gerar_texto(system, pergunta)


async def redigir_resumo(dados: str, periodo: str) -> str:
    """Transforma as estatísticas calculadas em um resumo amigável com dicas."""
    system = (
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
    )
    return await _backend().gerar_texto(system, dados)
