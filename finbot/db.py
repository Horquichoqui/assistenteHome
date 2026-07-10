"""Armazenamento em SQLite: cartões/contas, gastos e configurações."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cartoes (
    id INTEGER PRIMARY KEY,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
    tipo TEXT NOT NULL DEFAULT 'cartao',      -- 'cartao' | 'conta'
    dia_vencimento INTEGER,                   -- 1..31
    dia_fechamento INTEGER                    -- 1..31 (opcional, cartões)
);

CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY,
    registrado_em TEXT NOT NULL,              -- ISO datetime
    data_compra TEXT NOT NULL,                -- ISO date (YYYY-MM-DD)
    valor_centavos INTEGER NOT NULL,
    estabelecimento TEXT,
    categoria TEXT NOT NULL DEFAULT 'outros',
    forma_pagamento TEXT,                     -- texto livre informado/extraído
    cartao_id INTEGER REFERENCES cartoes(id) ON DELETE SET NULL,
    descricao TEXT,
    origem TEXT NOT NULL DEFAULT 'foto'       -- 'foto' | 'texto' | 'comando'
);

CREATE TABLE IF NOT EXISTS ajustes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE INDEX IF NOT EXISTS idx_gastos_data ON gastos(data_compra);
"""


@dataclass(frozen=True)
class Cartao:
    id: int
    nome: str
    tipo: str
    dia_vencimento: Optional[int]
    dia_fechamento: Optional[int]


@dataclass(frozen=True)
class Gasto:
    id: int
    registrado_em: str
    data_compra: date
    valor_centavos: int
    estabelecimento: Optional[str]
    categoria: str
    forma_pagamento: Optional[str]
    cartao_id: Optional[int]
    cartao_nome: Optional[str]
    descricao: Optional[str]
    origem: str

    @property
    def valor(self) -> float:
        return self.valor_centavos / 100


class Database:
    def __init__(self, caminho: Path | str):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _conectar(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.caminho)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ----- cartões e contas -----

    def adicionar_cartao(
        self,
        nome: str,
        tipo: str = "cartao",
        dia_vencimento: Optional[int] = None,
        dia_fechamento: Optional[int] = None,
    ) -> Cartao:
        with self._conectar() as conn:
            conn.execute(
                """INSERT INTO cartoes (nome, tipo, dia_vencimento, dia_fechamento)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(nome) DO UPDATE SET
                     tipo = excluded.tipo,
                     dia_vencimento = excluded.dia_vencimento,
                     dia_fechamento = excluded.dia_fechamento""",
                (nome.strip(), tipo, dia_vencimento, dia_fechamento),
            )
            row = conn.execute(
                "SELECT * FROM cartoes WHERE nome = ? COLLATE NOCASE", (nome.strip(),)
            ).fetchone()
        return _cartao(row)

    def remover_cartao(self, nome: str) -> bool:
        with self._conectar() as conn:
            cur = conn.execute(
                "DELETE FROM cartoes WHERE nome = ? COLLATE NOCASE", (nome.strip(),)
            )
            return cur.rowcount > 0

    def listar_cartoes(self) -> list[Cartao]:
        with self._conectar() as conn:
            rows = conn.execute("SELECT * FROM cartoes ORDER BY nome").fetchall()
        return [_cartao(r) for r in rows]

    def buscar_cartao(self, texto: Optional[str]) -> Optional[Cartao]:
        """Casa um texto livre ('paguei no nubank') com um cartão cadastrado."""
        if not texto:
            return None
        alvo = texto.casefold().strip()
        if not alvo:
            return None
        cartoes = self.listar_cartoes()
        # 1) nome exato; 2) nome contido no texto; 3) texto contido no nome
        for c in cartoes:
            if c.nome.casefold() == alvo:
                return c
        for c in cartoes:
            if c.nome.casefold() in alvo:
                return c
        for c in cartoes:
            if alvo in c.nome.casefold():
                return c
        return None

    # ----- gastos -----

    def adicionar_gasto(
        self,
        valor_centavos: int,
        data_compra: date,
        estabelecimento: Optional[str] = None,
        categoria: str = "outros",
        forma_pagamento: Optional[str] = None,
        cartao_id: Optional[int] = None,
        descricao: Optional[str] = None,
        origem: str = "foto",
    ) -> int:
        with self._conectar() as conn:
            cur = conn.execute(
                """INSERT INTO gastos
                   (registrado_em, data_compra, valor_centavos, estabelecimento,
                    categoria, forma_pagamento, cartao_id, descricao, origem)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    data_compra.isoformat(),
                    valor_centavos,
                    estabelecimento,
                    categoria or "outros",
                    forma_pagamento,
                    cartao_id,
                    descricao,
                    origem,
                ),
            )
            return int(cur.lastrowid)

    def remover_ultimo_gasto(self) -> Optional[Gasto]:
        with self._conectar() as conn:
            row = conn.execute(
                """SELECT g.*, c.nome AS cartao_nome
                   FROM gastos g LEFT JOIN cartoes c ON c.id = g.cartao_id
                   ORDER BY g.id DESC LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            conn.execute("DELETE FROM gastos WHERE id = ?", (row["id"],))
        return _gasto(row)

    def listar_gastos(
        self,
        inicio: Optional[date] = None,
        fim: Optional[date] = None,
    ) -> list[Gasto]:
        """Gastos com data_compra em [inicio, fim], ordenados por data."""
        sql = """SELECT g.*, c.nome AS cartao_nome
                 FROM gastos g LEFT JOIN cartoes c ON c.id = g.cartao_id"""
        cond, params = [], []
        if inicio is not None:
            cond.append("g.data_compra >= ?")
            params.append(inicio.isoformat())
        if fim is not None:
            cond.append("g.data_compra <= ?")
            params.append(fim.isoformat())
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY g.data_compra, g.id"
        with self._conectar() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_gasto(r) for r in rows]

    # ----- ajustes -----

    def definir_ajuste(self, chave: str, valor: str) -> None:
        with self._conectar() as conn:
            conn.execute(
                """INSERT INTO ajustes (chave, valor) VALUES (?, ?)
                   ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor""",
                (chave, valor),
            )

    def obter_ajuste(self, chave: str) -> Optional[str]:
        with self._conectar() as conn:
            row = conn.execute(
                "SELECT valor FROM ajustes WHERE chave = ?", (chave,)
            ).fetchone()
        return row["valor"] if row else None


def _cartao(row: sqlite3.Row) -> Cartao:
    return Cartao(
        id=row["id"],
        nome=row["nome"],
        tipo=row["tipo"],
        dia_vencimento=row["dia_vencimento"],
        dia_fechamento=row["dia_fechamento"],
    )


def _gasto(row: sqlite3.Row) -> Gasto:
    return Gasto(
        id=row["id"],
        registrado_em=row["registrado_em"],
        data_compra=date.fromisoformat(row["data_compra"]),
        valor_centavos=row["valor_centavos"],
        estabelecimento=row["estabelecimento"],
        categoria=row["categoria"],
        forma_pagamento=row["forma_pagamento"],
        cartao_id=row["cartao_id"],
        cartao_nome=row["cartao_nome"],
        descricao=row["descricao"],
        origem=row["origem"],
    )
