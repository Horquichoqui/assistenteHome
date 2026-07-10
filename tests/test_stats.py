from datetime import date

import pytest

from finbot import stats
from finbot.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "teste.db")


def test_formatar_reais():
    assert stats.formatar_reais(4590) == "R$ 45,90"
    assert stats.formatar_reais(123456789) == "R$ 1.234.567,89"
    assert stats.formatar_reais(0) == "R$ 0,00"


def test_limites_do_mes_e_da_semana():
    assert stats.limites_do_mes(date(2026, 2, 15)) == (date(2026, 2, 1), date(2026, 2, 28))
    # 10/07/2026 é uma sexta-feira; semana de segunda (06) a domingo (12)
    assert stats.limites_da_semana(date(2026, 7, 10)) == (date(2026, 7, 6), date(2026, 7, 12))


def test_totais_por_cartao_e_categoria(db):
    nubank = db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10)
    db.adicionar_gasto(5000, date(2026, 7, 1), categoria="mercado", cartao_id=nubank.id)
    db.adicionar_gasto(3000, date(2026, 7, 2), categoria="mercado", forma_pagamento="pix")
    db.adicionar_gasto(2000, date(2026, 7, 3), categoria="lazer", cartao_id=nubank.id)

    gastos = db.listar_gastos()
    assert stats.total_centavos(gastos) == 10000
    assert stats.total_por_cartao(gastos) == [("Nubank", 7000), ("pix", 3000)]
    assert stats.total_por_categoria(gastos) == [("mercado", 8000), ("lazer", 2000)]


def test_series_e_medias(db):
    hoje = date(2026, 7, 10)  # sexta-feira
    # Semana atual (06-12/07) e duas semanas anteriores
    db.adicionar_gasto(1000, date(2026, 7, 8))   # semana atual
    db.adicionar_gasto(2000, date(2026, 7, 1))   # semana de 29/06
    db.adicionar_gasto(4000, date(2026, 6, 24))  # semana de 22/06

    serie = stats.serie_semanal(db, hoje, semanas=3)
    assert serie == [
        (date(2026, 6, 22), 4000),
        (date(2026, 6, 29), 2000),
        (date(2026, 7, 6), 1000),
    ]
    # Média das 4 últimas semanas completas: (0 + 0 + 4000 + 2000) / 4
    assert stats.media_semanal_centavos(db, hoje, semanas=4) == 1500

    mensal = stats.serie_mensal(db, hoje, meses=2)
    assert mensal == [(date(2026, 6, 1), 4000), (date(2026, 7, 1), 3000)]
    # Média dos 3 últimos meses completos: (0 + 0 + 4000) / 3
    assert stats.media_mensal_centavos(db, hoje, meses=3) == 1333


def test_proximos_vencimentos(db):
    db.adicionar_cartao("Nubank", "cartao", dia_vencimento=15)
    db.adicionar_cartao("Luz", "conta", dia_vencimento=5)
    db.adicionar_cartao("Sem Dia", "conta")

    hoje = date(2026, 7, 10)
    vencimentos = stats.proximos_vencimentos(db, hoje)
    assert [(v.cartao.nome, v.data, v.dias_restantes) for v in vencimentos] == [
        ("Nubank", date(2026, 7, 15), 5),
        ("Luz", date(2026, 8, 5), 26),
    ]


def test_vencimento_em_dia_inexistente_no_mes(db):
    cartao = db.adicionar_cartao("Fatura", "conta", dia_vencimento=31)
    # Fevereiro não tem dia 31 → vence no último dia do mês
    assert stats.proximo_vencimento(cartao, date(2026, 2, 10)) == date(2026, 2, 28)
