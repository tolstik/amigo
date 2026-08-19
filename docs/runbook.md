# Amigo v2: production runbook

Этот документ описывает безопасное развёртывание Amigo v2 на
`192.168.31.3`. Все команды изменения состояния выполняются на origin-сервере
от `root`. Пароли, OAuth-токены, Telegram-токены и chat ID не копируются в
команды, логи или Markdown.

## Неизменяемые эксплуатационные условия

- Production URL: `https://amigo.tolstik.ru/amigo/`.
- Проект расположен в `/srv/amigo`; Compose-сервисы называются `web`, `worker`
  и `db`. Наружу публикуется только `127.0.0.1:18181` у `web`.
- Точный SHA запущенного image хранится в root-only
  `/var/lib/amigo/current-release`. Он может отличаться от HEAD checkout после
  отдельного documentation-checkpoint commit; verification использует файл
  состояния. `web` получает только PostgreSQL secret, интеграционные секреты
  монтируются в `worker` и одноразовые worker CLI jobs.
- TLS завершается на публичном edge `5.35.114.76`. Внешний HTTPS проверяется
  обычным `curl` без `-k`. Локальный nginx на `192.168.31.3` — отдельный origin;
  его локальный сертификат не является источником истины для public TLS.
- Origin nginx хранит действующую конфигурацию в
  `/etc/nginx/conf.d/my.conf`. Управляемый include добавляется только в
  существующие server-блоки `tolstik.ru` на портах 80 и 443.
- Legacy-приложение `/srv/www/amigo` и MariaDB `amigo` не удаляются и не
  перезаписываются. Rollback лишь возвращает nginx route и legacy-сборщик.
- `/srv/cron` — общий каталог. Скрипты никогда его не изменяют. В crontab
  пользователя `tolstik` управляется только точная строка
  `*/1 07-08 * * *  php /srv/cron/get_withings.php`. Строка
  `*/1 * * * *  php /srv/cron/send_telergam.php all` должна остаться без
  изменений.
- Снимки находятся только в `/srv/amigo-rollbacks/<UTC timestamp>`, имеют права
  root-only и не удаляются автоматически.

## Подготовка релиза

1. Разместить чистый root-owned Git checkout нужного commit в `/srv/amigo`.
   Tracked и untracked source-файлы не должны иметь локальных изменений;
   `.env`, `secrets/` и `data/` остаются ignored runtime state.
2. Создать `/srv/amigo/.env` из `.env.example`, установить права `0600` и
   проверить `docker compose --file /srv/amigo/compose.yaml --env-file
   /srv/amigo/.env config --quiet`.
3. Через защищённый канал поместить непустые root-only файлы с правами `0400`
   или `0600` в `/srv/amigo/secrets/`:

   - `postgres_password`;
   - `app_encryption_key`;
   - `withings_client_id` и `withings_client_secret`;
   - `withings_access_token` и `withings_refresh_token`;
   - `telegram_bot_token` и `telegram_chat_id`.

4. Проверить свободное место для архива `/srv/www/amigo`, полного dump MariaDB,
   Docker images и PostgreSQL volume. Убедиться, что `docker compose`,
   `mariadb-dump`, `nginx`, `curl` и `python3` установлены.
5. Проверить внешний сертификат и route с машины, которая обращается к public
   edge:

   ```bash
   curl --fail --silent --show-error --head https://amigo.tolstik.ru/amigo/
   ```

   `--insecure` в этой проверке запрещён. Локальный origin проверяется отдельно
   по HTTP с явным `Host`, поэтому старый локальный сертификат не маскирует
   ошибки внешнего TLS.
6. Ротация SSH-, Withings- и Telegram-реквизитов выполняется владельцем через
   соответствующие панели. Новые значения сохраняются только в secret files.

Для первого перехода с сохранённого legacy-приложения файлы можно создать без
вывода значений в terminal или аргументы процессов:

```bash
sudo bash /srv/amigo/deploy/bootstrap-production-secrets.sh
```

Команда fail-closed читает существующие PHP-настройки и строку OAuth из
legacy MariaDB, генерирует новые независимые пароль PostgreSQL и Fernet-ключ,
атомарно устанавливает ровно восемь файлов `0400` и `.env` с правами `0600`.
Она предназначена только для первичного развёртывания и отказывается
перезаписывать существующий каталог `secrets/`.

Полный preflight приведён в
[production-verification.md](production-verification.md).

## Backup и проверка rollback snapshot

`deploy.sh` всегда запускает backup автоматически до сборки и cutover. Для
отдельной проверки можно выполнить:

```bash
sudo bash /srv/amigo/deploy/pre-cutover-backup.sh
```

Скрипт fail-closed создаёт timestamped snapshot и печатает его абсолютный путь.
В него входят:

- архив `/srv/www/amigo` с ownership, ACL и xattrs;
- consistent dump MariaDB `amigo` с routines, events и triggers;
- crontab `tolstik` и root;
- полный архив `/etc/nginx`, копия `my.conf` и результат `nginx -T`;
- несекретные metadata и `SHA256SUMS`.

Snapshot допускает ровно одно из двух состояний legacy Withings cron: активную
строку до первого cutover или единственный disabled-marker при повторном
релизе. Архивы, SQL gzip и каждый SHA-256 проверяются до атомарного переименования
`.partial-*` в финальный каталог. Незавершённый snapshot сохраняется для
диагностики и никогда не считается rollback point. Повторная ручная проверка:

```bash
cd /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
sudo sha256sum --check --strict SHA256SUMS
```

Не распаковывать snapshot поверх production. При физическом повреждении legacy
сначала восстановить архивы в отдельный `/srv/recovery/...`; обратное
восстановление MariaDB согласовывается отдельно. Обычный rollback его не требует,
поскольку legacy-файлы и БД остаются на месте.

## Развёртывание и cutover

Команда требует явного выбора режима уведомления. Для релиза с одним помеченным
тестовым Telegram-сообщением:

```bash
sudo bash /srv/amigo/deploy/deploy.sh --send-telegram-test
```

Если отдельного разрешения на тестовое сообщение нет, релиз выполняется без него:

```bash
sudo bash /srv/amigo/deploy/deploy.sh --skip-telegram-test
```

Фиксированная последовательность:

1. Проверка layout, прав secret files, чистого Git SHA, Compose и nginx.
2. Проверенный legacy snapshot.
3. Pull PostgreSQL image и сборка одного Git-SHA image для `web`/`worker`.
4. Запуск `db`, остановка уже работающего v2 worker при повторном релизе, затем
   `migrate`, `bootstrap` и, только в выбранном режиме, Telegram smoke через
   одноразовые worker jobs. При ранней ошибке ранее работавший worker запускается
   снова. Непосредственно перед первым запросом к Withings отключается только
   точная legacy cron-строка; это исключает параллельную ротацию OAuth.
   Выполняется полный import с
   `--suppress-notifications`, после чего текущая пара OAuth сразу возвращается
   в единственную строку legacy MariaDB через root-only handoff без stdout.
   Затем legacy-веса
   экспортируются точным read-only SQL-запросом в root-only
   `/srv/amigo/data/import/legacy-weight.tsv` и объединяются через
   `legacy-weight-import` без уведомлений; Compose монтирует каталог как
   `/imports:ro`.
5. Запуск только `web` и проверка `127.0.0.1:18181/healthz`.
6. Атомарная установка nginx snippet и `my.conf`. Он задаёт exact relative `308` с
   `/amigo` на `/amigo/` и `location ^~ /amigo/`, снимающий prefix перед proxy.
   Отдельные директивы http-context ограничивают публичные запросы до `120r/m`
   на origin IP с burst 60 и дают immutable cache только hashed assets;
   разрешены лишь `GET`, `HEAD` и `OPTIONS`.
7. Общий `send_telergam.php all` и единственный disabled-marker ещё раз
   проверяются после nginx cutover.
8. Запуск и ожидание health `worker`, полный verification, практическое отключение/возврат только
   nginx route и повторный verification.
9. Documentation/memory checkpoint в `AGENTS.md`, этом runbook и
   `production-checkpoint.md`.

До шага 6 публичный legacy route не меняется. Владение сбором переключается
непосредственно перед full sync; любая ошибка после этого останавливает worker,
возвращает свежую OAuth-пару legacy, включает точную cron-строку и запускает
автоматический rollback по snapshot. Ошибка checkpoint после двух
успешных verification не откатывает здоровый runtime, но deployment нельзя
считать завершённым, пока документы не обновлены и не перенесены в канонический
репозиторий.

## Проверка production

Автоматическая проверка безопасна и не отправляет сообщений:

```bash
sudo bash /srv/amigo/deploy/verify-production.sh
```

Она подтверждает Compose health, PostgreSQL, loopback bind, direct health,
origin nginx, public edge TLS, dashboard/API JSON, относительный redirect,
защитные заголовки, закрытые `/healthz`, `/amigo/healthz` и
`/amigo/internal/health`, состояние двух cron-строк и
сохранность legacy assets. Дополнительно оператор должен проверить браузер на
desktop/mobile, логи и один следующий 5-минутный sync-цикл на отсутствие дублей.

## Rollback

Rollback всегда принимает конкретный проверенный snapshot; `latest`, glob и
неявный выбор запрещены:

```bash
sudo bash /srv/amigo/deploy/rollback.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Порядок минимизирует риск дублей: остановить `worker`, убрать только два managed
nginx include, проверить HTTP 200 legacy origin, выгрузить актуальную
расшифрованную OAuth-пару в одноразовый root-only `/run` bind и prepared UPDATE
единственной legacy-строки, удалить handoff-файлы, затем включить только точную
legacy Withings-строку и остановить `web`/`db`. До включения legacy cron ошибка
автоматически возвращает v2 route и worker. Compose
containers, images, PostgreSQL volume, snapshot, `/srv/www/amigo`, MariaDB и
общий Telegram cron сохраняются. Для повторного cutover исправить причину,
вернуть чистый release и снова запустить `deploy.sh`; не применять `down -v`.

## Documentation checkpoint

После каждого production deploy или изменения runtime-контракта выполнить:

```bash
sudo bash /srv/amigo/deploy/checkpoint.sh \
  /srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ
```

Скрипт повторяет verification и записывает public URL, UTC/MSK время, Git SHA,
image refs/IDs, SHA-256 Compose/nginx-конфигурации, результаты проверок, точный
rollback path и команду отката. Он
не читает и не записывает секреты. Изменённые `AGENTS.md` и Markdown-файлы нужно
вернуть и закоммитить в канонический репозиторий до сообщения о завершении.
Documentation commit можно синхронизировать в production checkout без rebuild:
`/var/lib/amigo/current-release` продолжит указывать на фактически запущенный
image SHA, а checkout после синхронизации обязан остаться чистым.

<!-- BEGIN AMIGO PRODUCTION CHECKPOINT -->
- Status: **deployed and verified**
- Production URL: `https://amigo.tolstik.ru/amigo/`
- Verified at: `2026-08-19T17:49:14Z` (`2026-08-19 20:49:14 MSK`)
- Git SHA: `77a6699f9af2d564bbb252212ed32baeea00e746`
- Latest rollback snapshot: `/srv/amigo-rollbacks/20260819T174504Z`
- Installed config SHA-256: Compose `6727c935a9960a6a83c332c83b304d8814c26a328dd04d5a82ee63b2009847d7`; nginx locations `37ca8718449885e28e44fb04ded159913169d43b839024960c861063186d574e`; nginx rate limit `a887ddf70734dda6821fcd4db984d99dcb70993eff64a5ae4a2108e517a93362`.
- Verification: Compose `web`, `worker`, and `db` running; PostgreSQL ready; web bound to `127.0.0.1:18181`; direct health, hidden public health routes, origin proxy, public HTTPS dashboard/API, relative `308`, security headers, route rollback rehearsal, cron isolation, and rollback assets passed.
- Installed image references and IDs:

- `web`: `amigo:77a6699f9af2d564bbb252212ed32baeea00e746` (`sha256:8b53c6bff5b9db086df6de412296b0c972ac20d23c199028cf6ba52fc0271227`)
- `worker`: `amigo:77a6699f9af2d564bbb252212ed32baeea00e746` (`sha256:8b53c6bff5b9db086df6de412296b0c972ac20d23c199028cf6ba52fc0271227`)
- `db`: `postgres:17-alpine` (`sha256:1bea307dfb3ee30541a7acf7de14b58bcd6948da98e5d31a04c627c4d35ec64b`)
- Rollback command: `sudo /srv/amigo/deploy/rollback.sh /srv/amigo-rollbacks/20260819T174504Z`
<!-- END AMIGO PRODUCTION CHECKPOINT -->
