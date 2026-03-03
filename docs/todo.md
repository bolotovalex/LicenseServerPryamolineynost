# Prompt для Claude Code + план реализации

## 1) Готовый prompt для Claude Code

```md
Ты работаешь в репозитории FastAPI `LicenseServerPryamolineynost`.
Нужно реализовать изменения по backend + frontend + API, без поломки текущей логики.

Ограничения:
- Делай изменения поэтапно и коммить по функциональным блокам.
- Не делай destructive-команд.
- Сохраняй существующую архитектуру (`app/routers`, `app/models.py`, Jinja templates).
- Для БД используй startup-миграции в `app/main.py` (SQLite совместимо).
- Добавь выбор типа БД через конфиг/переменные окружения: `sqlite`, `postgres`, `mariadb` (`mysql` как алиас).
- После каждого этапа запуск smoke-проверок и фикс regressions.

Обязательные задачи:
1. UI owner dashboard: убрать кнопку «Все клиенты».
2. Страница owner backup привести к общему стилю owner.
3. Feedback: добавить диалог «вопрос-ответ» внутри системы (не только email), дублировать ответ по email, хранить ответы в БД.
4. Ввести явные статусы ключей: `not_activated`, `blocked`, `released`, `activated`, `expired`.
5. Клиент (org) должен видеть диалог feedback и отвечать.
6. Логин: вход для org по `login` ИЛИ `contact_email` (owner по email как сейчас).
7. Обновить placeholder-примеры в формах.
8. В кабинете org добавить управление лицензиями: создание, изменение, отзыв, повторная активация; показывать `device_id`; нельзя менять `max_keys`.
9. Owner может менять `max_keys`; при уменьшении лимита дать инструмент блокировки «лишних» ключей.
10. Привести проект к единому оформлению (owner/org/public).
11. В таблицах клиентов убрать подпись/колонку «Открыть» (оставить переход по клику или кнопку без текста).
12. Изменить формат ключа на `xxxx-xxxx-xxxx` (12 символов, uppercase A-Z0-9).
13. Исправить меню «три точки» у ключа (z-index/overflow, меню скравается за элементами блока(не видно)).
14. Убрать блок «исчерпание квоты»; добавить кнопку «Генерация лицензий» в заголовок таблицы лицензий справа. Если лицензий = макс лицензий, то кнопка не активна.
15. В карточке клиента перенести `логин/создан/email` в заголовок блока «Информация о клиенте».
16. Таблица лицензий/журнал: убрать текст «Действия», центровать заголовки и почти все значения (кроме ключа), добавить изменение ширин колонок, в журнал добавить `license_id` и окончание ключа (последние 4 символа).
17. При удалении организации поддержать восстановление удаленных пользователей.

API для мобильного клиента:
- Реализовать сценарии активации/переактивации/переноса по device_id, деактивации, обработку blocked/expired/not found.
- Запрос активации должен принимать: ключ, `device_id`, `device_name`, `comment`, `activated_at`, `key_version`.
- Логировать все операции (БД + audit/logs) с IP и device_id.
- Добавить endpoint истории ключа для owner/org.
- При генерации ключа убрать обязательное описание, оставить «автоматическая генерация».

Требуемые изменения по файлам:
- `config/database.cfg`, `app/config.py`, `app/db.py`: выбор драйвера/DSN и инициализация движка для sqlite/postgres/mariadb(mysql).
- `app/models.py`: статус лицензии, feedback thread/message, soft-delete сущности.
- `app/main.py`: миграции новых колонок/таблиц.
- `app/utils.py`: новый формат генерации ключа.
- `app/routers/public_api.py`: новые mobile endpoints + error codes.
- `app/routers/owner_web.py`, `app/routers/org_web.py`, `app/routers/feedback.py`, `app/routers/auth.py`: новая логика.
- `templates/owner/*`, `templates/org/*`, `templates/feedback.html`, базовые шаблоны/стили.
- `app/email.py`: email-дубли ответов по feedback.
Создай файл для создания системы лицензирования в отдельном приложении(промпт для claude code) с поддержкой api данной системы.

После реализации:
- Обнови `README.md` (новые API, статусы, UX).
- Добавь/обнови тесты (pytest) для статусов и API-сценариев.
- Сформируй список выполненных пунктов 1–17 + API-сценарии 1–7.
```

## 2) Пошаговый план реализации

## Этап 0. Подготовка
- Снять baseline: `git status`, smoke login owner/org, `/api/activate`, `/owner/clients/{id}`.
- Зафиксировать текущие коды ошибок API и текущие статусы (через `is_blocked`, `activated_at`, `expires_at`).
- Зафиксировать текущую конфигурацию БД и подготовить матрицу подключений:
  - sqlite: `sqlite+aiosqlite:///./data/licserver.db`
  - postgres: `postgresql+asyncpg://user:pass@host:5432/dbname`
  - mariadb/mysql: `mysql+aiomysql://user:pass@host:3306/dbname`

## Этап 0.1. Поддержка выбора БД
- Ввести в `config/database.cfg` параметры `db_type` и `url` (или `dsn`), где:
  - `db_type=sqlite|postgres|mariadb|mysql`,
  - `mysql` нормализуется в `mariadb` для внутренней логики.
- В `app/config.py` валидировать поддерживаемые значения и строить итоговый DSN.
- В `app/db.py` создавать `engine` с правильным async-драйвером по `db_type`.
- Обновить `requirements.txt`: добавить async-драйверы для Postgres и MariaDB/MySQL.
- Обновить `docker-compose.yml` примерами сервисов для Postgres и MariaDB (профили или альтернативные compose-файлы).
- Проверить startup и базовые CRUD-операции на каждой БД.

## Этап 1. Данные и миграции
- В `License` добавить `status` (строка/enum), `device_name`, `device_comment`, `released_at`, `deleted_at` (если нужно soft-delete).
- Для feedback добавить таблицу сообщений (например `FeedbackMessage`) с направлением (`from_owner`, `from_org`), текстом, временем, флагом email-дублирования.
- Для восстановления после удаления добавить soft-delete для `Client` и связанных org-учеток.
- В `app/main.py` добавить SQLite-safe миграции `ALTER TABLE ... ADD COLUMN` + backfill статусов:
  - `blocked`, `expired`, `activated`, иначе `not_activated`.

## Этап 2. API мобильной активации
- Расширить `/api/activate` и/или добавить v2 endpoints:
  - activate (новые поля устройства),
  - deactivate/release,
  - transfer by device.
- Реализовать сценарии 1–6 с явными кодами ошибок:
  - `LICENSE_NOT_FOUND`, `LICENSE_EXPIRED`, `LICENSE_BLOCKED`, `DEVICE_MISMATCH`, `LICENSE_ALREADY_ACTIVE`, `INVALID_STATUS`.
- Во все ветки добавить audit + `LicenseAction` (IP, device_id, reason).
- В ответах передавать: срок, версию, организацию, `license_id`, логотип (если есть).

## Этап 3. Auth и роли
- В `auth.py` для org искать по `Client.login == login OR Client.contact_email == login`.
- Сохранить существующую логику owner по email.

## Этап 4. Owner/Org функционал лицензий
- `org_web.py`: дать org CRUD-подобные операции по своим ключам (генерация/редактирование/отзыв/переактивация) без права менять `max_keys`.
- `owner_web.py`: при снижении `max_keys` показать и дать блокировку лишних ключей.
- Добавить кнопку «История» у ключа для owner и org.

## Этап 5. Feedback-диалог
- Owner может отвечать на обращение внутри системы.
- Org видит ответ и может писать встречный ответ.
- Ответ сохраняется в БД и дублируется на email организации (`app/email.py` + email template).

## Этап 6. UI/UX унификация
- Удалить «Все клиенты» в owner dashboard.
- Убрать подпись «Открыть» в списках клиентов.
- Привести `owner/backup` к стилю `base_owner.html`.
- Перенести `логин/создан/email` в шапку блока информации клиента.
- Таблицы лицензий/журнала:
  - без подписи «Действия»,
  - шапка по центру,
  - данные по центру (кроме ключа),
  - настраиваемые ширины колонок (через `<colgroup>` + CSS resize/utility classes),
  - в журнале: `license_id` и хвост ключа (последние 4).
- Исправить выпадающее меню `...` (overflow/z-index/positioning).
- Удалить блок «исчерпание квоты», добавить кнопку генерации в правый край заголовка таблицы лицензий.
- Обновить примеры заполнения полей.

## Этап 7. Формат ключа и статусы
- Обновить `generate_license_key()` на `XXXX-XXXX-XXXX`.
- Проверить уникальность/коллизии и валидацию.
- Во всех UI/API показывать новые статусы: Не активирован, заблокирован, освобожден, активирован, истек.

## Этап 8. Тесты и документация
- Добавить `tests/`:
  - статусы и переходы,
  - mobile API сценарии 1–6,
  - auth login/email,
  - feedback reply thread.
- Обновить README (новые endpoints, статусы, управление ключами org).

## Рекомендуемая структура коммитов
1. `feat(db): add license status model and feedback message thread`
2. `feat(api): implement mobile activation/deactivation/transfer scenarios`
3. `feat(auth): allow org login by login or email`
4. `feat(org): add self-service license management and history`
5. `feat(owner): max_keys reduction flow and overflow key blocking`
6. `feat(feedback): add in-system replies with email duplication`
7. `refactor(ui): unify owner/org styling and table behaviors`
8. `chore(docs/tests): update README and add regression tests`
