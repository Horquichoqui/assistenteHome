"""Agregações sobre os gastos: totais, médias e vencimentos."""

from __future__ import annotations

import calendar
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .db import Cartao, Database, Gasto


def formatar_reais(centavos: int) -> str:
    valor = centavos / 100
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def limites_do_mes(ref: date) -> tuple[date, date]:
    ultimo = calendar.monthrange(ref.year, ref.month)[1]
    return ref.replace(day=1), ref.replace(day=ultimo)


def limites_da_semana(ref: date) -> tuple[date, date]:
    """Semana de segunda a domingo contendo a data de referência."""
    inicio = ref - timedelta(days=ref.weekday())
    return inicio, inicio + timedelta(days=6)


def total_centavos(gastos: list[Gasto]) -> int:
    return sum(g.valor_centavos for g in gastos)


def total_por_cartao(gastos: list[Gasto]) -> list[tuple[str, int]]:
    """Totais por cartão/conta (gastos sem cartão entram como 'Sem cartão')."""
    somas: dict[str, int] = defaultdict(int)
    for g in gastos:
        chave = g.cartao_nome or g.forma_pagamento or "Sem cartão"
        somas[chave] += g.valor_centavos
    return sorted(somas.items(), key=lambda kv: -kv[1])


def total_por_categoria(gastos: list[Gasto]) -> list[tuple[str, int]]:
    somas: dict[str, int] = defaultdict(int)
    for g in gastos:
        somas[g.categoria or "outros"] += g.valor_centavos
    return sorted(somas.items(), key=lambda kv: -kv[1])


def serie_semanal(db: Database, hoje: date, semanas: int = 8) -> list[tuple[date, int]]:
    """Total gasto por semana (rotulada pela segunda-feira), da mais antiga à atual."""
    inicio_atual, _ = limites_da_semana(hoje)
    resultado = []
    for i in range(semanas - 1, -1, -1):
        ini = inicio_atual - timedelta(weeks=i)
        fim = ini + timedelta(days=6)
        resultado.append((ini, total_centavos(db.listar_gastos(ini, fim))))
    return resultado


def serie_mensal(db: Database, hoje: date, meses: int = 6) -> list[tuple[date, int]]:
    """Total gasto por mês (rotulado pelo dia 1), do mais antigo ao atual."""
    resultado = []
    ano, mes = hoje.year, hoje.month
    pares = []
    for _ in range(meses):
        pares.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    for ano, mes in reversed(pares):
        ini, fim = limites_do_mes(date(ano, mes, 1))
        resultado.append((ini, total_centavos(db.listar_gastos(ini, fim))))
    return resultado


def media_semanal_centavos(db: Database, hoje: date, semanas: int = 4) -> int:
    """Média das últimas N semanas completas (exclui a semana atual)."""
    serie = serie_semanal(db, hoje, semanas + 1)[:-1]
    com_dados = [total for _, total in serie]
    return round(sum(com_dados) / len(com_dados)) if com_dados else 0


def media_mensal_centavos(db: Database, hoje: date, meses: int = 3) -> int:
    """Média dos últimos N meses completos (exclui o mês atual)."""
    serie = serie_mensal(db, hoje, meses + 1)[:-1]
    com_dados = [total for _, total in serie]
    return round(sum(com_dados) / len(com_dados)) if com_dados else 0


@dataclass(frozen=True)
class Vencimento:
    cartao: Cartao
    data: date
    dias_restantes: int


def proximo_vencimento(cartao: Cartao, hoje: date) -> Optional[date]:
    if not cartao.dia_vencimento:
        return None
    ano, mes = hoje.year, hoje.month
    ultimo = calendar.monthrange(ano, mes)[1]
    dia = min(cartao.dia_vencimento, ultimo)
    data = date(ano, mes, dia)
    if data < hoje:
        mes += 1
        if mes == 13:
            ano, mes = ano + 1, 1
        ultimo = calendar.monthrange(ano, mes)[1]
        data = date(ano, mes, min(cartao.dia_vencimento, ultimo))
    return data


def proximos_vencimentos(db: Database, hoje: date) -> list[Vencimento]:
    """Cartões e contas com vencimento, do mais próximo ao mais distante."""
    itens = []
    for cartao in db.listar_cartoes():
        data = proximo_vencimento(cartao, hoje)
        if data is not None:
            itens.append(Vencimento(cartao, data, (data - hoje).days))
    return sorted(itens, key=lambda v: v.data)
