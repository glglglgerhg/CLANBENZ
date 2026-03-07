# CLANBENZ

## Быстрый запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 SITEBENZ.py
```

Сервис по умолчанию слушает `http://127.0.0.1:8080`.

## Частая ошибка на Windows

Если при запуске появляется ошибка вида:

```text
SyntaxError: expected 'except' or 'finally' block
... line ...
@@ -136,52 +135,87 @@ def init_databases():
```

значит в `SITEBENZ.py` попали **текстовые маркеры diff/patch** (`@@ ... @@`, `<<<<<<<`, `=======`, `>>>>>>>`).

### Как исправить

1. Перекачайте проект именно через git:
   ```bash
   git clone <repo_url>
   ```
2. Убедитесь, что в файле нет patch-маркеров:
   ```bash
   rg -n "^@@|^<<<<<<<|^=======|^>>>>>>>" SITEBENZ.py
   ```
   Команда не должна выводить строк.
3. Повторите запуск:
   ```bash
   python SITEBENZ.py
   ```


## Деплой на Ubuntu 22

Подробная пошаговая инструкция находится в `DEPLOY.md`.

SSL/домен и автообновление сертификатов: `DEPLOY_SSL.md`.
