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


def test_total_por_pessoa(db):
    db.adicionar_gasto(1000, date(2026, 7, 1), responsavel="gabriel")
    db.adicionar_gasto(2000, date(2026, 7, 2), responsavel="gabriel")
    db.adicionar_gasto(500, date(2026, 7, 3), responsavel="maria_eduarda")
    db.adicionar_gasto(300, date(2026, 7, 4))  # sem responsável

    gastos = db.listar_gastos()
    assert stats.total_por_pessoa(gastos) == [
        ("gabriel", 3000),
        ("maria_eduarda", 500),
        (None, 300),
    ]


def test_total_renda_e_investimentos(db):
    db.definir_renda("gabriel", 550000)
    db.definir_renda("maria_eduarda", 420000)
    assert stats.total_renda_centavos(db.listar_rendas()) == 970000

    db.definir_investimento("Tesouro", 500000)
    db.definir_investimento("CDB", 100000)
    assert stats.total_investimentos_centavos(db.listar_investimentos()) == 600000


def test_percentual():
    assert stats.percentual(50, 200) == 25.0
    assert stats.percentual(50, 0) == 0.0


def test_classificar_fixos_variaveis(db):
    conta = db.adicionar_cartao("Luz", tipo="conta", dia_vencimento=15)
    cartao = db.adicionar_cartao("Nubank", tipo="cartao", dia_vencimento=10)

    db.adicionar_gasto(1000, date(2026, 7, 1), cartao_id=conta.id, estabelecimento="Conta de luz")
    db.adicionar_gasto(2000, date(2026, 7, 2), cartao_id=cartao.id, estabelecimento="Mercado")
    db.adicionar_gasto(3000, date(2026, 7, 3), estabelecimento="Presente")  # sem cartão
    db.adicionar_gasto_parcelado(
        9000, 3, date(2026, 7, 1), estabelecimento="Notebook", cartao_id=cartao.id
    )

    gastos = db.listar_gastos()
    cartoes = db.listar_cartoes()
    fixos, variaveis = stats.classificar_fixos_variaveis(gastos, cartoes)

    assert {g.estabelecimento for g in fixos} == {"Conta de luz", "Notebook"}
    assert {g.estabelecimento for g in variaveis} == {"Mercado", "Presente"}


def test_radar_categorias(db):
    db.adicionar_gasto(6000, date(2026, 7, 1), categoria="mercado")
    db.adicionar_gasto(1000, date(2026, 7, 2), categoria="lazer")
    db.definir_teto("mercado", 5000)

    gastos = db.listar_gastos()
    radar = {r.categoria: r for r in stats.radar_categorias(gastos, db.listar_tetos())}

    assert radar["mercado"].gasto_centavos == 6000
    assert radar["mercado"].teto_centavos == 5000
    assert radar["mercado"].percentual_do_teto == 120.0
    assert radar["mercado"].sinal == "🔴"

    assert radar["lazer"].teto_centavos is None
    assert radar["lazer"].percentual_do_teto is None
    assert radar["lazer"].sinal == "-"
