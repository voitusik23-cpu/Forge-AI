# Provider balance transparency — sources, semantics and honest absences

Status: implemented for the two providers that publish a balance, and explicitly **not**
implemented as invented values for the five that do not.

## 1. What was measured first

The existing layer (`app/dashboard/provider_health.py`) already performed exactly two official,
free balance requests — DeepSeek `GET /user/balance` and OpenRouter `GET /api/v1/auth/key` — and
returned **hardcoded historical verdicts** for the other five providers: OpenAI and xAI as
`$0.00`, Groq and Anthropic as `network blocked`, Gemini as a free tier. Those came from a past
observation and were stamped with a fresh `last_checked`, i.e. they read as current. No socket was
opened for them.

Two things were wrong from the owner's point of view:

* a fabricated `$0.00` and a five-week-old verdict presented as a live figure;
* DeepSeek's answer collapsed `granted_balance`, `topped_up_balance` and `total_balance` into one
  number, which is precisely the distinction the owner asked about.

`tests/test_dashboard_offline_health.py` pins the exact key set of `ProviderAccountStatus.to_dict()`
and those historical verdicts as deliberate contracts, so the existing record is left untouched and
the honest layer is additive: `app/dashboard/provider_balance.py`.

## 2. Where every displayed value comes from

| Displayed field | Source | Meaning | Notes |
|---|---|---|---|
| `available_minor` / `available_display` | DeepSeek `total_balance`; OpenRouter `limit_remaining` (or `limit − usage`) | spendable amount right now | integer minor units + currency |
| `granted_minor` / `granted_display` | DeepSeek `granted_balance` | **promotional credit the provider granted** | OpenRouter publishes none → `null`, never the limit |
| `topped_up_minor` / `topped_up_display` | DeepSeek `topped_up_balance` | **money the owner paid in** | the two are never merged or swapped |
| `usage_minor` / `usage_display` | OpenRouter `usage` | confirmed spend against the key | DeepSeek's balance endpoint does not report usage → `null` |
| `currency` | the provider's own field | `USD` / `CNY` … | a figure is never shown without it |
| `minor_unit_exponent` | this layer | `2` (cents) | states the unit the integers are in |
| `billing_period` | not published by either endpoint | — | stays empty; nothing is invented |
| `last_updated` | this layer, at the moment of a successful call | when the figure was obtained | empty when nothing was obtained |
| `stale`, `age_seconds` | this layer | the value is the last confirmed one, and how old | only `true` after a failed refresh |
| `connection_status` | this layer | `OBSERVED`, `NOT_CHECKED`, `UNSUPPORTED_BY_PROVIDER`, `UNAUTHORIZED`, `UNREACHABLE`, `ERROR` | never a readiness claim about an account that was not observed |
| `source` | this layer | the exact endpoint the figure came from | shown in the interface next to the value |
| `history[]` | this process | this session's attempts, with status, source and figures | labelled session-only, **not** a billing archive |

### DeepSeek: what `$1.29` and `$18.00` actually are

Measured against the owner's own account on 2026-10-15T13:15:33Z, through the documented endpoint:

```json
{"is_available": true,
 "balance_infos": [{"currency": "USD",
                    "total_balance": "19.29",
                    "granted_balance": "18.00",
                    "topped_up_balance": "1.29"}]}
```

So the mapping is the **opposite** of the first guess: the **granted** (promotional) credit is
**$18.00**, and the **topped-up** (paid-in) credit is **$1.29**; available total **$19.29 USD**.
This is exactly why neither number may be treated as a universal "balance type": they are three
distinct entities, and Forge shows them as three labelled values. DeepSeek does not report token
usage or spend through this endpoint, so `usage` is `null` — not zero.

## 3. Providers where a balance cannot be obtained

| Provider | Why not | What Forge shows |
|---|---|---|
| OpenAI | no balance endpoint; the figure exists only in the provider's billing console | `Недоступно у провайдера` + the reason, no money fields |
| Anthropic | no balance endpoint; credit only in the console | same |
| Google Gemini | quota-based, not balance-based; paid usage is billed through Google Cloud | same |
| xAI | no balance endpoint; prepaid credit only in the console | same |
| Groq | no balance endpoint; usage only in the console | same |

Reading those consoles would require the owner's session or cookies, which is explicitly out of
bounds; Forge therefore reports the absence instead of approximating it.

## 4. Behaviour when the API is unavailable

| Situation | Result |
|---|---|
| no key configured | `UNAUTHORIZED`, reason "no API key is configured", **no request is made** |
| HTTP 401/403 | `UNAUTHORIZED` with the status code — the response body and the key are never included |
| network failure | `UNREACHABLE`; the last confirmed value is returned with `stale=true` and `age_seconds` |
| provider reports the account unavailable | the figures it did return are kept, with the reason in `error` |
| TEST composition (`FORGE_ENV=test`) | `NOT_CHECKED`, and **zero outbound attempts** (same suppression signal as the health monitor) |
| provider has no endpoint | `UNSUPPORTED_BY_PROVIDER` with the reason, and every money field `null` |

Refresh happens on request: `GET /api/providers/balance` (all), `?provider=deepseek` (one),
`?refresh=1` (force). There is no background polling, so no value is ever refreshed without a
visible cause, and no paid completion is ever used to obtain a balance — both endpoints are free.

## 5. Security

* The key is read through the project's own `SecretStore` (process environment, then the repo-root
  `.env`) and is sent only in the `Authorization` header.
* A report can never contain the key: a test serializes a report produced with a known fake key and
  asserts the key does not appear, while the same test asserts the header did carry it.
* No session, cookie or private window is read; no console is scraped.
* No secret is written to a log, a document or Git.

## 6. Verification

| Claim | Evidence |
|---|---|
| DeepSeek's source is the documented endpoint | real call, `source` recorded as `GET https://api.deepseek.com/user/balance` |
| Granted, topped-up and total are distinct | real values 1800 / 129 / 1929 minor units, asserted unequal in `test_01` |
| Money is not a float | `test_02` asserts `int` and not `float` |
| An absent figure is never zero | `test_03`, `test_04`, `test_07`, `test_08` |
| OpenRouter's limit is not relabelled as granted credit | `test_05` |
| A report never carries the key, but the request did | `test_10` |
| A failed refresh keeps the value, marked stale with its age | `test_11` |
| 401 is reported without the body | `test_12` |
| Session history records each attempt | `test_13` |
| A TEST composition makes zero outbound attempts | `test_14` (socket spy) |
| The payload key set is exactly the declared one | `test_09` |

Sixteen tests, all offline. The single real network call in this work was the free balance query
above; no completion was requested and no paid call was made.

---

# Прозрачность балансов провайдеров — кратко по-русски

* Баланс берётся **только** из официальных бесплатных endpoint'ов: DeepSeek
  `GET /user/balance`, OpenRouter `GET /api/v1/auth/key`. Платные вызовы моделей для обновления
  баланса не выполняются.
* Для аккаунта владельца измерено: **granted (подаренные) = $18.00**, **topped‑up (пополнено
  владельцем) = $1.29**, **доступно всего = $19.29 USD**. Это три разные сущности, и в интерфейсе
  они показываются раздельно с валютой, источником и временем получения.
* Где официального endpoint'а нет (OpenAI, Anthropic, Gemini, xAI, Groq), Forge показывает
  «Недоступно у провайдера» и причину. Ноль, прочерк вместо суммы и любые оценки не выводятся.
* При сбое обновления сохраняется **последнее подтверждённое** значение с пометкой «устарело» и
  возрастом; устаревшие данные не выдаются за актуальные.
* Ключ читается через `SecretStore`, передаётся только в заголовке `Authorization` и никогда не
  попадает в отчёт, журнал, документ или Git. Сессии и cookies провайдеров не читаются.
* История обновлений — в пределах текущего процесса и помечена как таковая, а не как архив биллинга.
