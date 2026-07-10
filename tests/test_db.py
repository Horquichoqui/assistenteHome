from datetime import date

import pytest

from finbot.db import Database


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "teste.db")


def test_cadastro_e_listagem_de_cartoes(db):
    db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10, dia_fechamento=3)
    db.adicionar_cartao("Conta de Luz", "conta", dia_vencimento=15)

    cartoes = db.listar_cartoes()
    assert [c.nome for c in cartoes] == ["Conta de Luz", "Nubank"]
    nubank = next(c for c in cartoes if c.nome == "Nubank")
    assert nubank.dia_vencimento == 10
    assert nubank.dia_fechamento == 3


def test_cadastro_atualiza_existente(db):
    db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10)
    db.adicionar_cartao("nubank", "cartao", dia_vencimento=12)
    cartoes = db.listar_cartoes()
    assert len(cartoes) == 1
    assert cartoes[0].dia_vencimento == 12


def test_buscar_cartao_por_texto_livre(db):
    db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10)
    db.adicionar_cartao("Itaú Crédito", "cartao", dia_vencimento=5)

    assert db.buscar_cartao("paguei no nubank crédito").nome == "Nubank"
    assert db.buscar_cartao("NUBANK").nome == "Nubank"
    assert db.buscar_cartao("itaú").nome == "Itaú Crédito"
    assert db.buscar_cartao("pix") is None
    assert db.buscar_cartao(None) is None


def test_gastos_com_filtro_de_datas(db):
    cartao = db.adicionar_cartao("Nubank", "cartao", dia_vencimento=10)
    db.adicionar_gasto(5000, date(2026, 7, 1), estabelecimento="Mercado A", cartao_id=cartao.id)
    db.adicionar_gasto(3000, date(2026, 7, 15), estabelecimento="Mercado B")
    db.adicionar_gasto(2000, date(2026, 8, 1), estabelecimento="Mercado C")

    julho = db.listar_gastos(date(2026, 7, 1), date(2026, 7, 31))
    assert len(julho) == 2
    assert julho[0].cartao_nome == "Nubank"
    assert julho[0].valor == 50.0

    todos = db.listar_gastos()
    assert len(todos) == 3


def test_remover_ultimo_gasto(db):
    db.adicionar_gasto(1000, date(2026, 7, 1), estabelecimento="A")
    db.adicionar_gasto(2000, date(2026, 7, 2), estabelecimento="B")

    removido = db.remover_ultimo_gasto()
    assert removido.estabelecimento == "B"
    assert len(db.listar_gastos()) == 1
    db.remover_ultimo_gasto()
    assert db.remover_ultimo_gasto() is None


def test_ajustes(db):
    assert db.obter_ajuste("chat_id_dono") is None
    db.definir_ajuste("chat_id_dono", "123")
    db.definir_ajuste("chat_id_dono", "456")
    assert db.obter_ajuste("chat_id_dono") == "456"
