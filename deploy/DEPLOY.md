# Deploying Forge AI on a server (Ubuntu)

This guide moves Forge off the owner's desktop and onto a machine he reaches with a
browser: the models run there, the projects and their versions are written there, the
rollbacks happen there, and the local machine is only a window onto it.

The one thing that must be right before anything else is **access**. The Core API was
built for one local operator and has no accounts. On a public address, `POST
/api/tasks/run` writes files and `POST /api/projects/{id}/run` starts processes, so an open
port is remote code execution on the owner's server. This build therefore makes the safe
case the only one that starts:

* a bind beyond loopback **without an access token refuses to start**, with the reason in
  the log (`app/api/access.py::refuse_if_exposed`), for the API **and** for the interface
  server;
* with a token, every `/api/*` request needs `Authorization: Bearer <token>` or
  `X-Forge-Token: <token>`, compared in constant time; `/health` and `/api/version` stay
  open;
* the token is never logged. The startup line states the bind address, whether auth is on
  and the token's **fingerprint** (the first six hex characters of its SHA-256);
* a body over 8 MiB is refused with 413, mutations are limited to 30 a minute per address
  and reads to 240, and the refusals say when to come back (`Retry-After`).

Three topologies are possible. **Choose one, and choose it consciously.**

| Topology | Public port | Token | When |
| --- | --- | --- | --- |
| **A. SSH tunnel** (`deploy/README-SSH-TUNNEL.md`) | none | optional | The safest, and enough for one person. Nothing is exposed; the ports are forwarded over SSH. |
| **B. nginx + TLS** (this guide) | 443 | **required** | A browser anywhere, a real hostname. The token is what protects the surface; TLS is what protects the token. |
| C. Direct bind `0.0.0.0:8011` | 8011 | required | Only inside a private network (a VPN or a firewall allow-list), never on the open internet. It serves plain HTTP, so the token crosses the wire in clear text. |

Everything below is topology **B**.

---

## 1. Prerequisites

* Ubuntu 22.04 or 24.04, a sudo user, and a hostname whose DNS record points at the server.
* The provider keys the work needs (`DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, ...). They
  live in `.env` on the server, exactly as on the desktop.
* The interface bundle. `ui/dist` is **not** in the repository, so it is built once and
  copied, or built on the server (step 3).

## 2. System account and directories

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin forge
sudo mkdir -p /opt/forge /srv/forge
sudo chown -R forge:forge /opt/forge /srv/forge
```

`/opt/forge` is the checkout. `/srv/forge` is where a new project's files appear (the
projects folder the owner picks in «Настройки»); the systemd unit allows writes there.

## 3. The code, Python and the interface bundle

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip unzip rsync nginx certbot python3-certbot-nginx

# The code: a release zip, or a clone.
sudo -u forge unzip /tmp/forge-ai.zip -d /opt/forge
# or: sudo -u forge git clone <repository> /opt/forge

cd /opt/forge
sudo -u forge python3 -m venv .venv
sudo -u forge .venv/bin/pip install --upgrade pip
sudo -u forge .venv/bin/pip install -r requirements.txt
```

The interface bundle, either way:

**Option 1 -- build on the workstation and copy (no Node on the server):**

```bash
# on the workstation, in the checkout
cd ui && pnpm install && pnpm build      # writes ui/dist
rsync -av ui/dist/ user@server:/opt/forge/ui/dist/
ssh user@server 'sudo chown -R forge:forge /opt/forge/ui'
```

**Option 2 -- build on the server:**

```bash
sudo apt install -y nodejs npm
sudo npm install -g pnpm
cd /opt/forge/ui && sudo -u forge pnpm install && sudo -u forge pnpm build
```

`tools/ui_preview_server.py` refuses to start when `ui/dist/index.html` is missing, so a
forgotten build is a refusal with a sentence, not a blank page.

## 4. `.env` -- the credentials, including the access token

```bash
cd /opt/forge
sudo -u forge cp .env.example .env
sudo -u forge python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

Copy the printed value into `.env` as `FORGE_ACCESS_TOKEN=` (and fill in the provider
keys). Then:

```bash
sudo chmod 600 /opt/forge/.env
sudo chown forge:forge /opt/forge/.env
```

`FORGE_ACCESS_TOKEN` is read through the existing secrets layer
(`app/config/secrets.py`): process environment, then `.env`, then the operating system's
credential vault. A small box may instead keep it in the application settings file as
`access_token` (`/opt/forge/.forge/settings.json`) -- but `.env` is the better place,
because that is where the other credentials already are and the vault is not available on
Linux (see §9).

**Rotating the token** is one edit and one restart:

```bash
sudo -u forge python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
sudo nano /opt/forge/.env          # replace FORGE_ACCESS_TOKEN
sudo systemctl restart forge forge-ui
sudo journalctl -u forge -n 5 --no-pager   # the new fingerprint is in the line
```

Every tab that held the old token now gets 401 and needs the new link. There is no other
revocation, because there are no accounts.

## 5. Where the servers listen (the application settings file)

The bind address and port are **deployment shape**, so they live in the owner's own
settings file, not in `.env`:

```bash
sudo -u forge mkdir -p /opt/forge/.forge
sudo -u forge tee /opt/forge/.forge/settings.json >/dev/null <<'JSON'
{
  "api_bind_host": "127.0.0.1",
  "api_port": 8011,
  "ui_bind_host": "127.0.0.1",
  "ui_port": 8020
}
JSON
```

Both servers stay on **loopback** in topology B: nginx is the only process that faces the
internet, and the token is still enforced, because nginx forwards the caller's headers
untouched. `FORGE_API_HOST` / `FORGE_API_PORT` are the fallback a launcher may pass, and an
explicitly set variable wins over this file.

If you set `api_bind_host` to `0.0.0.0` **without** a token, the server refuses to start
and prints exactly why:

```
Forge API: Forge API refuses non-loopback binding (0.0.0.0) because no access token is
configured: anyone who can reach the port could write files and start processes. Set
FORGE_ACCESS_TOKEN (or 'access_token' in the application settings file) and restart, ...
```

## 6. systemd

```bash
sudo cp /opt/forge/deploy/forge.service     /etc/systemd/system/forge.service
sudo cp /opt/forge/deploy/forge-ui.service  /etc/systemd/system/forge-ui.service
sudo systemctl daemon-reload
sudo systemctl enable --now forge forge-ui
sudo systemctl status forge --no-pager
sudo journalctl -u forge -n 20 --no-pager
```

The first lines of the journal are the ones to read:

```
Forge API listening on 127.0.0.1:8011 | auth: on (token) | token fingerprint: 599a7f | body limit: 8388608 bytes | rate limit: 30 mutations/60s and 240 reads/60s per address
Forge UI shell listening on 127.0.0.1:8020 | auth: on (token) | token fingerprint: 599a7f | ...
```

Both units are `ProtectSystem=strict`: the checkout is read-only to the service, and
`.forge` (plus `/srv/forge`) is the only writable path. If the projects folder is
somewhere else, add it to `ReadWritePaths=` in `forge.service` **and** create it owned by
`forge`.

## 7. nginx and TLS

```bash
sudo cp /opt/forge/deploy/nginx.conf.sample /etc/nginx/sites-available/forge
sudo nano /etc/nginx/sites-available/forge        # server_name, certificate paths
sudo ln -s /etc/nginx/sites-available/forge /etc/nginx/sites-enabled/forge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

Get the certificate first (the sample's TLS block needs files that do not exist yet), so
start from a minimal HTTP server, or use certbot's nginx plugin, which edits the file:

```bash
sudo certbot --nginx -d forge.example.com
sudo nginx -t && sudo systemctl reload nginx
```

The sample sets `client_max_body_size 8m`, passes `Authorization` and `X-Forge-Token`
through, appends the real client to `X-Forwarded-For` (the Core's rate limit reads the last
entry, so a client cannot choose its own bucket), and uses 600 s read/send timeouts with
buffering off, because a task can take minutes and the interface polls while it does.

**Firewall.** Only 22, 80 and 443 are needed:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

8011 and 8020 must stay closed: they are loopback-only and the firewall is the second
proof of that.

## 8. Opening the interface

Build the one-time link with the token in it:

```
https://forge.example.com/?token=<the value of FORGE_ACCESS_TOKEN>
```

The page stores the token in `sessionStorage` (this tab only, gone when the tab closes) and
removes it from the address bar before the interface renders. Every later request carries
`X-Forge-Token`. Opening `https://forge.example.com/` without a token loads the page and
every call answers 401, with the message saying what is missing -- paste the link again. A
new tab always needs the link again: there is no cookie and no server-side session.

Sanity checks from the server itself:

```bash
# The refusal without a token (401) and the answer with it (200):
curl -s -o /dev/null -w '%{http_code}\n' https://forge.example.com/api/status
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Forge-Token: $FORGE_ACCESS_TOKEN" https://forge.example.com/api/status
# Open, no token needed:
curl -s -o /dev/null -w '%{http_code}\n' https://forge.example.com/api/version
```

Expected: `401`, `200`, `200`.

## 9. What works on Linux, and what does not

Measured by reading this checkout, not assumed:

**Works.** Discovery and the clarification loop, the model catalogue and routing, the agent
harness, the file writer (a task really writes its files into the project workspace),
project versions, rollback and change verification, the evidence block, the provider layer
(keys from `.env`), the run store, project lifecycle and launch/run of a project's own
entry point, the interface.

**Does not work, or works differently.**

* **Opening a project window (`--app`).** `POST /api/projects/{id}/launch` opens a browser
  window *on the machine running the server* (`app/projects/app_window.py`: `chrome.exe` /
  `msedge.exe` install paths, the Windows `Chrome_WidgetWin` window class, `os.startfile`).
  A headless server has no display, so that route cannot show anything. **Workaround:** use
  `POST /api/projects/{id}/run`, which reports the URL and the observed port, and open that
  URL in your own browser (through the tunnel or the domain). The project's preview server
  binds its own port on the server; nothing about it needs a desktop.
* **The Job Object sandbox.** `create_process_tree_job()` returns `None` on POSIX
  (`app/execution/adapter.py`), so a project's child process gets no job object: no
  `KILL_ON_JOB_CLOSE`, no per-process memory ceiling, no active-process ceiling. The
  sandbox report says so instead of pretending (`sandbox.job_object = false`).
* **The low-integrity token.** `lower_process_integrity()` and `process_integrity_level()`
  return `"unknown"` off Windows, so Forge cannot run a child below the owner's level, and
  `probe_write_boundary()` answers `unsupported_platform`. The write-boundary probe is a
  Windows measurement, not a general one.
* **`taskkill /T` tree kill.** Not used off Windows; there is no `taskkill` on Linux.
  **But the POSIX branch already exists**: `terminate_process_tree()`
  (`app/execution/adapter.py`, the `else:` branch) sends `SIGTERM` to the child's process
  group, waits 0.2 s, then `SIGKILL`, and the runner spawns with
  `start_new_session=True` on POSIX (`app/projects/runner.py`), so the group really is the
  child's own tree. A stop therefore terminates the project's processes and their children.
* **Windows Credential Manager.** `default_credential_backend()` returns
  `UnavailableCredentialBackend()` off Windows, so a provider key typed into «Провайдеры и
  ключи» is not stored on the server. **Workaround:** put the keys in `.env` (step 4); the
  provider layer reads them from there first anyway.
* **`tools/forge_launch.py`** (the desktop launcher: `pythonw`, Chrome paths,
  `Get-CimInstance`, `OpenProcess`) is Windows-only and is not used here; the systemd units
  replace it. Nothing else in the product depends on it.

## 10. Backups

Everything durable is under `.forge/` (`runs`, `projects`, `settings.json`, the idempotency
ledger) plus the projects folder:

```bash
sudo systemctl stop forge forge-ui
sudo tar --zstd -cpf /var/backups/forge-$(date +%F).tar.zst -C /opt/forge .forge
sudo tar --zstd -cpf /var/backups/forge-projects-$(date +%F).tar.zst -C /srv forge
sudo systemctl start forge forge-ui
```

Stop the services first: a copied run store that a run is still writing is a copy of half a
version. `.env` is not in these archives on purpose (it is the credential file); keep it in
whatever you use for secrets, or back it up separately with `chmod 600` preserved.

## 11. Known limits of this access model, stated

* **One token is the whole surface.** Anyone who has it can run code on the server. It is a
  single-operator product, and the token is the honest shape of that: rotating it is the
  only revocation.
* **The body ceiling reads `Content-Length`.** A chunked request without that header is not
  measured by the Core; nginx's `client_max_body_size` is the outer bound, which is why the
  sample sets it and why topology C is marked "private network only".
* **The rate limit is a sanity limit, not a quota.** It is a token bucket, in memory,
  per address: a restart forgets it.
* **No CSRF token.** The credential is a header, not a cookie, so a cross-site page cannot
  make the browser attach it; `sessionStorage` is per origin. CORS stays on an explicit
  allow-list with credentialed CORS off.

---

# Развёртывание Forge AI на сервере (Ubuntu) — русская версия

Этот гид переносит Forge с рабочего стола владельца на машину, к которой он обращается
браузером: модели работают там, проекты и их версии пишутся там, откаты происходят там, а
локальная машина становится лишь окном в это.

Прежде всего должен быть решён **доступ**. Core API создавался для одного локального
оператора и не имеет учётных записей. На публичном адресе `POST /api/tasks/run` пишет
файлы, а `POST /api/projects/{id}/run` запускает процессы, поэтому открытый порт — это
удалённое выполнение кода на сервере владельца. Поэтому безопасный случай здесь —
единственный, который запускается:

* привязка не к loopback **без токена доступа отказывается запускаться**, с причиной в
  логе (`app/api/access.py::refuse_if_exposed`), и для API, **и** для сервера интерфейса;
* при заданном токене каждый запрос к `/api/*` требует `Authorization: Bearer <token>`
  либо `X-Forge-Token: <token>`, сравнение выполняется за постоянное время; `/health` и
  `/api/version` остаются открытыми;
* токен никогда не попадает в лог. Стартовая строка сообщает адрес привязки, включена ли
  аутентификация и **отпечаток** токена (первые шесть hex-символов его SHA-256);
* тело больше 8 МиБ отклоняется с 413, мутации ограничены 30 в минуту на адрес, чтения —
  240, и отказы сообщают, когда приходить снова (`Retry-After`).

Возможны три топологии. **Выберите одну, и осознанно.**

| Топология | Публичный порт | Токен | Когда |
| --- | --- | --- | --- |
| **A. SSH-тоннель** (`deploy/README-SSH-TUNNEL.md`) | нет | необязателен | Самый безопасный, и для одного человека достаточный. Ничего не публикуется, порты пробрасываются по SSH. |
| **B. nginx + TLS** (этот гид) | 443 | **обязателен** | Браузер из любого места, настоящее имя хоста. Токен защищает поверхность, TLS защищает токен. |
| C. Прямая привязка `0.0.0.0:8011` | 8011 | обязателен | Только внутри частной сети (VPN или allow-list файрвола), никогда в открытом интернете. Это обычный HTTP, поэтому токен идёт по проводу открытым текстом. |

Всё ниже — топология **B**.

## 1. Что нужно заранее

* Ubuntu 22.04 или 24.04, пользователь с sudo, имя хоста, DNS-запись которого указывает на
  сервер.
* Ключи провайдеров, нужные работе (`DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, ...). Они
  живут в `.env` на сервере, ровно как на рабочем столе.
* Бандл интерфейса. `ui/dist` **не** хранится в репозитории, поэтому он собирается один раз
  и копируется либо собирается на сервере (шаг 3).

## 2. Системный аккаунт и каталоги

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin forge
sudo mkdir -p /opt/forge /srv/forge
sudo chown -R forge:forge /opt/forge /srv/forge
```

`/opt/forge` — это копия репозитория. `/srv/forge` — место, где появляются файлы нового
проекта (папка проектов, которую владелец выбирает в «Настройках»); systemd-юнит разрешает
запись туда.

## 3. Код, Python и бандл интерфейса

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip unzip rsync nginx certbot python3-certbot-nginx

# Код: zip-релиз или клон.
sudo -u forge unzip /tmp/forge-ai.zip -d /opt/forge
# или: sudo -u forge git clone <repository> /opt/forge

cd /opt/forge
sudo -u forge python3 -m venv .venv
sudo -u forge .venv/bin/pip install --upgrade pip
sudo -u forge .venv/bin/pip install -r requirements.txt
```

Бандл интерфейса, любым способом:

**Вариант 1 — собрать на рабочей машине и скопировать (без Node на сервере):**

```bash
# на рабочей машине, в копии репозитория
cd ui && pnpm install && pnpm build      # создаёт ui/dist
rsync -av ui/dist/ user@server:/opt/forge/ui/dist/
ssh user@server 'sudo chown -R forge:forge /opt/forge/ui'
```

**Вариант 2 — собрать на сервере:**

```bash
sudo apt install -y nodejs npm
sudo npm install -g pnpm
cd /opt/forge/ui && sudo -u forge pnpm install && sudo -u forge pnpm build
```

`tools/ui_preview_server.py` отказывается запускаться, если `ui/dist/index.html`
отсутствует, поэтому забытая сборка — это отказ с предложением, а не пустая страница.

## 4. `.env` — учётные данные, включая токен доступа

```bash
cd /opt/forge
sudo -u forge cp .env.example .env
sudo -u forge python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

Впишите напечатанное значение в `.env` как `FORGE_ACCESS_TOKEN=` (и заполните ключи
провайдеров). Затем:

```bash
sudo chmod 600 /opt/forge/.env
sudo chown forge:forge /opt/forge/.env
```

`FORGE_ACCESS_TOKEN` читается через существующий слой секретов
(`app/config/secrets.py`): окружение процесса, затем `.env`, затем хранилище учётных данных
операционной системы. Небольшая машина может вместо этого держать его в файле настроек
приложения как `access_token` (`/opt/forge/.forge/settings.json`), но `.env` — лучшее
место, потому что там уже лежат остальные учётные данные, а на Linux хранилища нет (§9).

**Ротация токена** — одна правка и один перезапуск:

```bash
sudo -u forge python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
sudo nano /opt/forge/.env          # заменить FORGE_ACCESS_TOKEN
sudo systemctl restart forge forge-ui
sudo journalctl -u forge -n 5 --no-pager   # новый отпечаток в строке
```

Каждая вкладка, державшая старый токен, теперь получает 401 и требует новую ссылку. Другого
отзыва нет, потому что нет учётных записей.

## 5. Где слушают серверы (файл настроек приложения)

Адрес и порт привязки — это **форма развёртывания**, поэтому они живут в собственном файле
настроек владельца, а не в `.env`:

```bash
sudo -u forge mkdir -p /opt/forge/.forge
sudo -u forge tee /opt/forge/.forge/settings.json >/dev/null <<'JSON'
{
  "api_bind_host": "127.0.0.1",
  "api_port": 8011,
  "ui_bind_host": "127.0.0.1",
  "ui_port": 8020
}
JSON
```

В топологии B оба сервера остаются на **loopback**: единственный процесс, смотрящий в
интернет, — это nginx, и токен всё равно проверяется, потому что nginx передаёт заголовки
вызывающего без изменений. `FORGE_API_HOST` / `FORGE_API_PORT` — запасной вариант, который
может передать launcher, и явно заданная переменная важнее этого файла.

Если задать `api_bind_host` = `0.0.0.0` **без** токена, сервер откажется запускаться и
напечатает точную причину:

```
Forge API: Forge API refuses non-loopback binding (0.0.0.0) because no access token is
configured: anyone who can reach the port could write files and start processes. Set
FORGE_ACCESS_TOKEN (or 'access_token' in the application settings file) and restart, ...
```

## 6. systemd

```bash
sudo cp /opt/forge/deploy/forge.service     /etc/systemd/system/forge.service
sudo cp /opt/forge/deploy/forge-ui.service  /etc/systemd/system/forge-ui.service
sudo systemctl daemon-reload
sudo systemctl enable --now forge forge-ui
sudo systemctl status forge --no-pager
sudo journalctl -u forge -n 20 --no-pager
```

Первые строки журнала — те, что нужно прочитать:

```
Forge API listening on 127.0.0.1:8011 | auth: on (token) | token fingerprint: 599a7f | body limit: 8388608 bytes | rate limit: 30 mutations/60s and 240 reads/60s per address
Forge UI shell listening on 127.0.0.1:8020 | auth: on (token) | token fingerprint: 599a7f | ...
```

Оба юнита используют `ProtectSystem=strict`: копия репозитория для сервиса только для
чтения, а `.forge` (плюс `/srv/forge`) — единственный путь, доступный для записи. Если папка
проектов находится в другом месте, добавьте её в `ReadWritePaths=` в `forge.service` **и**
создайте её с владельцем `forge`.

## 7. nginx и TLS

```bash
sudo cp /opt/forge/deploy/nginx.conf.sample /etc/nginx/sites-available/forge
sudo nano /etc/nginx/sites-available/forge        # server_name, пути сертификатов
sudo ln -s /etc/nginx/sites-available/forge /etc/nginx/sites-enabled/forge
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
```

Сертификат нужно получить до этого (TLS-блоку в образце нужны файлы, которых ещё нет),
поэтому начните с минимального HTTP-сервера либо воспользуйтесь плагином certbot для
nginx — он сам правит файл:

```bash
sudo certbot --nginx -d forge.example.com
sudo nginx -t && sudo systemctl reload nginx
```

Образец задаёт `client_max_body_size 8m`, передаёт `Authorization` и `X-Forge-Token`,
дописывает реального клиента в `X-Forwarded-For` (ограничитель скорости Core читает
последнюю запись, поэтому клиент не может выбрать себе корзину) и использует таймауты
чтения/записи 600 с с выключенной буферизацией, потому что задача может идти минутами, а
интерфейс в это время опрашивает сервер.

**Файрвол.** Нужны только 22, 80 и 443:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

8011 и 8020 должны оставаться закрытыми: они только для loopback, и файрвол — второе
доказательство этого.

## 8. Открыть интерфейс

Соберите одноразовую ссылку с токеном:

```
https://forge.example.com/?token=<значение FORGE_ACCESS_TOKEN>
```

Страница сохраняет токен в `sessionStorage` (только эта вкладка, исчезает с её закрытием) и
удаляет его из адресной строки до отрисовки интерфейса. Каждый следующий запрос несёт
`X-Forge-Token`. Если открыть `https://forge.example.com/` без токена, страница загрузится,
а каждый вызов ответит 401 с сообщением о том, чего не хватает, — вставьте ссылку снова.
Новая вкладка всегда требует ссылку заново: ни cookie, ни серверной сессии нет.

Проверки с самого сервера:

```bash
# Отказ без токена (401) и ответ с ним (200):
curl -s -o /dev/null -w '%{http_code}\n' https://forge.example.com/api/status
curl -s -o /dev/null -w '%{http_code}\n' -H "X-Forge-Token: $FORGE_ACCESS_TOKEN" https://forge.example.com/api/status
# Открыто, токен не нужен:
curl -s -o /dev/null -w '%{http_code}\n' https://forge.example.com/api/version
```

Ожидаемо: `401`, `200`, `200`.

## 9. Что работает на Linux, а что нет

Проверено чтением этой копии репозитория, а не предположением:

**Работает.** Discovery и цикл уточнений, каталог моделей и маршрутизация, agent harness,
запись файлов (задача действительно пишет свои файлы в рабочее пространство проекта),
версии проектов, откат и проверка изменений, блок доказательств, слой провайдеров (ключи из
`.env`), хранилище прогонов, жизненный цикл проекта и запуск собственной точки входа
проекта, интерфейс.

**Не работает или работает иначе.**

* **Открытие окна проекта (`--app`).** `POST /api/projects/{id}/launch` открывает окно
  браузера *на машине, где запущен сервер* (`app/projects/app_window.py`: пути установки
  `chrome.exe` / `msedge.exe`, класс окна Windows `Chrome_WidgetWin`, `os.startfile`). У
  headless-сервера нет дисплея, поэтому этот маршрут ничего показать не может.
  **Обходной путь:** используйте `POST /api/projects/{id}/run` — он сообщает URL и
  наблюдаемый порт, а URL откройте в своём браузере (через тоннель или домен). Сервер
  предпросмотра проекта слушает свой порт на сервере; рабочего стола ему не нужно.
* **Песочница Job Object.** `create_process_tree_job()` возвращает `None` на POSIX
  (`app/execution/adapter.py`), поэтому дочерний процесс проекта не получает job object:
  нет `KILL_ON_JOB_CLOSE`, нет потолка памяти на процесс, нет потолка числа процессов.
  Отчёт песочницы говорит об этом прямо (`sandbox.job_object = false`), а не делает вид.
* **Маркер низкой целостности.** `lower_process_integrity()` и `process_integrity_level()`
  вне Windows возвращают `"unknown"`, поэтому Forge не может запустить дочерний процесс
  ниже уровня владельца, а `probe_write_boundary()` отвечает `unsupported_platform`. Проба
  границы записи — это измерение Windows, а не общее.
* **`taskkill /T` для дерева процессов.** Вне Windows не используется; на Linux
  `taskkill` нет. **Но POSIX-ветка уже существует**: `terminate_process_tree()`
  (`app/execution/adapter.py`, ветка `else:`) посылает `SIGTERM` группе процессов ребёнка,
  ждёт 0.2 с, затем `SIGKILL`, а runner на POSIX запускает процесс с
  `start_new_session=True` (`app/projects/runner.py`), поэтому группа — это действительно
  собственное дерево ребёнка. Остановка завершает процессы проекта и их потомков.
* **Windows Credential Manager.** `default_credential_backend()` вне Windows возвращает
  `UnavailableCredentialBackend()`, поэтому ключ провайдера, введённый в «Провайдеры и
  ключи», на сервере не сохраняется. **Обходной путь:** положите ключи в `.env` (шаг 4);
  слой провайдеров и так читает их оттуда первыми.
* **`tools/forge_launch.py`** (настольный launcher: `pythonw`, пути Chrome,
  `Get-CimInstance`, `OpenProcess`) существует только для Windows и здесь не используется;
  его заменяют systemd-юниты. Больше ничто в продукте от него не зависит.

## 10. Резервные копии

Всё долговечное лежит в `.forge/` (`runs`, `projects`, `settings.json`, журнал
идемпотентности) плюс папка проектов:

```bash
sudo systemctl stop forge forge-ui
sudo tar --zstd -cpf /var/backups/forge-$(date +%F).tar.zst -C /opt/forge .forge
sudo tar --zstd -cpf /var/backups/forge-projects-$(date +%F).tar.zst -C /srv forge
sudo systemctl start forge forge-ui
```

Сначала остановите сервисы: скопированное хранилище прогонов, в которое всё ещё идёт запись,
— это копия половины версии. `.env` намеренно не входит в эти архивы (это файл учётных
данных); держите его там, где храните секреты, либо копируйте отдельно, сохраняя
`chmod 600`.

## 11. Известные ограничения этой модели доступа, названные прямо

* **Один токен — это вся поверхность.** У кого он есть, тот может выполнять код на
  сервере. Это продукт для одного оператора, и токен — честная форма этого: ротация —
  единственный отзыв.
* **Потолок тела читает `Content-Length`.** Chunked-запрос без этого заголовка Core не
  измеряет; внешняя граница — `client_max_body_size` в nginx, поэтому образец его задаёт, а
  топология C помечена как «только частная сеть».
* **Ограничитель скорости — это разумный предел, а не квота.** Это корзина токенов, в
  памяти, на адрес: перезапуск её забывает.
* **CSRF-токена нет.** Учётные данные — это заголовок, а не cookie, поэтому чужая страница
  не может заставить браузер их приложить; `sessionStorage` привязан к origin. CORS
  остаётся на явном allow-list с выключенным credentialed CORS.
