# CLANBENZ: домен + SSL + порт 80/443 (Ubuntu 22)

Да, можно сделать **автообновление SSL**. Лучший вариант — `certbot` + `certbot.timer` (systemd).

## Вариант A (рекомендуется): полностью автоматический SSL через Let's Encrypt

1) Установите Nginx и базовый конфиг домена:

```bash
sudo apt update
sudo apt install -y nginx
sudo cp deploy/nginx-clanbenz.conf /etc/nginx/sites-available/clanbenz.run.place
sudo ln -sf /etc/nginx/sites-available/clanbenz.run.place /etc/nginx/sites-enabled/clanbenz.run.place
sudo nginx -t
sudo systemctl reload nginx
```

2) Запустите helper-скрипт (укажите ваш email):

```bash
bash deploy/setup-certbot-renew.sh you@example.com
```

Скрипт:
- выпустит сертификат для `clanbenz.run.place` и `www.clanbenz.run.place`,
- включит `certbot.timer`,
- проверит `certbot renew --dry-run`.

После этого SSL будет продлеваться автоматически до истечения срока.

---

## Вариант B: использовать ваш текущий вручную выданный сертификат

Если хотите поставить именно текущий cert/key вручную:

```bash
sudo mkdir -p /etc/ssl/clanbenz
sudo nano /etc/ssl/clanbenz/clanbenz.run.place.crt
sudo nano /etc/ssl/clanbenz/clanbenz.run.place.key
sudo nano /etc/ssl/clanbenz/intermediate-ca.crt
```

Содержимое:
- `clanbenz.run.place.crt` → блок `SSL Cert`.
- `clanbenz.run.place.key` → блок `SSL Private Key`.
- `intermediate-ca.crt` → блок `SSL Intermediate CA`.

Соберите fullchain:

```bash
sudo bash -lc 'cat /etc/ssl/clanbenz/clanbenz.run.place.crt /etc/ssl/clanbenz/intermediate-ca.crt > /etc/ssl/clanbenz/clanbenz.run.place.fullchain.pem'
sudo chmod 600 /etc/ssl/clanbenz/clanbenz.run.place.key
```

> Важно: для **этого ручного сертификата** автообновления не будет, пока не перейдете на certbot/ACME.

---

## Открыть порты

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

Сайт будет доступен по `http://clanbenz.run.place` (редирект на HTTPS) и `https://clanbenz.run.place`.
