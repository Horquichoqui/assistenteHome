"""Assistente financeiro pessoal no Telegram.

Recebe fotos de notas fiscais, registra gastos, gera planilha Excel,
envia resumos semanais/mensais e responde perguntas sobre as finanças.
"""

from __future__ import annotations

import logging
import tempfile
from datetime import date, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Optional

import pytz
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from finbot import ia, planilha, resumos, stats
from finbot.config import PESSOA_COMBINADO, Config, carregar_config
from finbot.db import Database
from finbot.stats import formatar_reais

logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

AJUDA = """🤖 Assistente financeiro — como usar

📸 Envie a FOTO da nota fiscal ou comprovante, com uma legenda dizendo
como pagou. Ex.: "paguei no Nubank crédito".

✍️ Ou registre por texto: "gastei 52,90 no mercado no Itaú".

💳 Cartões e contas:
/cartao Nubank 10 → cartão Nubank, vence dia 10
/cartao Nubank 10 3 → vence dia 10, fecha dia 3
/conta Luz 15 → conta Luz, vence dia 15
/contas → lista tudo com os próximos vencimentos
/remover Nubank → remove um cartão/conta

💰 Renda e orçamento:
/renda 1 5500 Salário → define a renda da pessoa 1
/renda 2 4200 Salário → define a renda da pessoa 2
/investimento Tesouro 5000 → registra um investimento
/teto mercado 800 → define teto de gasto mensal da categoria
/pendentes → lista gastos ainda não marcados como pagos
/pago 42 → marca o gasto #42 como pago

📊 Relatórios:
/planilha → envia a planilha Excel atualizada
/resumo → resumo do mês atual
/semana → resumo da semana atual
/desfazer → apaga o último gasto registrado

❓ Perguntas livres: "quanto gastei no Nubank este mês?",
"qual conta vence agora?", "resume meus gastos" — é só mandar.

👤 Se o gasto foi de uma pessoa específica ou combinado (dividido),
diga isso na legenda/mensagem — ex.: "gastei 50, foi combinado".
Compras parceladas ("em 10x") já são lançadas automaticamente nos
próximos meses.

⏰ Automático: resumo toda semana (domingo à noite), resumo mensal
(dia 1º) e lembretes de vencimento."""


def _hoje(cfg: Config) -> date:
    return datetime.now(pytz.timezone(cfg.timezone)).date()


def _autorizado(cfg: Config, update: Update) -> bool:
    if not cfg.allowed_user_ids:
        return True
    return bool(update.effective_user and update.effective_user.id in cfg.allowed_user_ids)


async def _rejeitar(update: Update) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "⛔ Este bot é pessoal e você não está na lista de usuários autorizados."
        )


def _db(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def _cfg(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def _chats_registrados(db: Database) -> set[int]:
    valor = db.obter_ajuste("chats_donos")
    if not valor:
        return set()
    return {int(c) for c in valor.split(",") if c.strip()}


def _lembrar_chat(db: Database, update: Update) -> None:
    """Adiciona o chat à lista de destinatários dos envios automáticos."""
    if not update.effective_chat:
        return
    chat_id = update.effective_chat.id
    atuais = _chats_registrados(db)
    if chat_id not in atuais:
        atuais.add(chat_id)
        db.definir_ajuste("chats_donos", ",".join(str(c) for c in sorted(atuais)))


# --------------------------------------------------------------------------
# Comandos
# --------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    _lembrar_chat(_db(context), update)
    await update.message.reply_text(
        "Olá! 👋 Sou seu assistente financeiro.\n\n"
        "Me envie fotos das suas notas fiscais com uma legenda dizendo como "
        "pagou, e eu organizo tudo na sua planilha.\n\n" + AJUDA
    )


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    await update.message.reply_text(AJUDA)


def _parse_cadastro(args: list[str]) -> Optional[tuple[str, int, Optional[int]]]:
    """Interpreta '/cartao Nome 10 [3]' → (nome, vencimento, fechamento)."""
    numeros: list[int] = []
    while args and args[-1].isdigit() and len(numeros) < 2:
        numeros.insert(0, int(args.pop()))
    nome = " ".join(args).strip()
    if not nome or not numeros:
        return None
    venc = numeros[0]
    fech = numeros[1] if len(numeros) > 1 else None
    if not 1 <= venc <= 31 or (fech is not None and not 1 <= fech <= 31):
        return None
    return nome, venc, fech


async def _cadastrar(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    db = _db(context)
    _lembrar_chat(db, update)

    exemplo = "/cartao Nubank 10" if tipo == "cartao" else "/conta Luz 15"
    parsed = _parse_cadastro(list(context.args or []))
    if parsed is None:
        await update.message.reply_text(
            f"Formato: {exemplo}\n(nome seguido do dia de vencimento; para "
            "cartões, opcionalmente o dia de fechamento)"
        )
        return
    nome, venc, fech = parsed
    cartao = db.adicionar_cartao(nome, tipo=tipo, dia_vencimento=venc, dia_fechamento=fech)
    rotulo = "Cartão" if tipo == "cartao" else "Conta"
    extra = f", fecha dia {fech}" if fech else ""
    await update.message.reply_text(
        f"✅ {rotulo} \"{cartao.nome}\" cadastrado: vence dia {venc}{extra}."
    )


async def cmd_cartao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cadastrar(update, context, "cartao")


async def cmd_conta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cadastrar(update, context, "conta")


async def cmd_contas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    db = _db(context)
    cartoes = db.listar_cartoes()
    if not cartoes:
        await update.message.reply_text(
            "Nenhum cartão ou conta cadastrado ainda.\nUse /cartao Nubank 10 "
            "ou /conta Luz 15 para cadastrar."
        )
        return
    hoje = _hoje(cfg)
    vencimentos = {v.cartao.id: v for v in stats.proximos_vencimentos(db, hoje)}
    ini_mes, fim_mes = stats.limites_do_mes(hoje)
    por_cartao = dict(stats.total_por_cartao(db.listar_gastos(ini_mes, fim_mes)))
    linhas = ["💳 Cartões e contas cadastrados:\n"]
    for c in cartoes:
        icone = "💳" if c.tipo == "cartao" else "🧾"
        v = vencimentos.get(c.id)
        venc = (
            f"vence {v.data.strftime('%d/%m')} (em {v.dias_restantes} dia(s))"
            if v
            else "sem vencimento cadastrado"
        )
        gasto = por_cartao.get(c.nome, 0)
        linhas.append(f"{icone} {c.nome} — {venc} · mês atual: {formatar_reais(gasto)}")
    await update.message.reply_text("\n".join(linhas))


async def cmd_remover(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    nome = " ".join(context.args or []).strip()
    if not nome:
        await update.message.reply_text("Formato: /remover NomeDoCartão")
        return
    if _db(context).remover_cartao(nome):
        await update.message.reply_text(f"🗑️ \"{nome}\" removido.")
    else:
        await update.message.reply_text(f"Não encontrei \"{nome}\". Veja a lista com /contas.")


async def cmd_planilha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    db = _db(context)
    _lembrar_chat(db, update)
    hoje = _hoje(cfg)
    destino = Path(tempfile.mkdtemp()) / f"financeiro-{hoje.isoformat()}.xlsx"
    planilha.gerar_planilha(db, destino, hoje, cfg)
    with destino.open("rb") as arquivo:
        await update.message.reply_document(
            document=arquivo,
            filename=destino.name,
            caption="📊 Sua planilha financeira atualizada.",
        )


async def cmd_resumo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    await update.message.chat.send_action("typing")
    texto = await resumos.resumo_mensal(_db(context), _hoje(cfg))
    await update.message.reply_text(texto)


async def cmd_semana(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    await update.message.chat.send_action("typing")
    texto = await resumos.resumo_semanal(_db(context), _hoje(cfg))
    await update.message.reply_text(texto)


async def cmd_desfazer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    gasto = _db(context).remover_ultimo_gasto()
    if gasto is None:
        await update.message.reply_text("Não há gastos para desfazer.")
        return
    onde = gasto.estabelecimento or gasto.descricao or gasto.categoria
    await update.message.reply_text(
        f"↩️ Apagado o último gasto: {formatar_reais(gasto.valor_centavos)} "
        f"em {onde} ({gasto.data_compra.strftime('%d/%m/%Y')})."
    )


async def cmd_renda(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    if not cfg.ordem_pessoas:
        await update.message.reply_text(
            "O modo casal não está configurado (PESSOA_1_NOME/PESSOA_2_NOME "
            "vazios no .env)."
        )
        return
    args = list(context.args or [])
    guia = "\n".join(
        f"{i + 1} → {cfg.nome_pessoa(p)}" for i, p in enumerate(cfg.ordem_pessoas)
    )
    if len(args) < 2 or not args[0].isdigit():
        await update.message.reply_text(f"Formato: /renda <número> <valor> [descrição]\n{guia}")
        return
    pessoa = cfg.pessoa_por_numero(int(args[0]))
    if pessoa is None:
        await update.message.reply_text(f"Número inválido.\n{guia}")
        return
    try:
        valor = float(args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("Valor inválido. Ex.: /renda 1 5500 Salário")
        return
    descricao = " ".join(args[2:]).strip() or None
    centavos = round(valor * 100)
    _db(context).definir_renda(pessoa, centavos, descricao)
    await update.message.reply_text(
        f"✅ Renda de {cfg.nome_pessoa(pessoa)} definida: {formatar_reais(centavos)}"
        + (f" ({descricao})" if descricao else "")
    )


async def cmd_investimento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    args = list(context.args or [])
    if len(args) < 2:
        await update.message.reply_text(
            "Formato: /investimento <local> <valor>\nEx.: /investimento \"Tesouro Selic\" 5000"
        )
        return
    *partes_local, valor_bruto = args
    local = " ".join(partes_local).strip()
    try:
        valor = float(valor_bruto.replace(",", "."))
    except ValueError:
        local, valor_bruto = None, None
    if not local or valor_bruto is None:
        await update.message.reply_text(
            "Formato: /investimento <local> <valor>\nEx.: /investimento \"Tesouro Selic\" 5000"
        )
        return
    centavos = round(valor * 100)
    _db(context).definir_investimento(local, centavos)
    await update.message.reply_text(f"✅ Investimento \"{local}\" atualizado: {formatar_reais(centavos)}")


async def cmd_teto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    args = list(context.args or [])
    if len(args) < 2:
        await update.message.reply_text("Formato: /teto <categoria> <valor>\nEx.: /teto mercado 800")
        return
    *partes_categoria, valor_bruto = args
    categoria = " ".join(partes_categoria).strip().lower()
    try:
        valor = float(valor_bruto.replace(",", "."))
    except ValueError:
        categoria, valor_bruto = None, None
    if not categoria or valor_bruto is None:
        await update.message.reply_text("Formato: /teto <categoria> <valor>\nEx.: /teto mercado 800")
        return
    centavos = round(valor * 100)
    _db(context).definir_teto(categoria, centavos)
    await update.message.reply_text(
        f"✅ Teto de \"{categoria}\" definido: {formatar_reais(centavos)}/mês"
    )


async def cmd_pendentes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    pendentes = _db(context).listar_pendentes()
    if not pendentes:
        await update.message.reply_text("✅ Nenhum gasto pendente de pagamento.")
        return
    linhas = ["🕒 Gastos pendentes de pagamento:"]
    for g in pendentes:
        onde = g.estabelecimento or g.descricao or g.categoria
        linhas.append(
            f"#{g.id} · {formatar_reais(g.valor_centavos)} · {onde} "
            f"({g.data_compra.strftime('%d/%m')})"
        )
    linhas.append("\nUse /pago <número> para marcar como pago.")
    await update.message.reply_text("\n".join(linhas))


async def cmd_pago(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _autorizado(_cfg(context), update):
        return await _rejeitar(update)
    args = list(context.args or [])
    numero = args[0].lstrip("#") if args else ""
    if not numero.isdigit():
        await update.message.reply_text("Formato: /pago <número> (veja os números em /pendentes)")
        return
    gasto_id = int(numero)
    if _db(context).marcar_pago(gasto_id):
        await update.message.reply_text(f"✅ Gasto #{gasto_id} marcado como pago.")
    else:
        await update.message.reply_text(f"Não encontrei o gasto #{gasto_id}.")


# --------------------------------------------------------------------------
# Registro de gastos (foto e texto)
# --------------------------------------------------------------------------

def _data_da_compra(extraido: ia.GastoExtraido, hoje: date) -> date:
    if extraido.data_compra:
        try:
            data = date.fromisoformat(extraido.data_compra)
            if abs((data - hoje).days) <= 366:
                return data
        except ValueError:
            pass
    return hoje


_SINONIMOS_COMBINADO = ("combinado", "conjunto", "juntos", "dividido", "nosso", "casa")


def _resolver_responsavel(
    cfg: Config, extraido_texto: Optional[str], remetente_id: Optional[int]
) -> Optional[str]:
    """Usa o que a IA extraiu (nome ou 'combinado'); se vazio, assume quem enviou a mensagem."""
    if extraido_texto:
        texto = extraido_texto.strip().casefold()
        if texto in _SINONIMOS_COMBINADO:
            return PESSOA_COMBINADO
        for chave, nome in cfg.nomes_pessoas.items():
            nome_cf = nome.casefold()
            if nome_cf == texto or nome_cf in texto or texto in nome_cf:
                return chave
    return cfg.pessoa_do_chat(remetente_id)


async def _salvar_gasto(
    cfg: Config,
    db: Database,
    extraido: ia.GastoExtraido,
    hoje: date,
    legenda: Optional[str],
    origem: str,
    remetente_id: Optional[int],
) -> str:
    if not extraido.valor_total or extraido.valor_total <= 0:
        return (
            "⚠️ Não consegui identificar o valor. Tente enviar a foto mais "
            "nítida, ou registre por texto: \"gastei 45,90 no mercado no Nubank\"."
        )
    centavos = round(extraido.valor_total * 100)
    data_compra = _data_da_compra(extraido, hoje)
    cartao = db.buscar_cartao(extraido.forma_pagamento) or db.buscar_cartao(legenda)
    responsavel = _resolver_responsavel(cfg, extraido.responsavel, remetente_id)
    parcelas = extraido.parcelas if extraido.parcelas and extraido.parcelas > 1 else None

    if parcelas:
        db.adicionar_gasto_parcelado(
            valor_total_centavos=centavos,
            parcelas=parcelas,
            data_primeira_parcela=data_compra,
            estabelecimento=extraido.estabelecimento,
            categoria=extraido.categoria or "outros",
            forma_pagamento=extraido.forma_pagamento,
            cartao_id=cartao.id if cartao else None,
            descricao=legenda or extraido.observacoes,
            origem=origem,
            responsavel=responsavel,
        )
    else:
        db.adicionar_gasto(
            valor_centavos=centavos,
            data_compra=data_compra,
            estabelecimento=extraido.estabelecimento,
            categoria=extraido.categoria or "outros",
            forma_pagamento=extraido.forma_pagamento,
            cartao_id=cartao.id if cartao else None,
            descricao=legenda or extraido.observacoes,
            origem=origem,
            responsavel=responsavel,
        )

    linhas = [
        "✅ Gasto registrado!",
        f"💰 {formatar_reais(centavos)}"
        + (f" — {extraido.estabelecimento}" if extraido.estabelecimento else ""),
        f"📅 {data_compra.strftime('%d/%m/%Y')} · categoria: {extraido.categoria or 'outros'}",
    ]
    if cartao:
        linhas.append(f"💳 {cartao.nome}")
    elif extraido.forma_pagamento:
        linhas.append(
            f"💳 {extraido.forma_pagamento} (não cadastrado — use "
            f"/cartao {extraido.forma_pagamento} <dia> para acompanhar o vencimento)"
        )
    else:
        linhas.append("💳 Forma de pagamento não informada.")
    if responsavel:
        linhas.append(f"👤 {cfg.nome_pessoa(responsavel)}")
    if parcelas:
        linhas.append(
            f"📆 Parcelado em {parcelas}x de {formatar_reais(round(centavos / parcelas))} "
            "(lançado automaticamente nos próximos meses)"
        )
    return "\n".join(linhas)


async def receber_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    db = _db(context)
    _lembrar_chat(db, update)
    msg = update.message

    if msg.photo:
        arquivo = await context.bot.get_file(msg.photo[-1].file_id)
        media_type = "image/jpeg"
    elif msg.document and (msg.document.mime_type or "").startswith("image/"):
        arquivo = await context.bot.get_file(msg.document.file_id)
        media_type = msg.document.mime_type
    else:
        return

    await msg.chat.send_action("typing")
    imagem = bytes(await arquivo.download_as_bytearray())
    hoje = _hoje(cfg)
    try:
        extraido = await ia.extrair_de_foto(
            imagem,
            media_type,
            msg.caption,
            db.listar_cartoes(),
            hoje,
            pessoas=list(cfg.nomes_pessoas.values()),
        )
    except Exception:
        log.exception("Falha ao extrair dados da foto")
        await msg.reply_text(
            "❌ Não consegui ler essa imagem agora. Tente novamente em instantes "
            "ou registre por texto: \"gastei 45,90 no mercado no Nubank\"."
        )
        return
    remetente_id = update.effective_user.id if update.effective_user else None
    resposta = await _salvar_gasto(
        cfg, db, extraido, hoje, msg.caption, origem="foto", remetente_id=remetente_id
    )
    await msg.reply_text(resposta)


async def receber_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = _cfg(context)
    if not _autorizado(cfg, update):
        return await _rejeitar(update)
    db = _db(context)
    _lembrar_chat(db, update)
    msg = update.message
    texto = (msg.text or "").strip()
    if not texto:
        return

    await msg.chat.send_action("typing")
    hoje = _hoje(cfg)
    try:
        interpretacao = await ia.interpretar_texto(
            texto, db.listar_cartoes(), hoje, pessoas=list(cfg.nomes_pessoas.values())
        )
        if interpretacao.intencao == "registrar_gasto" and interpretacao.gasto:
            remetente_id = update.effective_user.id if update.effective_user else None
            resposta = await _salvar_gasto(
                cfg, db, interpretacao.gasto, hoje, texto, origem="texto", remetente_id=remetente_id
            )
        elif interpretacao.intencao == "pergunta":
            contexto = resumos.contexto_financeiro(db, hoje)
            resposta = await ia.responder_pergunta(texto, contexto)
        else:
            resposta = (
                "Posso registrar gastos (foto da nota ou texto como \"gastei "
                "50 no mercado no Nubank\") e responder perguntas sobre suas "
                "finanças. Digite /ajuda para ver tudo o que sei fazer."
            )
    except Exception:
        log.exception("Falha ao processar mensagem de texto")
        resposta = "❌ Tive um problema ao processar sua mensagem. Tente novamente em instantes."
    await msg.reply_text(resposta)


# --------------------------------------------------------------------------
# Tarefas agendadas
# --------------------------------------------------------------------------

async def _chats_dos_donos(context: ContextTypes.DEFAULT_TYPE) -> list[int]:
    return sorted(_chats_registrados(_db(context)))


async def _enviar_para_todos(
    context: ContextTypes.DEFAULT_TYPE, chats: list[int], texto: str
) -> None:
    """Envia a mesma mensagem para cada chat, sem deixar uma falha bloquear os demais."""
    for chat_id in chats:
        try:
            await context.bot.send_message(chat_id=chat_id, text=texto)
        except Exception:
            log.exception("Falha ao enviar mensagem automática para o chat %s", chat_id)


async def job_manha(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Todo dia às 9h: lembretes de vencimento; dia 1º: resumo do mês anterior."""
    cfg = _cfg(context)
    db = _db(context)
    chats = await _chats_dos_donos(context)
    if not chats:
        return
    hoje = _hoje(cfg)

    avisos = [
        v
        for v in stats.proximos_vencimentos(db, hoje)
        if v.dias_restantes in (0, 1, 3)
    ]
    if avisos:
        linhas = ["⏰ Lembrete de vencimentos:"]
        for v in avisos:
            quando = {0: "vence HOJE", 1: "vence AMANHÃ"}.get(
                v.dias_restantes, f"vence em {v.dias_restantes} dias"
            )
            linhas.append(f"• {v.cartao.nome} {quando} ({v.data.strftime('%d/%m')})")
        await _enviar_para_todos(context, chats, "\n".join(linhas))

    if hoje.day == 1:
        texto = await resumos.resumo_mensal(db, hoje, mes_anterior=True)
        await _enviar_para_todos(context, chats, "📅 Fechamento do mês!\n\n" + texto)


async def job_noite(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Domingo às 20h: resumo da semana."""
    cfg = _cfg(context)
    chats = await _chats_dos_donos(context)
    if not chats:
        return
    hoje = _hoje(cfg)
    if hoje.weekday() != 6:  # 6 = domingo
        return
    texto = await resumos.resumo_semanal(_db(context), hoje)
    await _enviar_para_todos(context, chats, "🗓️ Resumo da semana!\n\n" + texto)


# --------------------------------------------------------------------------

def main() -> None:
    cfg = carregar_config()
    db = Database(cfg.db_path)

    app = Application.builder().token(cfg.telegram_token).build()
    app.bot_data["config"] = cfg
    app.bot_data["db"] = db

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler(["ajuda", "help"], cmd_ajuda))
    app.add_handler(CommandHandler("cartao", cmd_cartao))
    app.add_handler(CommandHandler("conta", cmd_conta))
    app.add_handler(CommandHandler("contas", cmd_contas))
    app.add_handler(CommandHandler("remover", cmd_remover))
    app.add_handler(CommandHandler("planilha", cmd_planilha))
    app.add_handler(CommandHandler(["resumo", "mes"], cmd_resumo))
    app.add_handler(CommandHandler("semana", cmd_semana))
    app.add_handler(CommandHandler("desfazer", cmd_desfazer))
    app.add_handler(CommandHandler("renda", cmd_renda))
    app.add_handler(CommandHandler("investimento", cmd_investimento))
    app.add_handler(CommandHandler("teto", cmd_teto))
    app.add_handler(CommandHandler("pendentes", cmd_pendentes))
    app.add_handler(CommandHandler("pago", cmd_pago))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, receber_foto))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto))

    tz = pytz.timezone(cfg.timezone)
    app.job_queue.run_daily(job_manha, time=dtime(hour=9, minute=0, tzinfo=tz))
    app.job_queue.run_daily(job_noite, time=dtime(hour=20, minute=0, tzinfo=tz))

    log.info("Bot iniciado. Aguardando mensagens...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
