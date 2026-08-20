# Production verification checklist

Заполнять для каждого cutover. Автоматические пункты выполняет
`sudo bash /srv/amigo/deploy/verify-production.sh`. Ручные пункты нельзя
подменять ответом только одного HTTP endpoint. Не выводить в терминал,
лог или этот файл секреты, Codex auth и health payload.

## До cutover

- [ ] Выбран точный Git SHA; `/srv/amigo` root-owned и полностью чист,
      включая untracked source-файлы.
- [ ] `/var/lib/amigo/current-release` совпадает с реально запущенным previous
      Amigo image; exact image tag/ID и Git object доступны. Candidate не меняет
      rollback-protected Compose/nginx envelope, Alembic, ORM models или pinned
      Codex runtime относительно previous release.
- [ ] `/srv/amigo/.env` и ровно восемь файлов `/srv/amigo/secrets/`
      непустые, закрыты для group/world и не попали в Git или shell output.
- [ ] В `/srv/amigo/.env` задано `AMIGO_USER_HEIGHT_CM=176`; Compose config
      передаёт это значение application services без дополнительных profile
      identifiers.
- [ ] Сервис-владелец `tolstik` авторизован в Codex CLI; source binary имеет
      версию `0.148.0` и SHA-256
      `ac2cfed85fb647d61e0150b8548102b330e4799d9d81ad5d354de701edf6b074`.
      `auth.json` не открывался, не печатался и не копировался вручную.
- [ ] `docker compose ... config --quiet` и `nginx -t` успешны; Compose описывает
      ровно `web`, `worker`, `db`, `ingest`, `ai-worker`, `ai-gateway`.
- [ ] Public DNS ведёт на TLS edge; `curl` без `-k` принимает сертификат
      `amigo.tolstik.ru`. Origin и edge не смешиваются в одной TLS-проверке.
- [ ] `/srv/www/amigo`, MariaDB `amigo` и обе ожидаемые строки crontab на месте.
- [ ] Достаточно места для legacy archive, MariaDB/PostgreSQL dumps, images и
      PostgreSQL data.
- [ ] Создан конкретный `/srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ`; все строки
      `SHA256SUMS` имеют `OK`, tar/gzip читаются, а каталог PostgreSQL dump
      проверен, если `db` был запущен. Metadata содержит exact previous SHA,
      application и PostgreSQL image IDs, защищённые rollback tags,
      AI model/prompt, Compose hash и enabled managed route; snapshot содержит
      previous Compose и точные managed nginx files.
- [ ] Явно выбран один Telegram-режим: владелец разрешил одно
      помеченное smoke-сообщение либо выбран `--skip-telegram-test`.
      Historical import не создаёт уведомлений.
- [ ] При повторном релизе работающие `worker`, `ai-worker` и `ingest` будут
      остановлены до migrations/one-shot jobs и восстановлены при ранней
      ошибке; нет двух процессов, параллельно ротирующих Withings OAuth.
- [ ] Existing `ai-worker` останавливается с timeout 120 секунд. После остановки
      ровно один `ai-retry-current --worker-stopped` предшествует AI attempts;
      `ai-ready` принимает только exit `0/75`, foreground worker запускается не
      более четырёх раз, а `ai-enqueue` выполняется только между failed attempts
      1–3. Отдельного gateway retry loop нет.
- [ ] Если production до подготовки находился на legacy route/cron, сначала
      успешно выполнен `takeover-from-legacy.sh --resume-recorded-release`:
      current OAuth pair взята из live MariaDB через root-only handoff, записана
      в PostgreSQL и проверена suppressed sync. Stale token secret files не
      использовались; уже запущенный legacy PHP завершился до чтения токенов;
      после takeover managed route активен, legacy cron disabled. Для snapshot
      `20260820T055833Z` прошла явная legacy-v0 проверка recorded SHA/OCI image и
      exact force-recreate application containers.
- [ ] Если legacy origin отвечал не `200`, takeover получил явный
      `--allow-unhealthy-legacy-origin`; отсутствие HTTP-ответа не принималось.
      Failure test подтверждает, что такой legacy не получает cron обратно и
      started Amigo route/runtime/db остаются доступными fail-closed.
- [ ] До первого Withings API request legacy collector переведён в единственный
      disabled-marker; после full sync свежая OAuth-пара без stdout возвращена в
      ровно одну legacy token row.
- [ ] Android `1.0.2` (`versionCode 3`) получен как
      `Amigo-Sync-1.0.2.apk` из release `v3.1.0`; его SHA-256 равен
      `ca5612ad7a642bde582478b5eebf8edc7d83a87337cf5df71d522026cecc94fd`.
      Signing keystore и пароли не попали в checkout или документы.

## Автоматические проверки после cutover

- [ ] `db`, `web`, `worker`, `ingest`, `ai-worker`, `ai-gateway` имеют state
      `running` и health `healthy`; `pg_isready` успешен.
- [ ] После `StartedAt` текущего worker container появился
      `withings-incremental` `JobRun` с `finished_at` и status `success`.
      Автоматическая проверка читала только job name/status/timestamps, не
      `details`, provider payload или секреты; run от прежнего container не
      засчитан.
- [ ] Пять application services используют один immutable `amigo:<Git SHA>`;
      OCI label `org.opencontainers.image.revision` каждого из них равен этому
      SHA; `db` использует `postgres:17-alpine`; SHA совпадает с
      `/var/lib/amigo/current-release` после фиксации cutover.
- [ ] Docker network membership точное: `db`, `web`, `worker`, `ingest` входят
      только в `amigo_backend`; `ai-worker` — в `amigo_backend` и
      `amigo_ai_private`; `ai-gateway` — только в `amigo_ai_private`.
- [ ] `web`, `ingest`, `ai-worker` имеют только PostgreSQL secret; `worker` —
      ровно восемь ожидаемых mounts; `ai-gateway` — ноль Docker secrets.
- [ ] `ai-worker` работает с `AMIGO_ENV=production`, AI включён, а gateway URL
      равен ровно `http://ai-gateway:8090`; override на внешний endpoint
      отклоняется fail-closed.
- [ ] Pinned Codex binary на host и read-only mount в `ai-gateway` имеют ожидаемый
      SHA-256. Gateway health сообщает fixed model `gpt-5.6-sol`; synthetic
      `python -m app.ai_smoke` прошёл без real health data.
- [ ] `/srv/amigo/data/import/legacy-weight.tsv` root-owned, закрыт для group/world и
      смонтирован как read-only `/imports`.
- [ ] Listener `18181` — только `127.0.0.1:18181` для `web`; listener `18182` —
      только `127.0.0.1:18182` для `ingest`. `ai-gateway:8090` не опубликован
      в Docker и не слушает host.
- [ ] Direct `web`/`ingest` health отвечают; внешние `/healthz`,
      `/amigo/healthz`, `/amigo/internal/health`, `/amigo-ingest/healthz` и
      `/amigo-ai/healthz` не возвращают 2xx.
- [ ] В `my.conf` ровно два managed marker, snippets совпадают с release,
      read/ingest rate-limit zones установлены, `nginx -t` успешен.
- [ ] Origin с `Host: amigo.tolstik.ru` отвечает; exact `/amigo` возвращает
      `308`, `/amigo/` — `200`.
- [ ] Public `https://amigo.tolstik.ru/amigo` возвращает относительный
      `Location: /amigo/`; dashboard и public JSON API работают через
      валидный TLS.
- [ ] `/amigo/api/v1/overview`, `/amigo/api/v1/series/activity?range=30d`,
      `/amigo/api/v1/series/recovery?range=30d` и
      `/amigo/api/v1/ai-analysis` возвращают `no-store` JSON
      нужного контракта. Для завершения deployment AI status равен `fresh`,
      payload помечен `ai_generated`, model равен `gpt-5.6-sol`, prompt contract
      равен `amigo-health-v2`, а каждая опубликованная рекомендация имеет
      evidence keys.
- [ ] Пустой unsigned POST на точный
      `/amigo-ingest/v1/health-connect/batches` возвращает `400` с
      `missing_signature_header` до создания health record. Прочие ingest paths/methods
      не открыты.
- [ ] Присутствуют `Cache-Control: no-store`, `X-Robots-Tag: noindex, noarchive`,
      `X-Content-Type-Options` и CSP; hashed JavaScript и CSS assets имеют
      единственный immutable cache header.
- [ ] Активной точной `get_withings.php` строки нет, disabled-marker ровно
      один, точная общая `send_telergam.php all` сохранена.
- [ ] `/srv/www/amigo` и MariaDB `amigo` существуют как explicit disaster
      fallback, но успешный deploy не переключал public route на legacy.

## Previous-release recovery contract

- [ ] Failure-path test после `CUTOVER_STARTED` вызывает только
      `restore-previous-release.sh`; `rollback.sh --to-legacy` автоматически не
      вызывается даже при ошибке recovery.
- [ ] Recovery использует SHA/application и PostgreSQL image IDs/Compose/nginx
      из конкретного snapshot, оставляет legacy cron disabled и не меняет
      production checkout HEAD. Shared nginx config вне Amigo markers остаётся
      текущим.
- [ ] Recovery не выполняет автоматический `pg_restore`; PostgreSQL volume и уже
      принятые Withings/Health Connect данные сохраняются.
- [ ] Active AI jobs с другим model/prompt меняют только metadata status на
      `superseded` до старта previous `ai-worker`. Если metadata cleanup не
      подтверждён, AI worker остаётся stopped/degraded, а `worker`, web и ingest
      продолжают работать.
- [ ] Explicit legacy fallback без обязательного `--to-legacy` отклоняется до
      любых изменений route, cron, OAuth или Compose.
- [ ] Mocked transition test подтверждает, что failure до/после route enable
      возвращает cron только после legacy route, а при ошибке route restore
      оставляет Amigo web/db работающими и оба collectors выключенными. Ошибка
      остановки Amigo collector никогда не включает legacy cron и не выполняет
      OAuth handback.

## Ручная продуктовая проверка

- [ ] Desktop и mobile: «Обзор», «Прогресс», «Вся история», «Давление»,
      «Состав тела», «Активность» и «Восстановление» открываются без console errors.
- [ ] Программа начинается 15.08.2026 с базового веса 127,03 кг; более ранние
      веса видны только во всей истории и не влияют на KPI/forecast.
- [ ] План теряет 4 кг за календарный месяц, интерполируется между
      совпадающими числами и ограничен 76,5 кг.
- [ ] Последний вес и Withings group count сверены с импортом; pressure и
      composition заполнены, если source содержит значения.
- [ ] Root-only legacy TSV создан из `date_creat, weight`, импортирован с UTC и
      scale `0.001`; legacy-only строки присутствуют, совпадения не дублируются.
- [ ] Давление, heart metrics, SpO2 и VO2 max на детерминированных экранах
      показаны описательно, без медицинских категорий, severity-цветов, порогов
      и app-side диагнозов; BIA явно помечена как приблизительная.
- [ ] CSV export, фильтры периода, темы «Светлая», «Тёмная», «Океан» и «Закат»
      и табличные альтернативы графикам работают на desktop/mobile. При пустом
      storage старт остаётся светлым даже под dark OS; явный выбор сохраняется
      после reload и перекрашивает также графики.
- [ ] На «Прогрессе» видны оба недельных weight plan/fact графика:
      парные столбики «Факт/План» и линия минимума; дельта имеет верный знак.
- [ ] Weight weekly таблица совпадает с tooltip и
      `/amigo/api/v1/series/weight?range=program`: ISO-недели Mon–Sun в
      `Europe/Moscow`, первая обрезана датой 15.08.2026, текущая partial,
      пустые недели сохранены с null-фактом.
- [ ] На «Активности» дневные шаги/активные минуты и недельные
      столбики «факт/личная база» корректны; база использует соответствующие
      дни недели из предыдущих 28 полных дней.
- [ ] На «Восстановлении» видны фактически доступные sleep/resting HR/HRV/
      SpO2/VO2 поля; отсутствующая у Mi Fitness метрика не подменяется нулём.
- [ ] Корреляции показываются только при достаточном перекрытии и с
      явным предупреждением, что корреляция не доказывает причину.
- [ ] AI snapshot содержит identifier-free `profile.height_cm=176`; BMI
      присутствует только при наличии последнего доступного Withings weight,
      рассчитан детерминированно из него, имеет тот же `observed_on` и не
      создаётся моделью или из Health Connect weight.
- [ ] AI-блок содержит только валидированный generated text: каждая рекомендация
      называет concrete action, cadence или review period и фактические evidence
      keys. Рекомендации идут перед наблюдениями и на overview, и в Telegram.
      При `pending`/`unavailable` показан явный status, а не шаблонный совет;
      public refresh не создаёт AI job и не обращается к gateway.
- [ ] Pressure/heart/SpO2/VO2 evidence поддерживает только совет повторить
      измерения, вести журнал или обсудить устойчивый измеренный паттерн с
      врачом; при наличии этих метрик output содержит минимум одну такую
      bounded medical/measurement рекомендацию. В AI output отсутствуют диагноз,
      лечение, назначение или изменение лекарства/дозировки и фиксированная цель
      по калориям.
- [ ] Signed APK `1.0.2` установлен через `adb install -r`; прежние pairing
      state, non-exportable Keystore key, выбранный Mi Fitness origin и cursors
      сохранены. Amigo Sync имеет только read-only Health Connect permissions;
      location и exercise routes не запрошены.
- [ ] Pending pairing label/code сверен с нужным телефоном и одобрен через
      `python -m app.health_cli approve-device`; неожиданных pending devices нет.
- [ ] Тестовый pairing reset сначала заменяет Android Keystore identity и только
      затем очищает local state; повторная регистрация создаёт новый pending
      device/fingerprint и требует отдельного одобрения.
- [ ] Manual sync/backfill дошёл до success. Дневные/недельные activity/recovery
      агрегаты появились, но weight/pressure/location и raw heart samples из
      Health Connect не созданы; повторный sync не дублирует records. Batch
      starts выдерживают минимум 1 100 ms, payload остаётся меньше 1 MiB, а
      неуспешный запрос повторяется без продвижения cursor/token. Допустимый
      Health Connect step count `1 000 000` принимается.
- [ ] После следующего Withings sync-цикла нет duplicate group, duplicate Telegram
      event или refresh/outbox errors.
- [ ] Расписание проверено в `Europe/Moscow`: daily в 09:00 во вторник–
      воскресенье, weekly в 09:00 понедельника вместо daily. Weekly отправляет
      фото и отдельный полный текст; при недоступном AI отправляются только
      факты. Worker не зависит от host cron.

## Degraded-mode проверка

- [ ] При неготовом AI остаются доступны графики, API и Withings/Health Connect
      импорт; UI/Telegram не показывают fallback narrative.
- [ ] При временной ошибке ingest Android сохраняет resumable progress и повторяет
      idempotent batch после восстановления; уже принятые данные не повреждены.
- [ ] В логах degraded-сценария нет prompt, generated analysis, health payload,
      signature headers, credentials или auth contents. Ingest rejection
      содержит только стабильный `detail.code`, без device ID, batch ID,
      headers и validation details; Android отражает только allowlisted code,
      не произвольное response body.

## Завершение

- [ ] `deploy/checkpoint.sh` записал public URL, Git SHA, image IDs всех шести
      services, SHA-256 установленных Compose/nginx/Codex, результаты
      verification, exact previous-release recovery command и отдельную
      `rollback.sh --to-legacy` disaster command без секретов.
- [ ] Изменения `AGENTS.md`, runbook и `production-checkpoint.md` перенесены в
      канонический Git и закоммичены.
- [ ] Владелец получил production URL, APK или ссылку на release, а также точную
      previous-release recovery command. Legacy disaster command явно помечена
      как ручная. Deployment объявлен завершённым только после этих пунктов.
