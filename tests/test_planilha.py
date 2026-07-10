from datetime import date

from openpyxl import load_workbook

from finbot.db import Database
from finbot.planilha import gerar_planilha


def test_gerar_planilha_completa(tmp_path):
    db = Database(tmp_path / "teste.db")
    nubank = db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10, dia_fechamento=3)
    db.adicionar_cartao("Luz", "conta", dia_vencimento=15)
    db.adicionar_gasto(
        4590,
        date(2026, 7, 8),
        estabelecimento="Supermercado X",
        categoria="mercado",
        cartao_id=nubank.id,
        descricao="compra da semana",
    )
    db.adicionar_gasto(12000, date(2026, 7, 9), categoria="contas", forma_pagamento="pix")

    destino = tmp_path / "planilha.xlsx"
    gerar_planilha(db, destino, hoje=date(2026, 7, 10))

    wb = load_workbook(destino)
    assert wb.sheetnames == [
        "Lançamentos",
        "Cartões e Contas",
        "Resumo Mensal",
        "Resumo Semanal",
        "Categorias do Mês",
    ]

    lanc = wb["Lançamentos"]
    assert lanc.cell(row=2, column=2).value == 45.90
    assert lanc.cell(row=2, column=3).value == "Supermercado X"
    assert lanc.cell(row=2, column=5).value == "Nubank"
    assert lanc.cell(row=3, column=5).value == "pix"

    cartoes = wb["Cartões e Contas"]
    nomes = {cartoes.cell(row=r, column=1).value for r in (2, 3)}
    assert nomes == {"Nubank", "Luz"}

    # Gasto do mês atual aparece na linha do Nubank
    for r in (2, 3):
        if cartoes.cell(row=r, column=1).value == "Nubank":
            assert cartoes.cell(row=r, column=6).value == 45.90

    categorias = wb["Categorias do Mês"]
    assert categorias.cell(row=2, column=1).value == "contas"
    assert categorias.cell(row=2, column=2).value == 120.0
