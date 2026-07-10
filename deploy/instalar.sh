#!/usr/bin/env bash
# Instala o assistente financeiro em um servidor Ubuntu/Debian recém-criado
# (ex.: VM Always Free da Oracle Cloud). Rode duas vezes:
#   1ª vez: instala tudo e cria o .env para você preencher;
#   2ª vez: ativa o serviço que mantém o bot rodando 24/7.
#
# Uso:  bash instalar.sh
# Obs.: se o repositório for privado, clone manualmente antes e rode o script
#       de dentro da pasta do projeto.
set -euo pipefail

REPO="https://github.com/Horquichoqui/assistenteHome.git"
DIR="$HOME/assistenteHome"

# Se o script já está dentro do projeto, usa a pasta atual
if [ -f "$(dirname "$0")/../bot.py" ]; then
    DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi

echo "==> Instalando dependências do sistema..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv git

if [ ! -d "$DIR" ]; then
    echo "==> Clonando o projeto em $DIR..."
    git clone "$REPO" "$DIR"
fi
cd "$DIR"

echo "==> Criando ambiente Python..."
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "======================================================================"
    echo "  Quase lá! Agora edite o arquivo de configuração:"
    echo ""
    echo "      nano $DIR/.env"
    echo ""
    echo "  Preencha TELEGRAM_BOT_TOKEN (do @BotFather) e GEMINI_API_KEY"
    echo "  (gratuita, em https://aistudio.google.com/)."
    echo ""
    echo "  Depois rode este script de novo para ativar o bot:"
    echo ""
    echo "      bash $DIR/deploy/instalar.sh"
    echo "======================================================================"
    exit 0
fi

echo "==> Criando o serviço systemd (mantém o bot rodando e reinicia sozinho)..."
sudo tee /etc/systemd/system/finbot.service > /dev/null <<EOF
[Unit]
Description=Assistente financeiro Telegram
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now finbot

echo ""
echo "==> Pronto! O bot está no ar. Comandos úteis:"
echo "    sudo systemctl status finbot     # ver se está rodando"
echo "    sudo journalctl -u finbot -f     # acompanhar os logs"
echo "    sudo systemctl restart finbot    # reiniciar após mudar o .env"
