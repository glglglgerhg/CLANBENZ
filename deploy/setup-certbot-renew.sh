#!/usr/bin/env bash
set -euo pipefail

DOMAIN="clanbenz.run.place"
WWW_DOMAIN="www.clanbenz.run.place"
EMAIL="${1:-admin@clanbenz.run.place}"

sudo apt update
sudo apt install -y certbot python3-certbot-nginx

# Получение/обновление сертификата через nginx plugin.
sudo certbot --nginx \
  -d "$DOMAIN" -d "$WWW_DOMAIN" \
  --agree-tos --email "$EMAIL" --non-interactive --redirect

# Проверка автоматического продления (systemd timer certbot.timer)
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer
sudo systemctl status certbot.timer --no-pager

# Тестовый dry-run продления
sudo certbot renew --dry-run

echo "[OK] Auto-renew настроен. Сертификаты будут продлеваться автоматически."
