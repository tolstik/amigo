# Amigo v4.0.0: дашборд внутри Android-приложения

Статус: реализация, CI, production cutover и публикация `v4.0.0` завершены.
Установку и проверку на физическом телефоне владелец выполняет самостоятельно.

## Кратко

- Итоговый production cutover выполнен и проверен на SHA
  `c8a684b7684751e32395be565df98b82f8f10b21`; checkpoint зафиксирован
  `2026-08-20T17:15:07Z`, rollback snapshot —
  `/srv/amigo-rollbacks/20260820T170954Z`.
- Android `1.1.0` (`versionCode 4`) выпущен как обновление существующего
  package `ru.tolstik.amigo.sync`: приложение называется **Amigo**, по умолчанию
  открывает дашборд, а нативная синхронизация остаётся второй вкладкой.
- Итоговый серверный и Android-релиз опубликован как
  [`v4.0.0`](https://github.com/tolstik/amigo/releases/tag/v4.0.0); APK —
  [`Amigo-1.1.0.apk`](https://github.com/tolstik/amigo/releases/download/v4.0.0/Amigo-1.1.0.apk).

## Основные изменения

### Android

- Добавить нижнюю навигацию «Дашборд» / «Синхронизация». Один WebView
  сохраняется при переключении вкладок и восстановлении Activity; Back проходит
  по истории SPA, из синхронизации возвращает в дашборд, с корня закрывает
  приложение.
- Загружать только верхнеуровневый `https://amigo.tolstik.ru/amigo/`;
  редактируемый ingest-server на адрес дашборда не влияет.
- Разрешить JavaScript, DOM storage и first-party cookies; запретить third-party
  cookies, mixed content, обход TLS-ошибок, внешние origin и опасные схемы. Не
  добавлять JavaScript bridge и не раскрывать WebView pairing/device/Health
  Connect state.
- Оставить существующий логин и 90-дневную WebView-сессию. Пароль в приложении
  не хранить; logout отзывает серверную сессию и не сбрасывает pairing.
- Реализовать выбор одного PDF/JPG/PNG/HEIC/HEIF через системный SAF без
  storage/camera permissions.
- Перехватывать только CSV-export и скачивание лабораторного оригинала. После
  «Сохранить как» потоково загружать файл с cookie только в памяти, запрещать
  redirects и чужой origin, очищать неполный файл при ошибке; после успеха
  предлагать открыть системным viewer.
- Показывать нативный online-error/retry при недоступности сети или WebView
  renderer. Медицинские страницы офлайн не кешировать.
- Ограничить пятисекундное обновление Health Connect-состояния временем показа
  вкладки синхронизации; WorkManager и фоновой импорт не менять.

### Mobile dashboard и публичные интерфейсы

- На ширинах 320–412 px уплотнить header без горизонтального overflow,
  обеспечить touch targets не меньше 44 px, перенос нескольких page actions и
  автоматическую прокрутку горизонтальной навигации к активному разделу.
- Добавить доступный с телефона logout в профиль через существующий
  `api.logout()`.
- Сохранить все текущие маршруты, API, CSRF и SSE-контракты; backend API и схема
  БД для мобильного релиза не меняются.
- Добавить verified App Links для точного host `amigo.tolstik.ru`, путей
  `/amigo` и `/amigo/...`. Deep link переключает приложение на дашборд и
  сохраняется через login.
- Публиковать `GET /.well-known/assetlinks.json` с package
  `ru.tolstik.amigo.sync`, отношением `handle_all_urls` и действующим
  release-сертификатом
  `25:CC:38:EC:B3:10:81:F6:82:6F:F0:49:B8:07:33:5A:05:E8:6E:E9:89:54:70:97:5E:85:21:AF:95:19:1C:02`.
- Android runtime принимает только известные SPA-маршруты; API-download URL
  обрабатываются отдельным строгим allowlist.

## Проверки и выпуск

- Предварительный production cutover и полная проверка семи сервисов,
  auth/labs/assistant и post-start Withings job завершены на SHA `004d642…`.
- Frontend: unit/build и Playwright на 320, 360, 412 px — login/deep link/logout,
  все страницы, темы, навигация, формы/клавиатура, лабораторные действия,
  uploads всех форматов, CSV/original downloads и отсутствие общего overflow.
- Android: unit-тесты URL/navigation/download allowlist, имени файла и запрета
  cookie forwarding; instrumentation на API 28 и 36 для вкладок, Back, cookie
  persistence, file chooser, Save As, offline/retry, rotation и App Link
  intents. Выполнить `testDebugUnitTest`, `lintDebug`, `assembleDebug` и signed
  `assembleRelease`.
- APK signer и SHA-256 проверены до публикации и повторно после скачивания из
  GitHub release. Владелец устанавливает APK поверх 1.0.2 и подтверждает на
  физическом телефоне сохранность Keystore identity, pairing, выбранного origin
  и sync cursors.
- На физическом телефоне проверить verified App Links, login/logout, все
  разделы, темы, профиль, лабораторный upload/edit/confirm/delete/download,
  assistant SSE/reconnect, CSV, ручную и фоновую синхронизацию.
- Tag/release `v4.0.0` создан на deployed commit, `Amigo-1.1.0.apk` приложен.
  Production verification и documentation checkpoint фиксируют deployed
  SHA/images, APK URL/SHA-256/certificate, App Links result и rollback snapshot.

## Зафиксированные границы

- Подтверждение и отзыв Android pairing остаются в root-only CLI.
- Прямая съёмка камерой и встроенный PDF/image viewer не добавляются;
  используются Files/Photos и системный viewer.
- Веб-аутентификация не связывается с ingest-ключом.
- PWA/service worker и офлайн-хранилище медицинского дашборда не добавляются.
- Release signing использует существующий закрытый keystore; его пароли и
  другие секреты не выводятся, не коммитятся и не документируются.

## Текущий прогресс реализации

- Реализованы защищённый WebView, нижняя навигация, App Links, SAF upload/Save
  As, строгие navigation/download allowlists, native retry и ограничение
  polling вкладкой синхронизации.
- Mobile dashboard адаптирован для 320/360/412 px, добавлены mobile logout и
  E2E-сценарии навигации/download.
- Пройдены 29 Android unit-тестов, `lintDebug`, `assembleDebug`, signed
  `assembleRelease`, 22 frontend unit-теста и production frontend build.
- Signed `Amigo-1.1.0.apk` имеет SHA-256
  `6b950bc3c6e5ba58709830d3c25fcc04d25f16c86c748379298b8a423176984d`; package,
  version, label и signing certificate сверены.
- Полный Playwright run прошёл: 22/22 сценария в desktop Chromium и Pixel 7.
- Feature commit `adf16eb52dd366fcffe1e967b45e1104079d1bb9` отправлен в
  `origin/main`, CI полностью прошёл. Первый production deploy создал verified
  snapshot `/srv/amigo-rollbacks/20260820T164455Z`, но pre-cutover assistant
  smoke получил единичный error event; automatic recovery полностью вернул
  release `004d6423855f235f34e46a1643cc1de99e21e07e` и managed route.
- Добавлен один bounded retry только для invalid/error assistant smoke; analysis
  и laboratory smoke не повторяются, а второй assistant result обязан полностью
  валидироваться. Следующая попытка штатно восстановилась после ошибочно строгой
  public `assetlinks` POST-проверки; финальная проверка разделяет origin `405` и
  безопасный public edge `403`/`405`.
- CI `32395950697` полностью прошёл на `c8a684b…`. Production deployment и все
  проверки семи сервисов, post-start Withings job, App Links, auth/CSRF,
  лабораторий, assistant/SSE и signed ingest завершены успешно. Release
  `v4.0.0` опубликован; повторно скачанный APK сохранил SHA-256 и сертификат.
  Установку и физическую проверку APK владелец выполняет самостоятельно без
  подключения телефона к этой сессии.
