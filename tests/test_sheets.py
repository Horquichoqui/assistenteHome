from datetime import date

from finbot import sheets
from finbot.config import Config, _id_planilha
from finbot.db import Database


class _Aba:
    def __init__(self, title):
        self.title, self.dados = title, None

    def clear(self):
        self.dados = None

    def update(self, valores, intervalo):
        assert intervalo == "A1"
        self.dados = valores


class _Planilha:
    def __init__(self):
        self.abas = [_Aba("Página1")]

    def worksheets(self):
        return list(self.abas)

    def add_worksheet(self, title, rows, cols):
        aba = _Aba(title)
        self.abas.append(aba)
        return aba


class _Cliente:
    def __init__(self):
        self.planilha = _Planilha()

    def open_by_key(self, chave):
        assert chave == "abc123"
        return self.planilha


def test_id_planilha_aceita_link():
    link = "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing"
    assert _id_planilha(link) == "abc123"
    assert _id_planilha(" abc123 ") == "abc123"


def test_sincronizar_cria_uma_aba_por_mes(tmp_path):
    db = Database(tmp_path / "t.db")
    db.adicionar_gasto(5000, date(2026, 10, 3), estabelecimento="Mercado")
    db.adicionar_gasto_parcelado(3000, 2, date(2026, 10, 20), estabelecimento="Fone")  # out e nov
    db.registrar_conta_a_pagar("Luz", date(2026, 11, 15), 18000)
    cfg = Config(telegram_token="x", sheets_id="abc123")
    cliente = _Cliente()

    assert sheets.sincronizar(db, cfg, cliente) is None
    abas = {a.title: a.dados for a in cliente.planilha.abas}
    assert set(abas) == {"Página1", "2026-10", "2026-11"}
    outubro = [l for l in abas["2026-10"] if l and l[0] in ("Gasto", "Conta")]
    novembro = [l for l in abas["2026-11"] if l and l[0] in ("Gasto", "Conta")]
    assert [l[2] for l in outubro] == ["Mercado", "Fone"]
    assert [(l[0], l[2]) for l in novembro] == [("Gasto", "Fone"), ("Conta", "Luz")]

    # rodar de novo reescreve as mesmas abas, sem duplicar
    assert sheets.sincronizar(db, cfg, cliente) is None
    assert len(cliente.planilha.abas) == 3


def test_sem_planilha_configurada_nao_faz_nada(tmp_path):
    assert sheets.sincronizar(Database(tmp_path / "t.db"), Config(telegram_token="x")) is None


def test_conta_mostra_forma_de_pagamento(tmp_path):
    from finbot.db import Database
    from finbot.sheets import linhas_do_mes

    db = Database(tmp_path / "f.db")
    db.registrar_conta_a_pagar("Nayara", date(2026, 11, 9), 83100, pago=True, forma="Pix")
    conta = [l for l in linhas_do_mes(db, Config(telegram_token="x"), date(2026, 11, 1), date(2026, 11, 30)) if l and l[0] == "Conta"]
    assert conta[0][2] == "Nayara" and conta[0][5] == "Pix" and conta[0][7] == 831.0
