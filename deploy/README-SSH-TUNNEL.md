# The safest deployment: an SSH tunnel, no public exposure

This is the recommended way to run Forge on a server and use it from your own machine. It
needs **no open port, no TLS certificate, no nginx and no access token**: the server's
servers stay bound to `127.0.0.1`, exactly as they are on the desktop, and SSH carries the
two ports to your browser.

Because nothing is reachable from the network, "no token" is a valid configuration here --
and that is the only place where it is. The moment you give the machine a public address
or bind a server beyond loopback, the token becomes mandatory: with a non-loopback bind and
no `FORGE_ACCESS_TOKEN`, the API and the interface server both **refuse to start**.

## 1. On the server: start Forge, bound to loopback

Install the code exactly as in steps 2-6 of `deploy/DEPLOY.md`, with one difference: leave
`FORGE_ACCESS_TOKEN` **empty** in `.env`, and leave the bind settings at their defaults.

```bash
sudo systemctl start forge forge-ui
sudo journalctl -u forge -n 3 --no-pager
# Forge API listening on 127.0.0.1:8011 | auth: OFF (no token; loopback only) | ...
```

`auth: OFF` here means "loopback only", and the launcher enforces that pairing: the same
process refuses `api_bind_host: "0.0.0.0"` without a token before it builds anything.

Ports 8011 and 8020 must **not** be opened in the firewall.

## 2. On your machine: forward the two ports

```bash
ssh -N -L 8020:127.0.0.1:8020 -L 8011:127.0.0.1:8011 user@server
```

* `-L 8020:…` makes `http://127.0.0.1:8020` on your machine reach the interface on the
  server (which serves the bundle and proxies `/api` to 8011);
* `-L 8011:…` makes `http://127.0.0.1:8011` reach the API directly, which is handy for
  `curl` and for the desktop client;
* `-N` runs no remote command: the tunnel is the whole session. Drop it if you also want a
  shell.

Optionally keep it in `~/.ssh/config` so it is one word:

```
Host forge
    HostName server.example.com
    User user
    LocalForward 8020 127.0.0.1:8020
    LocalForward 8011 127.0.0.1:8011
    ServerAliveInterval 30
```

then `ssh -N forge`.

## 3. Open the interface

```
http://127.0.0.1:8020/
```

That is the real interface on the real server: the models, the projects, the versions and
the rollbacks all live there. Your machine is only a browser, and the connection is
encrypted by SSH, which is why the token is optional in this topology.

Check the API through the same tunnel:

```bash
curl -s http://127.0.0.1:8011/api/status | head -c 200; echo
curl -s http://127.0.0.1:8011/api/version
```

## 4. When to stop using the tunnel

Use the tunnel while one person uses one machine. Move to `deploy/DEPLOY.md` (nginx + TLS,
with `FORGE_ACCESS_TOKEN` set) when you need:

* a normal `https://` address that works from a phone or another computer;
* more than one browser at a time without `-L` flags;
* the interface reachable by somebody who does not have an SSH account.

Both topologies run the same processes with the same data; only the front door changes, and
a token set in `.env` is honoured in either one.

---

# Самый безопасный вариант: SSH-тоннель, без публичного доступа

Это рекомендуемый способ запустить Forge на сервере и пользоваться им со своей машины.
Он не требует **ни открытого порта, ни TLS-сертификата, ни nginx, ни токена доступа**:
серверные процессы остаются привязанными к `127.0.0.1`, ровно как на рабочем столе, а SSH
переносит два порта в ваш браузер.

Поскольку из сети ничего не доступно, «без токена» — допустимая конфигурация здесь, и
только здесь. Как только у машины появляется публичный адрес или сервер привязывается не к
loopback, токен становится обязательным: при не-loopback привязке без `FORGE_ACCESS_TOKEN`
и API, и сервер интерфейса **отказываются запускаться**.

## 1. На сервере: запустить Forge, привязанным к loopback

Установите код по шагам 2-6 из `deploy/DEPLOY.md`, с одним отличием: оставьте
`FORGE_ACCESS_TOKEN` **пустым** в `.env`, а настройки привязки — по умолчанию.

```bash
sudo systemctl start forge forge-ui
sudo journalctl -u forge -n 3 --no-pager
# Forge API listening on 127.0.0.1:8011 | auth: OFF (no token; loopback only) | ...
```

`auth: OFF` здесь означает «только loopback», и launcher обеспечивает именно эту пару:
тот же процесс отказывает `api_bind_host: "0.0.0.0"` без токена до того, как что-либо
соберёт.

Порты 8011 и 8020 **не** должны быть открыты в файрволе.

## 2. На своей машине: пробросить два порта

```bash
ssh -N -L 8020:127.0.0.1:8020 -L 8011:127.0.0.1:8011 user@server
```

* `-L 8020:…` делает `http://127.0.0.1:8020` на вашей машине доступом к интерфейсу на
  сервере (он отдаёт бандл и проксирует `/api` на 8011);
* `-L 8011:…` делает `http://127.0.0.1:8011` доступом к API напрямую — удобно для `curl`
  и для настольного клиента;
* `-N` не выполняет удалённую команду: весь сеанс — это тоннель. Уберите `-N`, если нужен
  ещё и shell.

При желании закрепите это в `~/.ssh/config`, чтобы команда стала одним словом:

```
Host forge
    HostName server.example.com
    User user
    LocalForward 8020 127.0.0.1:8020
    LocalForward 8011 127.0.0.1:8011
    ServerAliveInterval 30
```

тогда достаточно `ssh -N forge`.

## 3. Открыть интерфейс

```
http://127.0.0.1:8020/
```

Это настоящий интерфейс на настоящем сервере: модели, проекты, версии и откаты живут там.
Ваша машина — только браузер, а соединение шифруется SSH, поэтому в этой топологии токен
необязателен.

Проверить API через тот же тоннель:

```bash
curl -s http://127.0.0.1:8011/api/status | head -c 200; echo
curl -s http://127.0.0.1:8011/api/version
```

## 4. Когда пора уходить от тоннеля

Пользуйтесь тоннелем, пока один человек работает с одной машины. Переходите к
`deploy/DEPLOY.md` (nginx + TLS, с заданным `FORGE_ACCESS_TOKEN`), если нужно:

* обычный адрес `https://`, работающий с телефона или другого компьютера;
* несколько браузеров одновременно без флагов `-L`;
* интерфейс, доступный тому, у кого нет SSH-аккаунта.

Обе топологии запускают одни и те же процессы с одними и теми же данными; меняется только
входная дверь, а токен, заданный в `.env`, действует в любой из них.
