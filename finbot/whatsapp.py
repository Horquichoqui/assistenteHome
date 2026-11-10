"""Leitura de um grupo do WhatsApp pelo WhatsApp Web, com um navegador automatizado.

O Chromium abre o WhatsApp Web com um perfil salvo em disco: a sessão fica
gravada e o QR code só precisa ser lido uma vez. O grupo é aberto pela busca, o
histórico das últimas horas é carregado rolando a conversa para cima, e cada
mensagem volta com hora e remetente. Mensagens com imagem (boletos, comprovantes)
também viram um print da própria bolha, para a IA ler e para você conferir.

Aviso: a automação do WhatsApp Web não é permitida pelos Termos de Uso do
WhatsApp. Para ler um grupo uma vez por dia o risco de restrição da conta é
baixo, mas não é zero. Os seletores seguem a estrutura atual do WhatsApp Web e
podem precisar de ajuste quando ele mudar.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

URL_WHATSAPP = "https://web.whatsapp.com/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{versao}.0.0.0 Safari/537.36"
)
MAX_IMAGENS = 12  # prints por leitura (limita o uso da IA)
MAX_ROLAGENS = 60  # quantas vezes a conversa é rolada para cima
TEMPO_CARREGAR_S = 180  # espera o WhatsApp Web carregar (VM de 1 GB é lenta)
TEMPO_SINCRONIZAR_S = 20 * 60  # 1ª sincronização após ler o QR ("Carregando suas conversas")
QR_TENTATIVAS = 4  # o QR é reenviado enquanto espera a leitura
QR_INTERVALO_S = 25  # o QR muda a cada ~20 s
# Prefixo das mensagens que o próprio bot manda no grupo; elas não são lidas como contas.
PREFIXO_BOT = "🤖 finbot:"

# "[09:15, 16/10/2026] Maria Eduarda: " — atributo data-pre-plain-text do WhatsApp Web
_CABECALHO = re.compile(
    r"^\[(\d{1,2}:\d{2}(?::\d{2})?),\s*(\d{1,2}/\d{1,2}/\d{2,4})\]\s*(.*?):\s*$"
)

# Linhas (mensagens) abertas na conversa. Cada linha recebe um atributo
# data-finbot-linha para podermos tirar o print dela depois.
_JS_LINHAS = """() => {
    let linhas = Array.from(document.querySelectorAll('#main div[role="row"]'));
    if (!linhas.length) linhas = Array.from(document.querySelectorAll('#main div[data-id]'));
    return linhas.map((row, indice) => {
        row.setAttribute('data-finbot-linha', String(indice));
        const pre = row.querySelector('[data-pre-plain-text]');
        const texto = row.querySelector('span.selectable-text');
        return {
            indice: indice,
            pre: pre ? pre.getAttribute('data-pre-plain-text') : null,
            texto: texto ? texto.innerText.trim() : '',
            imagem: !!row.querySelector('img[src^="blob:"]'),
        };
    });
}"""

# Primeira mensagem com cabeçalho na conversa (para saber se já chegamos ao limite).
_JS_TOPO = """() => {
    let linhas = Array.from(document.querySelectorAll('#main div[role="row"]'));
    if (!linhas.length) linhas = Array.from(document.querySelectorAll('#main div[data-id]'));
    const primeira = linhas.find(r => r.querySelector('[data-pre-plain-text]'));
    return {
        total: linhas.length,
        pre: primeira ? primeira.querySelector('[data-pre-plain-text]').getAttribute('data-pre-plain-text') : null,
    };
}"""


@dataclass(frozen=True)
class MensagemWhatsApp:
    chave: str  # hash estável: evita analisar a mesma mensagem duas vezes
    momento: datetime
    remetente: str
    texto: str
    print_png: Optional[bytes] = None  # print da bolha, quando a mensagem tem imagem


@dataclass
class ColetaWhatsApp:
    status: str  # "ok" | "precisa_login" | "grupo_nao_encontrado" | "erro"
    mensagens: list[MensagemWhatsApp] = field(default_factory=list)
    erro: Optional[str] = None


def mesmo_nome_de_grupo(titulo: Optional[str], configurado: str) -> bool:
    """Compara nomes ignorando caixa, acentos e emojis.

    O WhatsApp mostra "NOSSAS NOTAS 🗒️👫" e o .env pode ter "Nossas Notas".
    """
    def normalizar(nome: str) -> str:
        sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
        return " ".join(re.sub(r"[^a-z0-9\s]", "", sem_acento.casefold()).split())

    if not titulo or not configurado.strip():
        return False
    return normalizar(titulo) == normalizar(configurado)


def parsear_cabecalho(pre: str) -> Optional[tuple[datetime, str]]:
    """Extrai (momento, remetente) do cabeçalho de uma mensagem; None se não reconhecer."""
    m = _CABECALHO.match(pre.strip())
    if not m:
        return None
    hora, data, remetente = m.groups()
    ano = "%Y" if len(data.split("/")[2]) == 4 else "%y"
    formato_hora = "%H:%M:%S" if hora.count(":") == 2 else "%H:%M"
    try:
        momento = datetime.strptime(f"{data} {hora}", f"%d/%m/{ano} {formato_hora}")
    except ValueError:
        return None
    return momento, remetente.strip()


async def coletar_grupo(
    nome_grupo: str,
    perfil: Path,
    agora: datetime,
    janela_horas: int,
    enviar_qr: Callable[[bytes], Awaitable[None]],
    url: str = URL_WHATSAPP,
) -> ColetaWhatsApp:
    """Lê as mensagens do grupo das últimas `janela_horas` horas.

    `agora` é a hora local, sem fuso, a mesma que o WhatsApp mostra nas mensagens.
    Se o WhatsApp não estiver conectado, o QR code é enviado por `enviar_qr`.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ColetaWhatsApp(
            "erro",
            erro=(
                "Playwright não instalado. Rode: pip install -r requirements.txt "
                "e python -m playwright install --with-deps chromium"
            ),
        )

    desde = agora - timedelta(hours=janela_horas)
    try:
        async with async_playwright() as p:
            contexto = await _abrir_navegador(p, perfil)
            try:
                pagina = contexto.pages[0] if contexto.pages else await contexto.new_page()
                return await _coletar(pagina, nome_grupo, desde, agora, enviar_qr, url)
            finally:
                await contexto.close()
    except Exception as exc:  # o navegador não pode derrubar o bot
        log.exception("Falha ao ler o grupo do WhatsApp")
        return ColetaWhatsApp("erro", erro=f"{type(exc).__name__}: {exc}")


async def enviar_ao_grupo(
    nome_grupo: str,
    perfil: Path,
    textos: list[str],
    url: str = URL_WHATSAPP,
) -> bool:
    """Manda mensagens no grupo usando a sessão já conectada. Não pede QR code:
    se o WhatsApp não estiver conectado, só devolve False."""
    textos = [t for t in textos if t.strip()]
    if not textos:
        return True
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False
    try:
        async with async_playwright() as p:
            contexto = await _abrir_navegador(p, perfil)
            try:
                pagina = contexto.pages[0] if contexto.pages else await contexto.new_page()
                await pagina.goto(url, wait_until="domcontentloaded", timeout=60_000)
                if await _aguardar_estado(pagina, TEMPO_CARREGAR_S) != "logado":
                    return False
                await asyncio.sleep(3)
                if not await _abrir_grupo(pagina, nome_grupo):
                    return False
                for texto in textos:
                    await _digitar_e_enviar(pagina, f"{PREFIXO_BOT} {texto}")
                await asyncio.sleep(3)  # dá tempo para as mensagens saírem
                return True
            finally:
                await contexto.close()
    except Exception:
        log.exception("Falha ao enviar mensagem no grupo do WhatsApp")
        return False


async def _abrir_navegador(p, perfil: Path):
    perfil.mkdir(parents=True, exist_ok=True)
    # O user agent precisa bater com a versão real do Chromium (cabeçalho Sec-CH-UA);
    # versão divergente ou "HeadlessChrome" faz o WhatsApp recusar vincular o aparelho.
    saida = subprocess.run([p.chromium.executable_path, "--version"],
                           capture_output=True, text=True, check=True).stdout
    versao = re.search(r"(\d+)\.\d+", saida).group(1)
    return await p.chromium.launch_persistent_context(
        user_data_dir=str(perfil),
        channel="chromium",  # Chromium completo, não o headless-shell
        headless=True,
        viewport={"width": 1280, "height": 900},
        user_agent=USER_AGENT.format(versao=versao),
        locale="pt-BR",
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


async def _digitar_e_enviar(pagina, texto: str) -> None:
    """Digita na caixa da conversa; Shift+Enter quebra linha, Enter envia."""
    caixa = pagina.locator('footer div[contenteditable="true"]').last
    await caixa.click()
    for i, linha in enumerate(texto.split("\n")):
        if i:
            await pagina.keyboard.press("Shift+Enter")
        if linha:
            await pagina.keyboard.insert_text(linha)
    await pagina.keyboard.press("Enter")
    await asyncio.sleep(1)


async def _coletar(pagina, nome_grupo: str, desde: datetime, agora: datetime,
                   enviar_qr, url: str) -> ColetaWhatsApp:
    await pagina.goto(url, wait_until="domcontentloaded", timeout=60_000)
    estado = await _aguardar_estado(pagina, TEMPO_CARREGAR_S)
    if estado == "qr":
        estado = await _aguardar_login(pagina, enviar_qr)
    if estado == "carregando":
        return ColetaWhatsApp("erro", erro="O WhatsApp Web não carregou a tempo.")
    if estado != "logado":
        return ColetaWhatsApp("precisa_login")

    await asyncio.sleep(3)  # dá tempo para a lista de conversas aparecer
    await pagina.keyboard.press("Escape")  # fecha avisos como "Novidades do WhatsApp Web"
    if not await _abrir_grupo(pagina, nome_grupo):
        return ColetaWhatsApp("grupo_nao_encontrado")
    await _carregar_historico(pagina, desde)
    return ColetaWhatsApp("ok", await _ler_mensagens(pagina, desde, agora))


async def _estado(pagina) -> str:
    if await pagina.locator("#pane-side").count():
        return "logado"
    if await pagina.locator("canvas").count():
        return "qr"
    return "carregando"


async def _sincronizando(pagina) -> bool:
    """Telas de sincronização ("Carregando suas conversas", "Não feche esta janela"): demoram minutos."""
    return bool(await pagina.get_by_text(re.compile("Carregando suas conversas|Não feche esta janela")).count())


async def _aguardar_estado(pagina, segundos: int) -> str:
    inicio = time.monotonic()
    while True:
        estado = await _estado(pagina)
        if estado != "carregando":
            return estado
        decorrido = time.monotonic() - inicio
        if decorrido > segundos and not (
            decorrido < TEMPO_SINCRONIZAR_S and await _sincronizando(pagina)
        ):
            return estado
        await asyncio.sleep(1)


async def _aguardar_login(pagina, enviar_qr) -> str:
    """Envia o QR code e espera a leitura pelo celular; reenvia porque ele muda sozinho."""
    for _ in range(QR_TENTATIVAS):
        # O WhatsApp troca o canvas ao renovar o QR ou ao processar uma leitura;
        # se ele sumir, continua esperando em vez de derrubar a leitura toda.
        try:
            png = await pagina.locator("canvas").first.screenshot(timeout=60_000)
        except Exception:
            if await pagina.locator("#pane-side").count() or await _sincronizando(pagina):
                return await _aguardar_estado(pagina, TEMPO_CARREGAR_S)
            texto = (await pagina.inner_text("body"))[:200].replace("\n", " | ")
            log.warning("QR code não apareceu; tela do WhatsApp: %s", texto)
            continue
        await enviar_qr(png)
        for _ in range(QR_INTERVALO_S):
            await asyncio.sleep(1)
            if await pagina.locator("#pane-side").count():
                return "logado"
            if await _sincronizando(pagina):  # QR lido; espera a sincronização
                return await _aguardar_estado(pagina, TEMPO_CARREGAR_S)
    return "qr"


async def _abrir_grupo(pagina, nome_grupo: str) -> bool:
    # Busca de conversas: <input> no WhatsApp Web atual, div editável nas versões antigas.
    caixa = pagina.locator(
        'input[aria-label^="Pesquisar"], div[contenteditable="true"][data-tab="3"]'
    ).first
    await caixa.click()
    await pagina.keyboard.insert_text(nome_grupo)
    for _ in range(10):
        await asyncio.sleep(1)
        # Lê os títulos de uma vez: a lista é redesenhada enquanto a busca filtra.
        titulos = await pagina.locator("#pane-side span[title]").evaluate_all(
            "els => els.map(e => e.title)"
        )
        for titulo in titulos:
            if mesmo_nome_de_grupo(titulo, nome_grupo):
                await pagina.locator("#pane-side").get_by_title(titulo, exact=True).first.click()
                await pagina.locator("#main").wait_for(timeout=20_000)
                await asyncio.sleep(2)
                return True
    return False


async def _carregar_historico(pagina, desde: datetime) -> None:
    """Rola a conversa para cima até alcançar o início da janela (ou o topo)."""
    anterior, parado = -1, 0
    for _ in range(MAX_ROLAGENS):
        topo = await pagina.evaluate(_JS_TOPO)
        if topo["pre"]:
            cabecalho = parsear_cabecalho(topo["pre"])
            if cabecalho and cabecalho[0] < desde:
                return
        if topo["total"] == anterior:
            parado += 1
            if parado >= 2:  # não carregou mais nada: chegamos ao início do chat
                return
        else:
            parado = 0
        anterior = topo["total"]
        await pagina.mouse.move(700, 450)
        await pagina.mouse.wheel(0, -1800)
        await asyncio.sleep(1.5)


async def _ler_mensagens(pagina, desde: datetime, agora: datetime) -> list[MensagemWhatsApp]:
    linhas = await pagina.evaluate(_JS_LINHAS)

    # 1) Identifica cada mensagem na ordem do chat, para que a chave seja estável.
    entradas = []
    contagem: dict[str, int] = {}
    for linha in linhas:
        if not linha["pre"] and not linha["imagem"]:
            continue  # aviso do sistema, data, "criou o grupo" etc.
        cabecalho = parsear_cabecalho(linha["pre"]) if linha["pre"] else None
        momento, remetente = cabecalho if cabecalho else (agora, "desconhecido")
        base = f"{linha['pre'] or ''}|{linha['texto']}|{int(linha['imagem'])}"
        n = contagem.get(base, 0)
        contagem[base] = n + 1
        chave = hashlib.sha256(f"{base}|{n}".encode("utf-8")).hexdigest()[:32]
        if momento >= desde:
            entradas.append((linha, momento, remetente, chave))

    # 2) Print só das imagens da janela; se passar do limite, ficam as mais recentes.
    prints: dict[str, bytes] = {}
    for linha, _, _, chave in reversed(entradas):
        if not linha["imagem"] or len(prints) >= MAX_IMAGENS:
            continue
        try:
            locator = pagina.locator(f'[data-finbot-linha="{linha["indice"]}"]')
            prints[chave] = await locator.screenshot()
        except Exception:
            log.warning("Não consegui capturar o print de uma imagem do grupo", exc_info=True)

    return [
        MensagemWhatsApp(chave, momento, remetente, linha["texto"], prints.get(chave))
        for linha, momento, remetente, chave in entradas
    ]
