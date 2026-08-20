# Amigo v3: production runbook

Этот документ описывает безопасное развёртывание Amigo v3 на
`192.168.31.3`. Все команды изменения состояния выполняются на
origin-сервере от `root`. Пароли, OAuth-токены, Telegram-токены,
chat ID, Codex `auth.json` и значения из медицинских payload не копируются
в команды, логи или Markdown.

## Неизменяемые эксплуатационные условия

- Production URL: `https://amigo.tolstik.ru/amigo/`.
- Проект расположен в `/srv/amigo`. Compose содержит ровно шесть
  сервисов: `web`, `worker`, `db`, `ingest`, `ai-worker`, `ai-gateway`.
- На host публикуются только `web` на `127.0.0.1:18181` и `ingest` на
  `127.0.0.1:18182`. `ai-gateway:8090` доступен только во внутренней
  Docker-сети и не имеет host listener.
- Точный SHA запущенного image хранится в root-only
  `/var/lib/amigo/current-release`. Verification использует его, а не HEAD
  checkout после documentation-checkpoint commit.
- `web`, `ingest` и `ai-worker` получают только PostgreSQL secret.
  `worker` получает ровно восемь DB/Withings/Fernet/Telegram secret files.
  `ai-gateway` не получает Docker secrets и доступ к БД.
- Детерминированный код считает KPI, план, тренд, прогноз, выбросы,
  личную базу и корреляции. В Codex уходят только минимизированные
  факты и ограниченные дневные ряды без имени, device ID, raw provider
  payload, GPS/location и учётных данных.
- В production задано `AMIGO_USER_HEIGHT_CM=176`. Snapshot передаёт этот
  identifier-free факт; BMI рассчитывается детерминированно только при наличии
  последнего доступного Withings weight, получает его `observed_on` и не
  пересчитывается моделью или из Health Connect weight.
- AI вызывается асинхронно через SHA-256-pinned Codex CLI `0.148.0`
  (`ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`),
  фиксированную модель `gpt-5.6-terra`, read-only sandbox и строгую JSON
  schema. Публичные GET только читают кэш PostgreSQL.
- Prompt contract `amigo-health-v2` требует для каждой рекомендации конкретное
  действие, cadence или review period и ссылки на существующие evidence keys.
  На overview и в Telegram рекомендации идут раньше общих наблюдений.
- Ни дашборд, ни Telegram не показывают шаблонную подмену AI-текста.
  При недоступном AI графики и факты остаются рабочими.
- Дашборд намеренно публичный и read-only. Health Connect публикуется только
  как дневные/недельные агрегаты без device/pairing metadata, signatures,
  nonces, raw provider payload и raw heart-rate samples.
- Withings — единственный источник веса, состава тела и давления. Mi Fitness
  передаёт только allowlisted activity/recovery records через Health Connect;
  weight, pressure, location и exercise routes из Health Connect не принимаются.
- Детерминированные экраны давления, сердечных метрик, SpO2 и VO2 max остаются
  описательными, без severity-цветов и app-side диагнозов. Валидированный AI
  может использовать эти метрики только для совета повторить измерения, вести
  журнал или обсудить устойчивую измеренную динамику с врачом. Запрещены
  диагноз, назначение лечения, лекарств или изменения дозировки и фиксированная
  цель по калориям.
- TLS завершается на public edge `5.35.114.76`. Внешний HTTPS проверяется
  без `-k`; локальный nginx — отдельный HTTP origin.
- Origin nginx хранит действующую конфигурацию в `/etc/nginx/conf.d/my.conf`.
  Managed include добавляется только в существующие server-блоки `tolstik.ru`.
- Legacy-приложение `/srv/www/amigo` и MariaDB `amigo` не удаляются и не
  перезаписываются.
- `/srv/cron` — общий каталог. Скрипты управляют только точной строкой
  `*/1 07-08 * * *  php /srv/cron/get_withings.php`. Строка
  `*/1 * * * *  php /srv/cron/send_telergam.php all` остаётся без изменений.
- Rollback snapshots находятся только в
  `/srv/amigo-rollbacks/<UTC timestamp>`, имеют root-only права и не удаляются
  автоматически.

## Подготовка релиза

1. Разместить чистый root-owned Git checkout нужного commit в `/srv/amigo`.
   Tracked и untracked source-файлы должны быть чисты; `.env`, `secrets/` и `data/`
   остаются ignored runtime state.
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
7. Для Android `1.0.2` (`versionCode 3`) использовать
   [`Amigo-Sync-1.0.2.apk`](https://github.com/tolstik/amigo/releases/download/v3.1.0/Amigo-Sync-1.0.2.apk)
   из GitHub release `v3.1.0` и сверить SHA-256
   `ca5612ad7a642bde582478b5eebf8edc7d83a87337cf5df71d522026cecc94fd`.
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
- crontab `tolstik` и `root`;
- полный `/etc/nginx`, `my.conf` и `nginx -T`;
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
3. Pull PostgreSQL image и сборка одного Git-SHA image для пяти application
   services.
4. Остановка уже работающих `worker`, `ai-worker` и `ingest`, запуск `db`,
   migrations, bootstrap и, только в выбранном режиме, Telegram smoke. При
   ранней ошибке все ранее работавшие сервисы запускаются снова.
5. Отключение только точной legacy Withings cron-строки, full sync без
   historical notifications, немедленный OAuth token handback в одну legacy
   MariaDB строку и импорт legacy-only весов из root-only TSV.
6. Запуск `web` без workers и direct health на `127.0.0.1:18181`.
7. Запуск изолированного `ai-gateway`; synthetic smoke через `ai-worker`
   проверяет auth, sandbox, model и JSON schema без реальных health data.
8. Постановка минимизированного production snapshot в AI queue и одноразовая
   обработка для первого кэша.
9. Запуск `ingest`, затем атомарная установка nginx route. Публичный dashboard
   разрешает только `GET`/`HEAD`/`OPTIONS`; ingest имеет точные rate-limited
   routes и body limit 1 MiB.
10. Запуск `worker` и `ai-worker`, полный verification, route-only rollback
    rehearsal и повторный verification. Проверка ждёт, пока текущий container
    `worker` завершит новый `withings-incremental` `JobRun` со статусом
    `success`; старый успешный run не засчитывается.
11. Запись `/var/lib/amigo/current-release` и обязательный
    documentation/memory checkpoint.

До nginx cutover public legacy route не меняется. Любая ошибка после передачи
сбора останавливает новые workers/services, возвращает свежую OAuth-пару legacy,
включает точную cron-строку и запускает rollback. Ошибка checkpoint после
двух verification не откатывает здоровый runtime, но deploy не завершён до
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

`codex login` — единственный интерактивный шаг. Не печатать, не читать и не
передавать `auth.json`. Prepare-скрипт проверяет JSON без вывода содержимого,
режим `0600` и pinned hash. Smoke передаёт только synthetic boolean и fail-closed
проверяет схему ответа.

AI job создаётся после новых Withings/Health Connect данных и в 08:45 перед
отчётом. Queue дедуплицирует snapshot hash, дебаунсит поток активности и
повторяет временные ошибки. Новый snapshot может оставить предыдущий
текст в status `stale` не более 24 часов. После этого status — `unavailable`,
без старого текста и без fallback. Если входные данные не менялись,
соответствующий кэш остаётся `ready`.

Контракт `amigo-health-v2` допускает устойчивые рекомендации по питанию,
активности, сну и измерениям, но каждый пункт должен содержать конкретное
действие, периодичность или срок пересмотра и фактические evidence keys.
Pressure/heart/SpO2/VO2 evidence разрешено только для repeat-measurement,
logging или обсуждения устойчивого паттерна с врачом; validator отклоняет
диагноз, лечение, назначение или изменение лекарств/дозировки и фиксированные
калории. Если хотя бы одна такая медицинская метрика присутствует, validator
требует минимум одну bounded medical/measurement рекомендацию. В Telegram и
overview рекомендации показываются до наблюдений.

## Android APK, pairing и backfill

1. Установить проверенный signed Android `1.0.2` (`versionCode 3`) из release
   `v3.1.0` или обновить предыдущую версию:

   ```bash
   adb install -r <PATH_TO_SIGNED_APK>
   ```

   SHA-256 asset `Amigo-Sync-1.0.2.apk`:
   `ca5612ad7a642bde582478b5eebf8edc7d83a87337cf5df71d522026cecc94fd`.
   Upgrade через `adb install -r` сохраняет pairing state, non-exportable
   Android Keystore key, выбранный Mi Fitness origin и resumable sync cursors.

2. В Amigo Sync выдать read-only Health Connect permissions, включая history и
   background access, если они доступны. Нажать «Найти источники» и
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
Android `1.0.2` показывает только allowlisted code рядом с HTTP status и не
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

## Проверка production

Автоматическая проверка не отправляет Telegram-сообщений и не создаёт health
records:

```bash
sudo bash /srv/amigo/deploy/verify-production.sh
```

Она проверяет:

- health всех шести services и immutable image SHA;
- PostgreSQL, точные secret mounts и отсутствие Docker secrets у gateway;
- успешный `withings-incremental` `JobRun`, начатый не раньше `StartedAt`
  текущего worker container; privacy-safe запрос читает только job name,
  status и timestamps, но не `details` или provider payload;
- loopback binds `18181`/`18182` и отсутствие published `8090`;
- pinned Codex hash на host и в gateway container;
- direct health, origin nginx, public TLS, relative `308`, defensive headers и immutable assets;
- public overview, activity, recovery и AI JSON-контракты;
- точный ingest route: unsigned empty batch отклоняется до создания записи;
- закрытые health endpoints, legacy assets и обе cron-строки.

Дополнительно оператор проверяет desktop/mobile, полный Health Connect backfill,
один следующий sync-цикл, отсутствие дублей, табличные альтернативы графиков
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
- **Health Connect ingest недоступен.** Withings и уже импортированные данные
  продолжают работать. Android WorkManager повторит подписанные idempotent
  batches; после восстановления запустить manual sync/backfill. Не импортировать
  payload вручную.
- **Mi Fitness не пишет отдельную метрику.** Это нормальная вариативность:
  UI показывает только фактически доступные HRV/SpO2/VO2/sleep/workout поля.
- **Withings worker недоступен.** Не включать legacy cron, пока Amigo worker может
  писать/ротировать OAuth. Для возврата legacy collector выполнить только
  полный documented rollback с token handback.

Логи и диагностический output не должны содержать health payload, prompt, generated
analysis, auth, headers или токены. Для ingest rejection допустим только
стабильный `detail.code`, без device ID, batch ID и validation details.

## Rollback

Rollback всегда принимает конкретный проверенный snapshot; `latest`, glob и
неявный выбор запрещены:

```bash
sudo bash /srv/amigo/deploy/rollback.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Скрипт:

1. Останавливает `worker`, `ai-worker`, `ingest` и `ai-gateway`.
2. Убирает только managed nginx route и проверяет HTTP 200 legacy origin.
3. Передаёт текущую OAuth-пару в ровно одну legacy MariaDB token row через
   одноразовый root-only bind без stdout, затем удаляет handoff files.
4. Включает только точную legacy Withings cron-строку.
5. Останавливает `web` и `db`, но не удаляет containers, images, PostgreSQL volume,
   Codex state, snapshot, APK, `/srv/www/amigo`, MariaDB и общий Telegram cron.

Android-приложение при rollback может остаться установленным: ingest закрыт,
а локальная Health Connect история сохранится для будущего resumable backfill.
Rollback не восстанавливает PostgreSQL dump автоматически; volume сохраняется,
а dump служит для отдельного аварийного восстановления.

Для повторного cutover исправить причину, вернуть чистый release и снова запустить
`deploy.sh`; не применять `down -v`.

## Documentation checkpoint

После каждого production deploy или изменения runtime-контракта выполнить:

```bash
sudo bash /srv/amigo/deploy/checkpoint.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Скрипт повторяет verification и записывает public URL, UTC/MSK время, Git SHA,
image refs/IDs всех шести services, SHA-256 Compose/nginx/Codex, результаты
проверок и точный rollback path. Он не читает и не записывает секреты.
Изменённые `AGENTS.md`, runbook и `production-checkpoint.md` нужно закоммитить в
канонический репозиторий до сообщения о завершении. Documentation-only commit
можно перенести в production checkout без rebuild; source of truth для runtime SHA остаётся
`/var/lib/amigo/current-release`.

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-20T04:27:20Z` (`2026-08-20 07:27:20 MSK`)
- Git SHA: `272a01052246c91869da2a6319cc54c66588f174`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260820T042221Z`
- Installed config SHA-256: Compose `05d80cfec15d859ad686069652c0b4a33d0f210fdfe6bd4502cce5a111558c49`; nginx locations `a1b07803174a6143014c32da55c166422253b8fdd9db8df5ed3b3afb61de4728`; nginx rate limit `1a1a7d7b3124592c960e600c9fa6ccc7a80f4200ff71464a3e4a689b97bc86fb`.
- Pinned Codex: `0.148.0` (`sha256:ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`).
- Verification: all six Compose services healthy; application services use the release image; PostgreSQL ready; the current worker completed a successful post-start Withings incremental job; web and ingest are bound only to `127.0.0.1:18181` and `127.0.0.1:18182`; container secret boundaries, pinned Codex hash, isolated unpublished AI gateway, direct health, hidden public health routes, exact unsigned-ingest rejection, origin proxy, public HTTPS dashboard and overview/activity/recovery/AI JSON, relative `308`, security headers, route rollback rehearsal, cron isolation, and rollback assets passed.
- Installed image references and IDs:

- `web`: `amigo:272a01052246c91869da2a6319cc54c66588f174` (`sha256:6e89f9fe9f78aac0d504ed258bf4e9d5f163ac7581a8c73aeb366f472229929c`)
- `worker`: `amigo:272a01052246c91869da2a6319cc54c66588f174` (`sha256:6e89f9fe9f78aac0d504ed258bf4e9d5f163ac7581a8c73aeb366f472229929c`)
- `ingest`: `amigo:272a01052246c91869da2a6319cc54c66588f174` (`sha256:6e89f9fe9f78aac0d504ed258bf4e9d5f163ac7581a8c73aeb366f472229929c`)
- `ai-worker`: `amigo:272a01052246c91869da2a6319cc54c66588f174` (`sha256:6e89f9fe9f78aac0d504ed258bf4e9d5f163ac7581a8c73aeb366f472229929c`)
- `ai-gateway`: `amigo:272a01052246c91869da2a6319cc54c66588f174` (`sha256:6e89f9fe9f78aac0d504ed258bf4e9d5f163ac7581a8c73aeb366f472229929c`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Rollback command: `sudo /srv/amigo/deploy/rollback.sh /srv/amigo-rollbacks/20260820T042221Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
