# Выполненные задачи

## Этап 1 — Dashboard + Backup (2026-03-04)

### 1а. Dashboard: убрана кнопка «Все клиенты»
- `templates/owner/dashboard.html`: кнопка «+ Новый клиент» перенесена в заголовок таблицы клиентов (справа), отдельная кнопка в шапке страницы убрана
- Строки таблицы стали кликабельными (`onclick`), колонка «Открыть» убрана

### 1б. Страница backup приведена к общему виду

- `templates/owner/backup.html`: уведомления об ошибке и успехе заменены с inline-стилей на классы `.flash.error` / `.flash.success`
- Кнопка «Удалить» использует `.btn.small.red` (единый стиль)
- `app/routers/owner_web.py`:
  - `backup_create` → flash redirect «Резервная копия создана»
  - `backup_upload` → flash redirect «Файл загружен»
  - `backup_delete` → flash redirect «Файл удалён»
  - `dt.datetime.utcnow()` заменено на `dt.datetime.now(dt.UTC)`

## Этап 2 — Карточка клиента (owner) (2026-03-04)

### 2а. Убран блок «превышение квоты»
- `templates/owner/client_detail.html`: удалён блок `{% if excess_licenses %}...{% endif %}`
- Кнопка «+ Выпустить лицензию» теперь всегда видна; при исчерпанной квоте — `disabled` с tooltip
- При `total_keys > client.max_keys` показывается индикатор `⚠ N/M` красным рядом с заголовком секции
- `app/routers/owner_web.py`: убраны вычисление и передача `excess_licenses` в контекст

### 2б. Русские названия статусов лицензий
- Используется `lic.computed_status(now)` вместо ручного вычисления
- Отображение: «Не активирован», «Активирован», «Освобождён», «Заблокирован», «Истёк»

### 2в. Устройство: device_name + device_id в title
- Колонка «Устройство» показывает `device_name` если задано, иначе обрезанный `device_id`; полный `device_id` в атрибуте `title`

### 2г. Кнопка «История действий» (📋)
- В таблице лицензий добавлена кнопка рядом с «📖»
- `app/routers/owner_web.py`: в контекст добавлен `actions_payloads` (группировка `LicenseAction` по `license_id`)
- Модалка `actionsModal` показывает хронологию действий с бейджами и причиной

### 2д. Resizable columns
- JS-resizer на `<th>` для таблиц лицензий и журнала действий (класс `resizable-table`)

### 2е. Плейсхолдеры в форме создания клиента
- `templates/owner/client_list.html`: обновлены примеры — «АО Технопарк Сибирь», `technopark_sib`, `admin@technopark-sib.ru`, «Договор №456 от 15.01.2025»

### Коммиты
- `4c7c93b` — refactor(ui): убрать кнопку «Все клиенты» из dashboard, привести backup к общему стилю
- `5f2de16` — refactor(owner): карточка клиента — убрать excess_licenses, русские статусы, модалка действий, resizable columns
