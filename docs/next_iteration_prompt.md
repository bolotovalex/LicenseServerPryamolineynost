# Промпт для Claude Code — следующая итерация LicenseServerPryamolineynost

Ты работаешь в репозитории **LicenseServerPryamolineynost** — FastAPI-сервер лицензирования ПО.
Стек: Python 3.13, FastAPI, SQLAlchemy async (aiosqlite), Jinja2, один файл `static/styles.css`.

---

## Архитектура (справка)

```
app/
  models.py           — ORM: AdminUser, Client, License, LicenseKey, LicenseAction,
                         AuditLog, Feedback, FeedbackMessage, LoginAttempt, AppSetting
  routers/
    auth.py           — /login, /logout, /forgot-password, /reset-password
    owner_web.py      — /owner/* (admin panel, JWT cookie owner_token)
    org_web.py        — /org/*   (org cabinet, JWT cookie org_token)
    public_api.py     — /api/*   (HMAC-protected JSON API для мобильных клиентов)
  utils.py            — generate_license_key() → XXXX-XXXX-XXXX (уже 12 символов!)
  api_signing.py      — HMAC-SHA256 проверка подписи
static/styles.css     — единый CSS (переменные, .section, .btn, .badge, .table-wrap, .row-actions)
templates/
  base_owner.html     — layout owner (nav: Панель, Клиенты, Резервные копии, Обратная связь, Журнал, Администраторы)
  base_org.html       — layout org
  owner/              — шаблоны владельца
  org/                — шаблоны организации
```

**Ключевые факты уже реализованного** (не переделывай без необходимости):
- Формат ключа `XXXX-XXXX-XXXX` (12 символов) — уже в `app/utils.py`
- Вход по логину **ИЛИ** `contact_email` — уже в `app/routers/auth.py`
- `Feedback` + `FeedbackMessage` модели — диалог owner ↔ org существует
- `POST /owner/feedback/{id}/reply` и `POST /org/feedback/{id}/reply` — уже работают, ответ дублируется на email
- Кнопка "Выпустить лицензию" в заголовке таблицы у owner — уже добавлена
- `overflow:visible` на `.section` с лицензиями — уже исправлено
- Столбец "Открыть" в `client_list.html` — уже убран; строки кликабельны
- `Client.deleted_at` — мягкое удаление уже в модели
- Статусы лицензии в БД: `not_activated`, `activated`, `released`, `blocked`; `expired` — вычисляется по `expires_at`
- `LicenseAction` — журнал действий; в `client_detail.html` уже есть колонки "Лиц." и "Ключ" (4 символа)

---

## Часть 1 — UI/UX правки

### 1. Dashboard: убрать кнопку «+ Новый клиент»
Файл: `templates/owner/dashboard.html`
В шапке страницы (строка с `<h1>Панель управления</h1>`) убрать кнопку
`<a href="/owner/clients" class="btn small">+ Новый клиент</a>`.
Новых клиентов создают через страницу `/owner/clients`.

---

### 2. Страница резервных копий — привести к общему виду
Файл: `templates/owner/backup.html`

Привести к стилю остальных owner-страниц:
- Заголовок `<h1>` без лишних `flex`-оберток → такой же стиль как у `/owner/clients`
- Кнопки "Создать резервную копию" и "Загрузить файл" — разместить в шапке `.section`, стандартными `.btn`
- Уведомления об ошибке / успехе восстановления — через flash-механизм (`_flash(url, msg, msg_type)` + redirect), а не inline-блоки в шаблоне. Если redirect невозможен (загрузка с ошибкой), допустимо оставить inline.
- Таблица файлов — обернуть в `.section` → `.table-wrap` по стандарту

---

### 3. Обратная связь — ответ в интерфейсе (уже реализован, проверь работоспособность)
Система диалогов `Feedback ↔ FeedbackMessage` уже есть. Проверь:
- `POST /owner/feedback/{id}/reply` сохраняет `FeedbackMessage(sender_type="admin")` и отправляет email org
- `POST /org/feedback/{id}/reply` сохраняет `FeedbackMessage(sender_type="org")` и отправляет email admin
- Шаблоны `templates/owner/feedback_detail.html` и `templates/org/feedback_detail.html` показывают диалог

Если что-то не работает — исправь. Ничего нового добавлять не нужно.

---

### 4. Статусы лицензии — русские названия и полный набор бейджей
Во **всех** шаблонах заменить английские статусы на русские с правильными классами `.badge`:

| Статус (computed_status) | Бейдж | Класс |
|---|---|---|
| `not_activated` | Не активирован | `.badge.new` |
| `activated` | Активирован | `.badge.success` |
| `released` | Освобождён | `.badge.warn` |
| `blocked` | Заблокирован | `.badge.danger` |
| `expired` | Истёк | `.badge.gray` |

Затронутые файлы:
- `templates/owner/client_detail.html` (таблица лицензий, строка статуса)
- `templates/org/dashboard.html` (таблица лицензий)
- `templates/owner/dashboard.html` (таблица клиентов — там статус клиента, не лицензии, оставь как есть)

---

### 5. Кабинет организации: ответ в обратной связи (уже реализован, проверь)
`/org/feedback` и `/org/feedback/{id}` — уже существуют. Проверь что организация может:
- Видеть список своих обращений (`GET /org/feedback`)
- Открывать обращение с историей переписки (`GET /org/feedback/{id}`)
- Отправлять ответ (`POST /org/feedback/{id}/reply`)

---

### 6. Вход по email или логину — уже реализован
`app/routers/auth.py` уже ищет Client по `or_(Client.login == login, Client.contact_email == login)`.
Проверить работу, не менять.

---

### 7. Примеры заполнения полей (placeholder) — заменить
Файл: `templates/owner/client_list.html` (форма создания клиента)

| Поле | Старый placeholder | Новый placeholder |
|---|---|---|
| org_name | `ООО Рога и Копыта` | `ООО Строй Мастер` |
| login | `roga_kopyta` | `stroy_master` |
| contact_email | `info@example.com` | `director@stroy-master.ru` |
| key_ttl_days | `365` | `365` (оставить) |
| notes | `Дополнительная информация` | `Договор №123 от 01.01.2025` |

В форме редактирования клиента (`templates/owner/client_detail.html`):
- Убрать примеры, если они слишком «тестовые»; поля уже заполнены данными клиента.

---

### 8. Кабинет организации: полное управление лицензиями

Org должна уметь: **создавать, изменять описание, деактивировать, сбрасывать (reset), видеть device_id**.

#### 8а. Текущий функционал (уже есть):
- `POST /org/licenses/generate` — выпустить новую лицензию
- `POST /org/licenses/{id}/edit` — изменить описание
- `POST /org/licenses/{id}/deactivate` — деактивировать активированную лицензию (статус → released)

#### 8б. Добавить: сброс ключа (reset)

Org должна иметь возможность сбросить лицензию в `not_activated` — старый ключ деактивируется, выпускается новый ключ.

Добавить endpoint в `app/routers/org_web.py`:
```python
@router.post("/licenses/{license_id}/reset")
async def org_license_reset(request, license_id, db):
    # 1. Проверить принадлежность лицензии org
    # 2. Деактивировать активный LicenseKey (is_active=False, deactivated_at=now, reason="reset by org")
    # 3. Сгенерировать новый ключ, создать LicenseKey(is_active=True)
    # 4. Сбросить license: status="not_activated", device_id=None, activated_at=None, device_name=None, version+=1
    # 5. Создать LicenseAction(action="reset", actor=org.login)
    # 6. log_action(...)
```

#### 8в. Показать device_id и device_name в таблице лицензий org

В `templates/org/dashboard.html` столбец "Устройство" должен показывать:
- `device_name` если задано, иначе `device_id[:20]…`
- **ID устройства** в `title` атрибуте для hover-просмотра
- Если нет ни того ни другого — `—`

#### 8г. Кнопки в таблице лицензий org

В `templates/org/dashboard.html` для каждой лицензии добавить кнопки:
- **Копировать** — уже есть
- **Изм.** (изменить описание) — уже есть
- **Деактивировать** — уже есть (только у activated)
- **Сбросить ключ** — новая кнопка (только у released, not_activated); `POST /org/licenses/{id}/reset`; с подтверждением `confirm()`
- **История** — открывает модалку с историей действий по лицензии (см. п. 16)

---

### 9. Владелец: изменение количества ключей при превышении квоты

Если `total_keys > client.max_keys` после сохранения в `owner_web.py`:
- **Убрать** специальный блок `excess_licenses` из шаблона (п. 14 ниже — этот блок уже убирается там)
- Владелец блокирует лишние ключи **через стандартный three-dots dropdown** в таблице лицензий
- В заголовке таблицы лицензий добавить индикатор (если превышение): `⚠ {{ total_keys }}/{{ client.max_keys }}` красным цветом вместо обычного `{{ total_keys }}/{{ client.max_keys }}`

В `app/routers/owner_web.py` функция `client_update_info`:
- Убрать вычисление `excess_licenses` и передачу его в контекст
- Если `total > new_max_keys`, flash-предупреждение: "Квота уменьшена до X. Выпущено Y лицензий — заблокируйте лишние через меню ⋮" (тип `warn`)

---

### 10. Единое оформление всего проекта

Привести **все** шаблоны к единому стилю `static/styles.css`:

#### Правила:
- Все карточки — класс `.section` (background:#fff, border:1px solid #e2e8f0, border-radius:14px, padding:20px, margin-bottom:20px)
- Все таблицы — `.table-wrap` → `<table class="table-compact">`
- `base_org.html` должен использовать те же CSS-переменные и `.section`, что и `base_owner.html`
- Бейджи: только классы `.badge .success/.danger/.warn/.new/.gray/.purple/.orange/.yellow`
- Кнопки: только `.btn`, `.btn.small`, `.btn.ghost`, `.btn.green`, `.btn.amber`, `.btn.red`
- Flash-сообщения — только через `?msg=...&msg_type=...` (класс `.flash`)
- Нет inline `background-color: #fef2f2` и подобных — только CSS-переменные или классы

#### Добавить в `static/styles.css` если не хватает:
```css
/* org-специфика (перенести из base_org.html если там есть локальные стили) */
.org-nav { /* аналогично .owner-nav */ }
```

Убедиться что org-страницы выглядят так же профессионально как owner-страницы.

---

### 11. Список клиентов: колонка «Открыть» — уже убрана ✓

---

### 12. Длина ключа XXXX-XXXX-XXXX — уже реализована ✓

---

### 13. Three-dots меню: видимость — уже исправлена (overflow:visible) ✓

---

### 14. Убрать блок «превышение квоты» из client_detail

Файл: `templates/owner/client_detail.html`

**Убрать полностью** блок `{% if excess_licenses %}...{% endif %}` с красным предупреждением.
Убрать также условие `{% if max_allowed > 0 %}...{% else %}<span>Квота исчерпана...</span>{% endif %}` —
кнопку "+ Выпустить лицензию" показывать **всегда** в заголовке таблицы, но если квота исчерпана —
кнопку деактивировать (`disabled`) с `title="Квота исчерпана"`.

В `app/routers/owner_web.py` убрать вычисление `excess_licenses` из контекста `client_detail`.

---

### 15. Карточка клиента: структурировать информацию

Файл: `templates/owner/client_detail.html`

Перенести **нередактируемые поля** (Логин, Создан, Создан кем) из строки под формой — в блок
заголовка карточки "Информация о клиенте" рядом с названием организации:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Информация о клиенте                                                 │
│                                                                      │
│ [Логотип]  | Название организации *   | Контактный email            │
│            | Максимум лицензий         | Срок ключей                 │
│            | Примечания                                              │
│            |                                                         │
│            | [Сохранить изменения]                                   │
│                                                                      │
│ Логин: stroy_master  ·  Создан: 2025-01-15  ·  Создан: admin@...   │
│ [Сбросить пароль] [Отключить] [Удалить клиента]                     │
└─────────────────────────────────────────────────────────────────────┘
```

Поля "Логин", "Создан", "Создан кем (email)" — вынести в отдельную строку под основной формой,
перед кнопками действий. Оформить как:
```html
<div class="info-row">
  <span>Логин: <code>{{ client.login or '—' }}</code></span>
  <span>Создан: {{ client.created_at.strftime('%Y-%m-%d') }}</span>
  {% if creator %}<span>Создан: {{ creator.email }}</span>{% endif %}
</div>
```
С небольшим разделителем `·` между полями.

---

### 16. Таблица лицензий и журнал действий

#### Таблица лицензий (`templates/owner/client_detail.html`):
- `<th>` для колонки действий — без текста (уже сделано ✓)
- Все заголовки колонок — `text-align:center` (уже частично сделано ✓)
- Все колонки с данными кроме "Ключ" и "Описание" — `text-align:center`
- **Изменяемая ширина колонок**: добавить CSS `resize:horizontal; overflow:auto` на `<th>` через JS:
  ```js
  // Resizable columns: добавить mousedown/mousemove на <th>
  ```
  Реализовать простой JS-resizer для `<th>` в `{% block scripts %}`.
- Показывать `device_name` в колонке "Устройство" (уже есть у owner), а в `title` — полный `device_id`

#### Журнал действий (`templates/owner/client_detail.html` БЛОК 4):
- Колонки "Лиц." (license_id) и "Ключ" (4 символа окончания) — уже добавлены ✓
- Изменяемая ширина колонок — аналогично таблице лицензий

#### Кнопка «История» у каждого ключа:
В таблице лицензий добавить кнопку "История" (рядом с книгой и QR),
открывающую модалку с **действиями по этой лицензии** (`LicenseAction`).
Данные передаются через `data-open-actions='[...]'` аналогично `data-open-keys`.

В `app/routers/owner_web.py` в `client_detail`:
```python
# Уже собираются keys_payloads — добавить actions_payloads:
actions_payloads = {
    lic.id: [
        {"action": a.action, "at": a.at.isoformat()[:16], "actor": a.actor or "—",
         "reason": a.reason or "", "ip": a.ip or ""}
        for a in sorted(lic.actions, key=lambda x: x.at, reverse=True)[:20]
    ]
    for lic in licenses
}
```

---

### 17. Восстановление удалённых организаций (soft-delete restore)

#### Модель (уже есть):
`Client.deleted_at: DateTime | None` — при мягком удалении ставится метка времени.

#### Роутер `app/routers/owner_web.py`:
Текущий `/clients` фильтрует `Client.deleted_at == None` — добавить параметр `show_deleted`:

```python
@router.get("/clients", ...)
async def client_list(..., show_deleted: bool = False):
    q = select(Client)
    if show_deleted:
        q = q.where(Client.deleted_at != None)
    else:
        q = q.where(Client.deleted_at == None)
    ...
```

Добавить endpoint восстановления:
```python
@router.post("/clients/{client_id}/restore")
async def client_restore(client_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    owner = await require_owner(request, db)
    client = await db.get(Client, client_id)
    if not client or client.deleted_at is None:
        raise HTTPException(404)
    client.deleted_at = None
    await log_action(db, actor_type="admin", actor_id=owner.id, actor_login=owner.email,
                     action="restore_client", entity_type="client", entity_id=client.id, request=request)
    await db.commit()
    return _flash(f"/owner/clients/{client.id}", "Организация восстановлена")
```

#### Шаблон `templates/owner/client_list.html`:
Добавить переключатель "Показать удалённые" (ссылка `?show_deleted=1`).
При `show_deleted=True` — показывать отдельную таблицу с удалёнными клиентами,
у каждого — дата удаления и кнопка "Восстановить" (`POST /owner/clients/{id}/restore`).

---

## Часть 2 — Публичный API (`/api/*`)

Файл: `app/routers/public_api.py`

### Текущие эндпоинты (уже есть):
- `POST /api/activate` — активация ключа
- `POST /api/deactivate` — деактивация
- `POST /api/transfer` — перенос (смена ключа)
- `GET /api/status?key=...` — статус
- `GET /api/history?key=...` — история

---

### Сценарий 1: Нормальная активация (уже работает)

`POST /api/activate` с `{key, device_id, device_name, comment, key_version}`

Если ключ `not_activated` и не истёк и не заблокирован → активировать, вернуть info.
**Добавить в ответ**: `logo_url` — URL логотипа организации (`/owner/clients/{client_id}/logo`),
только если у клиента есть `logo_data`.

Ответ 200:
```json
{
  "status": "activated",
  "license_id": 42,
  "organization": "ООО Строй Мастер",
  "description": "Рабочее место",
  "activated_at": "2026-03-01T10:00:00",
  "expires_at": "2027-03-01T00:00:00",
  "version": 1,
  "device_id": "abc-123",
  "device_name": "Смартфон Андрей",
  "logo_url": "/owner/clients/5/logo"
}
```

---

### Сценарий 2: Повторная активация того же устройства (уже работает)

Если `lic.status == "activated"` и `lic.device_id == data.device_id` → вернуть подтверждение (уже реализовано ✓).

---

### Сценарий 3: Замена ключа — устройство уже имеет другой активированный ключ

**НОВОЕ.** Добавить в `POST /api/activate` ПЕРЕД проверкой нового ключа:

```python
# Проверяем: device_id уже привязан к другой лицензии?
old_lic = (await db.execute(
    select(License).where(
        License.device_id == data.device_id,
        License.status == "activated",
        License.id != lic.id,          # не тот же ключ
    )
)).scalar_one_or_none()

if old_lic:
    # Освобождаем старый ключ (device_id отвязывается, статус → released)
    old_lic.status        = "released"
    old_lic.activated_at  = None
    old_lic.device_id     = None
    old_lic.device_name   = None
    old_lic.device_comment = None
    db.add(LicenseAction(
        license_id=old_lic.id,
        action="deactivate",
        reason="device switched to new key",
        actor=data.device_id,
        ip=_get_ip(request),
    ))
    await log_action(
        db=db, actor_type="api_client", action="device_key_swap",
        actor_login=data.device_id, entity_type="license", entity_id=old_lic.id,
        details={"old_key_prefix": old_lic.key[:8], "new_key": data.key[:8]},
        success=True, request=request,
    )
    # НЕ commit здесь — продолжаем в той же транзакции
```

После этого — стандартная активация нового ключа.

---

### Сценарий 4: Ошибки — логирование

Для **каждого** ответа с ошибкой (404, 403, 409) добавить запись в `LicenseAction` и `AuditLog`:

```python
# Добавить helper:
async def _log_error(db, lic_id, action, code, device_id, ip, request):
    if lic_id:
        db.add(LicenseAction(
            license_id=lic_id, action=action,
            reason=code, actor=device_id or "unknown",
            ip=ip,
        ))
    await log_action(
        db=db, actor_type="api_client", action=f"error_{action}",
        actor_login=device_id or "unknown", entity_type="license", entity_id=lic_id,
        details={"code": code, "device_id": device_id, "ip": ip},
        success=False, request=request,
    )
    await db.commit()
```

Вызывать для:
- `LICENSE_NOT_FOUND` (нет записи в LicenseAction, логируем только AuditLog)
- `LICENSE_BLOCKED`, `LICENSE_EXPIRED`, `DEVICE_MISMATCH`, `VERSION_MISMATCH`

---

### Сценарий 5: Деактивация (уже работает)

`POST /api/deactivate` → статус `released`, возвращает `{"status": "ok", "code": "DEACTIVATED", ...}`.
Добавить явное поле `"code": "DEACTIVATED"` в успешный ответ.

---

### Сценарий 6: Заблокированный ключ (уже работает)

`POST /api/activate` с заблокированным ключом → 403, `code: "LICENSE_BLOCKED"`, `reason: "причина из block_reason"`.
**Убедиться** что `block_reason` передаётся в поле `reason` ответа. Уже есть в коде, проверь.

---

### Сценарий 7: Убрать описание из генерации ключей (уже сделано)

`License.description` по умолчанию `"автоматическая генерация"` — уже в модели ✓.
В форме выпуска лицензий у owner сделать поле "Описание" необязательным (убрать `required`), дефолт — `"автоматическая генерация"`.

---

### Статусы API (в ответе `computed_status`):

```
not_activated → "not_activated"
activated     → "activated"
released      → "released"
blocked       → "blocked"
expired       → "expired"
```

В ответах API **оставить** английские коды статусов (они используются клиентскими приложениями).
Русские названия — только в веб-интерфейсе.

---

### История ключей — кнопка в UI

В таблице лицензий owner (`templates/owner/client_detail.html`) уже есть кнопка "📖" (история ключей).
**Добавить рядом кнопку "📋 История"** открывающую модалку с действиями `LicenseAction` по данной лицензии.

В таблице лицензий org (`templates/org/dashboard.html`) тоже добавить кнопку "История".
Данные можно получить через `GET /api/history?key=...` с HMAC-подписью,
**или** добавить новый endpoint только для org:

```python
# app/routers/org_web.py
@router.get("/licenses/{license_id}/history", response_class=HTMLResponse)
async def org_license_history(request, license_id, db):
    # Вернуть JSON или render HTML-фрагмент для модалки
```

Рекомендую JSON-endpoint (`/org/licenses/{id}/history` → возвращает список actions),
а в шаблоне открывать модалку через fetch + JS.

---

## Итоговый чеклист изменений

### Файлы Python:
- [ ] `app/routers/owner_web.py` — убрать excess_licenses, добавить actions_payloads, client restore, client_detail quota indicator
- [ ] `app/routers/org_web.py` — добавить `/licenses/{id}/reset`, `/licenses/{id}/history`
- [ ] `app/routers/public_api.py` — Сценарий 3 (device swap), логирование ошибок, `logo_url` в ответе, `code: "DEACTIVATED"` в deactivate

### Шаблоны:
- [ ] `templates/owner/dashboard.html` — убрать кнопку "+ Новый клиент"
- [ ] `templates/owner/backup.html` — привести к единому стилю
- [ ] `templates/owner/client_detail.html` — убрать excess_licenses блок, restructure info, добавить actions_payloads модалку, resizable columns
- [ ] `templates/owner/client_list.html` — placeholders, кнопка "Показать удалённых"
- [ ] `templates/org/dashboard.html` — русские статусы, кнопка Сбросить, кнопка История, device_id в title
- [ ] Все шаблоны — русские названия статусов лицензий

### CSS:
- [ ] `static/styles.css` — унифицировать org- и owner-стили

### Тесты:
- [ ] `tests/test_api.py` — добавить тест Сценария 3 (device swap)
- [ ] `tests/test_statuses.py` — добавить тест `released` → сброс и новая активация

---

## Правила написания кода

1. Не добавляй `Co-Authored-By` в коммиты
2. Не добавляй новые зависимости без крайней необходимости
3. В Python-коде: `datetime.datetime.now(datetime.UTC)` вместо `datetime.datetime.utcnow()`
4. Все данные только в БД (no filesystem for data)
5. Не трогай код который не нужно менять
6. Делай изменения итерационно, читая файлы перед правкой
