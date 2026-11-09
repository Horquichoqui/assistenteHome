"""Espelha os dados do bot numa planilha do Google Sheets (opcional, gratuito).

Cada mês ganha uma aba própria ("2026-11"). Gastos entram no mês da compra e as
contas do grupo do WhatsApp no mês do vencimento. A cada sincronização a aba do
mês é reescrita a partir do banco, que continua sendo a fonte de verdade.

Configuração: uma conta de serviço do Google (gratuita), com a chave JSON em
GOOGLE_CREDENCIAIS e a planilha compartilhada como Editor com o e-mail dela.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

CABECALHO = ["Tipo", "Data", "Descrição", "Categoria", "Responsável", "Cartão/Conta",
             "Parcela", "Valor (R$)", "Pago?", "Origem"]


def linhas_do_mes(db: Database, cfg: Config, inicio: date, fim: date) -> list[list]:
    """Linhas da aba de um mês: cabeçalho, gastos, contas e totais."""
    linhas: list[list] = [CABECALHO]
    total_gastos = 0
    for g in db.listar_gastos(inicio, fim):
        total_gastos += g.valor_centavos
        linhas.append([
            "Gasto", g.data_compra.strftime("%d/%m/%Y"),
            g.estabelecimento or g.descricao or "", g.categoria,
            cfg.nome_pessoa(g.responsavel) if g.responsavel else "",
            g.cartao_nome or g.forma_pagamento or "", g.parcela_rotulo or "",
            g.valor, "Sim" if g.pago else "Não", g.origem,
        ])
    a_pagar = 0
    for c in db.listar_contas_a_pagar(inicio, fim):
        if not c.pago:
            a_pagar += c.valor_centavos or 0
        linhas.append([
            "Conta", c.vencimento.strftime("%d/%m/%Y"), c.descricao, "contas", "", "", "",
            c.valor if c.valor is not None else "", "Sim" if c.pago else "Não", c.origem,
        ])
    linhas += [[], ["Total de gastos", "", "", "", "", "", "", total_gastos / 100],
               ["Contas a pagar", "", "", "", "", "", "", a_pagar / 100]]
    return linhas


def meses_com_dados(db: Database) -> list[tuple[date, date]]:
    """(início, fim) de cada mês que tem gasto ou conta, do mais antigo ao mais novo."""
    from . import stats

    datas = [g.data_compra for g in db.listar_gastos()]
    datas += [c.vencimento for c in db.listar_contas_a_pagar(date(1900, 1, 1), date(9999, 12, 31))]
    meses = sorted({d.replace(day=1) for d in datas})
    return [stats.limites_do_mes(m) for m in meses]


def sincronizar(db: Database, cfg: Config, cliente=None) -> Optional[str]:
    """Reescreve as abas mensais. Retorna um erro legível, ou None se deu certo
    (ou se a planilha não está configurada)."""
    if not cfg.sheets_id:
        return None
    try:
        if cliente is None:
            import gspread

            if not cfg.sheets_credenciais.exists():
                return f"chave da conta de serviço não encontrada em {cfg.sheets_credenciais}"
            cliente = gspread.service_account(filename=str(cfg.sheets_credenciais))
        planilha = cliente.open_by_key(cfg.sheets_id)
        existentes = {ws.title: ws for ws in planilha.worksheets()}
        for inicio, fim in meses_com_dados(db):
            nome = f"{inicio:%Y-%m}"
            linhas = linhas_do_mes(db, cfg, inicio, fim)
            ws = existentes.get(nome) or planilha.add_worksheet(
                title=nome, rows=len(linhas) + 20, cols=len(CABECALHO)
            )
            ws.clear()
            ws.update(linhas, "A1")
        return None
    except Exception as exc:  # a planilha online não pode derrubar o bot
        log.exception("Falha ao atualizar a planilha do Google")
        return f"{type(exc).__name__}: {exc}"
