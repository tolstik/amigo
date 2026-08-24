# Production verification checklist

Заполнять для каждого cutover. Автоматические пункты выполняет
`sudo bash /srv/amigo/deploy/verify-production.sh`. Ручные пункты нельзя
подменять ответом только одного HTTP endpoint. Не выводить в терминал,
лог или этот файл секреты, Codex auth и health payload.

## До cutover

- [ ] Выбран точный Git SHA; `/srv/amigo` root-owned и полностью чист,
      включая untracked source-файлы.
- [ ] `/usr/local/sbin/amigo-release` и `/etc/sudoers.d/amigo-release`
      root-owned, не являются symlink, совпадают с release и проходят
      `visudo -cf /etc/sudoers`. Правило даёт `tolstik` `NOPASSWD` только на
      wrapper, не на shell, Git, Docker, `deploy.sh` или `ALL`.
- [ ] `/var/lib/amigo/current-release` совпадает с реально запущенным previous
      Amigo image; exact image tag/ID и Git object доступны. Candidate не меняет
      rollback-protected базовые ORM models, pinned Codex runtime и существующие
      Alembic migration-файлы. Добавочные migration-файлы и новый Compose/nginx
      envelope допустимы, потому что snapshot сохраняет точные previous
      Compose/nginx files, image IDs и rollback tags, а recovery динамически
      останавливает candidate services.
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
      ровно `web`, `worker`, `db`, `ingest`, `ai-worker`, `ai-gateway`, `lab-parser`.
- [ ] Для первого auth cutover подготовлен пароль длиной минимум 14 символов;
      он вводится только в скрытом `/dev/tty` prompt и не находится в environment,
      argv, shell history, файлах или Markdown.
- [ ] Public DNS ведёт на TLS edge; `curl` без `-k` принимает сертификат
      `amigo.tolstik.ru`. Origin и edge не смешиваются в одной TLS-проверке.
- [ ] `/srv/www/amigo`, MariaDB `amigo` и обе ожидаемые строки crontab на месте.
- [ ] Достаточно места для legacy archive, MariaDB/PostgreSQL dumps, images и
      PostgreSQL data.
- [ ] Все CI jobs candidate SHA прошли; immutable
      `ghcr.io/tolstik/amigo:<Git SHA>` доступен production, а его OCI revision
      равен candidate SHA. На production application image не собирается.
- [ ] Создан конкретный `/srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ`; все строки
      `SHA256SUMS` имеют `OK`, tar/gzip читаются, а каталог PostgreSQL dump
      проверен, если `db` был запущен. Metadata содержит exact previous SHA,
      application и PostgreSQL image IDs, защищённые rollback tags,
      AI model/prompt, `previous_auth_floor`, Compose hash и enabled managed
      route; snapshot содержит previous Compose, точные managed nginx files,
      архив laboratory originals и точное предыдущее состояние Android APK.
- [ ] Явно выбран один Telegram-режим: владелец разрешил одно
      помеченное smoke-сообщение либо выбран `--skip-telegram-test`.
      Historical import не создаёт уведомлений.
- [ ] При повторном релизе работающие `worker`, `ai-worker`, `ingest` и `lab-parser` будут
      остановлены до migrations/one-shot jobs и восстановлены при ранней
      ошибке; нет двух процессов, параллельно ротирующих Withings OAuth.
- [ ] Existing `ai-worker` останавливается с timeout 180 секунд. После остановки
      ровно один `ai-retry-current --worker-stopped` предшествует AI attempts;
      `ai-ready` принимает только exit `0/75`, foreground worker запускается не
      более четырёх раз, а `ai-enqueue` выполняется только между failed attempts
      1–3. Отдельного gateway retry loop нет.
- [ ] `ai-gateway` использует отдельный 150-секундный deadline только для
      routine analysis; worker client ждёт не более 180 секунд, а laboratory,
      analyte-guide и assistant сохраняют 75-секундный Codex deadline.
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
      disabled-marker; после incremental sync свежая OAuth-пара без stdout возвращена в
      ровно одну legacy token row.
- [ ] Android `1.4.0` (`versionCode 15`) получен как
      [`Amigo-1.4.0.apk`](https://github.com/tolstik/amigo/releases/download/v5.2.0/Amigo-1.4.0.apk)
      из release [`v5.2.0`](https://github.com/tolstik/amigo/releases/tag/v5.2.0);
      его SHA-256 равен
      `4a3a083c2b5c54482d2393526c0e6775087df53a0d3f6d6f9f568e80db32f995`, размер
      равен `3 504 370` bytes, а
      signing certificate SHA-256 равен
      `25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02`.
      Signing keystore и пароли не попали в checkout или документы.

## Автоматические проверки после cutover

- [ ] Least-privilege release wrapper и sudoers policy имеют ожидаемые
      ownership/mode/content; полный sudoers config валиден.
- [ ] `db`, `web`, `worker`, `ingest`, `ai-worker`, `ai-gateway`, `lab-parser` имеют state
      `running` и health `healthy`; `pg_isready` успешен.
- [ ] После `StartedAt` текущего worker container появился
      `withings-incremental` `JobRun` с `finished_at` и status `success`.
      Автоматическая проверка читала только job name/status/timestamps, не
      `details`, provider payload или секреты; run от прежнего container не
      засчитан.
- [ ] Шесть application services используют один immutable `amigo:<Git SHA>`;
      OCI label `org.opencontainers.image.revision` каждого из них равен этому
      SHA; `db` использует `postgres:17-alpine`; SHA совпадает с
      `/var/lib/amigo/current-release` после фиксации cutover.
- [ ] Docker network membership точное: `db`, `web`, `worker`, `ingest` входят
      только в `amigo_backend`; `ai-worker` — в `amigo_backend` и
      `amigo_ai_private`/`amigo_lab_private`; `ai-gateway` — только в
      `amigo_ai_private`; `lab-parser` — только в internal `amigo_lab_private`.
- [ ] `web`, `ingest`, `ai-worker` имеют только PostgreSQL secret; `worker` —
      ровно восемь ожидаемых mounts; `ai-gateway`/`lab-parser` — ноль Docker
      secrets и database configuration.
- [ ] `ai-worker` работает с `AMIGO_ENV=production`, AI включён, а gateway URL
      равен ровно `http://ai-gateway:8090`, parser URL —
      `http://lab-parser:8085`; override на внешний endpoint отклоняется fail-closed.
- [ ] Pinned Codex binary на host и read-only mount в `ai-gateway` имеют ожидаемый
      SHA-256. Gateway health сообщает fixed model `gpt-5.6-sol` и
      `amigo-health-v4`; synthetic `python -m app.ai_smoke` прошёл live analysis,
      laboratory-extraction, analyte-guide и assistant-turn контракты без real
      health data или персонального контекста.
- [ ] `/srv/amigo/data/import/legacy-weight.tsv` root-owned, закрыт для group/world и
      смонтирован как read-only `/imports`.
- [ ] `/srv/amigo/data/lab-files` — real root:root directory `0700`; `web` видит
      `/lab-files` RW, `ai-worker` RO, `lab-parser` не имеет этого mount.
- [ ] `backfill-files` завершён без ошибки; у laboratory/study documents нет
      `stored_file_id IS NULL`, а PostgreSQL originals повторно прошли
      size/SHA-256 verification.
- [ ] После idempotent laboratory-date repair нет report/result с годом до
      1900 или более чем на год в будущем; исправление использовало только
      однозначно подписанные OCR-даты и не перезаписало ручные corrections.
- [ ] Bounded backfill сохранил хотя бы одну статью текущего контракта либо уже
      не имеет пропусков; текущая версия `lab_analyte_guide_jobs` не содержит
      terminal failed rows. Остаток исторической очереди может обрабатываться
      асинхронно пачками не более пяти.
- [ ] `/srv/amigo/data/android/amigo-sync.apk` — root:root regular file `0600`
      с точными hash/size `1.4.0`; `web` видит `/android` только read-only.
- [ ] Listener `18181` — только `127.0.0.1:18181` для `web`; listener `18182` —
      только `127.0.0.1:18182` для `ingest`. `ai-gateway:8090` и
      `lab-parser:8085` не опубликованы в Docker и не слушают host.
- [ ] Direct `web`/`ingest` health отвечают; внешние `/healthz`,
      `/amigo/healthz`, `/amigo/internal/health`, `/amigo-ingest/healthz` и
      `/amigo-ai/healthz` и `/amigo-lab-parser/healthz` не возвращают 2xx.
- [ ] В `my.conf` ровно два managed marker, snippets совпадают с release,
      read/ingest rate-limit zones установлены, `nginx -t` успешен.
- [ ] Dynamic labs/studies/assistant/tasks/doctor-report regex routes используют
      named captures и exact upstream URI; новые task/report captures принимают
      только canonical lowercase UUID, public method/path не искажается generic rewrite.
- [ ] Каждый managed `limit_req` имеет explicit `limit_req_status 429`; upload
      burst покрывает bounded verification/UI sequence без shared `503` handler.
- [ ] Origin с `Host: amigo.tolstik.ru` отвечает; exact `/amigo` возвращает
      `308`, `/amigo/` — `200`.
- [ ] Public `https://amigo.tolstik.ru/amigo` возвращает относительный
      `Location: /amigo/`; login shell/assets работают через валидный TLS.
- [ ] Public `GET /.well-known/assetlinks.json` имеет JSON/nosniff/cache
      headers и exact package/certificate contract для
      `ru.tolstik.amigo.sync`; origin возвращает exact `405` для POST, а
      public edge безопасно отклоняет его с `403` или `405`.
- [ ] Без cookie auth session, overview, data-quality, CSV, labs/compare,
      studies, tasks, doctor report/PDF, updater и assistant возвращают exact
      `401` при проверке реальных HTTP-методов route; signed Android ingest
      остаётся независимым.
- [ ] Root-only short-lived verification session создаётся CLI без печати
      token/cookie. С ней `/amigo/api/v1/overview`, `/amigo/api/v1/series/activity?range=30d`,
      `/amigo/api/v1/series/recovery?range=30d` и
      `/amigo/api/v1/ai-analysis` возвращают `no-store` JSON
      нужного контракта. Для завершения deployment AI status равен `fresh`,
      payload помечен `ai_generated`, model равен `gpt-5.6-sol`, prompt contract
      равен `amigo-health-v4`, а каждая опубликованная рекомендация имеет
      evidence IDs, каждый из которых разрешается в descriptor exact saved
      analysis snapshot; descriptor value/date/range не перечитывается из
      изменившихся source rows.
- [ ] Та же session проверяет profile, labs documents/summary/analytes,
      справочную карточку `/labs/analytes/leukocytes/history`, studies,
      data-quality, task list, updater metadata/APK, assistant messages и CSV.
      APK download совпадает с advertised exact size/SHA-256. Mutation с exact Origin, но без CSRF возвращает
      `403`; пустой unsupported upload возвращает consent/validation rejection и
      не создаёт document. Fake assistant ID и laboratory/study queue SSE
      проверяют `text/event-stream`, `no-store`, initial event и bounded error
      без создания turn. `X-Accel-Buffering: no`
      проверяется на origin: public nginx edge применяет этот служебный
      заголовок для отключения buffering и не пересылает его клиенту.
- [ ] Shared analytics selector публикует steps только как active/finalized
      Xiaomi Cloud rows. `/data-quality` даёт для steps policy
      `xiaomi_finalized_only`, нулевой Health Connect coverage и только
      `mi_fitness`/null day sources; сохранённые Health Connect rows остаются
      rollback history и не попадают в dashboard/CSV/Telegram/AI/correlations.
- [ ] Lab compare и task mutation routes проходят 403 без CSRF и безопасные
      404/422 с CSRF без создания документа/задачи. Временный 30-day doctor
      snapshot содержит только privacy allowlist, verified/corrected labs и
      verified studies, имеет TTL 24 часа; PDF не превышает 40 страниц/10 МиБ,
      явно помечает Xiaomi-only steps, показывает sleep scale в часах и удалён
      тем же verification run.
- [ ] Пустой unsigned POST на каждый точный signed route —
      `/amigo-ingest/v1/health-connect/batches`,
      `/amigo-ingest/v1/mi-fitness/batches` и
      `/amigo-ingest/v1/mi-fitness/status` — возвращает `400` с
      `missing_signature_header` до создания записи. Прочие ingest paths/methods
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
- [ ] Каждая post-nginx route проверка bounded: auth-capable shell ждёт exact
      `200`, а recovery на release без auth — exact maintenance `503` плюс
      рабочий signed-ingest rejection. Transient reload race не вызывает ложный
      откат; полный `verify-production.sh` после cutover остаётся обязательным.
- [ ] Recovery использует SHA/application и PostgreSQL image IDs/Compose/nginx
      из конкретного snapshot, оставляет legacy cron disabled и не меняет
      production checkout HEAD. Точное предыдущее наличие/содержимое APK
      восстановлено. Shared nginx config вне Amigo markers остаётся текущим.
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
      «Состав тела», «Активность», «Восстановление», «Качество данных»,
      «Задачи», «Анализы», «Сравнение анализов», «Исследования», «Пакет для
      врача», «Ассистент» и «Профиль» открываются без console errors после login; защищённый deep link
      сохраняется через форму входа, logout и повторный login работают.
- [ ] Auth: неверный пароль не раскрывает существование пользователя; cookies
      имеют `Secure`, `SameSite=Strict`, path `/amigo/`; password rotation
      отзывает прежнюю session.
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
      после reload и перекрашивает также графики. Явный период `30d`, `90d`,
      `1y` или `all` также сохраняется после reload и переносится между всеми
      страницами с переключателем графика.
- [ ] На «Прогрессе» видны оба недельных weight plan/fact графика:
      парные столбики «Факт/План» и линия минимума; дельта имеет верный знак.
- [ ] Weight weekly таблица совпадает с tooltip и
      `/amigo/api/v1/series/weight?range=program`: ISO-недели Mon–Sun в
      `Europe/Moscow`, первая обрезана датой 15.08.2026, текущая partial,
      пустые недели сохранены с null-фактом.
- [ ] На «Активности» дневные шаги/активные минуты и недельные
      столбики «факт/личная база» корректны; база использует соответствующие
      дни недели из предыдущих 28 полных дней. Дневные графики активности и сна
      имеют только даты без фиктивных часов; пульс с часов, наоборот, использует
      почасовую временную шкалу.
- [ ] Все опубликованные шаги сверены с Xiaomi Cloud: отключение/неактивность
      direct source даёт явное missing state, а retained Health Connect steps не
      заполняют dashboard, CSV, Telegram, AI, correlations или doctor PDF.
- [ ] На «Восстановлении» видны фактически доступные sleep/resting HR/HRV/
      SpO2/VO2 поля; отсутствующая у Mi Fitness метрика не подменяется нулём.
      Ось, tooltip и table display сна используют часы, хотя API/CSV/AI и
      doctor snapshot продолжают хранить `sleep_minutes`.
- [ ] Корреляции показываются только при достаточном перекрытии и с
      явным предупреждением, что корреляция не доказывает причину.
- [ ] AI snapshot содержит identifier-free `profile.height_cm=176`; BMI
      присутствует только при наличии последнего доступного Withings weight,
      рассчитан детерминированно из него, имеет тот же `observed_on` и не
      создаётся моделью или из Health Connect weight.
- [ ] В профиле сохранены корректные дата рождения/пол и осознанный
      `amigo-ai-data-v1` consent. Без consent upload и новый assistant turn
      возвращают понятный UI status.
- [ ] До 25 PDF/image upload появляются в очереди; новый batch принимается во
      время обработки старого без `429`, UI показывает position/stage/progress
      через SSE, а explicit mutations показывают blocking loading popup.
      OCR/extraction не блокируют GET.
      Результаты сначала `unverified`, source page/text позволяют сверку,
      correction оставляет audit и снимает confirmation, confirm переводит
      строки в verified. Диапазон бланка приоритетнее catalog; history отделяет
      несовместимые units; поиск/фильтры и responsive график работают; кнопка
      «Посмотреть» открывает database original; delete убирает БД-историю и оригинал.
- [ ] «Исследования» принимает отчёты УЗИ/МРТ/КТ/рентген/ЭКГ/other, но не DICOM;
      очередь/view/edit/confirm/retry/delete работают, findings/conclusion и
      original сохраняются в PostgreSQL, identifier header lines не попадают в
      structured assistant context.
- [ ] Assistant сохраняет один chat, показывает рекомендации, stream draft и
      validated final после reconnect. `amigo-health-chat-v2` получает всю
      structured health/lab/study history, но не originals, filenames, study
      titles или OCR pages; умеет обсуждать evidence-backed hypotheses и
      альтернативы. Clear history удаляет переписку. Static emergency note видна
      постоянно.
- [ ] AI и новые assistant finals показывают кликабельные evidence citations.
      После появления новой measurement или correction старый completed ответ
      сохраняет прежние value/date/range из собственного snapshot; если target
      удалён, меняется только его availability, а не зафиксированное evidence.
- [ ] «Качество данных» для 30d/90d различает available, confirmed-empty и
      missing по source/metric без device/account/provider payload. Steps всегда
      имеют Xiaomi-only policy и не приписываются телефону/Health Connect.
- [ ] «Задачи» создаёт/редактирует/завершает/отменяет once/daily/weekly/monthly
      задачи, в том числе из AI recommendation с frozen source snapshot.
      Повтор worker polling не дублирует Telegram reminder одной occurrence;
      сообщение содержит только title, due time и dashboard link.
- [ ] Сравнение 2–3 завершённых лабораторных панелей связывает только одинаковый
      persisted `analyte_id`; несовместимые unit/specimen/method, multiple,
      missing, qualified или textual values показывают явную причину и не
      получают ложный delta/конверсию.
- [ ] «Пакет для врача» создаёт immutable 30d/90d/1y preview/PDF и может удалить
      его до автоматического 24-hour expiry. В PDF не более 40 страниц/10 МиБ,
      сон показан в часах, steps помечены Xiaomi Cloud-only; отсутствуют
      filenames, originals, OCR, chat, device/account identity и provider data.
- [ ] AI-блок содержит только валидированный generated text: каждая рекомендация
      называет concrete action, cadence или review period и фактические evidence
      keys. Рекомендации идут перед наблюдениями и на overview, и в Telegram.
      При `pending`/`unavailable` показан явный status, а не шаблонный совет;
      public refresh не создаёт AI job и не обращается к gateway.
- [ ] При наличии laboratory evidence AI содержит cited laboratory assessment,
      а при supplied deviation — bounded verification/repeat/clinician step.
      Telegram отправляет такую оценку отдельным блоком после новых лабораторных
      фактов и не дублирует её в общем блоке рекомендаций.
- [ ] Pressure/heart/SpO2/VO2 evidence поддерживает только совет повторить
      измерения, вести журнал или обсудить устойчивый измеренный паттерн с
      врачом; при наличии этих метрик output содержит минимум одну такую
      bounded medical/measurement рекомендацию. В AI output отсутствуют диагноз,
      лечение, назначение или изменение лекарства/дозировки и фиксированная цель
      по калориям.
- [ ] Signed APK `1.4.0` установлен через `adb install -r`; прежние pairing
      state, non-exportable Keystore key, выбранный Mi Fitness origin и cursors
      сохранены. Amigo имеет только read-only Health Connect permissions;
      location и exercise routes не запрошены.
- [ ] На Xiaomi email-verification поле кода получает системную клавиатуру;
      переход в почтовое приложение и возврат сохраняют текущую форму и
      изолированную auth-сессию, а отмена входа очищает временное состояние.
- [ ] Каждый запуск Xiaomi sync сначала успешно повторно устанавливает signed
      server source status и только потом читает cloud или отправляет batch;
      прерванное первоначальное включение восстанавливается без очистки
      credentials, pairing, key или cursors. Серверный allowlisted
      `mi_fitness_not_enabled` показывается явно, а не как
      `invalid_cloud_response`.
- [ ] Повтор Xiaomi batch побайтно стабилен и использует content-bound `mi-v2`
      ID; overlap record IDs соседних provider pages удаляется по bounded
      persisted hashes. Старый `batch_id_conflict`/snapshot sequence conflict
      один раз перезапускает только незавершённый snapshot своей метрики с тем
      же диапазоном; credentials, pairing, completed history и другие cursors
      сохранены, повторного recovery-loop нет.
- [ ] После первого запуска `1.3.2` или новее сохранён одноразовый reconcile обычного
      `heart_rate` из `1.2.4`: он получает свежий
      changes token и полный reconcile; snapshot/token/cursor остальных типов
      не сброшены. Сервер принял новый heart-rate snapshot либо подтвердил
      provider-empty состояние без ingest rejection.
- [ ] Xiaomi login выполнен только в отдельном exact-host HTTPS WebView process;
      после входа server status не содержит credential/provider данных. Все
      десять recent coverages завершены, partial snapshots не видны, а direct
      source стал active только если cloud heart rate новее Health Connect
      watermark `2026-08-21 08:59:59 MSK`.
- [ ] Cloud heart rate хранится только как hourly min/avg/max/count; raw samples,
      provider JSON, cookies, tokens и account ID отсутствуют в PostgreSQL и
      логах. Confirmed-empty cloud coverage подавляет Health Connect только для
      совпадающего metric/range; explicit logout снова показывает HC history.
- [ ] Приложение открывает вкладку «Дашборд» по умолчанию; login, logout, все
      SPA-разделы, темы, profile, laboratory upload/edit/confirm/delete/download,
      CSV и assistant SSE работают внутри WebView. Web logout не сбрасывает
      ingest pairing.
- [ ] Verified App Links для `/amigo` и `/amigo/...` открывают приложение и
      сохраняют deep link через login. HTTP, lookalike host, unknown route и
      external origin блокируются; TLS errors не обходятся.
- [ ] Незавершённый backfill добавляет continuation через `APPEND_OR_REPLACE` и
      не отменяет собственный worker; DNS failure показывает русское retryable
      сообщение, не сдвигает cursor/token и не выдаёт внутреннюю
      `chrome-error://` страницу за внешний небезопасный адрес.
- [ ] SAF выбирает до 25 PDF/JPG/PNG/HEIC/HEIF по 20 МиБ без storage/camera
      permission. CSV и laboratory original сохраняются через system Save As;
      cookie не уходит вне exact same-origin allowlist, redirects запрещены,
      ошибочный/слишком большой download удаляется.
- [ ] Android doctor PDF download принимает только exact same-origin GET
      `/amigo/api/v1/reports/doctor/<canonical-lowercase-UUID>.pdf`, использует
      системный Save As и in-memory cookie; query/fragment/POST/malformed UUID,
      redirect, off-origin и файл больше 25 МиБ отклоняются.
- [ ] После 30 секунд в фоне WebView обновляется без ручного refresh. In-app
      update видит authenticated metadata, проверяет size/SHA/package/version/
      certificate и открывает только системный installer с явным подтверждением.
- [ ] Pending pairing label/code сверен с нужным телефоном и одобрен через
      `python -m app.health_cli approve-device`; неожиданных pending devices нет.
- [ ] Тестовый pairing reset сначала заменяет Android Keystore identity и только
      затем очищает local state; повторная регистрация создаёт новый pending
      device/fingerprint и требует отдельного одобрения.
- [ ] Manual sync/backfill дошёл до success. Дневные/недельные activity/recovery
      агрегаты появились, но weight/pressure/location и raw heart samples из
      Health Connect не созданы; hourly heart-rate aggregates появились после
      one-time replay, повторный sync не дублирует records. Immediate/hourly/
      one-minute continuation WorkManager runs видны в bounded diagnostics. Batch
      starts выдерживают минимум 1 100 ms, payload остаётся меньше 1 MiB, а
      неуспешный запрос повторяется без продвижения cursor/token. Допустимый
      Health Connect step count `1 000 000` принимается.
- [ ] После следующего Withings sync-цикла нет duplicate group, duplicate Telegram
      event или refresh/outbox errors.
- [ ] Расписание проверено в `Europe/Moscow`: daily в 09:00 во вторник–
      воскресенье, weekly в 09:00 понедельника вместо daily. Weekly отправляет
      фото и отдельный полный текст; при недоступном AI отправляются только
      факты. Новые labs отправляются полностью с unit/range/status/verification,
      но без filename/OCR/original/chat. Worker не зависит от host cron.

## Degraded-mode проверка

- [ ] При неготовом AI остаются доступны графики, API и Withings/Health Connect
      импорт; UI/Telegram не показывают fallback narrative.
- [ ] При parser/OCR failure document получает безопасный error/retry, а dashboard,
      measurements и assistant history остаются доступны; payload/OCR не попал в лог.
- [ ] При временной ошибке ingest Android сохраняет resumable progress и повторяет
      idempotent batch после восстановления; уже принятые данные не повреждены.
- [ ] В логах degraded-сценария нет prompt, generated analysis, health payload,
      signature headers, credentials или auth contents. Ingest rejection
      содержит только стабильный `detail.code`, без device ID, batch ID,
      headers и validation details; Android отражает только allowlisted code,
      не произвольное response body.

## Завершение

- [ ] `deploy/checkpoint.sh` записал production URL, Git SHA, image IDs всех семи
      services, SHA-256 установленных Compose/nginx/Codex, результаты
      verification, exact previous-release recovery command и отдельную
      `rollback.sh --to-legacy` disaster command без секретов.
- [ ] Release `v5.2.0` указывает на deployed feature commit; asset
      `Amigo-1.4.0.apk` скачивается, повторно даёт ожидаемые APK SHA-256/size и
      signing certificate, а verified App Link association остаётся доступна.
- [ ] Изменения `AGENTS.md`, runbook и `production-checkpoint.md` перенесены в
      канонический Git и закоммичены.
- [ ] Владелец получил production URL, APK или ссылку на release, а также точную
      previous-release recovery command. Legacy disaster command явно помечена
      как ручная. Deployment объявлен завершённым только после этих пунктов.
