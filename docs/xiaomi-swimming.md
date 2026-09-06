# Бассейн: контракт Xiaomi и Amigo

Раздел `/swimming` использует только опубликованные `exercise` из активного
Xiaomi Cloud. Health Connect не заполняет пропуски. Старые записи без подробностей
остаются допустимыми; Android 1.5.0 повторно читает спортивную историю до
`2000-01-01` окнами по 30 дней. Остальные метрики и сопряжение сохраняются.

## Источники формата

Поля проверены по публичному описанию SportBasicReport и опубликованным
декларациям типов Xiaomi, без доступа к пользовательским учётным данным:

- [SportBasicReport и запрос истории](https://github.com/kevinkwee/Mi-Fitness-Sync/blob/b51fd421fbdda18b53c13aad12b6868731684583/docs/mi-fitness-activity-findings.md).
- [FitnessSportType](https://github.com/KurenaiRyu/XiaomiHealth/blob/04e79cf5debe3f9a43e57f6d981e472ea6b59637/decompiler/com/xiaomi/fit/data/common/data/annotation/FitnessSportType.java):
  `sport_type=9` — бассейн, `10` — открытая вода.
- [SwimmingPosture](https://github.com/KurenaiRyu/XiaomiHealth/blob/04e79cf5debe3f9a43e57f6d981e472ea6b59637/decompiler/com/xiaomi/fit/data/common/data/sport/SwimmingPosture.java):
  `0` смешанный, `1` брасс, `2` вольный, `3` на спине, `4` баттерфляй;
  неизвестные значения не передаются.
- [Нормализация дистанции и времени](https://github.com/kevinkwee/Mi-Fitness-Sync/blob/b51fd421fbdda18b53c13aad12b6868731684583/src/mi_fitness_sync/activity/client.py).
- [Gadgetbridge, Xiaomi pool summary](https://github.com/Freeyourgadget/Gadgetbridge/blob/a0948ee1cbc2a870f91d313f8e37df5f524465f7/app/src/main/java/nodomain/freeyourgadget/gadgetbridge/service/devices/xiaomi/activity/impl/WorkoutSummaryParser.java):
  `getPoolSwimmingParser` явно задаёт метры для `configuredLaneLength`, ккал,
  секунды и `LAPS` для счётчика дорожек. Эти поля соответствуют `poolWidth`
  (42) и `turnCount` (39) в Xiaomi `SportReportBaseParser`.
- [Xiaomi Huami converter](https://github.com/KurenaiRyu/XiaomiHealth/blob/04e79cf5debe3f9a43e57f6d981e472ea6b59637/decompiler/com/xiaomi/fit/fitness/device/hm/parse/repo/HmSportReportParse.java):
  `total_trips` переносится в `turnCount`, `swim_pool_length` — в `poolWidth`.

Спортивный запрос `/app/v1/data/get_sport_records_by_time` принимает
`startTime`, `endTime`, `reverse`, `limit=50`, `next_key`. Эти имена отличаются
от `start_time/end_time` у обычных fitness records.

## Разрешённые данные

| Поле Xiaomi | Поле `values.swimming` | Единица |
| --- | --- | --- |
| `distance` | `distance_meters` | метры |
| `valid_duration` | `active_duration_seconds` | секунды без пауз |
| `calories` | `kilocalories` | активные ккал |
| `min_hrm`, `avg_hrm`, `max_hrm` | `minimum_bpm`, `average_bpm`, `maximum_bpm` | уд/мин |
| `pool_width` | `pool_length_meters` | метры; значения вне 1–200 не интерпретируются |
| `turn_count` | `pool_lengths` | переданный счётчик дорожек, целое число |
| `main_posture` | `stroke_style` | нормализованный стиль из таблицы выше |

Счётчик `turn_count` переносится буквально: к нему не прибавляется единица,
он не удваивается и не выводится из гребков или округлённого отношения
дистанции к длине бассейна. Если счётчик отсутствует, UI показывает прочерк.
`avg_pace` не переносится: единица зависит от вида спорта. Сервер
рассчитывает секунды на 100 метров только из положительных `valid_duration`
и `distance`. Полная длительность `exercise` остаётся разностью конца и начала.

Спортивные поля не добавляются повторно к дневным метрам, калориям или числу
тренировок. Новые детали не расширяют контекст ИИ, Telegram или отчёт врача.
GPS, маршруты, названия тренировок, исходные ответы и отсчёты пульса в пакеты
Amigo не включаются. Числовой тип спорта имеет приоритет над категорией;
неопределённое `swimming` не считается бассейном.

## API и обновление

Защищённый `GET /api/v1/series/swimming?range=90d&offset=0` принимает
`30d/90d/1y/all`, возвращает сводку всего периода, точки графиков, до 50 сессий,
`next_offset` и агрегированное покрытие `missing/partial/available/confirmed_empty`.
Диапазон определяется московской датой начала тренировки; незавершённые будущие
сессии не публикуются. Пустая сумма неизвестной метрики — `null`; рядом с суммой
передаётся количество сессий с этой метрикой. Внутренние идентификаторы источника,
аккаунта и снимка не публикуются.

Полнота покрытия проверяется до конца последнего завершённого спортивного
окна, который возвращается в `coverage.to` и показывается в интерфейсе.
Промежуток после синхронизации не считается дырой в истории; для `all`
начало покрытия — `2000-01-01T00:00:00Z`, поэтому незавершённая полная
повторная загрузка остаётся частичной.

Миграция локального состояния Android атомарно записывает версию расширения,
перезапускает только исторический курсор `exercise` от момента обновления,
а его незавершённый свежий снимок получает новый ID с прежними границами окна.
Повторный запуск приложения не повторяет миграцию. Сервер принимает и старые
пакеты без `swimming`, и новый формат; контракт Health Connect остаётся строгим.
