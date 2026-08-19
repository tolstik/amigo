# Production verification checklist

Заполнять для каждого cutover. Автоматические пункты выполняет
`sudo bash /srv/amigo/deploy/verify-production.sh`; ручные пункты нельзя
подменять ответом только одного HTTP endpoint.

## До cutover

- [ ] Выбран и записан точный Git SHA; production checkout root-owned и
      полностью чист, включая untracked source-файлы.
- [ ] `/srv/amigo/.env` и восемь файлов `/srv/amigo/secrets/` непустые,
      доступны только владельцу и не попали в Git или shell output.
- [ ] `docker compose ... config --quiet` и `nginx -t` успешны.
- [ ] Public DNS ведёт на TLS edge; `curl` без `-k` принимает сертификат для
      `amigo.tolstik.ru`. Origin и edge не смешиваются в одной TLS-проверке.
- [ ] `/srv/www/amigo`, MariaDB `amigo` и обе ожидаемые строки crontab на месте.
- [ ] Достаточно места для legacy archive, SQL dump, images и PostgreSQL data.
- [ ] Создан конкретный `/srv/amigo-rollbacks/YYYYMMDDTHHMMSSZ`, все строки
      `SHA256SUMS` имеют `OK`, tar/gzip читаются.
- [ ] Владелец явно разрешил помеченный Telegram smoke; сообщение пришло в
      правильный чат. Старые измерения при полном импорте не отправлены.
- [ ] До первого Withings API request legacy collector переведён в единственный
      disabled-marker; после full sync актуальная OAuth-пара без stdout
      возвращена в ровно одну legacy token row для рабочего rollback.

## Автоматические проверки после cutover

- [ ] `db`, `web`, `worker` имеют state `running`; объявленный health не хуже
      `healthy`; `pg_isready` успешен. `web` и `worker` используют один image
      `amigo:<Git SHA>`, а не `latest`; SHA совпадает с
      `/var/lib/amigo/current-release` после фиксации cutover.
- [ ] Публичный `web` не имеет Withings/Telegram/Fernet secret mounts;
      интеграционные секреты доступны только `worker`.
- [ ] `/srv/amigo/data/import/legacy-weight.tsv` принадлежит root, закрыт для
      group/world и смонтирован в `web` как read-only `/imports`.
- [ ] Единственный listener 18181 — `127.0.0.1:18181`; direct `/healthz`
      отвечает, внешние `/healthz`, `/amigo/healthz` и
      `/amigo/internal/health` не возвращают 2xx.
- [ ] В `my.conf` ровно два managed marker, установленный snippet совпадает с
      release, rate-limit zone `amigo_read` установлен, `nginx -t` успешен.
- [ ] Origin с `Host: amigo.tolstik.ru` отвечает; exact `/amigo` возвращает
      `308`, а `/amigo/` — `200`.
- [ ] Public `https://amigo.tolstik.ru/amigo` возвращает относительный
      `Location: /amigo/`; dashboard и `/amigo/api/v1/overview` отвечают через
      валидный внешний TLS, API body — JSON.
- [ ] Присутствуют `Cache-Control: no-store`, `X-Robots-Tag: noindex,
      noarchive`, `X-Content-Type-Options` и CSP; hashed assets имеют один
      `Cache-Control: public, max-age=31536000, immutable`.
- [ ] Активной точной строки `get_withings.php` нет, disabled-marker ровно один,
      точная общая строка `send_telergam.php all` сохранена.
- [ ] `/srv/www/amigo` и схема MariaDB `amigo` всё ещё существуют.
- [ ] Route-only rehearsal реально показал legacy dashboard, затем v2 route
      снова включён и полный verification повторно прошёл.

## Ручная продуктовая проверка

- [ ] Desktop и mobile: «Обзор», «Прогресс», «Вся история», «Давление» и
      «Состав тела» открываются без browser console errors.
- [ ] Программа начинается 15.08.2026; более ранние веса видны только во всей
      истории и не влияют на KPI/forecast; до первого программного замера KPI
      веса остаётся пустым.
- [ ] Последний вес и количество Withings groups сверены с импортом; история
      давления и состава тела не пустая там, где source содержит значения.
- [ ] Root-only legacy TSV создан из `date_creat, weight`, импортирован с UTC и
      scale `0.001`; legacy-only строки присутствуют, совпадения не дублируются.
- [ ] Давление показано описательно, без медицинских категорий и рекомендаций;
      BIA-показатели явно отмечены как приблизительные.
- [ ] CSV export, фильтры периода, светлая/тёмная тема и табличная альтернатива
      графикам работают.
- [ ] После следующего sync-цикла (не менее 5 минут) нет повторной группы,
      duplicate Telegram event или ошибок refresh/outbox в логах.
- [ ] В понедельничном расписании используется `08:00 Europe/Moscow`; worker не
      зависит от host cron.

## Завершение

- [ ] `deploy/checkpoint.sh` записал public URL, Git SHA, image IDs, SHA-256
      установленной Compose/nginx-конфигурации, результаты verification и
      точный rollback snapshot без секретов.
- [ ] Изменения `AGENTS.md`, runbook и `production-checkpoint.md` перенесены в
      канонический Git и закоммичены.
- [ ] Владелец получил production URL и rollback command. Deployment объявлен
      завершённым только после этих пунктов.
