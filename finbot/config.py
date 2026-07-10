"""Configuração via variáveis de ambiente (com suporte a arquivo .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_dotenv(caminho: Path) -> None:
    """Carrega um .env simples sem dependência externa."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave, valor = chave.strip(), valor.strip().strip("'\"")
        if chave and chave not in os.environ:
            os.environ[chave] = valor


@dataclass
class Config:
    telegram_token: str
    anthropic_api_key: str
    timezone: str = "America/Sao_Paulo"
    db_path: Path = field(default_factory=lambda: RAIZ / "data" / "financeiro.db")
    allowed_user_ids: frozenset[int] = frozenset()


def carregar_config() -> Config:
    _carregar_dotenv(RAIZ / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN não definido. Crie um bot com o @BotFather e "
            "configure o token no arquivo .env (veja .env.example)."
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "ANTHROPIC_API_KEY não definida. Gere uma chave em "
            "https://platform.claude.com/ e configure no arquivo .env."
        )

    ids_brutos = os.environ.get("ALLOWED_USER_IDS", "")
    allowed = frozenset(
        int(parte) for parte in ids_brutos.replace(";", ",").split(",") if parte.strip()
    )

    db_env = os.environ.get("DB_PATH", "").strip()
    db_path = Path(db_env) if db_env else RAIZ / "data" / "financeiro.db"

    return Config(
        telegram_token=token,
        anthropic_api_key=api_key,
        timezone=os.environ.get("TIMEZONE", "America/Sao_Paulo"),
        db_path=db_path,
        allowed_user_ids=allowed,
    )
