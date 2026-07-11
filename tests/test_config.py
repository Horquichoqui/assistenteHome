from finbot.config import Config, slug_pessoa


def test_slug_pessoa():
    assert slug_pessoa("Maria Eduarda") == "maria_eduarda"
    assert slug_pessoa("Gabriel") == "gabriel"
    assert slug_pessoa("João") == "joao"


def test_config_pessoas():
    cfg = Config(
        telegram_token="x",
        pessoas_por_id={111: "gabriel", 222: "maria_eduarda"},
        nomes_pessoas={"gabriel": "Gabriel", "maria_eduarda": "Maria Eduarda"},
        ordem_pessoas=["gabriel", "maria_eduarda"],
    )
    assert cfg.pessoa_do_chat(111) == "gabriel"
    assert cfg.pessoa_do_chat(222) == "maria_eduarda"
    assert cfg.pessoa_do_chat(999) is None
    assert cfg.pessoa_do_chat(None) is None

    assert cfg.pessoa_por_numero(1) == "gabriel"
    assert cfg.pessoa_por_numero(2) == "maria_eduarda"
    assert cfg.pessoa_por_numero(3) is None

    assert cfg.nome_pessoa("gabriel") == "Gabriel"
    assert cfg.nome_pessoa("combinado") == "Combinado"
    assert cfg.nome_pessoa(None) == "não informado"
    assert cfg.nome_pessoa("chave_desconhecida") == "chave_desconhecida"


def test_config_sem_pessoas():
    cfg = Config(telegram_token="x")
    assert cfg.pessoa_do_chat(111) is None
    assert cfg.pessoa_por_numero(1) is None
    assert cfg.ordem_pessoas == []
