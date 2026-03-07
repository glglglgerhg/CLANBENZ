# Деплой CLANBENZ на Ubuntu 22 (systemd + Nginx + SSL)

Ниже — готовая рабочая схема: приложение работает как служба `systemd` на `127.0.0.1:8080`, а Nginx принимает трафик на `80/443` и проксирует в Python-сервис.

## 1) Установка зависимостей

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx
```

## 2) Клонирование и подготовка проекта

```bash
cd /opt
sudo git clone <URL_ВАШЕГО_РЕПО> clanbenz
sudo chown -R $USER:$USER /opt/clanbenz
cd /opt/clanbenz

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

## 3) Создание systemd-службы

```bash
sudo tee /etc/systemd/system/clanbenz.service > /dev/null <<'UNIT'
[Unit]
Description=CLANBENZ Python service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/clanbenz
ExecStart=/opt/clanbenz/.venv/bin/python3 /opt/clanbenz/SITEBENZ.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
```

Дайте права на папку проекту (нужно для БД/логов):

```bash
sudo chown -R www-data:www-data /opt/clanbenz
```

Запуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable clanbenz
sudo systemctl start clanbenz
sudo systemctl status clanbenz
```

Логи:

```bash
sudo journalctl -u clanbenz -f
```

## 4) Подключение домена и SSL

Есть два варианта:

- Автообновляемый SSL (рекомендуется): смотрите `DEPLOY_SSL.md`, раздел «Вариант A».
- Ручной SSL (ваш cert/key): смотрите `DEPLOY_SSL.md`, раздел «Вариант B».

Коротко для auto-SSL:

```bash
cd /opt/clanbenz
sudo cp deploy/nginx-clanbenz.conf /etc/nginx/sites-available/clanbenz.run.place
sudo ln -sf /etc/nginx/sites-available/clanbenz.run.place /etc/nginx/sites-enabled/clanbenz.run.place
sudo nginx -t
sudo systemctl reload nginx
bash deploy/setup-certbot-renew.sh you@example.com
```

## 5) Firewall

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

## 6) Проверки после деплоя

```bash
curl -I http://clanbenz.run.place
curl -I https://clanbenz.run.place
sudo systemctl status clanbenz --no-pager
sudo systemctl status nginx --no-pager
```

Если нужно, могу сделать отдельный вариант деплоя через Docker Compose.
