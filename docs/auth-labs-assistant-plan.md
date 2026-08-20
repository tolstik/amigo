# Amigo: авторизация, архив анализов и персональный ассистент

Статус: **реализовано и проверено локально; production cutover ожидает выполнения**.

Реализация включает миграции `20260820_0004`/`20260820_0005`, семь Compose-
сервисов, закрытые auth/CSRF API, лабораторный архив и дашборды, локальный
OCR/parser-контур, `amigo-health-v3`, `amigo-health-chat-v1`, PostgreSQL FTS,
SSE-чат, Telegram-добавления и auth-floor recovery. До production выполнены
128 backend-тестов, 22 frontend-теста, PostgreSQL migration round-trip, 16 E2E
desktop/mobile, Compose/shell checks и сборка общего образа. Фактические
SHA/image/checkpoint будут записаны только после успешного production deployment.

Дата фиксации: 2026-08-20.

## Кратко

- Закрыть все медицинские API, CSV и дашборды одним локальным аккаунтом с login-страницей, серверными сессиями и CSRF-защитой. Android-ingest остаётся независимым.
- Добавить загрузку PDF/JPG/PNG/HEIC, локальный OCR, хранение оригиналов на диске и полного извлечённого текста в PostgreSQL.
- Автоматически публиковать распознанные показатели с пометкой «не проверено», поддержать исправление и подтверждение.
- Добавить лабораторный дашборд с текущими значениями, историей и детерминированными статусами относительно диапазона лаборатории или введённого пользователем диапазона. Fallback-справочник включается только после отдельной проверки источников.
- Расширить существующий Codex-контур рекомендациями по всем данным и одним сохраняемым потоковым чатом.
- Завершить работу production-деплоем и обязательным документальным checkpoint.

## Ключевые изменения

### База и авторизация

- Сначала проверить и зафиксировать текущие незакоммиченные AI/deploy-изменения отдельным коммитом `fix: harden AI retries and release recovery`. На момент фиксации плана baseline чистый: 105 backend-тестов, 21 frontend-тест и production-сборка проходят.
- Создать единственного пользователя `amigo`: Argon2id-хеш пароля и только SHA-256-хеши случайных session-токенов хранятся в PostgreSQL.
- Сессия действует максимум 90 дней без отдельного idle-timeout; logout отзывает текущую сессию, смена пароля — все сессии.
- Cookie: `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/amigo/`; небезопасные методы требуют отдельный CSRF-токен и точное совпадение `Origin` с `AMIGO_PUBLIC_URL`. В production `Secure` выставляется безусловно.
- Пароль задаётся и меняется root-only CLI-командой через скрытый TTY-ввод; пароль, cookie и заголовки не попадают в аргументы процессов, логи или Markdown. Нового runtime-секрета web-сервису не требуется.
- Публичными остаются только обезличенный SPA/login-shell и статические assets. Из frontend bundle убрать персональные значения программы; все API, выгрузки и оригиналы доступны только после входа.
- Nginx получает отдельные лимиты и явный allowlist для login, upload, profile и assistant mutations; общий `/amigo/` остаётся deny-by-default для иных методов. Android `/amigo-ingest/` не меняется.

### Анализы и дашборды

- Добавить седьмой Compose-сервис `lab-parser`: без БД, Docker secrets, Codex-auth, host-порта, общего файлового volume и внешней сети. Он работает non-root/read-only во внутренней сети только с `ai-worker`, с лимитами памяти/CPU/PID/tmpfs.
- Web сохраняет оригиналы под случайными storage keys в `/srv/amigo/data/lab-files`: каталог `0700`, файлы `0600`, без прикладного шифрования. `ai-worker` видит volume read-only и передаёт конкретный файл parser-сервису по внутреннему HTTP.
- Поддержать текстовые и сканированные PDF до 50 страниц, JPG/JPEG, PNG и HEIC/HEIF; один файл до 20 MiB и 40 Мп. MIME определяется по содержимому. Encrypted PDF, архивы, Office-файлы и несовпадение magic/extension отклоняются стабильным кодом.
- Parser выполняет PDF extraction/OCR `rus+eng` и возвращает ограниченный plain text с координатами страниц. Полный текст и поисковые chunks сохраняются в PostgreSQL; SHA-256 файла обеспечивает дедупликацию.
- `ai-worker` порционно отправляет полный извлечённый текст, включая возможные персональные данные, в новый строгий контракт `amigo-lab-extraction-v1`. Оригинальный бинарный файл в Codex не передаётся.
- Добавить отдельные сущности документов, processing jobs, отчётов внутри документа, канонических анализов, результатов, исходного AI extraction, пользовательских правок, текстовых chunks и reference ranges.
- Codex только извлекает название, значение/comparator, единицу, дату, материал, метод, диапазон/флаг лаборатории и страницу-источник. Статус рассчитывает backend: `within_reference`, `below_reference`, `above_reference`, `outside_reference` или `indeterminate`.
- Результаты публикуются сразу после успешного разбора с отметкой `unverified`; пользователь может исправлять, добавлять, удалять строки и подтверждать документ. Неполные строки без даты или значения видны в документе, но не строят ложную историю.
- Добавить профиль с датой рождения и биологическим полом для лабораторных диапазонов. Диапазон самого бланка всегда основной; внешний fallback допустим только после проверки источника и при точном совпадении analyte/specimen/unit/sex/возраста.
- Справочник хранится как versioned data asset и не генерируется AI. В первой версии fallback-диапазоны явно отключены: asset используется только для канонических названий и aliases, пока не добавлен проверяемый авторитетный источник и отдельная дата проверки диапазонов.
- Раздел `/labs` показывает последние показатели, источник диапазона, статус и отметку проверки; `/labs/upload` — загрузку и очередь; страница документа — текст и редактор; страница показателя — историю с reference band. Несовместимые единицы строятся отдельными рядами без AI-конвертации.

### Рекомендации, чат и Telegram

- Расширить health snapshot лабораторными evidence-записями и поднять активный контракт до `amigo-health-v3`; старые AI-строки сохраняются, но не считаются активным cache.
- Рекомендации в разделе «Ассистент» обновляются асинхронно после новых измерений, готового/исправленного анализа или изменения профиля. GET-маршруты по-прежнему только читают PostgreSQL и никогда не вызывают Codex.
- Чат один, непрерывный, хранится до ручной очистки. В каждый turn входят полный детерминированный health snapshot, вся структурированная лабораторная история в агрегированном виде, последние 12 turns, сводка более старой переписки и релевантные full-text chunks, найденные локальным PostgreSQL FTS.
- Сообщение создаётся идемпотентным POST и отдельной очередью. Разрешён только один in-flight turn; scheduler обслуживает не более трёх chat jobs подряд, затем даёт ход lab/background job.
- Для настоящего live-потока использовать документированный `codex app-server` по локальному stdio и `item/agentMessage/delta`, без WebSocket и открытого порта. Каждый процесс создаёт ephemeral thread, использует `gpt-5.6-sol`, read-only sandbox, запрет approvals/tools/search и завершается после turn.
- Ответ `amigo-health-chat-v1` состоит из коротких segments с evidence keys. Каждый законченный segment проверяется до показа и помечается в UI как «Черновик». После полной schema/evidence validation он заменяется финальным ответом; при ошибке черновик полностью сбрасывается и допускается максимум одна автоматическая повторная попытка.
- Web SSE читает только draft/final state из PostgreSQL, поддерживает reconnect/heartbeat; nginx отключает buffering. Raw app-server events, reasoning, prompts и validation details не сохраняются и не логируются.
- Ассистент объясняет факты и помогает подготовить вопросы врачу, но не ставит диагнозы, не назначает лечение, препараты/дозировки или фиксированные калории. Лабораторные значения описываются только относительно указанного диапазона; acute triage остаётся вне scope, на странице постоянно показан статический emergency disclaimer.
- Перед первой загрузкой или вопросом пользователь подтверждает disclosure: CLI запускается локально, но inference выполняется сервисом OpenAI; полный извлечённый текст и вопросы могут покидать сервер.
- Полные новые лабораторные результаты — название, значение, единица, диапазон, статус и рекомендации — добавляются в ближайший ежедневный/недельный Telegram-отчёт и разбиваются на сообщения без усечения. Непроверенные значения явно маркируются. Оригиналы, OCR-текст, filenames и chat-переписка в Telegram не отправляются.

## Публичные интерфейсы и типы

- Auth/profile: `POST /api/v1/auth/login`, `GET /api/v1/auth/session`, `POST /api/v1/auth/logout`, `GET/PATCH /api/v1/profile`.
- Анализы: `POST/GET /api/v1/labs/documents`, detail/download/delete/retry/confirm по document id, `POST /api/v1/labs/documents/{id}/results`, `PATCH /api/v1/labs/results/{id}`, summary, analyte list/history и reference-catalog endpoints.
- Ассистент: `GET/POST /api/v1/assistant/messages`, `GET .../{id}/events` для SSE, retry и `DELETE /api/v1/assistant/history`.
- Upload и chat POST возвращают `202` с UUID и статусом. Assistant states: `queued`, `streaming`, `validating`, `complete`, `failed`; SSE events: `status`, `draft_segment`, `reset`, `complete`, `error`.
- Все перечисленные интерфейсы, включая downloads/SSE, требуют session auth; mutations дополнительно требуют CSRF и canonical Origin.

## Проверки и production rollout

- Auth: успешный/неуспешный login без утечки различий, Argon2id, cookie flags/path, CSRF/Origin, expiry/revocation, отсутствие DB-запросов к health data до авторизации, logout и password rotation.
- Files/parser: synthetic PDF/OCR/JPG/PNG/HEIC fixtures, MIME spoofing, encrypted/oversize/decompression bombs, timeouts/OOM, дедупликация, retry/leases и отсутствие содержимого в логах.
- Labs: числовые/качественные значения, comparators, приоритет диапазона бланка, выключенный fallback, неизвестные единицы, auto-publish/unverified, добавление/исправление/подтверждение/удаление и перестроение истории/AI cache.
- Assistant: retrieval coverage, prompt injection внутри документа/вопроса, разрешённые citations, live segments, reconnect, финальный reset, timeout/retry/fairness, очистка истории и запрет tools/diagnosis/treatment.
- Frontend: login gate и deep links, profile setup, upload progress, document editor, history charts, assistant streaming и mobile Playwright flow; сохранить существующие темы и accessible labels.
- Перед первым cutover создать проверенный backup PostgreSQL, оригиналов и конфигурации. Ввести auth-floor: откат на старый публичный release обязан закрыть `/amigo/` через maintenance `503`, а не снова раскрыть данные; signed ingest продолжает работать.
- Production развернуть только через `deploy/deploy.sh --send-telegram-test`. Проверить семь healthy services, новые zero-secret/unpublished boundaries parser/gateway, loopback-порты web/ingest, authenticated HTTPS/API/upload/SSE, Telegram lab output, post-start successful Withings incremental job и rollback rehearsal.
- После деплоя обновить `AGENTS.md`, README, runbook и production checkpoint: URL, deployed Git SHA, семь image IDs/hashes, pinned Codex hash, verification results и новый rollback snapshot. Секреты и медицинские данные в документацию не записывать.
