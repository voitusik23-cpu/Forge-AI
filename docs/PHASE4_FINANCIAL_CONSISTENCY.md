# Phase 4 — Financial data consistency and acceptance

Scope: make every financial figure in Forge AI traceable to one source with one meaning, give the
missing **Costs & usage** section a real screen, and verify the whole thing locally and in CI.

## 1. What was found

1. **The navigation had two dead entries.** `NavRail` lists nine sections; `App.tsx` rendered only
   seven. Clicking **Расходы** (`nav=usage`) or **Инструменты** (`nav=tools`) produced an empty
   content area. The costs section therefore did not exist as a screen at all. `usage` is fixed
   here; `tools` is reported, not silently taken on.
2. **The Models KPI claimed something false.** It showed "0 accounts / no key configured" while two
   providers were returning real balances from official endpoints. The number counts accounts in
   the **model registry**, so the label now says exactly that, in both languages, with a tooltip
   naming the source.
3. **The legacy money text on Overview is gone.** `account_status.balance_display` came from the
   legacy record, which for OpenAI and xAI is a hardcoded `$0.00` and for Groq/Anthropic is
   `BLOCKED` / `VIA GATEWAY`. Overview now shows the status labelled as a *historical verdict* and
   links to the sourced balances instead of printing a balance.
4. **Failure states were conflated.** A 200 whose body was not JSON was reported as `UNREACHABLE`
   (a network claim about a working network), and a well-formed answer with no amount produced a
   record with every field empty and no reason. Both are now `MALFORMED`, and the response body is
   never quoted or logged.

## 2. Source map — screen → field → source → time → semantics

| Screen | Field | Source | Time | Semantics |
|---|---|---|---|---|
| Overview | providers ready `3/7` | `/api/status` (`providers_ready`/`providers_total`) | each poll | readiness of configured accounts, **not** a balance |
| Overview | provider status chip | `/api/dashboard` `providers[].account_status.status` | legacy record | **historical verdict**, labelled as such in the UI |
| Overview | provider balance | — | — | **removed**; replaced by a link to the sourced balances |
| Models & Providers | available / granted / topped-up / usage | `/api/providers/balance` → DeepSeek `GET /user/balance`, OpenRouter `GET /api/v1/auth/key` | per provider `last_updated`, staleness shown with its age | four separate entities, each with currency and source; never summed, never converted |
| Models & Providers | catalogue counts (`9 providers`, `21 models`) | `/api/models` | each poll | registry contents, **not** keys and **not** availability |
| Models & Providers | provider readiness `3/7` | `/api/status` probe | each poll | same source and same meaning as on Overview |
| Agents | agent spend `effective_cost` | `/api/dashboard` `agents[].effective_cost` | dashboard `generated_at` | Forge-attributed spend in USD; unknown (never 0) when the dashboard has no row |
| Agents | tokens `total_tokens` | `/api/dashboard` `agents[].total_tokens` | dashboard `generated_at` | same rule |
| Agents | provider balance | link only | — | an account balance is **not** an agent budget and is never copied |
| **Costs & usage** | spend `effective_cost` | `all_time_totals.total_spent` + Σ `providers[].effective_cost` | `generated_at`, printed on screen | Forge's own accounting, USD |
| **Costs & usage** | confirmed by provider | Σ `providers[].provider_reported_cost` | `generated_at` | only what a provider API reported; silence is unknown, not zero |
| **Costs & usage** | Forge estimate | Σ `providers[].estimated_cost` | `generated_at` | computed from Forge's price table, explicitly not a provider fact |
| **Costs & usage** | tokens, runs, requests, today | `all_time_totals`, `today_totals` | `generated_at` | counters, never money |
| **Costs & usage** | account balances | — | — | deliberately absent from this screen; a card states that a balance is not spend and that no conversion is performed |
| **Costs & usage** | internal budgets and limits | `/api/agents`, `/api/dashboard` | — | no agent balance, budget, limit or ledger exists; the card says so instead of showing a zero |
| Runs | per-run cost, tokens, usage basis | `/api/tasks/run` response + `X-Forge-Usage-Basis` / `X-Forge-Cost-Basis` / `X-Forge-History-Recorded` | per dispatch | Stage C semantics: a provenance label, not a reinterpretation |

Rules that hold on every screen: one source has one meaning everywhere; every value carries its
currency; every value carries the moment it was obtained; an unknown value is never rendered as
`0`; a confirmed zero appears only when nothing ran at all.

## 3. Costs & usage screen

New `ui/src/screens/Costs.tsx`, wired to `nav=usage`. It separates:

* **confirmed spend** — what a provider API reported;
* **API usage** — the same provider-reported figures, plus the `usage` field of the balance layer;
* **computed cost** — Forge's estimate from its own price table, labelled as an estimate;
* **unknown / unavailable** — the explicit unknown marker, with the reason in the legend;
* **internal budgets and limits** — stated as absent, because they do not exist.

It never adds a balance to spend, never sums across currencies, and shows the dashboard's
`generated_at` so a stale figure is visible as one.

## 4. Failure and staleness behaviour

| State | Reported | Verified by |
|---|---|---|
| no key | `UNAUTHORIZED`, no request made | `test_17` |
| HTTP 401 / 403 | `UNAUTHORIZED` + code, body and key never included | `test_12`, `test_18` |
| HTTP 5xx | `ERROR` + code (not confused with auth failure) | `test_19` |
| timeout / network down | `UNREACHABLE`; the **last confirmed value** is returned with `stale=true` and `age_seconds` | `test_11`, `test_20`, `test_26` |
| unparseable body | `MALFORMED`, body never quoted | `test_21` |
| JSON of the wrong shape | `MALFORMED` | `test_22`, `test_23` |
| well-formed but no amount | `MALFORMED` with "contained no amount" | `test_24` |
| TEST composition | `NOT_CHECKED`, zero outbound attempts | `test_14` (socket spy) |
| TLS | verification never disabled; every endpoint is `https` | `test_25` |

## 5. Verification

* focused: `tests/test_provider_balance.py` — 26 tests, all offline;
* full suite: `Ran 2482 tests … OK (skipped=36)`;
* `tsc --noEmit` exit 0, `vite build` exit 0, `python -m compileall -q app tests` exit 0,
  `git diff --check` exit 0;
* `authorizer.py` unchanged (`189C97CFEEF672B7`); `.env` untouched; no secret patterns in the diff;
* GitHub Actions on the published commit: the platform contract job (PostgreSQL 16) is green, and
  the full-suite job result is recorded in the phase report.

## 6. Providers

**Supported (official balance endpoint):** DeepSeek, OpenRouter.
**Not supported (no balance endpoint published):** OpenAI, Anthropic, Google Gemini, xAI, Groq —
each reported as `UNSUPPORTED_BY_PROVIDER` with its reason and **no monetary value**.

## 7. Known limitations and unfinished functions

1. **`tools` has no screen.** The navigation entry exists and renders nothing; it is outside this
   phase's financial scope and is reported rather than fixed.
2. **A reported zero cannot be distinguished from silence** in the dashboard's
   `provider_reported_cost` (a plain numeric sum). The Costs screen therefore shows it as unknown
   when runs exist. Separating them properly means changing a protected read-model contract and
   needs its own justification.
3. **The legacy `account_status` verdict** is still present in `/api/dashboard` and still shown
   where it always was (Models registry cards, labelled historical). Removing it would break the
   components that depend on it.
4. **No currency conversion.** A balance in CNY is never converted into the USD accounting; an
   explicit rate and its timestamp do not exist in the system.
5. **No internal budgets or limits exist**, so nothing is displayed for them.
6. **No paid provider completion has been executed**, so a provider-confirmed *run* cost is
   unverified end to end; balance endpoints and the mock path are verified.
7. Balance history is per-process, not a billing archive.

---

# Phase 4 — согласованность финансовых данных (кратко по-русски)

* Найден и закрыт главный дефект: пункт навигации **«Расходы»** не имел экрана — клик давал пустую
  область. Экран создан: подтверждённый провайдером расход, расчёт Forge, tokens/запуски, явное
  «неизвестно» и отдельная карточка о том, что **баланс аккаунта — это не расход**. Второй мёртвый
  пункт, **«Инструменты»**, обнаружен и заявлен в отчёте, но не тронут (вне финансовой задачи).
* Убрана ложная подпись «ни одного ключа не настроено» на «Моделях»: число считает аккаунты
  **реестра**, а не ключи окружения.
* С Overview убрана легаси-сумма `balance_display` (у части провайдеров это зашитый `$0.00`), статус
  помечен как исторический вердикт, а баланс ведёт ссылкой в раздел с источниками.
* Все состояния сбоя различаются: нет ключа, 401/403, 5xx, таймаут, битый ответ, неполный ответ,
  устаревшее значение, тестовый режим. Тело ответа и ключ никогда не логируются, TLS не отключается.
* Один источник — одна семантика на всех экранах; у каждого значения валюта и время получения;
  неизвестное не подменяется нулём; подтверждённый ноль — только когда запусков не было.
* Полный набор тестов: **2482 OK**. CI: платформенная job (PostgreSQL 16) зелёная, результат
  полного набора зафиксирован в отчёте.
