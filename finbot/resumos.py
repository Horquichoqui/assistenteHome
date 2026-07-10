"""Monta o retrato financeiro (contexto) e os resumos semanais/mensais."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from . import ia, stats
from .db import Database
from .stats import formatar_reais

log = logging.getLogger(__name__)

_MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def _bloco_totais(titulo: str, pares: list[tuple[str, int]]) -> str:
    if not pares:
        return f"{titulo}: (sem gastos)"
    linhas = [f"  - {nome}: {formatar_reais(total)}" for nome, total in pares]
    return f"{titulo}:\n" + "\n".join(linhas)


def contexto_financeiro(db: Database, hoje: date) -> str:
    """Retrato completo das finanças, usado como contexto para o Claude."""
    ini_mes, fim_mes = stats.limites_do_mes(hoje)
    ini_sem, fim_sem = stats.limites_da_semana(hoje)
    gastos_mes = db.listar_gastos(ini_mes, fim_mes)
    gastos_semana = db.listar_gastos(ini_sem, fim_sem)

    partes = [f"Data de hoje: {hoje.isoformat()} ({_MESES[hoje.month - 1]})"]

    cartoes = db.listar_cartoes()
    if cartoes:
        linhas = []
        for v in stats.proximos_vencimentos(db, hoje):
            linhas.append(
                f"  - {v.cartao.nome} ({v.cartao.tipo}): vence em "
                f"{v.data.isoformat()} (daqui a {v.dias_restantes} dia(s))"
            )
        sem_venc = [c.nome for c in cartoes if not c.dia_vencimento]
        if sem_venc:
            linhas.append("  - Sem dia de vencimento cadastrado: " + ", ".join(sem_venc))
        partes.append("Cartões e contas cadastrados (próximos vencimentos):\n" + "\n".join(linhas))
    else:
        partes.append("Nenhum cartão ou conta cadastrado.")

    partes.append(
        f"Total do mês atual ({_MESES[hoje.month - 1]}): "
        f"{formatar_reais(stats.total_centavos(gastos_mes))}"
    )
    partes.append(_bloco_totais("Gastos do mês por cartão/conta", stats.total_por_cartao(gastos_mes)))
    partes.append(_bloco_totais("Gastos do mês por categoria", stats.total_por_categoria(gastos_mes)))
    partes.append(
        f"Total da semana atual: {formatar_reais(stats.total_centavos(gastos_semana))}"
    )
    partes.append(
        f"Média semanal (últimas 4 semanas completas): "
        f"{formatar_reais(stats.media_semanal_centavos(db, hoje))}"
    )
    partes.append(
        f"Média mensal (últimos 3 meses completos): "
        f"{formatar_reais(stats.media_mensal_centavos(db, hoje))}"
    )

    serie = stats.serie_mensal(db, hoje, 6)
    partes.append(
        "Totais dos últimos meses:\n"
        + "\n".join(
            f"  - {_MESES[d.month - 1]}/{d.year}: {formatar_reais(t)}" for d, t in serie
        )
    )

    ultimos = db.listar_gastos()[-15:]
    if ultimos:
        linhas = []
        for g in reversed(ultimos):
            onde = g.estabelecimento or g.descricao or "-"
            pagto = g.cartao_nome or g.forma_pagamento or "?"
            linhas.append(
                f"  - {g.data_compra.isoformat()}: {formatar_reais(g.valor_centavos)} "
                f"em {onde} ({g.categoria}, pago com {pagto})"
            )
        partes.append("Últimos lançamentos (mais recentes primeiro):\n" + "\n".join(linhas))

    return "\n\n".join(partes)


def _dados_periodo(db: Database, hoje: date, inicio: date, fim: date, rotulo: str) -> str:
    gastos = db.listar_gastos(inicio, fim)
    partes = [
        f"Período: {rotulo} ({inicio.isoformat()} a {fim.isoformat()})",
        f"Total gasto no período: {formatar_reais(stats.total_centavos(gastos))}",
        f"Quantidade de lançamentos: {len(gastos)}",
        _bloco_totais("Por cartão/conta", stats.total_por_cartao(gastos)),
        _bloco_totais("Por categoria", stats.total_por_categoria(gastos)),
        f"Média semanal (últimas 4 semanas completas): "
        f"{formatar_reais(stats.media_semanal_centavos(db, hoje))}",
        f"Média mensal (últimos 3 meses completos): "
        f"{formatar_reais(stats.media_mensal_centavos(db, hoje))}",
    ]
    vencimentos = [v for v in stats.proximos_vencimentos(db, hoje) if v.dias_restantes <= 10]
    if vencimentos:
        partes.append(
            "Vencimentos nos próximos 10 dias:\n"
            + "\n".join(
                f"  - {v.cartao.nome}: {v.data.isoformat()} (em {v.dias_restantes} dia(s))"
                for v in vencimentos
            )
        )
    return "\n\n".join(partes)


def _resumo_simples(db: Database, hoje: date, inicio: date, fim: date, titulo: str) -> str:
    """Resumo determinístico usado como fallback se a API falhar."""
    gastos = db.listar_gastos(inicio, fim)
    linhas = [
        f"📊 {titulo}",
        f"Total: {formatar_reais(stats.total_centavos(gastos))} em {len(gastos)} lançamento(s)",
    ]
    por_cat = stats.total_por_categoria(gastos)[:5]
    if por_cat:
        linhas.append("Por categoria:")
        linhas += [f"  • {nome}: {formatar_reais(t)}" for nome, t in por_cat]
    por_cartao = stats.total_por_cartao(gastos)[:5]
    if por_cartao:
        linhas.append("Por cartão/conta:")
        linhas += [f"  • {nome}: {formatar_reais(t)}" for nome, t in por_cartao]
    for v in stats.proximos_vencimentos(db, hoje)[:5]:
        linhas.append(
            f"⏰ {v.cartao.nome} vence em {v.data.strftime('%d/%m')} "
            f"({v.dias_restantes} dia(s))"
        )
    return "\n".join(linhas)


async def resumo_semanal(db: Database, hoje: date) -> str:
    """Resumo da semana atual (segunda até hoje)."""
    inicio, fim = stats.limites_da_semana(hoje)
    dados = _dados_periodo(db, hoje, inicio, min(fim, hoje), "semana atual")
    try:
        return await ia.redigir_resumo(dados, "semanal")
    except Exception:
        log.exception("Falha ao gerar resumo semanal com IA; usando fallback")
        return _resumo_simples(db, hoje, inicio, min(fim, hoje), "Resumo da semana")


async def resumo_mensal(db: Database, hoje: date, mes_anterior: bool = False) -> str:
    """Resumo do mês atual, ou do mês anterior (para o fechamento do dia 1º)."""
    ref = hoje
    if mes_anterior:
        ref = hoje.replace(day=1) - timedelta(days=1)
    inicio, fim = stats.limites_do_mes(ref)
    rotulo = f"mês de {_MESES[ref.month - 1]}/{ref.year}"
    dados = _dados_periodo(db, hoje, inicio, fim, rotulo)
    try:
        return await ia.redigir_resumo(dados, "mensal")
    except Exception:
        log.exception("Falha ao gerar resumo mensal com IA; usando fallback")
        return _resumo_simples(db, hoje, inicio, fim, f"Resumo do {rotulo}")
