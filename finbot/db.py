"""Armazenamento em SQLite: cartões/contas, gastos, rendas, orçamentos e ajustes."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    origem TEXT NOT NULL DEFAULT 'foto',      -- 'foto' | 'texto' | 'comando'
    responsavel TEXT,                         -- 'gabriel' | 'maria_eduarda' | 'combinado'
    pago INTEGER NOT NULL DEFAULT 1,          -- 0/1
    parcela_atual INTEGER,                    -- 1..parcela_total
    parcela_total INTEGER,
    grupo_parcelamento TEXT                   -- liga as parcelas de uma mesma compra
);

CREATE TABLE IF NOT EXISTS rendas (
    pessoa TEXT PRIMARY KEY,                  -- 'gabriel' | 'maria_eduarda'
    valor_centavos INTEGER NOT NULL,
    descricao TEXT,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orcamentos (
    categoria TEXT PRIMARY KEY COLLATE NOCASE,
    teto_centavos INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS investimentos (
    local TEXT PRIMARY KEY COLLATE NOCASE,
    valor_centavos INTEGER NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ajustes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE INDEX IF NOT EXISTS idx_gastos_data ON gastos(data_compra);
"""

# Migrações idempotentes para bancos criados antes destas colunas existirem.
_COLUNAS_NOVAS_GASTOS = {
    "responsavel": "TEXT",
    "pago": "INTEGER NOT NULL DEFAULT 1",
    "parcela_atual": "INTEGER",
    "parcela_total": "INTEGER",
    "grupo_parcelamento": "TEXT",
}


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
    responsavel: Optional[str]
    pago: bool
    parcela_atual: Optional[int]
    parcela_total: Optional[int]
    grupo_parcelamento: Optional[str]

    @property
    def valor(self) -> float:
        return self.valor_centavos / 100

    @property
    def parcela_rotulo(self) -> Optional[str]:
        if self.parcela_atual and self.parcela_total:
            return f"{self.parcela_atual}/{self.parcela_total}"
        return None


@dataclass(frozen=True)
class Renda:
    pessoa: str
    valor_centavos: int
    descricao: Optional[str]
    atualizado_em: str

    @property
    def valor(self) -> float:
        return self.valor_centavos / 100


class Database:
    def __init__(self, caminho: Path | str):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self._conectar() as conn:
            conn.executescript(_SCHEMA)
            self._migrar(conn)

    def _migrar(self, conn: sqlite3.Connection) -> None:
        colunas_existentes = {
            row["name"] for row in conn.execute("PRAGMA table_info(gastos)")
        }
        for nome, tipo_sql in _COLUNAS_NOVAS_GASTOS.items():
            if nome not in colunas_existentes:
                conn.execute(f"ALTER TABLE gastos ADD COLUMN {nome} {tipo_sql}")

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

    def ultimo_valor_pago(self, cartao_id: int) -> Optional[int]:
        """Valor do lançamento mais recente para esse cartão/conta (para estimar contas de valor variável)."""
        with self._conectar() as conn:
            row = conn.execute(
                """SELECT valor_centavos FROM gastos
                   WHERE cartao_id = ? ORDER BY data_compra DESC, id DESC LIMIT 1""",
                (cartao_id,),
            ).fetchone()
        return row["valor_centavos"] if row else None

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
        responsavel: Optional[str] = None,
        pago: bool = True,
        parcela_atual: Optional[int] = None,
        parcela_total: Optional[int] = None,
        grupo_parcelamento: Optional[str] = None,
    ) -> int:
        with self._conectar() as conn:
            cur = conn.execute(
                """INSERT INTO gastos
                   (registrado_em, data_compra, valor_centavos, estabelecimento,
                    categoria, forma_pagamento, cartao_id, descricao, origem,
                    responsavel, pago, parcela_atual, parcela_total, grupo_parcelamento)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    responsavel,
                    1 if pago else 0,
                    parcela_atual,
                    parcela_total,
                    grupo_parcelamento,
                ),
            )
            return int(cur.lastrowid)

    def adicionar_gasto_parcelado(
        self,
        valor_total_centavos: int,
        parcelas: int,
        data_primeira_parcela: date,
        estabelecimento: Optional[str] = None,
        categoria: str = "outros",
        forma_pagamento: Optional[str] = None,
        cartao_id: Optional[int] = None,
        descricao: Optional[str] = None,
        origem: str = "foto",
        responsavel: Optional[str] = None,
    ) -> list[int]:
        """Cria uma parcela por mês, cada uma com valor_total/parcelas."""
        grupo = secrets.token_hex(4)
        valor_parcela = round(valor_total_centavos / parcelas)
        ids = []
        for n in range(parcelas):
            data_parcela = _somar_meses(data_primeira_parcela, n)
            # A última parcela absorve o arredondamento das demais.
            valor = valor_parcela
            if n == parcelas - 1:
                valor = valor_total_centavos - valor_parcela * (parcelas - 1)
            ids.append(
                self.adicionar_gasto(
                    valor_centavos=valor,
                    data_compra=data_parcela,
                    estabelecimento=estabelecimento,
                    categoria=categoria,
                    forma_pagamento=forma_pagamento,
                    cartao_id=cartao_id,
                    descricao=descricao,
                    origem=origem,
                    responsavel=responsavel,
                    parcela_atual=n + 1,
                    parcela_total=parcelas,
                    grupo_parcelamento=grupo,
                )
            )
        return ids

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

    def listar_pendentes(self) -> list[Gasto]:
        """Gastos ainda não marcados como pagos, mais antigos primeiro."""
        with self._conectar() as conn:
            rows = conn.execute(
                """SELECT g.*, c.nome AS cartao_nome
                   FROM gastos g LEFT JOIN cartoes c ON c.id = g.cartao_id
                   WHERE g.pago = 0 ORDER BY g.data_compra, g.id"""
            ).fetchall()
        return [_gasto(r) for r in rows]

    def marcar_pago(self, gasto_id: int, pago: bool = True) -> bool:
        with self._conectar() as conn:
            cur = conn.execute(
                "UPDATE gastos SET pago = ? WHERE id = ?", (1 if pago else 0, gasto_id)
            )
            return cur.rowcount > 0

    # ----- rendas -----

    def definir_renda(
        self, pessoa: str, valor_centavos: int, descricao: Optional[str] = None
    ) -> None:
        with self._conectar() as conn:
            conn.execute(
                """INSERT INTO rendas (pessoa, valor_centavos, descricao, atualizado_em)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(pessoa) DO UPDATE SET
                     valor_centavos = excluded.valor_centavos,
                     descricao = excluded.descricao,
                     atualizado_em = excluded.atualizado_em""",
                (pessoa, valor_centavos, descricao, datetime.now().isoformat(timespec="seconds")),
            )

    def listar_rendas(self) -> list[Renda]:
        with self._conectar() as conn:
            rows = conn.execute("SELECT * FROM rendas ORDER BY pessoa").fetchall()
        return [
            Renda(
                pessoa=r["pessoa"],
                valor_centavos=r["valor_centavos"],
                descricao=r["descricao"],
                atualizado_em=r["atualizado_em"],
            )
            for r in rows
        ]

    # ----- orçamentos (teto de gasto por categoria) -----

    def definir_teto(self, categoria: str, teto_centavos: int) -> None:
        with self._conectar() as conn:
            conn.execute(
                """INSERT INTO orcamentos (categoria, teto_centavos) VALUES (?, ?)
                   ON CONFLICT(categoria) DO UPDATE SET teto_centavos = excluded.teto_centavos""",
                (categoria.strip(), teto_centavos),
            )

    def listar_tetos(self) -> dict[str, int]:
        with self._conectar() as conn:
            rows = conn.execute("SELECT categoria, teto_centavos FROM orcamentos").fetchall()
        return {r["categoria"]: r["teto_centavos"] for r in rows}

    # ----- investimentos -----

    def definir_investimento(self, local: str, valor_centavos: int) -> None:
        with self._conectar() as conn:
            conn.execute(
                """INSERT INTO investimentos (local, valor_centavos, atualizado_em)
                   VALUES (?, ?, ?)
                   ON CONFLICT(local) DO UPDATE SET
                     valor_centavos = excluded.valor_centavos,
                     atualizado_em = excluded.atualizado_em""",
                (local.strip(), valor_centavos, datetime.now().isoformat(timespec="seconds")),
            )

    def listar_investimentos(self) -> list[tuple[str, int]]:
        with self._conectar() as conn:
            rows = conn.execute(
                "SELECT local, valor_centavos FROM investimentos ORDER BY local"
            ).fetchall()
        return [(r["local"], r["valor_centavos"]) for r in rows]

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


def _somar_meses(data: date, meses: int) -> date:
    """Mesmo dia, N meses depois (cai no último dia do mês se o dia não existir)."""
    total = (data.year * 12 + (data.month - 1)) + meses
    ano, mes = divmod(total, 12)
    mes += 1
    ultimo_dia = _ultimo_dia_do_mes(ano, mes)
    return date(ano, mes, min(data.day, ultimo_dia))


def _ultimo_dia_do_mes(ano: int, mes: int) -> int:
    if mes == 12:
        proximo = date(ano + 1, 1, 1)
    else:
        proximo = date(ano, mes + 1, 1)
    return (proximo - timedelta(days=1)).day


def _cartao(row: sqlite3.Row) -> Cartao:
    return Cartao(
        id=row["id"],
        nome=row["nome"],
        tipo=row["tipo"],
        dia_vencimento=row["dia_vencimento"],
        dia_fechamento=row["dia_fechamento"],
    )


def _gasto(row: sqlite3.Row) -> Gasto:
    chaves = row.keys()
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
        responsavel=row["responsavel"] if "responsavel" in chaves else None,
        pago=bool(row["pago"]) if "pago" in chaves else True,
        parcela_atual=row["parcela_atual"] if "parcela_atual" in chaves else None,
        parcela_total=row["parcela_total"] if "parcela_total" in chaves else None,
        grupo_parcelamento=row["grupo_parcelamento"] if "grupo_parcelamento" in chaves else None,
    )
