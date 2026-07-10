import pytest

from finbot import ia, ia_claude, ia_gemini
from finbot.ia_gemini import limpar_json


def test_limpar_json_sem_cerca():
    assert limpar_json('{"a": 1}') == '{"a": 1}'


def test_limpar_json_com_cerca():
    assert limpar_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert limpar_json('```\n{"a": 1}\n```') == '{"a": 1}'


def test_escolha_de_provedor(monkeypatch):
    monkeypatch.delenv("IA_PROVIDER", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(RuntimeError):
        ia._backend()

    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert ia._backend() is ia_gemini

    # Com as duas chaves, o Claude tem prioridade...
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert ia._backend() is ia_claude

    # ...a menos que IA_PROVIDER force o Gemini.
    monkeypatch.setenv("IA_PROVIDER", "gemini")
    assert ia._backend() is ia_gemini
    monkeypatch.setenv("IA_PROVIDER", "claude")
    assert ia._backend() is ia_claude


def test_schema_dos_modelos_extraidos():
    # Garante que os modelos usados nos dois provedores continuam serializáveis
    schema = ia.Interpretacao.model_json_schema()
    assert "intencao" in schema["properties"]
    gasto = ia.GastoExtraido(valor_total=45.9, categoria="mercado")
    assert gasto.valor_total == 45.9
