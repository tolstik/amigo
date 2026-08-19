# Production verification checklist

Заполнять для каждого cutover. Автоматические пункты выполняет
`sudo bash /srv/amigo/deploy/verify-production.sh`. Ручные пункты нельзя
подменять ответом только одного HTTP endpoint. Не выводить в терминал,
лог или этот файл секреты, Codex auth и health payload.

## До cutover

- [ ] Выбран точный Git SHA; `/srv/amigo` root-owned и полностью чист,
      включая untracked source-файлы.
- [ ] `/srv/amigo/.env` и ровно восемь файлов `/srv/amigo/secrets/`
      непустые, закрыты для group/world и не попали в Git или shell output.
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
      проверен, если `db` был запущен.
- [ ] Явно выбран один Telegram-режим: владелец разрешил одно
      помеченное smoke-сообщение либо выбран `--skip-telegram-test`.
      Historical import не создаёт уведомлений.
- [ ] При повторном релизе работающие `worker`, `ai-worker` и `ingest` будут
      остановлены до migrations/one-shot jobs и восстановлены при ранней
      ошибке; нет двух процессов, параллельно ротирующих Withings OAuth.
- [ ] До первого Withings API request legacy collector переведён в единственный
      disabled-marker; после full sync свежая OAuth-пара без stdout возвращена в
      ровно одну legacy token row.
- [ ] Signed Android APK получен из доверенного release, его SHA-256 сверен;
      signing keystore и пароли не попали в checkout или документы.

## Автоматические проверки после cutover

- [ ] `db`, `web`, `worker`, `ingest`, `ai-worker`, `ai-gateway` имеют state
      `running` и health `healthy`; `pg_isready` успешен.
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
      SHA-256. Gateway health сообщает fixed model `gpt-5.6-terra`; synthetic
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
      нужного контракта. AI status принадлежит только
      `fresh|stale|pending|unavailable` и payload помечен `ai_generated`.
- [ ] Пустой unsigned POST на точный
      `/amigo-ingest/v1/health-connect/batches` возвращает `400` с
      `missing_signature_header` до создания health record. Прочие ingest paths/methods
      не открыты.
- [ ] Присутствуют `Cache-Control: no-store`, `X-Robots-Tag: noindex, noarchive`,
      `X-Content-Type-Options` и CSP; hashed assets имеют единственный immutable
      cache header.
- [ ] Активной точной `get_withings.php` строки нет, disabled-marker ровно
      один, точная общая `send_telergam.php all` сохранена.
- [ ] `/srv/www/amigo` и MariaDB `amigo` существуют. Route-only rehearsal
      показал legacy dashboard, затем Amigo v3 route возвращён и полный verification
      повторно прошёл.

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
- [ ] Давление, heart metrics, SpO2 и VO2 max показаны описательно, без медицинских
      категорий, severity-цветов, порогов, диагнозов, лечения и советов;
      BIA явно помечена как приблизительная.
- [ ] CSV export, фильтры периода, светлая/тёмная тема и табличные альтернативы
      графикам работают на desktop/mobile.
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
- [ ] AI-блок содержит только валидированный generated text с фактическими
      evidence keys. При `pending`/`unavailable` показан явный status, а не
      шаблонный совет. Public refresh не создаёт AI job и не обращается к
      gateway.
- [ ] Signed APK установлен через `adb install -r`; Amigo Sync имеет только
      read-only Health Connect permissions, источник Mi Fitness выбран явно, location
      и exercise routes не запрошены.
- [ ] Pending pairing label/code сверен с нужным телефоном и одобрен через
      `python -m app.health_cli approve-device`; неожиданных pending devices нет.
- [ ] Тестовый pairing reset сначала заменяет Android Keystore identity и только
      затем очищает local state; повторная регистрация создаёт новый pending
      device/fingerprint и требует отдельного одобрения.
- [ ] Manual sync/backfill дошёл до success. Дневные/недельные activity/recovery
      агрегаты появились, но weight/pressure/location и raw heart samples из
      Health Connect не созданы; повторный sync не дублирует records. Batch
      starts выдерживают минимум 1 100 ms, payload остаётся меньше 1 MiB, а
      неуспешный запрос повторяется без продвижения cursor/token.
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
      signature headers, credentials или auth contents.

## Завершение

- [ ] `deploy/checkpoint.sh` записал public URL, Git SHA, image IDs всех шести
      services, SHA-256 установленных Compose/nginx/Codex, результаты
      verification и точный rollback snapshot без секретов.
- [ ] Изменения `AGENTS.md`, runbook и `production-checkpoint.md` перенесены в
      канонический Git и закоммичены.
- [ ] Владелец получил production URL, APK или ссылку на release, а также точную
      rollback command. Deployment объявлен завершённым только после этих пунктов.
