from datetime import date

from bot import _data_da_compra, _parse_cadastro, _resolver_responsavel
from finbot.config import Config
from finbot.ia import GastoExtraido


def _config_casal() -> Config:
    return Config(
        telegram_token="x",
        pessoas_por_id={111: "gabriel", 222: "maria_eduarda"},
        nomes_pessoas={"gabriel": "Gabriel", "maria_eduarda": "Maria Eduarda"},
        ordem_pessoas=["gabriel", "maria_eduarda"],
    )


def test_resolver_responsavel_pelo_remetente_quando_ia_nao_extraiu_nada():
    cfg = _config_casal()
    assert _resolver_responsavel(cfg, None, 111) == "gabriel"
    assert _resolver_responsavel(cfg, None, 222) == "maria_eduarda"
    assert _resolver_responsavel(cfg, None, 999) is None


def test_resolver_responsavel_pelo_nome_extraido_pela_ia():
    cfg = _config_casal()
    # A IA identificou o nome de uma pessoa específica, mesmo remetente sendo outro
    assert _resolver_responsavel(cfg, "Maria Eduarda", 111) == "maria_eduarda"
    assert _resolver_responsavel(cfg, "maria eduarda", 111) == "maria_eduarda"
    assert _resolver_responsavel(cfg, "Gabriel", 222) == "gabriel"


def test_resolver_responsavel_combinado():
    cfg = _config_casal()
    for sinonimo in ("combinado", "conjunto", "juntos", "dividido", "nosso", "casa"):
        assert _resolver_responsavel(cfg, sinonimo, 111) == "combinado"


def test_parse_cadastro():
    assert _parse_cadastro(["Nubank", "10"]) == ("Nubank", 10, None)
    assert _parse_cadastro(["Nubank", "Roxinho", "10", "3"]) == ("Nubank Roxinho", 10, 3)
    assert _parse_cadastro(["Nubank", "40"]) is None  # dia inválido
    assert _parse_cadastro(["10"]) is None  # sem nome
    assert _parse_cadastro([]) is None


def test_data_da_compra_usa_extraida_quando_plausivel():
    hoje = date(2026, 7, 10)
    extraido = GastoExtraido(data_compra="2026-07-05")
    assert _data_da_compra(extraido, hoje) == date(2026, 7, 5)


def test_data_da_compra_ignora_data_absurda_ou_invalida():
    hoje = date(2026, 7, 10)
    assert _data_da_compra(GastoExtraido(data_compra=None), hoje) == hoje
    assert _data_da_compra(GastoExtraido(data_compra="não é uma data"), hoje) == hoje
    # mais de 366 dias de diferença é descartado
    assert _data_da_compra(GastoExtraido(data_compra="2020-01-01"), hoje) == hoje
