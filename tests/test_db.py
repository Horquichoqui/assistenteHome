import sqlite3
from datetime import date

import pytest

from finbot.db import Database, _somar_meses


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


def test_gasto_tem_valores_padrao_de_responsavel_e_pago(db):
    gid = db.adicionar_gasto(1000, date(2026, 7, 1))
    gasto = db.listar_gastos()[0]
    assert gasto.id == gid
    assert gasto.responsavel is None
    assert gasto.pago is True
    assert gasto.parcela_atual is None
    assert gasto.parcela_total is None


def test_migracao_de_banco_antigo_sem_as_novas_colunas(tmp_path):
    """Um banco criado antes das colunas responsavel/pago/parcela existirem
    deve ganhar essas colunas automaticamente, sem perder os dados."""
    caminho = tmp_path / "antigo.db"
    conn = sqlite3.connect(caminho)
    conn.executescript(
        """
        CREATE TABLE cartoes (
            id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
            tipo TEXT NOT NULL DEFAULT 'cartao', dia_vencimento INTEGER, dia_fechamento INTEGER
        );
        CREATE TABLE gastos (
            id INTEGER PRIMARY KEY, registrado_em TEXT NOT NULL, data_compra TEXT NOT NULL,
            valor_centavos INTEGER NOT NULL, estabelecimento TEXT,
            categoria TEXT NOT NULL DEFAULT 'outros', forma_pagamento TEXT,
            cartao_id INTEGER, descricao TEXT, origem TEXT NOT NULL DEFAULT 'foto'
        );
        CREATE TABLE ajustes (chave TEXT PRIMARY KEY, valor TEXT);
        INSERT INTO gastos (registrado_em, data_compra, valor_centavos, estabelecimento, origem)
        VALUES ('2026-01-01T10:00:00', '2026-01-01', 5000, 'Loja Antiga', 'foto');
        """
    )
    conn.commit()
    conn.close()

    db = Database(caminho)
    gastos = db.listar_gastos()
    assert len(gastos) == 1
    assert gastos[0].estabelecimento == "Loja Antiga"
    assert gastos[0].pago is True
    assert gastos[0].responsavel is None

    # E as novas funcionalidades já funcionam nesse banco migrado.
    novo_id = db.adicionar_gasto(2000, date(2026, 7, 1), responsavel="gabriel", pago=False)
    assert len(db.listar_pendentes()) == 1
    db.marcar_pago(novo_id)
    assert len(db.listar_pendentes()) == 0


def test_adicionar_gasto_parcelado_soma_o_valor_total(db):
    ids = db.adicionar_gasto_parcelado(
        valor_total_centavos=1000,
        parcelas=3,
        data_primeira_parcela=date(2026, 1, 1),
        estabelecimento="Loja",
        responsavel="maria_eduarda",
    )
    assert len(ids) == 3
    parcelas = db.listar_gastos()
    assert sum(p.valor_centavos for p in parcelas) == 1000
    assert [p.parcela_rotulo for p in parcelas] == ["1/3", "2/3", "3/3"]
    assert [p.data_compra for p in parcelas] == [
        date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)
    ]
    assert all(p.responsavel == "maria_eduarda" for p in parcelas)
    # todas as parcelas pertencem ao mesmo grupo
    assert len({p.grupo_parcelamento for p in parcelas}) == 1


def test_somar_meses_lida_com_fim_de_mes():
    assert _somar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert _somar_meses(date(2026, 1, 31), 11) == date(2026, 12, 31)
    assert _somar_meses(date(2026, 12, 15), 2) == date(2027, 2, 15)


def test_pendentes_e_marcar_pago(db):
    pendente_id = db.adicionar_gasto(1000, date(2026, 7, 1), pago=False)
    db.adicionar_gasto(2000, date(2026, 7, 2), pago=True)

    pendentes = db.listar_pendentes()
    assert len(pendentes) == 1
    assert pendentes[0].id == pendente_id

    assert db.marcar_pago(pendente_id) is True
    assert db.listar_pendentes() == []
    assert db.marcar_pago(9999) is False


def test_renda(db):
    assert db.listar_rendas() == []
    db.definir_renda("gabriel", 550000, "Salário")
    db.definir_renda("maria_eduarda", 420000, "Salário")
    db.definir_renda("gabriel", 560000, "Salário reajustado")  # atualiza, não duplica

    rendas = {r.pessoa: r.valor_centavos for r in db.listar_rendas()}
    assert rendas == {"gabriel": 560000, "maria_eduarda": 420000}


def test_orcamentos(db):
    assert db.listar_tetos() == {}
    db.definir_teto("mercado", 80000)
    db.definir_teto("Mercado", 90000)  # case-insensitive: atualiza o mesmo
    assert db.listar_tetos() == {"mercado": 90000}


def test_investimentos(db):
    assert db.listar_investimentos() == []
    db.definir_investimento("Tesouro Selic", 500000)
    db.definir_investimento("CDB Banco X", 300000)
    assert db.listar_investimentos() == [
        ("CDB Banco X", 300000),
        ("Tesouro Selic", 500000),
    ]


def test_ultimo_valor_pago(db):
    conta = db.adicionar_cartao("Luz", tipo="conta", dia_vencimento=15)
    assert db.ultimo_valor_pago(conta.id) is None
    db.adicionar_gasto(15000, date(2026, 6, 15), cartao_id=conta.id)
    db.adicionar_gasto(16000, date(2026, 7, 15), cartao_id=conta.id)
    assert db.ultimo_valor_pago(conta.id) == 16000
