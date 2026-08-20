# Amigo v3: production runbook

Этот документ описывает безопасное развёртывание Amigo v3 на
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
- Prompt contract `amigo-health-v3` требует для каждой рекомендации конкретное
  действие, cadence или review period и ссылки на существующие evidence keys.
  На overview и в Telegram рекомендации идут раньше общих наблюдений.
- Ни дашборд, ни Telegram не показывают шаблонную подмену AI-текста.
  При недоступном AI графики и факты остаются рабочими.
- Исторические AI jobs/results сохраняются в PostgreSQL, но authenticated cache и
  worker используют только текущую пару model/prompt. Несовместимые pending или
  просроченные jobs становятся `superseded`; явный deployment `ai-enqueue`
  может повторно поставить active failed/superseded same-key job без ожидания
  backoff, обычный background enqueue terminal history не оживляет.
- Dashboard, health API, CSV, laboratory archive/originals и assistant закрыты
  одним локальным Argon2id-аккаунтом: opaque server sessions живут 90 дней,
  cookies имеют `Secure`/`SameSite=Strict`, мутации требуют exact Origin и CSRF.
  Signed Android ingest остаётся отдельным. Health Connect показывается только
  как агрегаты без device/pairing metadata, signatures, nonces, raw provider
  payload и raw heart-rate samples.
- Оригиналы анализов находятся только в root-owned
  `/srv/amigo/data/lab-files`: каталог `0700`, файлы `0600`. `web` монтирует его
  read-write, `ai-worker` read-only, `lab-parser` не монтирует вообще. Parser
  работает non-root/read-only без внешней сети; PDF/JPG/PNG/HEIC ограничены 20
  МиБ, PDF — 50 страницами.
- Withings — единственный источник веса, состава тела и давления. Mi Fitness
  передаёт только allowlisted activity/recovery records через Health Connect;
  weight, pressure, location и exercise routes из Health Connect не принимаются.
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
8. Для Android `1.1.0` (`versionCode 4`) использовать signed
   `Amigo-1.1.0.apk` из GitHub release `v4.0.0` и сверить SHA-256
   `6b950bc3c6e5ba58709830d3c25fcc04d25f16c86c748379298b8a423176984d`.
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

`deploy.sh` всегда запускает backup до сборки и cutover. Отдельная проверка:

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
2. Проверенный legacy/PostgreSQL snapshot и подготовка pinned Codex runtime.
3. После создания защищённых rollback image tags включается automatic recovery,
   затем выполняются pull PostgreSQL image и сборка одного Git-SHA image для
   шести application services. Повторная сборка того же SHA запрещена.
4. Остановка уже работающих `worker`, `ai-worker`, `ingest` и `lab-parser` (`ai-worker`
   получает до 120 секунд на штатное завершение), запуск `db`,
   migrations и bootstrap. Если локальный аккаунт ещё не создан, deploy скрыто
   запрашивает пароль дважды и передаёт одну строку в root-only CLI через stdin.
   Затем, только в выбранном режиме, выполняется Telegram smoke.
5. Отключение только точной legacy Withings cron-строки, full sync без
   historical notifications, немедленный OAuth token handback в одну legacy
   MariaDB строку и импорт legacy-only весов из root-only TSV.
6. Запуск `web` без workers и direct health на `127.0.0.1:18181`.
7. Запуск изолированных `ai-gateway` и `lab-parser`; synthetic smoke через
   `ai-worker` последовательно проверяет live-контракты analysis, laboratory
   extraction и assistant turn, включая auth, sandbox, model, strict JSON schema
   и streaming completion, без реальных health data или персонального контекста.
8. При всё ещё остановленном persistent `ai-worker` один
   `ai-retry-current --worker-stopped` готовит exact current job. `ai-ready`
   принимает только `0` (готово) или `75` (ещё не готово); любой другой exit
   fatal. Выполняется не более четырёх foreground one-shot workers, и только
   между неуспешными попытками 1–3 вызывается `ai-enqueue` для снятия backoff.
   Тройной gateway smoke/retry не повторяется.
9. Запуск `ingest`, затем атомарная установка nginx route. Общий prefix
   разрешает только `GET`/`HEAD`/`OPTIONS`; exact auth/profile/labs/assistant
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

Контракт `amigo-health-v3` допускает устойчивые рекомендации по питанию,
активности, сну и измерениям, но каждый пункт должен содержать конкретное
действие, периодичность или срок пересмотра и фактические evidence keys.
Pressure/heart/SpO2/VO2 evidence разрешено только для repeat-measurement,
logging или обсуждения устойчивого паттерна с врачом; validator отклоняет
диагноз, лечение, назначение или изменение лекарств/дозировки и фиксированные
калории. Если хотя бы одна такая медицинская метрика присутствует, validator
требует минимум одну bounded medical/measurement рекомендацию. В Telegram и
overview рекомендации показываются до наблюдений.

## Локальный аккаунт, лаборатория и assistant

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

До первой загрузки или вопроса владелец заполняет дату рождения/биологический
пол в `/amigo/profile` и подтверждает disclosure `amigo-ai-data-v1`: Codex CLI
запускается на origin, но полный извлечённый текст и вопросы передаются в OpenAI
inference. Отзыв consent запрещает новые uploads/turns, но не удаляет историю.

Uploads принимают PDF/JPG/PNG/HEIC до 20 МиБ и PDF до 50 страниц. `lab-parser`
получает байты только на время внутреннего HTTP request, извлекает текст/OCR и
возвращает его `ai-worker`; оригиналы parser не монтирует. Codex extraction
`amigo-lab-extraction-v1` публикуется как `unverified`. Пользователь сверяет
текст/страницу, исправляет строки и подтверждает документ. Диапазон бланка
приоритетнее versioned catalog; статус считает backend. Удаление документа
удаляет его БД-историю и конкретный оригинал.

Один persistent chat использует `amigo-health-chat-v1`, структурированную
историю, последние 12 сообщений, детерминированную сводку старых сообщений и
до восьми OCR chunks, найденных локальным PostgreSQL FTS. Gateway запускает
ephemeral `codex app-server` по stdio с `turn/start.outputSchema`; draft-сегменты
попадают в PostgreSQL/SSE, а финал публикуется только после validation. Raw
events, reasoning, prompts и validation details не сохраняются и не логируются.
Официальный turn/event contract:
[Codex app-server](https://learn.chatgpt.com/docs/app-server#turns).

## Android APK, pairing и backfill

1. Установить проверенный signed Android `1.1.0` (`versionCode 4`) из release
   `v4.0.0` или обновить `1.0.2`:

   ```bash
   adb install -r <PATH_TO_SIGNED_APK>
   ```

   SHA-256 asset `Amigo-1.1.0.apk`:
   `6b950bc3c6e5ba58709830d3c25fcc04d25f16c86c748379298b8a423176984d`.
   Upgrade через `adb install -r` сохраняет pairing state, non-exportable
   Android Keystore key, выбранный Mi Fitness origin и resumable sync cursors.

2. В Amigo открыть вкладку «Синхронизация» и выдать read-only Health Connect
   permissions, включая history и background access, если они доступны.
   Нажать «Найти источники» и
   явно выбрать Mi Fitness. Не разрешать и не добавлять location/routes.
3. Оставить server `https://amigo.tolstik.ru`, задать нечувствительную метку
   телефона и зарегистрировать устройство. Приложение создаст
   non-exportable P-256 key и покажет временный pairing code.
4. На сервере сверить label/code с экраном нужного телефона. Снача
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
5. На телефоне нажать «Проверить» и «Синхронизировать сейчас». Полный
   backfill возобновляем и может потребовать несколько запусков. «Вся
   история» означает все записи, которые Health Connect фактически разрешает
   читать; без extended-history permission безопасный fallback начинается за 30 дней
   до первого выданного permission.
6. Дождаться успешного status в приложении и проверить «Активность»/
   «Восстановление». WorkManager продолжит best-effort sync примерно раз в час.

Вкладка «Дашборд» использует тот же local account и 90-дневную session, что и
браузер. Login/logout не меняет Android pairing. Проверить, что verified App
Link на `https://amigo.tolstik.ru/amigo/labs` открывает приложение; неизвестный
route/origin блокируется. Upload выбирает один PDF/JPG/PNG/HEIC/HEIF до 20 МиБ
через Files/Photos. CSV и лабораторный оригинал сохраняются через системный
«Сохранить как»; приложение не следует redirect и не передаёт cookie вне
allowlisted same-origin routes. Камера, встроенный viewer и offline medical
cache намеренно отсутствуют.

Кнопка «Сбросить сопряжение» сначала удаляет и немедленно создаёт новый
non-exportable P-256 key, а уже затем очищает pairing и sync cursors. После неё
нужны новая регистрация и явное одобрение нового code; старый server device не
переиспользуется.

Каждый batch подписан ECDSA/SHA-256 по сырому JSON, timestamp, nonce и batch ID.
Сервер проверяет replay/idempotency, data-origin pinning, allowlist и размер.
Client формирует deterministic canonical batches строго меньше 1 MiB, не более
2 000 records и 5 000 heart-rate samples в одном record; snapshot windows не
длиннее 30 дней. Начала upload разделены минимум 1 100 ms, то есть остаются ниже
origin rate limit 60 requests/minute. При HTTP-ошибке cursor/token не сдвигается,
и следующий запуск повторяет тот же batch ID/body. Не исправлять БД вручную:
client/server idempotency и snapshot reconciliation уже предусмотрены.
Health Connect step record принимается до документированного значения
`1 000 000` включительно. При отклонении сервер пишет только стабильный
`detail.code`, без payload, headers, device ID, batch ID и validation details;
Android `1.1.0` показывает только allowlisted code рядом с HTTP status и не
отражает произвольное тело ответа.

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
  `ru.tolstik.amigo.sync` и release signing certificate;
- explicit named-capture upstream URI для dynamic labs/assistant routes без
  capture-unsafe generic rewrite;
- explicit `429` для каждого managed rate-limit; upload допускает bounded burst
  из пяти запросов при сохранении лимита 6 запросов в минуту;
- public login shell и method-correct `401` для health JSON/CSV/labs/assistant
  без session;
- short-lived root-only verification session, authenticated overview/activity/
  recovery/AI-v3/labs/assistant/CSV, exact Origin+CSRF, безопасное отклонение
  пустого upload и no-buffer SSE без создания chat turn;
- root-only lab storage, web RW/ai-worker RO/parser no-mount и внутренний parser health;
- точный ingest route: unsigned empty batch отклоняется до создания записи;
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
PostgreSQL на сохранённом volume. Для auth-capable previous release возвращаются
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
Изменённые `AGENTS.md`, runbook и `production-checkpoint.md` нужно закоммитить в
канонический репозиторий до сообщения о завершении. Documentation-only commit
можно перенести в production checkout без rebuild; source of truth для runtime SHA остаётся
`/var/lib/amigo/current-release`.

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-20T14:53:36Z` (`2026-08-20 17:53:36 MSK`)
- Git SHA: `004d6423855f235f34e46a1643cc1de99e21e07e`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260820T144819Z`
- Installed config SHA-256: Compose `235e56984f07d87e60b001690f83e214e1806c42903748d90992aa6f5e628f7a`; nginx locations `905e0fe5bdafd4c94aac3e08361d47f2c571dc90807a837ba0dbe9c7815dbf9a`; nginx rate limit `7d1edd7c5d03f142c8a576ddea0e4a81cfecd849e7426dd1837e4139dcbcb649`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Release access SHA-256: wrapper `721eabf3e79806d3b4ffecaaba7d2105632016ba1e4c90ae99f41af361818527`; sudoers policy `c02cd113d07deac89aaac689777fcdb89deafb3f011135a17d04428d25dee8ea`.
- Verification: all seven Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; authentication, exact Origin/CSRF, short-lived authenticated API/CSV/upload/SSE checks, root-only laboratory storage, parser/gateway isolation and unpublished ports, container secret boundaries, pinned Codex hash, fixed `gpt-5.6-sol`/`amigo-health-v3` gateway health, root-owned least-privilege release access, signed-ingest rejection, origin proxy, HTTPS login shell, hidden health routes, immutable frontend assets, cron isolation, previous-release auth-floor recovery assets, and the explicit legacy disaster-fallback guard passed.
- Installed image references and IDs:

- `web`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `worker`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `ingest`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `ai-worker`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `ai-gateway`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `lab-parser`: `amigo:004d6423855f235f34e46a1643cc1de99e21e07e` (`sha256:4c96fd4e23376275dfb86a7ef34fcd4640dfb4c8c965e301974c8f8e49086642`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Previous-release recovery command: `sudo /srv/amigo/deploy/restore-previous-release.sh /srv/amigo-rollbacks/20260820T144819Z`
- Legacy disaster fallback command: `sudo /srv/amigo/deploy/rollback.sh --to-legacy /srv/amigo-rollbacks/20260820T144819Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
