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
