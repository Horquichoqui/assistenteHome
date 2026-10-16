"""Transforma as mensagens do grupo do WhatsApp em contas a pagar.

Fluxo de uma leitura: pega as mensagens novas do grupo (finbot/whatsapp.py), pede
à IA para identificar contas em lotes de texto e em cada imagem (boleto, fatura,
comprovante), registra ou atualiza as contas no banco e monta o relatório, com os
prints das imagens que tinham conta. Mensagens já analisadas são ignoradas.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable, Optional

from . import ia, stats, whatsapp
from .config import Config
from .db import ContaAPagar, Database

log = logging.getLogger(__name__)

JANELA_HORAS = 48  # olha as últimas 48 h; o que já foi analisado é ignorado
PAUSA_ENTRE_CHAMADAS_S = 15  # respeita o limite por minuto do plano gratuito da IA
ESPERA_LIMITE_S = 60  # tempo de espera quando a IA responde 429
MENSAGENS_POR_LOTE = 100
DIAS_ALERTA = 3  # avisa contas que vencem em até 3 dias
DIAS_ATRASO_MAX = 30  # contas atrasadas há mais que isso deixam de ser avisadas
VALOR_MAXIMO = 1_000_000  # acima disso o valor extraído é considerado erro da IA


@dataclass
class ResultadoSincronizacao:
    status: str  # "ok" ou o status de erro vindo da coleta
    texto: str
    prints: list[tuple[bytes, str]] = field(default_factory=list)  # (png, legenda)


async def sincronizar(
    cfg: Config,
    db: Database,
    agora: datetime,
    hoje: date,
    enviar_qr: Callable[[bytes], Awaitable[None]],
) -> ResultadoSincronizacao:
    if not cfg.whatsapp_grupo:
        return ResultadoSincronizacao("erro", "Defina WHATSAPP_GRUPO no .env (nome exato do grupo).")

    coleta = await whatsapp.coletar_grupo(
        cfg.whatsapp_grupo, cfg.whatsapp_perfil, agora, JANELA_HORAS, enviar_qr
    )
    if coleta.status == "precisa_login":
        return ResultadoSincronizacao(
            coleta.status,
            "📱 O WhatsApp ainda não está conectado. Escaneie o QR code que enviei "
            "(WhatsApp › Aparelhos conectados › Conectar um aparelho) e rode /whatsapp de novo.",
        )
    if coleta.status == "grupo_nao_encontrado":
        return ResultadoSincronizacao(
            coleta.status,
            f"Não encontrei o grupo \"{cfg.whatsapp_grupo}\" no WhatsApp. "
            "Confira o nome em WHATSAPP_GRUPO (precisa ser exatamente igual).",
        )
    if coleta.status != "ok":
        return ResultadoSincronizacao(coleta.status, f"Não consegui ler o WhatsApp: {coleta.erro}")

    novas = [m for m in coleta.mensagens if not db.mensagem_processada(m.chave)]
    textos = [m for m in novas if m.print_png is None and m.texto]
    imagens = [m for m in novas if m.print_png is not None]

    contagem = {"criadas": 0, "atualizadas": 0, "pagas": 0, "sem_vencimento": 0, "falhas": 0}
    prints: list[tuple[bytes, str]] = []
    processadas: list[str] = []
    chamadas = 0

    # 1) Texto: lotes de mensagens, uma chamada por lote.
    for inicio in range(0, len(textos), MENSAGENS_POR_LOTE):
        lote = textos[inicio : inicio + MENSAGENS_POR_LOTE]
        linhas = [f"[{m.momento:%d/%m/%Y %H:%M}] {m.remetente}: {m.texto}" for m in lote]
        if chamadas:
            await asyncio.sleep(PAUSA_ENTRE_CHAMADAS_S)
        chamadas += 1
        try:
            itens = await _chamar_ia(functools.partial(ia.extrair_contas_de_texto, linhas, hoje))
        except Exception:
            log.exception("Falha ao analisar um lote de mensagens do WhatsApp")
            contagem["falhas"] += 1
            continue  # sem marcar: a análise é refeita na próxima leitura
        _registrar(db, itens, hoje, contagem)
        processadas.extend(m.chave for m in lote)

    # 2) Imagens: uma chamada por imagem (boleto, comprovante, nota).
    for m in imagens:
        if chamadas:
            await asyncio.sleep(PAUSA_ENTRE_CHAMADAS_S)
        chamadas += 1
        momento = f"{m.momento:%d/%m/%Y %H:%M}"
        try:
            itens = await _chamar_ia(
                functools.partial(
                    ia.extrair_contas_de_imagem, m.print_png, "image/png", m.texto, momento, hoje
                )
            )
        except Exception:
            log.exception("Falha ao analisar uma imagem do WhatsApp")
            contagem["falhas"] += 1
            continue
        processadas.append(m.chave)
        contas_da_imagem = _registrar(db, itens, hoje, contagem)
        if contas_da_imagem:  # o print só vai para o Telegram se tiver conta nele
            prints.append((m.print_png, f"📸 {m.remetente} · {m.momento:%d/%m %H:%M}"))

    db.marcar_mensagens_processadas(processadas)

    texto = _montar_texto(cfg, db, hoje, coleta.mensagens, novas, contagem)
    return ResultadoSincronizacao("ok", texto, prints)


def alertas_contas(db: Database, hoje: date) -> list[str]:
    """Linhas de alerta para contas não pagas que vencem em breve ou já venceram."""
    limite = hoje + timedelta(days=DIAS_ALERTA)
    linhas = []
    for conta in db.contas_pendentes_ate(limite):
        dias = (conta.vencimento - hoje).days
        if dias < -DIAS_ATRASO_MAX:
            continue
        if dias < 0:
            quando = f"ATRASADA (venceu {conta.vencimento:%d/%m})"
        elif dias == 0:
            quando = "vence HOJE"
        elif dias == 1:
            quando = "vence AMANHÃ"
        else:
            quando = f"vence em {dias} dias ({conta.vencimento:%d/%m})"
        linhas.append(f"• {conta.descricao} — {_valor_conta(conta)} — {quando}")
    return linhas


def formatar_contas(contas: list[ContaAPagar]) -> list[str]:
    """Uma linha por conta, com o número usado em /pagarconta."""
    linhas = []
    for c in contas:
        situacao = "✅ paga" if c.pago else "🕒 pendente"
        linhas.append(
            f"#{c.id} {situacao} · {c.descricao} · {_valor_conta(c)} · vence {c.vencimento:%d/%m}"
        )
    return linhas


# ---------------------------------------------------------------------------

def _registrar(db: Database, itens, hoje: date, contagem: dict[str, int]) -> int:
    """Grava as contas extraídas. Retorna quantas contas foram registradas."""
    registradas = 0
    for item in itens:
        descricao = (item.descricao or "").strip()
        vencimento = _data_valida(item.vencimento, hoje)
        if not descricao:
            continue
        if vencimento is None:
            contagem["sem_vencimento"] += 1
            continue
        resultado = db.registrar_conta_a_pagar(
            descricao, vencimento, _centavos(item.valor), pago=item.pago
        )
        contagem["criadas" if resultado == "criada" else "atualizadas"] += 1
        if item.pago:
            contagem["pagas"] += 1
        registradas += 1
    return registradas


async def _chamar_ia(fabrica: Callable[[], Awaitable[list]]) -> list:
    """Chama a IA e espera um minuto e tenta de novo se o limite do plano gratuito estourar."""
    for tentativa in range(3):
        try:
            return await fabrica()
        except Exception as exc:
            if tentativa < 2 and _limite_atingido(exc):
                log.warning("Limite da IA atingido; aguardando %ss", ESPERA_LIMITE_S)
                await asyncio.sleep(ESPERA_LIMITE_S)
                continue
            raise
    raise RuntimeError("inalcançável")


def _limite_atingido(exc: Exception) -> bool:
    texto = str(exc)
    return "429" in texto or "RESOURCE_EXHAUSTED" in texto or "rate limit" in texto.lower()


def _data_valida(texto: Optional[str], hoje: date) -> Optional[date]:
    if not texto:
        return None
    try:
        data = date.fromisoformat(texto[:10])
    except ValueError:
        return None
    # Descarta datas absurdas (ex.: ano errado da IA).
    return data if abs(data.year - hoje.year) <= 1 else None


def _centavos(valor: Optional[float]) -> Optional[int]:
    if valor is None or valor <= 0 or valor > VALOR_MAXIMO:
        return None
    return round(valor * 100)


def _valor_conta(conta: ContaAPagar) -> str:
    if conta.valor_centavos is None:
        return "valor a confirmar"
    return stats.formatar_reais(conta.valor_centavos)


def _montar_texto(
    cfg: Config,
    db: Database,
    hoje: date,
    lidas: list,
    novas: list,
    contagem: dict[str, int],
) -> str:
    linhas = [
        f"📥 Grupo \"{cfg.whatsapp_grupo}\": {len(lidas)} mensagem(ns) nas últimas "
        f"{JANELA_HORAS}h, {len(novas)} nova(s) analisada(s)."
    ]
    if not lidas:
        linhas.append("Não encontrei mensagens nessa janela. Confira o grupo e se o WhatsApp está atualizado.")
    linhas.append(
        f"Contas: {contagem['criadas']} nova(s), {contagem['atualizadas']} atualizada(s), "
        f"{contagem['pagas']} marcada(s) como paga(s)."
    )
    if contagem["falhas"]:
        linhas.append(f"⚠️ {contagem['falhas']} análise(s) falharam; serão refeitas na próxima leitura.")
    if contagem["sem_vencimento"]:
        linhas.append(f"ℹ️ {contagem['sem_vencimento']} conta(s) sem data de vencimento foram ignoradas.")

    inicio, fim = stats.limites_do_mes(hoje)
    contas_mes = db.listar_contas_a_pagar(inicio, fim)
    if contas_mes:
        pendente = sum(c.valor_centavos or 0 for c in contas_mes if not c.pago)
        linhas += ["", "💸 Contas do mês:"] + formatar_contas(contas_mes)
        linhas.append(f"\nTotal ainda a pagar (valores informados): {stats.formatar_reais(pendente)}")
    alertas = alertas_contas(db, hoje)
    if alertas:
        linhas += ["", "⏰ Atenção:"] + alertas
    return "\n".join(linhas)
