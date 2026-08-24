# Amigo v5: production runbook

Этот документ описывает безопасное развёртывание Amigo v5 на
`192.168.31.3`. Все команды изменения состояния выполняются на
origin-сервере от `root`. Пароли, OAuth-токены, Telegram-токены,
chat ID, Codex `auth.json` и значения из медицинских payload не копируются
в команды, логи или Markdown.

## Неизменяемые эксплуатационные условия

- Production URL: `https://amigo.tolstik.ru/amigo/`.
- Проект расположен в `/srv/amigo`. Compose содержит ровно семь сервисов:
  `web`, `worker`, `db`, `ingest`, `ai-worker`, `ai-gateway`, `lab-parser`.
- На host публикуются только `web` на `127.0.0.1:18181` и `ingest` на
  `127.0.0.1:18182`. `ai-gateway:8090` и `lab-parser:8085` доступны только в
  выделенных Docker-сетях и не имеют host listener.
- Точный SHA запущенного image хранится в root-only
  `/var/lib/amigo/current-release`. Verification использует его, а не HEAD
  checkout после documentation-checkpoint commit.
- GitHub Actions после всех backend/frontend/E2E/Android/release-gate проверок
  публикует immutable `ghcr.io/tolstik/amigo:GIT_SHA`. Production только
  загружает этот image и сверяет OCI revision; сборка приложения на слабом
  сервере запрещена.
- `web`, `ingest` и `ai-worker` получают только PostgreSQL secret.
  `worker` получает ровно восемь DB/Withings/Fernet/Telegram secret files.
  `ai-gateway` и `lab-parser` не получают Docker secrets и доступ к БД.
- Детерминированный код считает KPI, план, тренд, прогноз, выбросы,
  личную базу и корреляции. В Codex уходят только минимизированные
  факты и ограниченные дневные ряды без имени, device ID, raw provider
  payload, GPS/location и учётных данных. После отдельного consent полный OCR-
  текст и вопрос могут передаваться через тот же локальный Codex runtime в
  OpenAI inference для лабораторного extraction и чата.
- В production задано `AMIGO_USER_HEIGHT_CM=176`. Snapshot передаёт этот
  identifier-free факт; BMI рассчитывается детерминированно только при наличии
  последнего доступного Withings weight, получает его `observed_on` и не
  пересчитывается моделью или из Health Connect weight.
- AI вызывается асинхронно через SHA-256-pinned Codex CLI `0.148.0`
  (`ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`),
  фиксированную модель `gpt-5.6-sol`, read-only sandbox и строгую JSON
  schema. Авторизованные GET только читают PostgreSQL.
- Prompt contract `amigo-health-v4` требует для каждой рекомендации конкретное
  действие, cadence или review period и ссылки на существующие evidence keys.
  На overview и в Telegram рекомендации идут раньше общих наблюдений.
- Assistant contract `amigo-health-chat-v2` получает полную структурированную
  историю здоровья, лабораторных результатов и исследований, но не originals,
  filenames, study titles или OCR pages. Он может разбирать evidence-backed
  гипотезы и альтернативы, но не утверждать диагноз и не назначать лечение,
  лекарства/дозировки или фиксированную калорийность.
- Ни дашборд, ни Telegram не показывают шаблонную подмену AI-текста.
  При недоступном AI графики и факты остаются рабочими.
- Исторические AI jobs/results сохраняются в PostgreSQL, но authenticated cache и
  worker используют только текущую пару model/prompt. Несовместимые pending или
  просроченные jobs становятся `superseded`; явный deployment `ai-enqueue`
  может повторно поставить active failed/superseded same-key job без ожидания
  backoff, обычный background enqueue terminal history не оживляет.
- Dashboard, health API, CSV, laboratory/study archive/originals, app update и assistant закрыты
  одним локальным Argon2id-аккаунтом: opaque server sessions живут 90 дней,
  cookies имеют `Secure`/`SameSite=Strict`, мутации требуют exact Origin и CSRF.
  Signed Android ingest остаётся отдельным. Health Connect показывается только
  как агрегаты без device/pairing metadata, signatures, nonces, raw provider
  payload и raw heart-rate samples.
- PostgreSQL `stored_files` — источник истины для оригиналов анализов и
  исследований. Для совместимости с предыдущим release лабораторные файлы
  временно dual-write сохраняются также в root-owned
  `/srv/amigo/data/lab-files`: каталог `0700`, файлы `0600`. `web` монтирует его
  read-write, `ai-worker` read-only, `lab-parser` не монтирует вообще. Parser
  работает non-root/read-only без внешней сети; PDF/JPG/PNG/HEIC ограничены 20
  МиБ, PDF — 50 страницами, одна UI-выборка — 25 файлами. Исследования v1
  принимают отчёты УЗИ/МРТ/КТ/рентген/ЭКГ/other; DICOM не принимается.
- Очереди анализов и исследований обновляются через PostgreSQL
  `LISTEN/NOTIFY` и SSE. AI worker использует bounded notification wait с
  60-секундным fallback, healthchecks облегчены, minute heartbeat JobRun удалён.
- Withings — единственный источник веса, состава тела и давления. Mi Fitness
  передаёт только allowlisted activity/recovery records через Health Connect;
  weight, pressure, location и exercise routes из Health Connect не принимаются.
  Raw heart-rate samples не сохраняются; дневные агрегаты используются в
  CSV/Telegram/AI, а почасовые min/avg/max — только для временной шкалы графика
  пульса с часов.
- Одинаковая Withings group, повторно пришедшая из overlap-window, считается
  неизменной и не запускает AI повторно. Measurement-trigger создаётся только
  для новой или структурно изменившейся provider group.
- Переключатель дашборда содержит темы «Светлая», «Тёмная», «Океан» и «Закат».
  Без сохранённого выбора старт всегда светлый независимо от темы ОС; явный
  выбор сохраняется и применяется также к графикам.
- Детерминированные экраны давления, сердечных метрик, SpO2 и VO2 max остаются
  описательными, без severity-цветов и app-side диагнозов. Валидированный AI
  может использовать эти метрики только для совета повторить измерения, вести
  журнал или обсудить устойчивую измеренную динамику с врачом. Запрещены
  диагноз, назначение лечения, лекарств или изменения дозировки и фиксированная
  цель по калориям.
- TLS завершается на public edge `5.35.114.76`. Внешний HTTPS проверяется
  без `-k`; локальный nginx — отдельный HTTP origin.
- Для assistant SSE приложение и origin передают `X-Accel-Buffering: no`.
  Public nginx edge применяет этот служебный заголовок и не возвращает его
  браузеру, поэтому verification требует его на origin, а на внешнем HTTPS
  проверяет `text/event-stream`, `no-store` и bounded error event.
- Origin nginx хранит действующую конфигурацию в `/etc/nginx/conf.d/my.conf`.
  Managed include добавляется только в существующие server-блоки `tolstik.ru`.
- Legacy-приложение `/srv/www/amigo` и MariaDB `amigo` не удаляются и не
  перезаписываются.
- `/srv/cron` — общий каталог. Скрипты управляют только точной строкой
  `*/1 07-08 * * *  php /srv/cron/get_withings.php`. Строка
  `*/1 * * * *  php /srv/cron/send_telergam.php all` остаётся без изменений.
- Recovery/disaster snapshots находятся только в
  `/srv/amigo-rollbacks/<UTC timestamp>`, имеют root-only права и не удаляются
  автоматически.
- Ошибка repeat deployment автоматически возвращает immutable image предыдущего
  Amigo-релиза. Legacy PHP никогда не включается автоматически: это отдельный
  явно подтверждаемый disaster fallback.

## Почему прошлые deploy были долгими и откатывались

История релизов 2026-08-20 показывает не одну общую неисправность, а цепочку
release-gate расхождений: dynamic nginx captures, auth-floor maintenance status,
явный `429`, assistant SSE buffering, transient assistant smoke, runtime source
permissions и App Links проверялись уже около cutover. Fail-closed recovery
делал правильный откат, но каждая следующая небольшая поправка требовала нового
полного цикла.

Время дополнительно тратили сборка application image на слабом production host,
отдельный `migrate` перед уже мигрирующим `bootstrap`, full Withings sync и
перезапись неизменившегося legacy TSV. В v5 эти операции вынесены или удалены:
image строится после всех CI gates и только скачивается production, выполняется
один bootstrap, затем incremental sync и compare-before-replace TSV. Route/SSE/
auth-floor/App Links контракты остаются в pre-cutover recovery test и полном
verification. Это сокращает нормальный cutover, не ослабляя snapshot и
automatic recovery.

Preflight релиза v5 также выявил отдельную повторяемую причину: обязательный
checkpoint прошлого deploy обновлял `AGENTS.md` и два документа, но оставлял
checkout dirty, поэтому следующий root-owned wrapper останавливался ещё до
backup. Теперь checkpoint сам создаёт локальный documentation-only commit,
сохраняет его под `refs/amigo/checkpoints/GIT_SHA` и проверяет, что checkout
снова чист. Перенос фактов в canonical `main` по-прежнему обязателен.

## Подготовка релиза

1. Разместить чистый root-owned Git checkout нужного commit в `/srv/amigo`.
   Tracked и untracked source-файлы должны быть чисты; `.env`, `secrets/` и `data/`
   остаются ignored runtime state.
   `/var/lib/amigo/current-release` должен указывать на реально запущенный
   предыдущий Amigo image. Candidate обязан пройти консервативную rollback-
   compatibility проверку: базовые ORM models и pinned Codex runtime не
   меняются, а существующие Alembic migration-файлы нельзя изменять или
   удалять. Новые migration-файлы и новый Compose/nginx envelope допустимы:
   snapshot сохраняет точные previous Compose/nginx files, image IDs и
   rollback tags, а recovery останавливает candidate services динамически.
2. Создать `/srv/amigo/.env` из `.env.example`, оставить
   `AMIGO_USER_HEIGHT_CM=176`, установить `0600` и выполнить:

   ```bash
   candidate_sha="$(sudo git -C /srv/amigo rev-parse HEAD)"
   sudo env AMIGO_IMAGE_TAG="${candidate_sha}" \
     docker compose --file /srv/amigo/compose.yaml \
     --env-file /srv/amigo/.env config --quiet
   unset candidate_sha
   ```

3. Через защищённый канал поместить непустые root-only файлы `0400` или
   `0600` в `/srv/amigo/secrets/`:

   - `postgres_password`;
   - `app_encryption_key`;
   - `withings_client_id` и `withings_client_secret`;
   - `withings_access_token` и `withings_refresh_token`;
   - `telegram_bot_token` и `telegram_chat_id`.

4. Сервис-владелец `tolstik` должен быть заранее интерактивно авторизован
   в серверном Codex CLI. `deploy/prepare-ai-runtime.sh` проверяет pinned binary,
   атомарно копирует его и только `auth.json` в dedicated runtime-каталог с
   UID/GID `65532`. Содержимое auth-файла не просматривать и не копировать
   вручную. OpenAI описывает saved CLI authentication в
   [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode#authenticate-in-automation).
5. Проверить свободное место для legacy archive, dump MariaDB, текущего dump
   PostgreSQL, Docker images и PostgreSQL volume. Убедиться, что установлены
   `docker compose`, `mariadb-dump`, `nginx`, `curl`, `python3`, `sha256sum`.
6. Проверить public TLS с машины, которая обращается к edge:

   ```bash
   curl --fail --silent --show-error --head https://amigo.tolstik.ru/amigo/
   ```

   `--insecure` запрещён. Origin проверяется отдельно по HTTP с явным `Host`.
7. Для первого auth cutover подготовить уникальный пароль не короче 14 символов.
   Его дважды вводят только в скрытом prompt `deploy.sh` через `/dev/tty`; пароль
   не помещают в shell history, `.env`, secret file, командные аргументы или
   Markdown. На повторных deploy prompt отсутствует, пока аккаунт настроен.
8. Убедиться, что anonymous pull точного candidate
   `ghcr.io/tolstik/amigo:GIT_SHA` доступен production или root Docker уже
   авторизован только для чтения package. OCI label
   `org.opencontainers.image.revision` должен совпадать с `GIT_SHA`.
9. Для Android `1.3.3` (`versionCode 13`) использовать signed
   [`Amigo-1.3.3.apk`](https://github.com/tolstik/amigo/releases/download/v5.1.4/Amigo-1.3.3.apk)
   из GitHub release
   [`v5.1.4`](https://github.com/tolstik/amigo/releases/tag/v5.1.4) и сверить SHA-256
   `6f4156d6cf24df27b95b6cc53b26f83bd965c266da144e04f6feb3ccb884f156`.
   Signing certificate SHA-256 должен быть
   `25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02`.
   Keystore и его пароли не хранятся в Git или Markdown.

Для первого перехода с legacy файлы секретов можно создать без вывода
значений:

```bash
sudo bash /srv/amigo/deploy/bootstrap-production-secrets.sh
```

Команда fail-closed читает legacy-настройки, генерирует независимые
PostgreSQL/Fernet значения и отказывается перезаписывать существующий
`secrets/`. Ротация реквизитов выполняется владельцем через соответствующие
панели. Полный preflight приведён в
[production-verification.md](production-verification.md).

## Backup и rollback snapshot

`deploy.sh` сначала скачивает и проверяет immutable candidate image и signed APK,
но всегда запускает backup до остановки сервисов, миграции и cutover. Отдельная
проверка:

```bash
sudo bash /srv/amigo/deploy/pre-cutover-backup.sh
```

Скрипт создаёт timestamped snapshot и печатает его абсолютный путь. В snapshot
входят:

- архив `/srv/www/amigo` с ownership, ACL и xattrs;
- consistent dump legacy MariaDB `amigo` с routines, events и triggers;
- PostgreSQL custom-format dump, если текущий `db` уже запущен; его каталог
  проверяется через `pg_restore --list`;
- архив защищённых лабораторных originals из `/srv/amigo/data/lab-files`;
- установленный `/srv/amigo/data/android/amigo-sync.apk` либо явная отметка,
  что до cutover APK отсутствовал;
- crontab `tolstik` и `root`;
- полный `/etc/nginx`, `my.conf` и `nginx -T`;
- previous release SHA, exact application и PostgreSQL image IDs, защищённые
  rollback tags, Compose envelope, active AI model/prompt, `previous_auth_floor`
  и точные pre-cutover managed nginx files;
- несекретные metadata и `SHA256SUMS`.

Архивы, SQL gzip, PostgreSQL dump и все SHA-256 проверяются до атомарного
переименования `.partial-*`. Незавершённый snapshot сохраняется для
диагностики, но не считается rollback point. Повторная проверка:

```bash
cd /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
sudo sha256sum --check --strict SHA256SUMS
```

Не распаковывать snapshot поверх production. Физическое восстановление БД
выполняется отдельно и не входит в обычный route rollback.

## Развёртывание и cutover

Первый переход или восстановление release-доступа выполняются обычным
интерактивным `sudo`. В ходе deploy root-owned installer размещает
`/usr/local/sbin/amigo-release` и точное правило
`/etc/sudoers.d/amigo-release`. Оно разрешает `tolstik` без пароля запускать
только этот wrapper, а не shell, Git, Docker, `deploy.sh` или `NOPASSWD: ALL`.
Wrapper принимает ровно 40-символьный SHA текущего `origin/main`, требует
чистый root-owned checkout и descendant текущего recorded release, после чего
сам вызывает штатный `deploy.sh`.

Повторный unattended deploy запускается так:

```bash
sudo /usr/local/sbin/amigo-release GIT_SHA --send-telegram-test
```

Для режима без Telegram smoke используется `--skip-telegram-test`. Сам wrapper
не отменяет первый скрытый prompt пароля Amigo, если локальный аккаунт ещё не
создан.

Только при явном решении оператора на время текущей сессии допускается
fix-forward режим `--no-auto-recovery` третьим аргументом wrapper. Проверенный
snapshot всё равно создаётся. Если candidate дошёл до healthy runtime, но не
прошёл финальную verification, он остаётся активным и записывается как текущий,
чтобы следующий SHA можно было накатить поверх. Без этого флага штатное
automatic recovery остаётся обязательным.

Команда требует ровно один явный режим Telegram-проверки:

```bash
sudo bash /srv/amigo/deploy/deploy.sh --send-telegram-test
```

Без отдельного разрешения на тестовое сообщение:

```bash
sudo bash /srv/amigo/deploy/deploy.sh --skip-telegram-test
```

Фиксированная последовательность:

1. Проверка layout, прав secret files, чистого Git SHA, Compose и nginx.
2. Подготовка pinned Codex runtime, pull точного CI-built
   `ghcr.io/tolstik/amigo:GIT_SHA`, проверка OCI revision, pull PostgreSQL image
   и скачивание signed Android APK с проверкой pinned SHA-256. Production image
   не собирается на сервере.
3. Проверенный legacy/PostgreSQL snapshot с rollback image tags и предыдущим
   состоянием APK; после snapshot включается automatic recovery.
4. Остановка уже работающих `worker`, `ai-worker`, `ingest` и `lab-parser` (`ai-worker`
   получает до 180 секунд на штатное завершение), запуск `db`,
   один idempotent bootstrap с migrations, затем `backfill-files`, который
   копирует и проверяет legacy laboratory originals в PostgreSQL. Если локальный аккаунт ещё не создан, deploy скрыто
   запрашивает пароль дважды и передаёт одну строку в root-only CLI через stdin.
   Затем, только в выбранном режиме, выполняется Telegram smoke.
5. Отключение только точной legacy Withings cron-строки, incremental sync без
   historical notifications, немедленный OAuth token handback в одну legacy
   MariaDB строку и импорт legacy-only весов из root-only TSV. Неизменившийся
   TSV не переписывается.
6. Запуск `web` без workers, direct health на `127.0.0.1:18181` и атомарная
   установка проверенного APK `1.3.3` в root-only Android directory.
7. Запуск изолированных `ai-gateway` и `lab-parser`; synthetic smoke через
   `ai-worker` последовательно проверяет live-контракты analysis, laboratory
   extraction, analyte guide и assistant turn, включая auth, sandbox, model,
   strict JSON schema и streaming completion, без реальных health data или
   персонального контекста. Analysis, laboratory extraction и analyte guide
   выполняются один раз. Только invalid/error assistant
   result получает ровно одну повторную попытку с `attempt=2`; второй ответ
   обязан полностью пройти schema, evidence и medical-safety validation.
   Routine health analysis имеет отдельный bounded Codex deadline 150 секунд и
   worker HTTP timeout 180 секунд; extraction, analyte-guide и assistant
   сохраняют 75-секундный Codex deadline.
8. При всё ещё остановленном persistent `ai-worker` один
   `ai-retry-current --worker-stopped` готовит exact current job. `ai-ready`
   принимает только `0` (готово) или `75` (ещё не готово); любой другой exit
   fatal. Выполняется не более четырёх foreground one-shot workers, и только
   между неуспешными попытками 1–3 вызывается `ai-enqueue` для снятия backoff.
   Тройной gateway smoke/retry не повторяется.
9. Запуск `ingest`, затем атомарная установка nginx route. Общий prefix
   разрешает только `GET`/`HEAD`/`OPTIONS`; exact
   auth/profile/labs/studies/assistant
   mutation routes имеют отдельные rate/body limits, upload — 21 МиБ, SSE —
   отключённый buffering. Ingest имеет точные rate-limited routes и body limit
   1 МиБ. Сразу после nginx reload origin получает до
   15 проверок с интервалом 2 секунды для стабилизации на exact HTTP 200;
   последующий полный verification этим не заменяется.
10. Запуск `worker` и `ai-worker` и полный verification. Проверка ждёт, пока
    текущий container
    `worker` завершит новый `withings-incremental` `JobRun` со статусом
    `success`; старый успешный run не засчитывается.
11. Запись `/var/lib/amigo/current-release` и обязательный
    documentation/memory checkpoint.

До nginx cutover действующий managed Amigo route не меняется. Любая ошибка после
начала передачи сбора вызывает `restore-previous-release.sh`: candidate services
останавливаются, legacy cron остаётся выключенным, exact previous application и
PostgreSQL images запускаются на сохранённом PostgreSQL volume, а pre-cutover
Amigo snippets/route возвращаются атомарно без замены остального shared
`my.conf`. Если previous release не содержит auth-контур, runtime возвращается
только за maintenance route: `/amigo/` отвечает `503`, а signed ingest остаётся
доступен; медицинские данные публичными снова не становятся.
PostgreSQL dump автоматически не восстанавливается, чтобы не терять уже принятые
Withings/Health Connect записи. Активные AI jobs другого model/prompt переводятся
только по metadata в `superseded`; если это невозможно, previous AI worker остаётся
остановленным в degraded mode. Legacy fallback при ошибке recovery только
предлагается оператору и никогда не запускается автоматически. Ошибка checkpoint
после verification не откатывает здоровый runtime, но deploy не завершён до
коммита документов.

## Codex auth и AI-эксплуатация

Обычный deploy не перезатирает рабочий dedicated auth state. При истечшей или
отозванной ChatGPT/Codex-сессии:

```bash
sudo -iu tolstik codex login
sudo bash /srv/amigo/deploy/prepare-ai-runtime.sh --refresh-auth
release_sha="$(sudo cat /var/lib/amigo/current-release)"
sudo env AMIGO_IMAGE_TAG="${release_sha}" \
  docker compose --file /srv/amigo/compose.yaml \
  --env-file /srv/amigo/.env up -d --wait ai-gateway
sudo env AMIGO_IMAGE_TAG="${release_sha}" \
  docker compose --file /srv/amigo/compose.yaml \
  --env-file /srv/amigo/.env run --rm --no-deps \
  ai-worker python -m app.ai_smoke
unset release_sha
```

`codex login` — единственный интерактивный шаг обновления Codex auth. Не печатать, не читать и не
передавать `auth.json`. Prepare-скрипт проверяет JSON без вывода содержимого,
режим `0600` и pinned hash. Smoke передаёт только synthetic boolean и fail-closed
проверяет схему ответа.

AI job создаётся после новых Withings/Health Connect данных и в 08:45 перед
отчётом. Queue дедуплицирует snapshot hash, дебаунсит поток активности и
повторяет временные ошибки. Новый snapshot может оставить предыдущий
текст в status `stale` не более 24 часов. После этого status — `unavailable`,
без старого текста и без fallback. Если входные данные не менялись,
соответствующий кэш остаётся `ready`.

Контракт `amigo-health-v4` допускает устойчивые рекомендации по питанию,
активности, сну и измерениям, но каждый пункт должен содержать конкретное
действие, периодичность или срок пересмотра и фактические evidence keys.
Pressure/heart/SpO2/VO2 evidence разрешено только для repeat-measurement,
logging или обсуждения устойчивого паттерна с врачом; validator отклоняет
диагноз, лечение, назначение или изменение лекарств/дозировки и фиксированные
калории. Если хотя бы одна такая медицинская метрика присутствует, validator
требует минимум одну bounded medical/measurement рекомендацию. В Telegram и
overview рекомендации показываются до наблюдений.

## Локальный аккаунт, лаборатория, исследования и assistant

Пароль меняется только интерактивной root-only командой; аргумент с паролем не
используется. Операция отзывает все действующие sessions:

```bash
release_sha="$(sudo cat /var/lib/amigo/current-release)"
sudo env AMIGO_IMAGE_TAG="${release_sha}" \
  docker compose --file /srv/amigo/compose.yaml \
  --env-file /srv/amigo/.env run --rm --no-deps --user 0 \
  worker python -m app.cli auth-set-password
unset release_sha
```

До первой загрузки анализа или вопроса владелец заполняет дату рождения/биологический
пол в `/amigo/profile` и подтверждает disclosure `amigo-ai-data-v1`: Codex CLI
запускается на origin, но полный извлечённый текст и вопросы передаются в OpenAI
inference. Отзыв consent запрещает новые uploads/turns, но не удаляет историю.

Одна UI-выборка принимает до 25 PDF/JPG/PNG/HEIC, каждый файл до 20 МиБ и
PDF до 50 страниц. Web сначала сохраняет оригинал в PostgreSQL и временно
dual-write пишет лабораторный файл в root-only каталог для previous-release
recovery. `lab-parser`
получает байты только на время внутреннего HTTP request, извлекает текст/OCR и
возвращает его `ai-worker`; оригиналы parser не монтирует. Codex extraction
`amigo-lab-extraction-v1` публикуется как `unverified`. Пользователь сверяет
текст/страницу, исправляет строки и подтверждает документ. Диапазон бланка
приоритетнее versioned catalog; статус считает backend. Явно подписанная OCR-
дата имеет приоритет над model date; idempotent bootstrap исправляет тот же
однозначный случай в существующих строках, не меняя ручные corrections. Страница
истории показателя читает справочную карточку с назначением показателя и
возможными категориями причин отклонений. Для неизвестного показателя
`ai-worker` до завершения нового импорта вызывает изолированный
`amigo-lab-analyte-guide-v2` контракт локального Codex и сохраняет статью в
PostgreSQL. После обновления batched queue (до 5 показателей за вызов, не более
трёх попыток на версию контракта) постепенно заполняет уже импортированные
неизвестные показатели. После каждой пачки worker предлагает выполнение
assistant/analysis очередям, поэтому backfill их не блокирует; GET никогда не
запускает inference. Очередь показывает
позицию, этап и прогресс через SSE; новый batch можно добавлять во время
обработки предыдущего. Удаление документа удаляет его БД-историю и конкретный
database original.

Раздел «Исследования» использует ту же очередь для отчётов
УЗИ/МРТ/КТ/рентген/ЭКГ/other; DICOM отклоняется. Оригинал, findings и conclusion
хранятся в PostgreSQL, доступны view/edit/confirm/retry/delete. Очевидные строки
с ФИО, датой рождения, адресом и другими идентификаторами исключаются из
структурированных study facts.

Один persistent chat использует `amigo-health-chat-v2`, полную
структурированную историю здоровья, анализов и исследований, последние 12
сообщений и детерминированную сводку старых сообщений. Originals, filenames,
study titles и OCR pages в assistant context не попадают. Gateway запускает
ephemeral `codex app-server` по stdio с `turn/start.outputSchema`; draft-сегменты
попадают в PostgreSQL/SSE, а финал публикуется только после validation. Raw
events, reasoning, prompts и validation details не сохраняются и не логируются.
Ассистенту разрешены evidence-backed hypotheses, альтернативы и способы их
различить; validator по-прежнему отклоняет definitive diagnosis, treatment,
medication/dosage instructions и fixed calorie target.
Официальный turn/event contract:
[Codex app-server](https://learn.chatgpt.com/docs/app-server#turns).

## Android APK, pairing и backfill

1. Установить проверенный signed Android `1.3.3` (`versionCode 13`) —
   [`Amigo-1.3.3.apk`](https://github.com/tolstik/amigo/releases/download/v5.1.4/Amigo-1.3.3.apk)
   из release [`v5.1.4`](https://github.com/tolstik/amigo/releases/tag/v5.1.4) —
   или обновить `1.3.2`:

   ```bash
   adb install -r <PATH_TO_SIGNED_APK>
   ```

   SHA-256 asset `Amigo-1.3.3.apk`:
   `6f4156d6cf24df27b95b6cc53b26f83bd965c266da144e04f6feb3ccb884f156`.
   Upgrade через `adb install -r` сохраняет pairing state, non-exportable
   Android Keystore key, выбранный Mi Fitness origin и resumable sync cursors.
   При подтверждении Xiaomi по email системная клавиатура должна открываться
   для поля кода, а переход в почтовое приложение и возврат не должны сбрасывать
   текущую форму или cookies из изолированного auth-процесса. Отмена или успешное
   завершение входа очищают временное WebView-состояние.

2. Оставить server `https://amigo.tolstik.ru`, задать нечувствительную метку
   телефона и зарегистрировать устройство. Приложение создаст
   non-exportable P-256 key и покажет временный pairing code.
3. На сервере сверить label/code с экраном нужного телефона. Сначала
   показать только pending metadata, затем одобрить ровно один code:

   ```bash
   release_sha="$(sudo cat /var/lib/amigo/current-release)"
   sudo env AMIGO_IMAGE_TAG="${release_sha}" \
     docker compose --file /srv/amigo/compose.yaml \
     --env-file /srv/amigo/.env exec -T ingest \
     python -m app.health_cli list-pending
   sudo env AMIGO_IMAGE_TAG="${release_sha}" \
     docker compose --file /srv/amigo/compose.yaml \
     --env-file /srv/amigo/.env exec -T ingest \
     python -m app.health_cli approve-device <PAIRING_CODE>
   unset release_sha
   ```

   CLI не печатает public key, signature или записи здоровья. Не одобрять
   неожиданные запросы.
4. На телефоне нажать «Проверить», затем «Войти в Xiaomi». Пароль вводится
   только на HTTPS-странице Xiaomi в отдельном WebView process. Не копировать и
   не выводить cookies/tokens; сервер получает только нормализованные signed
   snapshots. Первые три дня всех десяти типов должны завершиться, а cloud
   heart rate — быть новее сохранённого Health Connect watermark; иначе источник
   остаётся `pending` и cutover данных не считается подтверждённым.
5. Оставить read-only Health Connect как rollback history: выдать history и
   background access, нажать «Найти источники» и явно выбрать Mi Fitness. Не
   разрешать location/routes. Эти записи продолжают загружаться, но finalized
   Xiaomi coverage подавляет их только на совпадающем type/range.
6. Нажать «Синхронизировать сейчас» и дождаться успешного status. Cloud backfill
   идёт 30-дневными окнами до `2000-01-01`, продолжение ставится через минуту;
   hourly job сверяет 3 дня, weekly — 30 дней. Проверить «Активность» и
   «Восстановление», включая hourly min/avg/max pulse без raw samples. Повторный
   `auth_required` создаёт одно Telegram-уведомление на эпизод; явное
   «Отключить» возвращает Health Connect без удаления его истории.
7. Во вкладке «Синхронизация» нажать «Проверить обновление». Metadata и APK
   доступны только в authenticated session. Клиент обязан сверить exact-origin
   URL, size, SHA-256, package, более высокий versionCode и текущий signing
   certificate; установка начинается только после системного подтверждения.

Вкладка «Дашборд» использует тот же local account и 90-дневную session, что и
браузер. Login/logout не меняет Android pairing. Проверить, что verified App
Link на `https://amigo.tolstik.ru/amigo/labs` открывает приложение; неизвестный
route/origin блокируется. Upload выбирает до 25 PDF/JPG/PNG/HEIC/HEIF по 20 МиБ
через Files/Photos. CSV и загруженный оригинал сохраняются через системный
«Сохранить как»; приложение не следует redirect и не передаёт cookie вне
allowlisted same-origin routes. После 30 секунд в фоне WebView автоматически
reload показывает свежие данные. Камера и offline medical cache намеренно
отсутствуют; документы просматриваются защищённым web viewer.

Кнопка «Сбросить сопряжение» сначала удаляет и немедленно создаёт новый
non-exportable P-256 key, а уже затем очищает pairing и sync cursors. После неё
нужны новая регистрация и явное одобрение нового code; старый server device не
переиспользуется.

Каждый batch подписан ECDSA/SHA-256 по сырому JSON, timestamp, nonce и batch ID.
Сервер проверяет replay/idempotency, data-origin pinning, allowlist и размер.
Client формирует deterministic canonical batches строго меньше 1 MiB, не более
2 000 records и 5 000 heart-rate samples в одном record; snapshot с records не
длиннее 30 дней. Только подтверждённый Health Connect пустой диапазон может
передаваться одной final page без records и ограничен 50 годами. После каждого
непустого окна client снова ищет следующую запись и одним snapshot пропускает
многолетний пробел. Начала upload разделены минимум 1 100 ms, то есть остаются ниже
origin rate limit 60 requests/minute. При HTTP-ошибке cursor/token не сдвигается,
и следующий запуск повторяет тот же batch ID/body. Не исправлять БД вручную:
client/server idempotency и snapshot reconciliation уже предусмотрены.
Xiaomi Cloud использует отдельные exact routes
`/amigo-ingest/v1/mi-fitness/batches` и `/amigo-ingest/v1/mi-fitness/status`
с той же P-256 signature/replay-защитой. Partial pages не видны аналитике;
final page атомарно публикует coverage, а confirmed-empty interval также
подавляет Health Connect только для совпадающего metric/range. Xiaomi
credentials, cookies, account ID и provider JSON остаются на телефоне в
Android-Keystore AES-GCM storage; сервер и логи их не получают. При rollback
старый runtime игнорирует additive `mi_fitness_*` tables, а Android продолжает
обновлять Health Connect rollback history.
Health Connect step record принимается до документированного значения
`1 000 000` включительно. При отклонении сервер пишет только стабильный
`detail.code`, без payload, headers, device ID, batch ID и validation details;
Android `1.3.3` показывает только allowlisted code рядом с HTTP status и не
отражает произвольное тело ответа. Для freshness watermark он предпочитает
Health Connect `lastModifiedTime`, а ошибка одного record type не отменяет
попытку синхронизации следующих типов, включая sleep. Сервер совместимости
принимает watermark не более чем на сутки вперед и сохраняет его не позднее
момента приема, поэтому установленный `1.2.2` также не застревает на текущем
интервале Mi Fitness до обновления приложения.
Каждая Xiaomi-синхронизация в `1.3.3` сначала повторно подтверждает на сервере
signed source status и только затем читает cloud или загружает batches. Это
восстанавливает прерванное первоначальное включение без очистки зашифрованной
Xiaomi-сессии, pairing, ключа и cursors; allowlisted `mi_fitness_not_enabled`
показывается явно, а не как общий `invalid_cloud_response`.
После обновления до `1.3.2` или новее приложение сохраняет поведение `1.2.4`: один раз удаляет только changes token и
snapshot state обычного `heart_rate`, затем выполняет его полный reconcile.
Pairing, non-exportable key, выбранный origin и состояния всех остальных типов
не меняются. На сервере отсутствие записей для типа при свежей принятой пустой
snapshot/changes page означает, что Health Connect не предоставил этот тип, а
не что очередь синхронизации зависла.

## Telegram schedule

- `08:45 Europe/Moscow` — scheduled AI snapshot перед сводкой.
- `09:00 Europe/Moscow` во вторник–воскресенье — daily report.
- `09:00 Europe/Moscow` в понедельник — weekly report вместо daily: отдельно
  картинка и полный текст.
- Новые Withings weight/pressure events сохраняют немедленные уведомления.
- Расписание живёт в `worker` и не зависит от host cron. Outbox event keys
  обеспечивают idempotency.
- Если AI-кэш не `ready`, Telegram явно пишет, что отправлены только
  факты, и не подставляет шаблонный совет.
- Следующий daily/weekly digest добавляет новые лабораторные название, значение,
  единицу, диапазон, статус и отметку проверки, разбивая сообщения без усечения.
  Filenames, originals, OCR и chat в Telegram не отправляются.

## Проверка production

Автоматическая проверка не отправляет Telegram-сообщений и не создаёт health
records:

```bash
sudo bash /srv/amigo/deploy/verify-production.sh
```

Она проверяет:

- health всех семи services и immutable image SHA;
- PostgreSQL, точные secret mounts и отсутствие Docker secrets/DB у gateway/parser;
- успешный `withings-incremental` `JobRun`, начатый не раньше `StartedAt`
  текущего worker container; privacy-safe запрос читает только job name,
  status и timestamps, но не `details` или provider payload;
- loopback binds `18181`/`18182` и отсутствие published `8090`/`8085`;
- pinned Codex hash на host и в gateway container;
- direct health, origin nginx, public TLS, relative `308`, defensive headers и
  immutable JavaScript/CSS assets;
- exact public `/.well-known/assetlinks.json`, package
  `ru.tolstik.amigo.sync` и release signing certificate, а также origin `405`
  и public `403`/`405` для POST;
- explicit named-capture upstream URI для dynamic labs/studies/assistant routes без
  capture-unsafe generic rewrite;
- explicit `429` для каждого managed rate-limit; upload допускает bounded burst
  из 25 запросов при сохранении лимита 30 запросов в минуту;
- public login shell и method-correct `401` для
  health JSON/CSV/labs/studies/updater/assistant
  без session;
- short-lived root-only verification session, authenticated overview/activity/
  recovery/AI-v4/labs/studies/updater/assistant/CSV, exact Origin+CSRF,
  безопасное отклонение пустого upload и no-buffer assistant/lab/study SSE без
  создания chat turn;
- database-owned originals после проверенного backfill, отсутствие implausible
  laboratory dates после deterministic repair, подтверждённый прогресс
  ограниченного фонового backfill статей неизвестных analytes без terminal
  failure текущего контракта и analyte guide contract,
  root-only dual-write lab storage, web RW/ai-worker RO/parser no-mount и
  внутренний parser health;
- root-only signed APK `1.3.3`, точный hash, read-only web mount,
  authenticated metadata и повторно скачанный APK с тем же hash;
- все три точных signed ingest route: unsigned empty Health Connect/Xiaomi
  batch и Xiaomi status отклоняются до создания записи;
- закрытые health endpoints, legacy assets и обе cron-строки.

Дополнительно оператор проверяет desktop/mobile/WebView, verified App Links,
SAF upload/download, полный Health Connect backfill, один следующий sync-цикл,
отсутствие дублей, табличные альтернативы графиков
и то, что pressure/heart/SpO2/VO2 max остаются описательными в детерминированных
экранах. AI-рекомендации должны идти до наблюдений, содержать action,
cadence/review period и evidence, не содержать диагноз, лечение,
лекарства/дозировки или фиксированные калории. Полный checklist — в
[production-verification.md](production-verification.md).

## Degraded mode

- **AI gateway/auth недоступен.** Не откатывать здоровые `web`, `worker`, `ingest`
  и `db`: детерминированная аналитика и импорт продолжаются. UI
  покажет `stale`/`pending`/`unavailable`, Telegram отправит только факты.
  Проверить gateway health, pinned hash и логи без payload; при auth-ошибке
  выполнить auth refresh и synthetic smoke.
- **Локальная авторизация недоступна.** Не открывать dashboard через старый
  public release. Проверить DB/session миграции или сменить пароль root-only CLI;
  recovery на release без auth обязан оставить `/amigo/` в maintenance `503`.
- **Lab parser/OCR недоступен.** Измерения и dashboard продолжают работать;
  document остаётся queued/failed с безопасным error code. Проверить только
  parser health/resources и повторить документ из UI, не копируя OCR/payload в лог.
- **Lab extraction завершился `timeout` на 40%.** Новый worker отправляет не
  более 3 000 OCR-символов за вызов и делит только timed-out chunk максимум два
  раза. После установки исправленного release проверить originals и повторить
  только точную terminal-сигнатуру командой
  `python -m app.cli lab-retry-extraction-timeouts` внутри `ai-worker`. Команда
  возвращает только счётчики eligible/requeued/skipped; `skipped > 0` даёт код
  `75`. Не переводить другие failed jobs в pending вручную.
- **Health Connect ingest недоступен.** Withings и уже импортированные данные
  продолжают работать. Android WorkManager повторит подписанные idempotent
  batches; после восстановления запустить manual sync/backfill. Не импортировать
  payload вручную.
- **Mi Fitness не пишет отдельную метрику.** Это нормальная вариативность:
  UI показывает только фактически доступные HRV/SpO2/VO2/sleep/workout поля.
- **Withings worker недоступен.** Не включать legacy cron, пока Amigo worker может
  писать/ротировать OAuth. Для возврата legacy collector выполнить только
  явный documented disaster fallback с token handback.

Логи и диагностический output не должны содержать health payload, prompt, generated
analysis, auth, headers или токены. Для ingest rejection допустим только
стабильный `detail.code`, без device ID, batch ID и validation details.

## Previous-release recovery

При ошибке после начала cutover `deploy.sh` вызывает эту операцию автоматически.
Для ручного повтора используется только конкретный snapshot нового формата;
`latest`, glob и неявный выбор запрещены:

```bash
sudo bash /srv/amigo/deploy/restore-previous-release.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Recovery проверяет текущие managed route и disabled legacy cron, записанные
previous SHA/application и PostgreSQL image IDs/Compose hash, останавливает
candidate, оставляет legacy Withings cron выключенным, запускает предыдущие
`web`, `worker`, `ingest`, `ai-worker`, `ai-gateway`, optional `lab-parser` и
PostgreSQL на сохранённом volume и возвращает точное предыдущее состояние APK.
Для auth-capable previous release возвращаются
его exact managed snippets и shell `200`. Для release без auth ставится текущий
maintenance snippet: `/amigo/` стабилизируется на `503`, unsigned ingest всё ещё
точно отклоняется его backend-контрактом. Shared `my.conf` вне markers сохраняется;
maintenance status завершается в собственном named nginx handler и не зависит
от общего server-level `error_page` или наличия shared `50x.html`; локальное
неиспользуемое правило отключает наследование shared error handlers;
worker ждёт новый minute run-key и успешный incremental run. Окончательная
production verification остаётся обязательной.
Повторный cutover из такого fail-closed состояния разрешён только когда
установленные locations snippet и HTTP config байт-в-байт совпадают с versioned
maintenance snippet и HTTP config нового candidate; произвольная
nginx-конфигурация backup не принимается.
Production checkout остаётся на candidate commit; фактический runtime определяет
`/var/lib/amigo/current-release`. Автоматического `pg_restore` нет.

## Возврат из уже активного legacy state

Если прежний disaster fallback уже остановил Compose, снял managed route и включил
legacy cron, сначала безопасно вернуть recorded Amigo release:

```bash
sudo bash /srv/amigo/deploy/takeover-from-legacy.sh \
  --resume-recorded-release \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Для текущего `/srv/amigo-rollbacks/20260820T055833Z` поддерживается проверенный
старый формат snapshot: recorded SHA берётся из root-only release marker,
Compose допускается только после rollback-compatibility gate, а установленный
image сверяется по OCI revision и принудительно пересоздаёт application
containers. Новые snapshots используют полный release envelope.

По умолчанию takeover требует HTTP 200 от legacy origin. Если legacy отвечает
конкретным HTTP-статусом, но уже неисправен, требуется отдельное явное разрешение:

```bash
sudo bash /srv/amigo/deploy/takeover-from-legacy.sh \
  --resume-recorded-release \
  --allow-unhealthy-legacy-origin \
  /srv/amigo-rollbacks/20260820T055833Z
```

Этот режим не считает legacy пригодным для failure fallback: при ошибке его
Withings cron не включается, started Amigo route/runtime/db не останавливаются.
Отсутствие реального HTTP-ответа override не разрешает.

Команда отключает только exact legacy collector и ждёт целую cron-границу плюс
подтверждённое отсутствие уже запущенного `get_withings.php`. Затем она читает
текущую OAuth-пару прямо из live MariaDB во временный root-only `/run` handoff,
сверяет Withings client credentials, шифрует пару в существующую PostgreSQL row
и выполняет notification-suppressed sync. Старые `secrets/withings_*token` для
takeover не используются. До включения managed route свежая пара возвращается
также в выключенный legacy fallback. При ошибке takeover Amigo collectors
останавливаются, актуальная пара возвращается legacy, а Amigo web/db
останавливаются только после подтверждённого возврата route. Если route или
token handback невозможно подтвердить, collectors остаются выключенными
fail-closed и обслуживающий активный route backend не останавливается. После
успешного takeover обычный deploy создаст snapshot нового формата.

## Explicit legacy disaster fallback

Legacy включается только вручную с обязательным флагом `--to-legacy` и конкретным
проверенным snapshot:

```bash
sudo bash /srv/amigo/deploy/rollback.sh --to-legacy \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Disaster fallback:

1. Останавливает `worker`, `ai-worker`, `ingest`, `ai-gateway` и `lab-parser`.
2. Убирает только managed nginx route и проверяет HTTP 200 legacy origin.
3. Передаёт текущую OAuth-пару в ровно одну legacy MariaDB token row через
   одноразовый root-only bind без stdout, затем удаляет handoff files.
4. Включает только точную legacy Withings cron-строку.
5. Останавливает `web` и `db`, но не удаляет containers, images, PostgreSQL volume,
   Codex state, snapshot, APK, `/srv/www/amigo`, MariaDB и общий Telegram cron.

Android-приложение при rollback может остаться установленным: ingest закрыт,
а локальная Health Connect история сохранится для будущего resumable backfill.
Legacy fallback не восстанавливает PostgreSQL dump автоматически; volume сохраняется,
а dump служит для отдельного аварийного восстановления.

Для повторного cutover сначала выполнить takeover выше, убедиться, что recorded
Amigo release снова владеет OAuth/collection, затем вернуть чистый candidate и
запустить `deploy.sh`; не применять `down -v`.

## Documentation checkpoint

После каждого production deploy или изменения runtime-контракта выполнить:

```bash
sudo bash /srv/amigo/deploy/checkpoint.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Скрипт повторяет verification и записывает production URL, UTC/MSK время, Git SHA,
image refs/IDs всех семи services, SHA-256 Compose/nginx/Codex, результаты
проверок и точный rollback path. Он не читает и не записывает секреты.
Штатный `deploy.sh` вызывает checkpoint с внутренним флагом
`--verification-passed` сразу после полного успешного прогона и не повторяет
тот же mutation/upload-набор второй раз; при отдельном ручном вызове без флага
checkpoint по-прежнему самостоятельно выполняет полную verification.
Checkpoint атомарно создаёт локальный documentation-only commit и сохраняет его
под `refs/amigo/checkpoints/GIT_SHA`, поэтому production checkout после deploy
снова чист и следующий guarded release не блокируется на старых Markdown-
изменениях. Факты из этого commit всё равно нужно перенести отдельным commit в
канонический репозиторий до сообщения о завершении; rebuild runtime для этого не
нужен. Source of truth для runtime SHA остаётся `/var/lib/amigo/current-release`,
а не HEAD локального documentation commit.

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-24T18:09:56Z` (`2026-08-24 21:09:56 MSK`)
- Git SHA: `b85fbe41b307247414c4857e41cc4a7c949bae3d`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260824T180550Z`
- Installed config SHA-256: Compose `4794466f10092109cb764498e2805794bb1a8b5f2724e8ae90da506d7ab39914`; nginx locations `fa20983b63b276d5440b9c9ca2c3aa7211bf441c9dda51656e9b948a55032475`; nginx rate limit `4b2d524cb9cedb0059671b601d983f6f2f73ec1f011054c443c6cd7dafab0104`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Release access SHA-256: wrapper `721eabf3e79806d3b4ffecaaba7d2105632016ba1e4c90ae99f41af361818527`; sudoers policy `c02cd113d07deac89aaac689777fcdb89deafb3f011135a17d04428d25dee8ea`.
- Verification: all seven Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; database-owned originals, repaired laboratory dates, analyte guides, signed Android updater/APK, laboratory and study queues, assistant/queue SSE, authentication, exact Origin/CSRF, authenticated API/CSV/upload checks, root-only laboratory storage, parser/gateway isolation and unpublished ports, container secret boundaries, pinned Codex hash, fixed `gpt-5.6-sol`/`amigo-health-v4` gateway health, root-owned least-privilege release access, signed-ingest rejection, origin proxy, HTTPS login shell, hidden health routes, immutable frontend assets, cron isolation, previous-release auth-floor recovery assets, and the explicit legacy disaster-fallback guard passed.
- Installed image references and IDs:

- `web`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `worker`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `ingest`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `ai-worker`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `ai-gateway`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `lab-parser`: `amigo:b85fbe41b307247414c4857e41cc4a7c949bae3d` (`sha256:48ca11856745b859031e6cf5f78a00ece34bb8f938d33a73abab93b03e370904`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Previous-release recovery command: `sudo /srv/amigo/deploy/restore-previous-release.sh /srv/amigo-rollbacks/20260824T180550Z`
- Legacy disaster fallback command: `sudo /srv/amigo/deploy/rollback.sh --to-legacy /srv/amigo-rollbacks/20260824T180550Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
