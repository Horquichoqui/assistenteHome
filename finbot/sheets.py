"""Espelha os dados do bot numa planilha do Google Sheets (opcional, gratuito).

Cada mês ganha uma aba própria ("2026-11"). Gastos entram no mês da compra e as
contas do grupo do WhatsApp no mês do vencimento. A cada sincronização a aba do
mês é reescrita a partir do banco, que continua sendo a fonte de verdade.

Configuração: uma conta de serviço do Google (gratuita), com a chave JSON em
GOOGLE_CREDENCIAIS e a planilha compartilhada como Editor com o e-mail dela.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .config import Config
from .db import Database

log = logging.getLogger(__name__)

CABECALHO = ["Tipo", "Data", "Descrição", "Categoria", "Responsável", "Cartão/Conta",
             "Parcela", "Valor (R$)", "Pago?", "Origem"]


def linhas_do_mes(db: Database, cfg: Config, inicio: date, fim: date) -> list[list]:
    """Linhas da aba de um mês: cabeçalho, gastos, contas e totais."""
    linhas: list[list] = [CABECALHO]
    total_gastos = 0
    for g in db.listar_gastos(inicio, fim):
        total_gastos += g.valor_centavos
        linhas.append([
            "Gasto", g.data_compra.strftime("%d/%m/%Y"),
            g.estabelecimento or g.descricao or "", g.categoria,
            cfg.nome_pessoa(g.responsavel) if g.responsavel else "",
            g.cartao_nome or g.forma_pagamento or "", g.parcela_rotulo or "",
            g.valor, "Sim" if g.pago else "Não", g.origem,
        ])
    a_pagar = 0
    for c in db.listar_contas_a_pagar(inicio, fim):
        if not c.pago:
            a_pagar += c.valor_centavos or 0
        linhas.append([
            "Conta", c.vencimento.strftime("%d/%m/%Y"), c.descricao, "contas", "", c.forma or "", "",
            c.valor if c.valor is not None else "", "Sim" if c.pago else "Não", c.origem,
        ])
    linhas += [[], ["Total de gastos", "", "", "", "", "", "", total_gastos / 100],
               ["Contas a pagar", "", "", "", "", "", "", a_pagar / 100]]
    return linhas


def meses_com_dados(db: Database) -> list[tuple[date, date]]:
    """(início, fim) de cada mês que tem gasto ou conta, do mais antigo ao mais novo."""
    from . import stats

    datas = [g.data_compra for g in db.listar_gastos()]
    datas += [c.vencimento for c in db.listar_contas_a_pagar(date(1900, 1, 1), date(9999, 12, 31))]
    meses = sorted({d.replace(day=1) for d in datas})
    return [stats.limites_do_mes(m) for m in meses]


def sincronizar(db: Database, cfg: Config, cliente=None) -> Optional[str]:
    """Reescreve as abas mensais. Retorna um erro legível, ou None se deu certo
    (ou se a planilha não está configurada)."""
    if not cfg.sheets_id:
        return None
    try:
        if cliente is None:
            import gspread

            if not cfg.sheets_credenciais.exists():
                return f"chave da conta de serviço não encontrada em {cfg.sheets_credenciais}"
            cliente = gspread.service_account(filename=str(cfg.sheets_credenciais))
        planilha = cliente.open_by_key(cfg.sheets_id)
        existentes = {ws.title: ws for ws in planilha.worksheets()}
        for inicio, fim in meses_com_dados(db):
            nome = f"{inicio:%Y-%m}"
            linhas = linhas_do_mes(db, cfg, inicio, fim)
            ws = existentes.get(nome) or planilha.add_worksheet(
                title=nome, rows=len(linhas) + 20, cols=len(CABECALHO)
            )
            ws.clear()
            ws.update(linhas, "A1")
        return None
    except Exception as exc:  # a planilha online não pode derrubar o bot
        log.exception("Falha ao atualizar a planilha do Google")
        return f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Aba do mês no padrão do casal ("Jan".."Dez"):
#   L6:S32  Gastos Fixos do casal  (Responsável, Categoria, Descrição, Data,
#           Parcela, Pagamento, Valor, Pago?)
#   L37:R127 Gastos do dia a dia (variáveis), mesmas colunas sem Pago?
#   U5:U23  lista de categorias
# O bot só preenche células: nunca apaga linhas nem mexe nas fórmulas.

MESES = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
FIXOS = (6, 32)
VARIAVEIS = (37, 127)


@dataclass
class ItemPlanilha:
    descricao: str
    data: date
    valor: Optional[float]
    forma: Optional[str]
    pago: bool
    categoria: Optional[str] = None
    responsavel: Optional[str] = None


def _normal(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def _numero(texto) -> Optional[float]:
    if texto in (None, ""):
        return None
    try:
        return float(str(texto).replace("R$", "").replace(".", "").replace(",", ".")) \
            if "," in str(texto) else float(texto)
    except ValueError:
        return None


def _mesma_descricao(a, b) -> bool:
    a, b = _normal(a), _normal(b)
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


def ler_mes(ws) -> dict:
    """Lê as duas tabelas e a lista de categorias da aba do mês."""
    grade = ws.get(f"L1:S{VARIAVEIS[1]}", value_render_option="UNFORMATTED_VALUE")
    grade += [[]] * (VARIAVEIS[1] - len(grade))
    linha = lambda n: (list(grade[n - 1]) + [""] * 8)[:8]
    categorias = [c[0] for c in ws.get("U5:U23") if c and str(c[0]).strip()]
    return {
        "fixos": {n: linha(n) for n in range(FIXOS[0], FIXOS[1] + 1)},
        "variaveis": {n: linha(n) for n in range(VARIAVEIS[0], VARIAVEIS[1] + 1)},
        "categorias": categorias,
    }


def contexto_para_ia(mes: dict) -> str:
    fixos = [str(l[2]).strip() for l in mes["fixos"].values() if str(l[2]).strip()]
    responsaveis = sorted({str(l[0]).strip() for l in mes["fixos"].values() if str(l[0]).strip()})
    return (
        "Gastos fixos já cadastrados na planilha deste mês (se a conta for um deles, "
        f"use exatamente esse nome na descricao): {', '.join(fixos)}.\n"
        f"categoria: escolha uma destas: {', '.join(c.strip() for c in mes['categorias'])}.\n"
        f"responsavel: quem mandou a mensagem, um destes: {', '.join(responsaveis)}; "
        "contas da casa (moradia) são 'Combinado'."
    )


def planejar(mes: dict, item: ItemPlanilha) -> tuple[list[tuple[str, object]], str]:
    """(células a gravar [(A1, valor)], descrição do que foi feito) para um item."""
    data = item.data.strftime("%d/%m/%Y")
    # 1) Gasto fixo: mesma descrição, ou mesmo valor e forma de pagamento.
    alvo = next((n for n, l in mes["fixos"].items() if _mesma_descricao(l[2], item.descricao)), None)
    if alvo is None and item.valor:
        alvo = next((n for n, l in mes["fixos"].items()
                     if _numero(l[6]) == round(item.valor, 2) and _normal(l[5]) == _normal(item.forma)), None)
    if alvo is not None:
        l = mes["fixos"][alvo]
        celulas, feito = [], []
        if item.pago and l[7] is not True and str(l[7]).upper() != "TRUE":
            celulas.append((f"S{alvo}", True)); feito.append("marquei pago")
        if item.valor and not _numero(l[6]):
            celulas.append((f"R{alvo}", item.valor)); feito.append("preenchi o valor")
        if not str(l[3]).strip():
            celulas.append((f"O{alvo}", data)); feito.append("preenchi a data")
        if item.forma and not str(l[5]).strip():
            celulas.append((f"Q{alvo}", item.forma)); feito.append("preenchi o pagamento")
        l[7] = True if item.pago else l[7]
        nome = str(l[2]).strip()
        return celulas, f"fixo \"{nome}\" (linha {alvo}): " + (", ".join(feito) if feito else "já estava certo")
    # 2) Variável já lançado: mesma descrição e mesmo valor.
    for n, l in mes["variaveis"].items():
        if _mesma_descricao(l[2], item.descricao) and (not item.valor or _numero(l[6]) == round(item.valor, 2)):
            return [], f"já estava nos gastos do dia a dia (linha {n})"
    # 3) Novo: primeira linha vazia dos variáveis.
    livre = next((n for n, l in mes["variaveis"].items() if not any(str(x).strip() for x in l[:3])), None)
    if livre is None:
        return [], "sem linha livre nos gastos do dia a dia"
    # O SUMIF da aba compara o texto exato (há categorias com espaço no fim, ex.: "Lazer ").
    categoria = next((c for c in mes["categorias"] if _normal(c) == _normal(item.categoria)), "")
    valores = [item.responsavel or "Combinado", categoria, item.descricao, data, "",
               item.forma or "", item.valor if item.valor else ""]
    mes["variaveis"][livre] = valores + [""]
    return [(f"{col}{livre}", v) for col, v in zip("LMNOPQR", valores) if v != ""], \
        f"novo nos gastos do dia a dia (linha {livre})"


def anotar_no_mes(cfg: Config, itens: list[ItemPlanilha], cliente=None) -> tuple[list[str], Optional[str]]:
    """Grava os itens na aba do mês. Retorna (uma linha por item, erro ou None)."""
    if not cfg.sheets_id or not itens:
        return [], None
    try:
        planilha = _abrir(cfg, cliente)
        linhas, cache = [], {}
        for item in itens:
            aba = MESES[item.data.month - 1]
            if aba not in cache:
                ws = planilha.worksheet(aba)
                cache[aba] = (ws, ler_mes(ws), [])
            ws, mes, celulas = cache[aba]
            novas, feito = planejar(mes, item)
            celulas += novas
            linhas.append(f"{aba}: {feito}")
        for ws, _, celulas in cache.values():
            if celulas:
                ws.batch_update([{"range": a1, "values": [[v]]} for a1, v in celulas],
                                value_input_option="USER_ENTERED")
        return linhas, None
    except Exception as exc:
        log.exception("Falha ao anotar na aba do mês")
        return [], f"{type(exc).__name__}: {exc}"


def ler_contexto(cfg: Config, hoje: date, cliente=None) -> str:
    """Texto com os fixos e categorias do mês, para orientar a IA. Vazio se falhar."""
    if not cfg.sheets_id:
        return ""
    try:
        return contexto_para_ia(ler_mes(_abrir(cfg, cliente).worksheet(MESES[hoje.month - 1])))
    except Exception:
        log.warning("Não consegui ler a aba do mês para orientar a IA", exc_info=True)
        return ""


def _abrir(cfg: Config, cliente=None):
    if cliente is None:
        import gspread

        cliente = gspread.service_account(filename=str(cfg.sheets_credenciais))
    return cliente.open_by_key(cfg.sheets_id)
