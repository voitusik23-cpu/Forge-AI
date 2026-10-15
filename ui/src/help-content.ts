// The guide. Every statement describes the implementation as it exists today, including the
// parts that are not wired yet: a guide that describes intentions would be worse than none.
import type { Lang } from "./i18n";

export type HelpTopic = {
  id: string;
  title: Record<Lang, string>;
  body: Record<Lang, string[]>;
};

export const HELP_TOPICS: HelpTopic[] = [
  {
    id: "project",
    title: { ru: "Проект: что это и как он появляется", en: "Projects: what they are and how they appear" },
    body: {
      ru: [
        "Проект — это рабочее пространство с путём на диске, к которому привязаны запуски. Список приходит из backend: GET /api/projects, а также GET /api/product/onboarding.",
        "Сегодня в системе один проект — «Forge Main Workspace», путь C:\\Users\\User\\Desktop\\Forge-AI, статус active.",
        "Создание проекта из интерфейса пока не реализовано: в backend нет соответствующего endpoint'а записи, и я не стал придумывать контракт. Ближайший безопасный шаг — POST /api/projects поверх существующего проектного хранилища, с той же проектной изоляцией.",
      ],
      en: [
        "A project is a workspace with a path on disk that runs belong to. The list comes from the backend: GET /api/projects, and GET /api/product/onboarding.",
        "Today the system has one project — \"Forge Main Workspace\", path C:\\Users\\User\\Desktop\\Forge-AI, status active.",
        "Creating a project from the interface is not implemented: the backend has no write endpoint for it, and I did not invent a contract. The nearest safe step is POST /api/projects over the existing project store, keeping the same project isolation.",
      ],
    },
  },
  {
    id: "providers",
    title: { ru: "Модели и провайдеры", en: "Models and providers" },
    body: {
      ru: [
        "Провайдер — внешний поставщик моделей. Их состояние приходит из GET /api/dashboard: для каждого указаны статус аккаунта, расход Forge, оценка стоимости, сообщённая провайдером стоимость, учтённая стоимость, запросы, запуски, токены и счётчики успехов и ошибок.",
        "Сегодня подключено 7 провайдеров, готовых — 0 из 7: без ключей провайдеры не опрашиваются, поэтому provider_status = DEGRADED.",
        "Модели: GET /api/dashboard отдаёт models: [], а GET /api/capabilities в этой среде содержит только домены task и tool. Раздел Models поэтому честно показывает отсутствие данных, а не список из каталога.",
        "Ключи читаются из переменных окружения. Не вставляйте ключ в файл, который попадает в git: .env в игнорировании, а .env.example перечисляет имена.",
      ],
      en: [
        "A provider is an external supplier of models. Its state comes from GET /api/dashboard: account status, Forge spend, Forge estimate, provider-reported cost, accounted cost, requests, runs, tokens and success/failure counters.",
        "Seven providers are configured, none ready (0/7): without keys they are not probed, so provider_status = DEGRADED.",
        "Models: GET /api/dashboard answers models: [], and GET /api/capabilities in this environment carries only the task and tool domains. The Models section therefore states that no data exists instead of showing a catalogue.",
        "Keys are read from the environment. Never paste a key into a file that is committed: .env is ignored by git and .env.example lists the names.",
      ],
    },
  },
  {
    id: "agents",
    title: { ru: "Агент: создание и настройка", en: "Agents: creating and configuring" },
    body: {
      ru: [
        "Агент — исполнитель задачи. Список приходит из GET /api/agents: имя, провайдер, модель, статус, возможности.",
        "Сегодня зарегистрирован один агент — mock (offline-исполнитель, возможности code, analysis, review). Он появляется потому, что зарегистрирован в рантайме; агенты реальных провайдеров появятся, когда будут настроены ключи.",
        "Создание агента из интерфейса не реализовано: реестр агентов — часть рантайма, и запись в него из UI была бы обходом модели доступа. Минимальный безопасный шаг — серверный endpoint регистрации, который проходит через существующую авторизацию.",
      ],
      en: [
        "An agent executes a task. The list comes from GET /api/agents: name, provider, model, status, capabilities.",
        "One agent is registered today — mock (the offline executor, capabilities code, analysis, review). It exists because it is registered in the runtime; provider-backed agents appear once keys are configured.",
        "Creating an agent from the interface is not implemented: the agent registry belongs to the runtime, and writing to it from the UI would bypass the access model. The minimal safe step is a server-side registration endpoint that goes through the existing authorization.",
      ],
    },
  },
  {
    id: "prompt",
    title: { ru: "Как читать system prompt", en: "How to read a system prompt" },
    body: {
      ru: [
        "Важно: у агента нет собственного system prompt в текущей модели. Агент — это исполнитель с именем, провайдером, моделью и возможностями.",
        "Инструкции живут в навыках (skills): app/skills/assembler.py берёт skill.instructions, обрезает до 1000 символов и собирает их в промпт при запуске. Пример — навык проверки Python-тестов.",
        "Модели объявляют флаг supports_system_prompt — то есть поддержку системного сообщения на стороне провайдера.",
        "Поэтому в карточке агента прямо написано, что персональной инструкции нет, а вместо выдуманного текста показан список инструментов и возможностей, которые действительно определяют поведение.",
      ],
      en: [
        "Important: an agent has no system prompt of its own in the current model. An agent is an executor with a name, a provider, a model and capabilities.",
        "Instructions live in skills: app/skills/assembler.py reads skill.instructions, trims them to 1000 characters and assembles them into the prompt at run time. The Python test verification skill is an example.",
        "Models advertise supports_system_prompt, meaning the provider supports a system message.",
        "The agent card therefore states plainly that there is no personal instruction, and shows the tools and capabilities that actually determine behaviour instead of invented text.",
      ],
    },
  },
  {
    id: "tools",
    title: { ru: "Подключение инструментов", en: "Connecting tools" },
    body: {
      ru: [
        "Инструменты перечисляет GET /api/tools: сейчас это read_project_file (чтение одного разрешённого файла проекта как UTF-8) и write_project_file (создание или явная перезапись одного текстового файла в рабочей области запуска).",
        "Инструмент не подключается к агенту вручную: модель не хранит привязку инструмента к агенту. Доступ к инструменту выдаётся в рамках запуска, а запись файла выполняется только в рабочей области запуска.",
        "Полный список возможностей, включая рабочие пространства и инструменты, отдаёт GET /api/capabilities.",
      ],
      en: [
        "Tools are listed by GET /api/tools: currently read_project_file (read one allow-listed project file as UTF-8 text) and write_project_file (create or explicitly overwrite one text file in the run workspace).",
        "A tool is not attached to an agent by hand: the model does not store a tool-to-agent binding. Access is granted within a run, and a file write happens only inside the run workspace.",
        "The full list of capabilities, including workspaces and tools, comes from GET /api/capabilities.",
      ],
    },
  },
  {
    id: "run",
    title: { ru: "Запуск задачи и проверка результата", en: "Running a task and checking the result" },
    body: {
      ru: [
        "Backend принимает запуск через POST /api/tasks/run, а состояние запусков отдаёт GET /api/product/runs (сейчас total_recorded = 0, запусков нет).",
        "Кнопка «Новый запуск» в интерфейсе пока не подключена: я не стал делать вид, что запуск происходит. Это ближайший следующий шаг — форма запуска поверх существующего endpoint'а, с показом авторизации и результата.",
        "Когда запуски появятся, они отобразятся в Overview и в таблице раздела Runs с реальными статусами и стоимостью.",
      ],
      en: [
        "The backend accepts a run through POST /api/tasks/run, and run state comes from GET /api/product/runs (currently total_recorded = 0, no runs).",
        "The \"New run\" button is not wired yet: I did not want to pretend a run happens. It is the nearest next step — a run form over the existing endpoint, showing authorization and the result.",
        "Once runs exist they appear in Overview and in the Runs table with real statuses and cost.",
      ],
    },
  },
  {
    id: "status",
    title: { ru: "Как читать статусы и ошибки", en: "Reading statuses and errors" },
    body: {
      ru: [
        "PASSED — объявленная проверка выполнена и подтвердилась. FAILED — выполнена и не подтвердилась. ERROR и UNKNOWN — проверить не удалось. INDETERMINATE — попытку нельзя свести к успеху.",
        "Отчёт никогда не считается доказательством: доказательство — это проверка, которая действительно выполнялась.",
        "Статусные слова (CONNECTED, DEGRADED, NOT_CHECKED, AVAILABLE) не переводятся — это литералы, как PASSED и UNKNOWN.",
        "Ошибка API объясняется человеческим языком: если запрос не удался, интерфейс пишет, какой именно путь и какой код ответа, и показывает кнопку повторного запроса.",
      ],
      en: [
        "PASSED means a declared check was evaluated and held. FAILED means it was evaluated and did not hold. ERROR and UNKNOWN mean it could not be evaluated. INDETERMINATE means the attempt cannot be summarised as success.",
        "A report is never treated as evidence: evidence is a check that actually ran.",
        "Status words (CONNECTED, DEGRADED, NOT_CHECKED, AVAILABLE) are not translated — they are literals, like PASSED and UNKNOWN.",
        "An API error is explained in plain language: when a request fails, the interface names the path and the response code and offers a retry.",
      ],
    },
  },
  {
    id: "money",
    title: { ru: "Стоимость, лимиты и баланс", en: "Cost, limits and balance" },
    body: {
      ru: [
        "Показатели не взаимозаменяемы и называются точно: estimated_cost — оценка Forge; provider_reported_cost — то, что сообщил провайдер; effective_cost — учтённая сумма; forge_spent — расход Forge с этим провайдером; all_time_totals.total_spent — итог по всем записанным запускам.",
        "Баланс и квота относятся к провайдеру, а не к агенту. Раздел «Балансы провайдеров» читает официальные endpoint'ы: DeepSeek GET /user/balance и OpenRouter GET /api/v1/auth/key. Доступный остаток, подаренные кредиты (granted) и пополнение (topped_up) показываются раздельно, с валютой, источником и временем получения. Провайдеры без официального баланса (OpenAI, Anthropic, Gemini, xAI, Groq) помечены как «Недоступно у провайдера» — без нулей и оценок. При сбое обновления сохраняется последнее подтверждённое значение с пометкой «устарело» и возрастом.",
        "Отдельного баланса агента, лимита агента и агентского леджера в системе нет — это прямо указано в карточке агента. Остаток не вычисляется, потому что подтверждённого лимита не существует.",
      ],
      en: [
        "The measures are not interchangeable and are named exactly: estimated_cost (Forge's estimate), provider_reported_cost (what the provider reported), effective_cost (accounted), forge_spent (Forge spend with that provider), all_time_totals.total_spent (all recorded runs).",
        "Balance and quota belong to the provider, not to an agent. The provider balances section reads the official endpoints: DeepSeek GET /user/balance and OpenRouter GET /api/v1/auth/key. Available balance, granted credit and topped-up credit are shown separately, with currency, source and the time the value was obtained. Providers with no official balance (OpenAI, Anthropic, Gemini, xAI, Groq) are marked as not available at the provider -- no zeros and no estimates. After a failed refresh the last confirmed value is kept, marked stale with its age.",
        "There is no agent balance, no agent limit and no agent ledger — the agent card says so directly. No remainder is computed, because no confirmed limit exists.",
      ],
    },
  },
  {
    id: "permissions",
    title: { ru: "Разрешения и подтверждение действий", en: "Permissions and approvals" },
    body: {
      ru: [
        "Три инварианта платформы: рекомендация ≠ разрешение, разрешение ≠ исполнение, отчёт ≠ доказательство.",
        "Полномочия выдаёт цепочка авторизации запуска. Интерфейс не выдаёт прав, которых нет в backend, и не создаёт обходов авторизации.",
        "Отдельной модели разрешений агента нет: ограничения задаются на уровне запуска и его рабочей области.",
      ],
      en: [
        "Three platform invariants: a recommendation is not a permission, a permission is not an execution, a report is not evidence.",
        "Authority is issued by the run authorization chain. The interface never grants rights the backend does not have and never creates an authorization bypass.",
        "There is no per-agent permission model: limits are set at the level of a run and its workspace.",
      ],
    },
  },
  {
    id: "appearance",
    title: { ru: "Тема и настройки", en: "Theme and settings" },
    body: {
      ru: [
        "Settings → Appearance: тема Dark, Light или System, четыре акцентных цвета, плотность интерфейса и уменьшение анимации. Переключение применяется мгновенно, без перезагрузки.",
        "Выбор сохраняется в localStorage под ключом forge.appearance и применяется до первой отрисовки, поэтому светлая тема не даёт белой вспышки при запуске.",
        "Режим System следует за настройкой Windows; при её изменении интерфейс переключается сам.",
        "Язык RU/EN переключает весь интерфейс, включая это руководство.",
      ],
      en: [
        "Settings → Appearance: Dark, Light or System theme, four accents, interface density and reduced motion. A change applies instantly, without a reload.",
        "The choice is stored in localStorage under forge.appearance and applied before the first paint, so Light never flashes a white page at start-up.",
        "System follows the Windows setting and switches on its own when that setting changes.",
        "The RU/EN switch relabels the whole interface, including this guide.",
      ],
    },
  },
];

export const HELP_TOPIC_BY_ID = (id: string): HelpTopic | undefined =>
  HELP_TOPICS.find((topic) => topic.id === id);

// Which topic each screen should offer through its "How does this work?" link.
export const CONTEXT_TOPIC: Record<string, string> = {
  overview: "status",
  agents: "agents",
  settings: "appearance",
  projects: "project",
  models: "providers",
  runs: "run",
  tools: "tools",
  usage: "money",
  help: "project",
};
