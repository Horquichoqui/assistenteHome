# 🤖 Assistente Financeiro no Telegram

Bot pessoal de Telegram que organiza suas finanças:

- 📸 **Recebe fotos de notas fiscais e comprovantes** — você envia a foto com uma
  legenda dizendo como pagou ("paguei no Nubank crédito") e ele extrai valor,
  data, estabelecimento e categoria usando a API do Claude (visão computacional).
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
Foto da nota + legenda ──▶ Claude (visão) extrai valor/data/loja/categoria
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

### 2. Pegue sua chave da Anthropic

Crie uma chave de API em [platform.claude.com](https://platform.claude.com/)
(menu API Keys). A leitura de cada nota fiscal custa centavos de dólar.

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
| `/planilha` | Gera e envia a planilha Excel atualizada |
| `/resumo` | Resumo do mês atual, na hora |
| `/semana` | Resumo da semana atual, na hora |
| `/desfazer` | Apaga o último gasto registrado (errou? desfaz) |

Fora dos comandos, tudo é linguagem natural: fotos registram gastos, frases
como "gastei 30 de uber no Nubank" registram gastos, e perguntas são
respondidas com base nos seus dados.

## A planilha

`/planilha` gera um `.xlsx` com cinco abas:

1. **Lançamentos** — todos os gastos: data, valor, estabelecimento, categoria,
   cartão/conta, descrição;
2. **Cartões e Contas** — dias de vencimento e fechamento, próximo vencimento
   e gasto do mês em cada um;
3. **Resumo Mensal** — total por mês (12 meses) e média mensal;
4. **Resumo Semanal** — total por semana (12 semanas) e média semanal;
5. **Categorias do Mês** — para onde o dinheiro foi neste mês.

A fonte de verdade é o banco SQLite em `data/financeiro.db` — a planilha é
gerada dele a qualquer momento, sempre atualizada. Faça backup da pasta `data/`.

## Estrutura do código

```
bot.py               # handlers do Telegram, comandos e tarefas agendadas
finbot/
  config.py          # variáveis de ambiente (.env)
  db.py              # SQLite: cartões/contas, gastos, ajustes
  stats.py           # totais, médias, séries e vencimentos
  ia.py              # chamadas ao Claude: visão, interpretação e respostas
  resumos.py         # resumos semanais/mensais (com fallback sem IA)
  planilha.py        # geração do Excel
tests/               # testes de banco, estatísticas e planilha
```

## Testes

```bash
pip install pytest
pytest
```
