# 🤖 Assistente Financeiro no Telegram

Bot pessoal de Telegram que organiza suas finanças:

- 📸 **Recebe fotos de notas fiscais e comprovantes** — você envia a foto com uma
  legenda dizendo como pagou ("paguei no Nubank crédito") e ele extrai valor,
  data, estabelecimento e categoria com IA de visão computacional — **Google
  Gemini (gratuito)** ou Anthropic Claude, você escolhe.
- 📊 **Mantém sua planilha financeira** — o comando `/planilha` gera um Excel
  atualizado com todos os lançamentos, os dias de pagamento de cada cartão e
  conta, resumo mensal, resumo semanal e gastos por categoria.
- 🗓️ **Resumos automáticos** — todo domingo à noite chega o resumo da semana e
  todo dia 1º o fechamento do mês, com total gasto, comparação com suas médias
  semanais/mensais e sugestões de onde economizar.
- ⏰ **Lembretes de vencimento** — avisa quando um cartão ou conta vence em 3
  dias, amanhã e no dia.
- ❓ **Responde perguntas** — "quanto gastei no Nubank este mês?", "qual conta
  vence agora?", "resume meus gastos" — é só perguntar em linguagem natural.
- ✍️ **Registro por texto** — sem foto? Mande "gastei 52,90 no mercado no Itaú"
  e ele registra do mesmo jeito.

> **Por que Telegram e não WhatsApp?** O bot do Telegram é oficial, gratuito e
> criado em 2 minutos com o @BotFather. A API oficial do WhatsApp (Meta Business)
> exige aprovação de empresa e cobrança por conversa, e as bibliotecas
> não-oficiais quebram com frequência e arriscam banimento do número. Toda a
> lógica deste projeto (banco, planilha, resumos, IA) é independente do canal —
> dá para plugar o WhatsApp depois se você tiver acesso à API oficial.

## Como funciona

```
Foto da nota + legenda ──▶ IA (Gemini grátis ou Claude) extrai valor/data/loja/categoria
                                     │
                                     ▼
                    SQLite (data/financeiro.db) ──▶ /planilha gera o Excel
                                     │
                     ┌───────────────┼────────────────┐
                     ▼               ▼                ▼
              resumo semanal   resumo mensal   perguntas em
              (domingo 20h)      (dia 1º 9h)   linguagem natural
```

## Instalação

Requisitos: Python 3.10+.

### 1. Crie o bot no Telegram

1. Abra o Telegram e fale com o [@BotFather](https://t.me/BotFather);
2. Envie `/newbot`, escolha um nome e um username;
3. Copie o **token** que ele devolve.

### 2. Escolha a IA que lê as notas (tem opção gratuita)

O bot funciona com **um** destes dois provedores — configure a chave de um deles:

| Provedor | Custo | Como obter a chave |
|---|---|---|
| **Google Gemini** (recomendado para custo zero) | **Gratuito** — a camada free dá centenas de leituras/dia, de sobra para uso pessoal | [aistudio.google.com](https://aistudio.google.com/) → *Get API key* |
| Anthropic Claude | Pago (centavos por nota), leitura um pouco melhor | [platform.claude.com](https://platform.claude.com/) → *API Keys* |

Preencha `GEMINI_API_KEY` **ou** `ANTHROPIC_API_KEY` no `.env`. Se preencher as
duas, o Claude é usado por padrão (mude com `IA_PROVIDER=gemini`).

### 3. Configure e rode

```bash
git clone <este-repositório>
cd assistenteHome

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edite o .env com o token do bot e a chave da Anthropic

python bot.py
```

Abra o chat do seu bot no Telegram, envie `/start` e pronto.

**Recomendado:** preencha `ALLOWED_USER_IDS` no `.env` com o seu ID do Telegram
(descubra com o bot [@userinfobot](https://t.me/userinfobot)). Assim ninguém
mais consegue usar o seu bot — os dados são financeiros.

### Rodando com Docker (opcional)

```bash
docker build -t finbot .
docker run -d --name finbot --restart unless-stopped \
  --env-file .env -v "$(pwd)/data:/app/data" finbot
```

## 💸 Rodando 100% de graça

Custo zero de verdade é possível:

1. **Telegram**: o bot é gratuito, sem limite relevante para uso pessoal.
2. **IA**: use o **Gemini** (`GEMINI_API_KEY`) — a camada gratuita do Google
   cobre com folga o uso pessoal (dezenas de notas por dia). Sem cartão de
   crédito.
3. **Hospedagem** — o bot precisa de um processo Python sempre ligado.
   Opções gratuitas que funcionam bem:

   | Opção | Observações |
   |---|---|
   | **Oracle Cloud Always Free** | VM gratuita para sempre (ARM até 4 vCPU/24 GB). Pede cartão no cadastro, mas não cobra. A melhor opção "servidor de verdade". |
   | **Google Cloud e2-micro** | 1 VM e2-micro *always free* (regiões dos EUA). Também pede cartão sem cobrar. |
   | **PC/notebook antigo ou Raspberry Pi em casa** | `docker run --restart unless-stopped ...` e esquece. Zero burocracia. |
   | **Celular Android velho com Termux** | `pkg install python` + `pip install -r requirements.txt` + `python bot.py`. Deixe na tomada. |

   Evite os planos gratuitos de Render/Railway/Fly: eles "dormem" o processo
   após minutos sem tráfego, o que quebra os resumos agendados e o polling.

   **Instalação em 1 comando na VM** (Ubuntu/Debian — ex.: Oracle Always Free):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/Horquichoqui/assistenteHome/main/deploy/instalar.sh | bash
   # edite o .env quando ele pedir, e rode o script de novo
   ```

   O script instala o Python, clona o projeto, cria o `.env` para você
   preencher e configura um serviço systemd que mantém o bot no ar e o
   reinicia sozinho se a VM reiniciar. Como o bot só faz conexões de SAÍDA
   (polling do Telegram), não é preciso abrir nenhuma porta no firewall.

   Alternativas manuais em uma VM Linux:

   ```bash
   # com Docker (recomendado)
   docker run -d --name finbot --restart unless-stopped \
     --env-file .env -v "$(pwd)/data:/app/data" finbot

   # ou com systemd
   sudo tee /etc/systemd/system/finbot.service > /dev/null <<'EOF'
   [Unit]
   Description=Assistente financeiro Telegram
   After=network-online.target

   [Service]
   WorkingDirectory=/home/SEU_USUARIO/assistenteHome
   ExecStart=/home/SEU_USUARIO/assistenteHome/.venv/bin/python bot.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   EOF
   sudo systemctl enable --now finbot
   ```

## Comandos

| Comando | O que faz |
|---|---|
| `/start` | Inicia o bot e ativa os envios automáticos neste chat |
| `/ajuda` | Mostra a ajuda |
| `/cartao Nubank 10` | Cadastra o cartão Nubank com vencimento dia 10 |
| `/cartao Nubank 10 3` | Idem, com fechamento da fatura dia 3 |
| `/conta Luz 15` | Cadastra a conta Luz com vencimento dia 15 |
| `/contas` | Lista cartões/contas, próximos vencimentos e gasto do mês |
| `/remover Nubank` | Remove um cartão/conta |
| `/renda 1 5500 Salário` | Define a renda mensal da pessoa 1 (modo casal) |
| `/investimento Tesouro 5000` | Registra/atualiza um investimento |
| `/teto mercado 800` | Define o teto de gasto mensal de uma categoria |
| `/pendentes` | Lista gastos ainda não marcados como pagos |
| `/pago 42` | Marca o gasto #42 como pago |
| `/planilha` | Gera e envia a planilha Excel atualizada |
| `/resumo` | Resumo do mês atual, na hora |
| `/semana` | Resumo da semana atual, na hora |
| `/desfazer` | Apaga o último gasto registrado (errou? desfaz) |

Fora dos comandos, tudo é linguagem natural: fotos registram gastos, frases
como "gastei 30 de uber no Nubank" registram gastos, e perguntas são
respondidas com base nos seus dados. Diga na mensagem se o gasto foi de uma
pessoa específica ou combinado ("foi a Maria Eduarda", "foi combinado") e
mencione parcelamento ("em 10x") — o bot lança as parcelas automaticamente
nos meses seguintes.

## Modo casal (opcional)

Preenchendo `PESSOA_1_ID`/`PESSOA_1_NOME`/`PESSOA_2_ID`/`PESSOA_2_NOME` no
`.env`, o bot passa a atribuir cada gasto a quem enviou a mensagem (ou a quem
a legenda/IA indicar), separa "combinado" para gastos conjuntos, e habilita
`/renda 1`/`/renda 2` para acompanhar o quanto cada um ganha e gasta. Sem essa
configuração, o bot funciona normalmente sem essa separação por pessoa.

## A planilha

`/planilha` gera um `.xlsx` com dez abas:

1. **Resumo do Mês** — renda, despesas, investimentos e saldo do mês, % da
   renda gasto, gasto por pessoa, gráfico e um espaço para metas/anotações;
2. **Gastos Fixos** — parcelamentos e gastos vinculados a contas cadastradas
   (aluguel, condomínio, assinaturas), com responsável, parcela e status de
   pagamento;
3. **Gastos do Dia a Dia** — compras avulsas, no mesmo formato;
4. **Faturas de Cartão** — total do mês por cartão de crédito, calculado
   automaticamente, com status de pagamento;
5. **Categorias e Orçamento** — teto definido por categoria, gasto do mês e
   um radar (🟢🟡🔴) de quão perto do limite você está, com gráfico;
6. **Investimentos** — valores registrados com `/investimento`;
7. **Cartões e Contas** — dias de vencimento/fechamento e último valor pago
   (útil para contas de valor variável, como luz e água);
8. **Resumo Mensal** — total por mês (12 meses) e média mensal;
9. **Resumo Semanal** — total por semana (12 semanas) e média semanal;
10. **Lançamentos** — histórico completo de todos os gastos.

A fonte de verdade é o banco SQLite em `data/financeiro.db` — a planilha é
gerada dele a qualquer momento, sempre atualizada. Faça backup da pasta `data/`.
As colunas "Pago?" na planilha são texto (Sim/Não) — o Excel gerado não tem
caixas de marcar clicáveis; use `/pendentes` e `/pago <número>` para atualizar
o status pelo bot.

## Estrutura do código

```
bot.py               # handlers do Telegram, comandos e tarefas agendadas
finbot/
  config.py          # variáveis de ambiente (.env)
  db.py              # SQLite: cartões/contas, gastos, ajustes
  stats.py           # totais, médias, séries e vencimentos
  ia.py              # prompts e escolha do provedor de IA
  ia_claude.py       # backend Anthropic (Claude)
  ia_gemini.py       # backend Google Gemini (gratuito)
  resumos.py         # resumos semanais/mensais (com fallback sem IA)
  planilha.py        # geração do Excel
tests/               # testes de banco, estatísticas e planilha
```

## Testes

```bash
pip install pytest
pytest
```
