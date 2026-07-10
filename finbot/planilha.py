"""Geração da planilha financeira (.xlsx) com openpyxl."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import stats
from .db import Database

_FORMATO_MOEDA = '"R$" #,##0.00'
_COR_CABECALHO = "1F4E78"


def _cabecalho(ws, titulos: list[str], larguras: list[int]) -> None:
    fill = PatternFill("solid", fgColor=_COR_CABECALHO)
    fonte = Font(bold=True, color="FFFFFF")
    for col, (titulo, largura) in enumerate(zip(titulos, larguras), start=1):
        celula = ws.cell(row=1, column=col, value=titulo)
        celula.font = fonte
        celula.fill = fill
        celula.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(col)].width = largura
    ws.freeze_panes = "A2"


def gerar_planilha(db: Database, destino: Path, hoje: date) -> Path:
    wb = Workbook()

    # --- Lançamentos ---
    ws = wb.active
    ws.title = "Lançamentos"
    _cabecalho(
        ws,
        ["Data", "Valor", "Estabelecimento", "Categoria", "Cartão/Conta", "Descrição", "Origem"],
        [12, 14, 28, 16, 18, 32, 10],
    )
    linha = 2
    for g in db.listar_gastos():
        ws.cell(row=linha, column=1, value=g.data_compra).number_format = "DD/MM/YYYY"
        ws.cell(row=linha, column=2, value=g.valor).number_format = _FORMATO_MOEDA
        ws.cell(row=linha, column=3, value=g.estabelecimento)
        ws.cell(row=linha, column=4, value=g.categoria)
        ws.cell(row=linha, column=5, value=g.cartao_nome or g.forma_pagamento)
        ws.cell(row=linha, column=6, value=g.descricao)
        ws.cell(row=linha, column=7, value=g.origem)
        linha += 1

    # --- Cartões e contas (dias de pagamento) ---
    ws = wb.create_sheet("Cartões e Contas")
    _cabecalho(
        ws,
        ["Nome", "Tipo", "Dia de vencimento", "Dia de fechamento", "Próximo vencimento", "Gasto no mês atual"],
        [20, 10, 18, 18, 20, 20],
    )
    ini_mes, fim_mes = stats.limites_do_mes(hoje)
    gastos_mes = db.listar_gastos(ini_mes, fim_mes)
    por_cartao_mes = dict(stats.total_por_cartao(gastos_mes))
    linha = 2
    for c in db.listar_cartoes():
        proximo = stats.proximo_vencimento(c, hoje)
        ws.cell(row=linha, column=1, value=c.nome)
        ws.cell(row=linha, column=2, value=c.tipo)
        ws.cell(row=linha, column=3, value=c.dia_vencimento)
        ws.cell(row=linha, column=4, value=c.dia_fechamento)
        if proximo:
            ws.cell(row=linha, column=5, value=proximo).number_format = "DD/MM/YYYY"
        total = por_cartao_mes.get(c.nome, 0)
        ws.cell(row=linha, column=6, value=total / 100).number_format = _FORMATO_MOEDA
        linha += 1

    # --- Resumo mensal ---
    ws = wb.create_sheet("Resumo Mensal")
    _cabecalho(ws, ["Mês", "Total gasto", "Média mensal (3 meses)"], [14, 16, 22])
    linha = 2
    for inicio, total in stats.serie_mensal(db, hoje, 12):
        ws.cell(row=linha, column=1, value=inicio.strftime("%m/%Y"))
        ws.cell(row=linha, column=2, value=total / 100).number_format = _FORMATO_MOEDA
        linha += 1
    ws.cell(row=2, column=3, value=stats.media_mensal_centavos(db, hoje) / 100).number_format = _FORMATO_MOEDA

    # --- Resumo semanal ---
    ws = wb.create_sheet("Resumo Semanal")
    _cabecalho(ws, ["Semana (segunda-feira)", "Total gasto", "Média semanal (4 semanas)"], [22, 16, 24])
    linha = 2
    for inicio, total in stats.serie_semanal(db, hoje, 12):
        ws.cell(row=linha, column=1, value=inicio).number_format = "DD/MM/YYYY"
        ws.cell(row=linha, column=2, value=total / 100).number_format = _FORMATO_MOEDA
        linha += 1
    ws.cell(row=2, column=3, value=stats.media_semanal_centavos(db, hoje) / 100).number_format = _FORMATO_MOEDA

    # --- Categorias do mês ---
    ws = wb.create_sheet("Categorias do Mês")
    _cabecalho(ws, ["Categoria", "Total no mês atual"], [20, 20])
    linha = 2
    for nome, total in stats.total_por_categoria(gastos_mes):
        ws.cell(row=linha, column=1, value=nome)
        ws.cell(row=linha, column=2, value=total / 100).number_format = _FORMATO_MOEDA
        linha += 1

    destino.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destino)
    return destino
