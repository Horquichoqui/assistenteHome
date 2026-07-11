"""Configuração via variáveis de ambiente (com suporte a arquivo .env)."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

RAIZ = Path(__file__).resolve().parent.parent

PESSOA_COMBINADO = "combinado"


def slug_pessoa(nome: str) -> str:
    """Normaliza um nome para uma chave interna estável (ex.: 'Maria Eduarda' -> 'maria_eduarda')."""
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    return "_".join(sem_acento.strip().lower().split())


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
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    timezone: str = "America/Sao_Paulo"
    db_path: Path = field(default_factory=lambda: RAIZ / "data" / "financeiro.db")
    allowed_user_ids: frozenset[int] = frozenset()
    # Mapeamento para o modo "casal": id do Telegram -> chave interna da pessoa.
    pessoas_por_id: dict[int, str] = field(default_factory=dict)
    # Chave interna -> nome de exibição (ex.: "maria_eduarda" -> "Maria Eduarda").
    nomes_pessoas: dict[str, str] = field(default_factory=dict)
    # Ordem de cadastro, para comandos como "/renda 1 ..." / "/renda 2 ...".
    ordem_pessoas: list[str] = field(default_factory=list)

    def pessoa_do_chat(self, user_id: Optional[int]) -> Optional[str]:
        """Chave interna da pessoa dona desse ID do Telegram, se configurada."""
        if user_id is None:
            return None
        return self.pessoas_por_id.get(user_id)

    def pessoa_por_numero(self, numero: int) -> Optional[str]:
        """Resolve '1'/'2' (usado em comandos) para a chave interna da pessoa."""
        if 1 <= numero <= len(self.ordem_pessoas):
            return self.ordem_pessoas[numero - 1]
        return None

    def nome_pessoa(self, chave: Optional[str]) -> str:
        if not chave:
            return "não informado"
        if chave == PESSOA_COMBINADO:
            return "Combinado"
        return self.nomes_pessoas.get(chave, chave)


def carregar_config() -> Config:
    _carregar_dotenv(RAIZ / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN não definido. Crie um bot com o @BotFather e "
            "configure o token no arquivo .env (veja .env.example)."
        )

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    gemini_key = os.environ.get("GEMINI_API_KEY", "")
    if not anthropic_key and not gemini_key:
        raise SystemExit(
            "Nenhuma chave de IA definida. Configure no .env UMA das opções:\n"
            "- GEMINI_API_KEY (GRATUITA — gere em https://aistudio.google.com/); ou\n"
            "- ANTHROPIC_API_KEY (paga, melhor leitura — https://platform.claude.com/)."
        )

    ids_brutos = os.environ.get("ALLOWED_USER_IDS", "")
    allowed = frozenset(
        int(parte) for parte in ids_brutos.replace(";", ",").split(",") if parte.strip()
    )

    db_env = os.environ.get("DB_PATH", "").strip()
    db_path = Path(db_env) if db_env else RAIZ / "data" / "financeiro.db"

    pessoas_por_id: dict[int, str] = {}
    nomes_pessoas: dict[str, str] = {}
    ordem_pessoas: list[str] = []
    for n in (1, 2):
        id_bruto = os.environ.get(f"PESSOA_{n}_ID", "").strip()
        nome_bruto = os.environ.get(f"PESSOA_{n}_NOME", "").strip()
        if not id_bruto or not nome_bruto:
            continue
        chave = slug_pessoa(nome_bruto)
        pessoas_por_id[int(id_bruto)] = chave
        nomes_pessoas[chave] = nome_bruto
        ordem_pessoas.append(chave)

    return Config(
        telegram_token=token,
        anthropic_api_key=anthropic_key,
        gemini_api_key=gemini_key,
        timezone=os.environ.get("TIMEZONE", "America/Sao_Paulo"),
        db_path=db_path,
        allowed_user_ids=allowed,
        pessoas_por_id=pessoas_por_id,
        nomes_pessoas=nomes_pessoas,
        ordem_pessoas=ordem_pessoas,
    )
