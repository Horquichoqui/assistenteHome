import asyncio
from datetime import datetime

import pytest

from finbot.whatsapp import coletar_grupo, mesmo_nome_de_grupo, parsear_cabecalho

# Imita a estrutura do WhatsApp Web usada pelo leitor: painel de conversas com a
# busca e o nome do grupo, e a conversa aberta com as mensagens e uma imagem.
PAGINA_FALSA = """<!doctype html>
<html><body>
<div id="pane-side">
  <div contenteditable="true" data-tab="3"></div>
  <span title="Casa Gabriel e Maria">Casa Gabriel e Maria</span>
  <span title="Outro chat">Outro chat</span>
</div>
<div id="main" style="display:none"><div role="grid" id="mensagens"></div></div>
<script>
  document.querySelector('#pane-side span[title="Casa Gabriel e Maria"]').addEventListener('click', () => {
    document.querySelector('#main').style.display = 'block';
    document.querySelector('#mensagens').innerHTML = `
      <div role="row"><div data-pre-plain-text="[09:15, 16/10/2026] Maria Eduarda: ">
        <span class="selectable-text">Luz vence dia 15, R$ 180,00</span></div></div>
      <div role="row"><div data-pre-plain-text="[10:02, 16/10/2026] Gabriel: ">
        <span class="selectable-text">Boleto da internet</span><img id="boleto"></div></div>
      <div role="row"><div data-pre-plain-text="[08:00, 10/10/2026] Gabriel: ">
        <span class="selectable-text">mensagem de outra semana</span></div></div>`;
    const c = document.createElement('canvas');
    c.width = 300; c.height = 120;
    const g = c.getContext('2d');
    g.fillStyle = 'white'; g.fillRect(0, 0, 300, 120);
    g.fillStyle = 'black'; g.font = '24px sans-serif'; g.fillText('BOLETO 99,90', 10, 60);
    c.toBlob(b => { document.getElementById('boleto').src = URL.createObjectURL(b); });
  });
</script>
</body></html>
"""


def test_parsear_cabecalho_formato_brasileiro():
    assert parsear_cabecalho("[09:15, 16/10/2026] Maria Eduarda: ") == (
        datetime(2026, 10, 16, 9, 15),
        "Maria Eduarda",
    )


def test_parsear_cabecalho_com_segundos_e_ano_curto():
    assert parsear_cabecalho("[23:05:10, 1/3/26] Gabriel:") == (
        datetime(2026, 3, 1, 23, 5, 10),
        "Gabriel",
    )


def test_mesmo_nome_de_grupo_ignora_caixa_acento_e_emoji():
    # título real do WhatsApp (com emojis) x nome digitado no .env
    assert mesmo_nome_de_grupo("NOSSAS NOTAS 🗒️👫", "Nossas Notas")
    assert mesmo_nome_de_grupo("Família Job", "familia job")
    assert mesmo_nome_de_grupo("Casa  Gabriel e Maria", "Casa Gabriel e Maria")
    assert not mesmo_nome_de_grupo("Nossas Notas Antigas", "Nossas Notas")
    assert not mesmo_nome_de_grupo("Outro chat", "Nossas Notas")
    assert not mesmo_nome_de_grupo(None, "Nossas Notas")
    assert not mesmo_nome_de_grupo("Nossas Notas", "   ")


def test_parsear_cabecalho_invalido():
    assert parsear_cabecalho("texto qualquer") is None
    assert parsear_cabecalho("[25:99, 40/13/2026] Alguém:") is None


def test_coletar_grupo_le_mensagens_e_tira_print_da_imagem(tmp_path):
    pytest.importorskip("playwright.async_api")
    html = tmp_path / "whatsapp.html"
    html.write_text(PAGINA_FALSA, encoding="utf-8")
    enviados_qr = []

    async def enviar_qr(png: bytes) -> None:
        enviados_qr.append(png)

    coleta = asyncio.run(
        coletar_grupo(
            "Casa Gabriel e Maria",
            tmp_path / "perfil",
            agora=datetime(2026, 10, 16, 12, 0),
            janela_horas=48,
            enviar_qr=enviar_qr,
            url=html.as_uri(),
        )
    )
    if coleta.status == "erro" and "Executable" in (coleta.erro or ""):
        pytest.skip("Chromium do Playwright não instalado neste ambiente")

    assert coleta.status == "ok", coleta.erro
    assert enviados_qr == []  # já estava "conectado": não pede QR code
    # A mensagem de 10/10 está fora da janela de 48 h e fica de fora
    assert [m.remetente for m in coleta.mensagens] == ["Maria Eduarda", "Gabriel"]
    maria, gabriel = coleta.mensagens
    assert maria.texto == "Luz vence dia 15, R$ 180,00"
    assert maria.momento == datetime(2026, 10, 16, 9, 15)
    assert maria.print_png is None
    assert gabriel.print_png is not None and gabriel.print_png.startswith(b"\x89PNG")
    assert maria.chave != gabriel.chave


def test_enviar_ao_grupo_digita_com_prefixo_e_quebra_de_linha(tmp_path):
    pytest.importorskip("playwright.async_api")
    from finbot.whatsapp import PREFIXO_BOT, enviar_ao_grupo

    # Página falsa com caixa de mensagem: guarda o que foi enviado com Enter.
    html = tmp_path / "whatsapp.html"
    html.write_text(PAGINA_FALSA.replace(
        '<div id="main" style="display:none">',
        '<div id="main" style="display:none"><footer><div contenteditable="true" id="caixa"></div></footer>',
    ).replace("</script>", """
      window.enviadas = [];
      document.addEventListener('keydown', e => {
        const caixa = document.getElementById('caixa');
        if (e.key === 'Enter' && e.shiftKey) { e.preventDefault(); document.execCommand('insertText', false, '\\n'); }
        else if (e.key === 'Enter') { e.preventDefault(); window.enviadas.push(caixa.innerText); caixa.innerText = ''; document.title = JSON.stringify(window.enviadas); }
      });
    </script>"""), encoding="utf-8")

    ok = asyncio.run(enviar_ao_grupo("Casa Gabriel e Maria", tmp_path / "perfil", ["Luz vence amanhã\nR$ 180"], url=html.as_uri()))
    assert ok is True

    # O que exatamente sai na caixa de mensagem: prefixo + linhas separadas.
    from playwright.async_api import async_playwright
    from finbot.whatsapp import _digitar_e_enviar

    async def digitar():
        async with async_playwright() as p:
            navegador = await p.chromium.launch()
            pagina = await navegador.new_page()
            await pagina.goto(html.as_uri())
            await pagina.click('#pane-side span[title="Casa Gabriel e Maria"]')
            await _digitar_e_enviar(pagina, f"{PREFIXO_BOT} Luz vence amanhã\nR$ 180")
            enviadas = await pagina.evaluate("window.enviadas")
            await navegador.close()
            return enviadas

    enviadas = asyncio.run(digitar())
    assert len(enviadas) == 1
    assert enviadas[0].startswith(PREFIXO_BOT)
    assert "Luz vence amanhã" in enviadas[0] and "R$ 180" in enviadas[0]


def test_enviar_ao_grupo_sem_textos_nao_abre_navegador(tmp_path):
    from finbot.whatsapp import enviar_ao_grupo

    assert asyncio.run(enviar_ao_grupo("Grupo", tmp_path / "perfil", ["", "  "])) is True


def test_espera_a_sincronizacao_depois_do_qr_alem_do_tempo_de_carregar():
    """Após ler o QR, "Carregando suas conversas" pode passar do tempo normal de carga."""
    from playwright.async_api import async_playwright

    from finbot.whatsapp import _aguardar_estado

    async def rodar():
        async with async_playwright() as p:
            navegador = await p.chromium.launch()
            pagina = await navegador.new_page()
            await pagina.set_content(
                "<div id='s'>Carregando suas conversas</div><script>setTimeout(() => "
                "{document.body.innerHTML = '<div id=\"pane-side\"></div>'}, 3000)</script>"
            )
            estado = await _aguardar_estado(pagina, 1)  # 1 s de carga normal; sincroniza em 3 s
            await navegador.close()
            return estado

    assert asyncio.run(rodar()) == "logado"
