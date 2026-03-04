# Промпт для Claude Code — следующая итерация LicenseServerPryamolineynost

## Инструкции для Claude Code

```
Ты работаешь в репозитории LicenseServerPryamolineynost — FastAPI-сервер лицензирования ПО.
Стек: Python 3.13, FastAPI, SQLAlchemy async (aiosqlite), Jinja2, static/styles.css.

Правила:
- Читай файлы перед правкой. Не трогай код который не нужно менять.
- Делай изменения итерационно и коммить по функциональным блокам.
- Не используй `datetime.utcnow()` — только `datetime.datetime.now(datetime.UTC)`.
- Все данные только в БД (no filesystem for data).
- Не добавляй зависимости без крайней необходимости.
- После каждого этапа запускай smoke-тесты: `pytest tests/ -v`.
```

---

## Что уже реализовано (НЕ ПЕРЕДЕЛЫВАТЬ)

- Формат ключа `XXXX-XXXX-XXXX` (12 символов) — `app/utils.py`
- Вход org по `login` ИЛИ `contact_email` — `app/routers/auth.py`
- Модели `Feedback` + `FeedbackMessage` (диалог owner ↔ org) — `app/models.py`
- `POST /owner/feedback/{id}/reply` и `POST /org/feedback/{id}/reply` — с email-дублированием
- Статусы лицензии в БД: `not_activated`, `activated`, `released`, `blocked`; `expired` вычисляется по `expires_at`
- `License.computed_status(now)` — метод уже есть
- Эндпоинты API: `POST /api/activate`, `POST /api/deactivate`, `POST /api/transfer`, `GET /api/status`, `GET /api/history`
- Колонка «Открыть» в списке клиентов убрана; строки кликабельны
- `overflow:visible` на `.section` с лицензиями — three-dots меню исправлено
- `Client.deleted_at` — soft-delete уже в модели
- `POST /org/licenses/generate`, `POST /org/licenses/{id}/edit`, `POST /org/licenses/{id}/deactivate`
- Кнопка генерации лицензий в заголовке таблицы (owner)
- `LicenseAction` — журнал; колонки «Лиц.» и «Ключ» (4 символа) в `client_detail.html`

---

## Этап 1. UI-правки Owner Dashboard и Backup

### 1а. Dashboard: убрать кнопку «Все клиенты»

Файл: `templates/owner/dashboard.html`

Найти и убрать кнопку `<a href="/owner/clients" ...>Все клиенты</a>` в шапке страницы.
Навигация в списке клиентов — через sidebar/nav.

### 1б. Страница backup — единый стиль

Файл: `templates/owner/backup.html`

Привести к стилю остальных owner-страниц:
- Унаследовать от `base_owner.html`
- Заголовок `<h1>` и кнопки ("Создать резервную копию", "Загрузить файл") — стандартные `.btn`
- Flash-уведомления через `_flash(url, msg, msg_type)` + redirect вместо inline-блоков
- Таблица файлов в `.section` → `.table-wrap` → `<table class="table-compact">`

---

## Этап 2. Карточка клиента (Owner): структурирование и UX

### 2а. Убрать блок «превышение квоты»

Файл: `templates/owner/client_detail.html`

- Убрать полностью блок `{% if excess_licenses %}...{% endif %}`
- Кнопку «+ Выпустить лицензию» показывать **всегда** в заголовке таблицы лицензий (справа)
- Если квота исчерпана (`total_keys >= client.max_keys`) — кнопка `disabled` с `title="Квота исчерпана"`
- Если `total_keys > client.max_keys` — показать индикатор `⚠ N/M` красным рядом с заголовком

Файл: `app/routers/owner_web.py`
- В `client_detail` убрать вычисление `excess_licenses` из контекста
- При сохранении `client_update_info` с уменьшенным `max_keys`: flash-warn «Квота уменьшена до X. Выпущено Y лицензий — заблокируйте лишние через меню ⋮»

### 2б. Перенести метаданные клиента в заголовок блока

Файл: `templates/owner/client_detail.html`

Под основной формой редактирования (перед кнопками «Сбросить пароль», «Отключить», «Удалить»):

```html
<div class="info-row">
  <span>Логин: <code>{{ client.login or '—' }}</code></span>
  <span>·</span>
  <span>Создан: {{ client.created_at.strftime('%d.%m.%Y') }}</span>
  {% if creator %}
  <span>·</span>
  <span>Создал: {{ creator.email }}</span>
  {% endif %}
</div>
```

### 2в. Примеры заполнения полей

Файл: `templates/owner/client_list.html` (форма создания клиента):

| Поле | Новый placeholder |
|---|---|
| org_name | `АО Технопарк Сибирь` |
| login | `technopark_sib` |
| contact_email | `admin@technopark-sib.ru` |
| notes | `Договор №456 от 15.01.2025` |

### 2г. Таблица лицензий и журнал: выравнивание и ширины

Файл: `templates/owner/client_detail.html`

- Заголовок колонки действий — без текста (уже ✓)
- Все `<th>` — `text-align:center`
- Все колонки с данными кроме «Ключ» и «Описание» — `text-align:center`
- Добавить JS-resizer для изменения ширины колонок через mousedown/mousemove на `<th>`:

```js
document.querySelectorAll('.resizable-table th').forEach(th => {
  th.style.position = 'relative';
  const grip = document.createElement('div');
  grip.style.cssText = 'position:absolute;right:0;top:0;width:5px;height:100%;cursor:col-resize;user-select:none';
  grip.addEventListener('mousedown', e => {
    const startX = e.clientX, startW = th.offsetWidth;
    const onMove = ev => th.style.width = (startW + ev.clientX - startX) + 'px';
    const onUp = () => { document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp); };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
  th.appendChild(grip);
});
```

Применить к таблицам лицензий и журнала: добавить класс `resizable-table`.

### 2д. Кнопка «История» у каждого ключа

В таблице лицензий рядом с кнопкой «📖» (история ключей) добавить кнопку «📋 Действия»,
открывающую модалку с `LicenseAction` по данной лицензии.

В `app/routers/owner_web.py` в `client_detail` добавить в контекст:
```python
actions_payloads = {
    lic.id: [
        {"action": a.action, "at": a.at.isoformat()[:16], "actor": a.actor or "—",
         "reason": a.reason or "", "ip": a.ip or ""}
        for a in sorted(lic.actions, key=lambda x: x.at, reverse=True)[:20]
    ]
    for lic in licenses
}
```
Данные передавать через `data-actions='...'` аналогично `data-open-keys`.

---

## Этап 3. Статусы: русские названия в UI

Во **всех** шаблонах заменить английские статусы на русские с правильными `.badge`:

| computed_status | Бейдж | Класс |
|---|---|---|
| `not_activated` | Не активирован | `.badge.new` |
| `activated` | Активирован | `.badge.success` |
| `released` | Освобождён | `.badge.warn` |
| `blocked` | Заблокирован | `.badge.danger` |
| `expired` | Истёк | `.badge.gray` |

Затронутые файлы:
- `templates/owner/client_detail.html`
- `templates/org/dashboard.html`

В ответах API — **оставить** английские коды (используются клиентскими приложениями).

---

## Этап 4. Кабинет организации: просмотр истории

**Политика**: организация управляет только описанием лицензии и выпуском новых (в рамках квоты).
Сброс ключа, деактивация и удаление — исключительно через владельца (owner).
Это исключает манипуляции с ключами со стороны клиентов.

Уже удалено:
- `POST /org/licenses/{id}/deactivate` — эндпоинт удалён из `org_web.py`
- Кнопка «Деактивировать» — убрана из `org/dashboard.html`

### 4а. История действий по лицензии (read-only)

Добавить в `app/routers/org_web.py`:

```python
from fastapi.responses import JSONResponse
from sqlalchemy.orm import selectinload

@router.get("/licenses/{license_id}/history")
async def org_license_history(license_id: int, request: Request, db: AsyncSession = Depends(get_session)):
    org, redir = await _require_org(request, db)
    if redir:
        return redir
    lic = (await db.execute(
        select(License)
        .where(License.id == license_id, License.client_id == org.id)
        .options(selectinload(License.actions))
    )).scalar_one_or_none()
    if not lic:
        from fastapi import HTTPException
        raise HTTPException(404)
    actions = sorted(lic.actions, key=lambda a: a.at, reverse=True)
    return JSONResponse([
        {"action": a.action, "at": a.at.isoformat()[:16],
         "actor": a.actor or "—", "reason": a.reason or ""}
        for a in actions[:30]
    ])
```

### 4б. UI org/dashboard.html: кнопка «История»

В таблице лицензий добавить кнопку **«История»** рядом с «Изм.»:
- `fetch` → `GET /org/licenses/{id}/history`
- Показывает результат в модалке (список действий с датой, типом и причиной)

Org **не может** менять `max_keys` — не добавлять это поле.
Org **не получает** кнопки сброса, деактивации, удаления — они только у owner.

---

## Этап 5. Владелец: управление max_keys при превышении

В `app/routers/owner_web.py` функция `client_update_info`:

```python
if new_max_keys < total_active:
    await flash(request, f"Квота уменьшена до {new_max_keys}. Выпущено {total_active} лицензий — заблокируйте лишние через меню ⋮", "warn")
```

Блокировка лишних ключей — через стандартный three-dots dropdown в таблице лицензий (уже есть).
Никакого специального блока `excess_licenses` — убрать полностью.

---

## Этап 6. Восстановление удалённых организаций

### 6а. Роутер `app/routers/owner_web.py`

Добавить параметр `show_deleted: bool = False` в `/clients`:

```python
@router.get("/clients")
async def client_list(request: Request, db: AsyncSession = Depends(get_session), show_deleted: bool = False):
    q = select(Client)
    if show_deleted:
        q = q.where(Client.deleted_at.isnot(None))
    else:
        q = q.where(Client.deleted_at.is_(None))
    clients = (await db.execute(q)).scalars().all()
    return templates.TemplateResponse("owner/client_list.html",
        {"request": request, "clients": clients, "show_deleted": show_deleted, ...})
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

### 6б. Шаблон `templates/owner/client_list.html`

Добавить:
- Ссылку-переключатель «Показать удалённые» / «Показать активные» (`?show_deleted=1`)
- При `show_deleted=True` — таблица с удалёнными: дата удаления + кнопка «Восстановить»

---

## Этап 7. Единое оформление

Привести **все** шаблоны к единому стилю `static/styles.css`:

**Правила:**
- Все карточки — класс `.section`
- Все таблицы — `.table-wrap` → `<table class="table-compact">`
- `base_org.html` — те же CSS-переменные и классы что и `base_owner.html`
- Бейджи — только классы `.badge .success/.danger/.warn/.new/.gray`
- Кнопки — только `.btn`, `.btn.small`, `.btn.ghost`, `.btn.green`, `.btn.amber`, `.btn.red`
- Flash — только через `?msg=...&msg_type=...`
- Нет inline `style="background-color:..."` — только CSS-классы

Добавить в `static/styles.css` если не хватает org-специфики:
```css
.org-nav { /* аналогично .owner-nav */ }
```

---

## Этап 8. Публичный API — мобильные сценарии

Файл: `app/routers/public_api.py`

### Текущие эндпоинты:
- `POST /api/activate` — активация ключа
- `POST /api/deactivate` — деактивация (статус → released)
- `POST /api/transfer` — перенос (смена ключа)
- `GET /api/status?key=...` — статус ключа
- `GET /api/history?key=...` — история действий по ключу

---

### Сценарий 1: Первичная активация нового ключа

`POST /api/activate` принимает:
```json
{
  "key": "ABCD-EFGH-IJKL",
  "device_id": "uuid-устройства",
  "device_name": "Смартфон Андрей",
  "comment": "Рабочее устройство",
  "key_version": 1
}
```

Логика:
1. Найти `License` по `key`; если нет → 404 `LICENSE_NOT_FOUND`
2. Если `status == "blocked"` → 403 `LICENSE_BLOCKED` + `reason: block_reason`
3. Если `expires_at` прошёл → 403 `LICENSE_EXPIRED`
4. Если `status == "not_activated"` → активировать:
   - `status = "activated"`, `device_id`, `device_name`, `device_comment = comment`, `activated_at = now()`
   - Создать `LicenseAction(action="activate", actor=device_id, ip=ip)`
   - `await log_action(...)`
   - `await db.commit()`
   - Вернуть 200 с данными (см. ниже)

Ответ 200:
```json
{
  "status": "activated",
  "license_id": 42,
  "organization": "АО Технопарк Сибирь",
  "description": "Рабочее место",
  "activated_at": "2026-03-01T10:00:00",
  "expires_at": "2027-03-01T00:00:00",
  "version": 1,
  "device_id": "uuid-устройства",
  "device_name": "Смартфон Андрей",
  "logo_url": "/owner/clients/5/logo"
}
```

`logo_url` — только если у клиента есть `logo_data`, иначе `null`.

---

### Сценарий 2: Повторная активация того же устройства (переустановка приложения)

В `POST /api/activate` ПОСЛЕ проверки blocked/expired:

```python
if lic.status == "activated" and lic.device_id == data.device_id:
    # Устройство то же — подтвердить активацию без изменений
    return _ok_response(lic, client)
```

Обновить `device_name` и `comment` если переданы (пользователь мог изменить).

---

### Сценарий 3: Устройство меняет ключ (старый ключ освобождается)

В `POST /api/activate` ПЕРЕД основной проверкой нового ключа:

```python
# Проверяем: device_id уже привязан к другой активированной лицензии?
old_lic = (await db.execute(
    select(License).where(
        License.device_id == data.device_id,
        License.status == "activated",
        License.id != lic.id,
    )
)).scalar_one_or_none()

if old_lic:
    # Освобождаем старый ключ — device_id отвязывается, статус → released
    old_lic.status = "released"
    old_lic.activated_at = None
    old_lic.device_id = None
    old_lic.device_name = None
    old_lic.device_comment = None
    db.add(LicenseAction(
        license_id=old_lic.id,
        action="deactivate",
        reason=f"device switched to new key",
        actor=data.device_id,
        ip=_get_ip(request),
    ))
    await log_action(db=db, actor_type="api_client", action="device_key_swap",
                     actor_login=data.device_id, entity_type="license", entity_id=old_lic.id,
                     details={"old_key": old_lic.key[:8], "new_key": data.key[:8]},
                     success=True, request=request)
    # НЕ commit — продолжаем в той же транзакции
```

После — стандартная активация нового ключа (Сценарий 1).

---

### Сценарий 4: Ошибки активации — логирование в БД и журнал

Добавить helper:
```python
async def _log_api_error(db, lic_id, action, code, device_id, ip, request):
    if lic_id:
        db.add(LicenseAction(
            license_id=lic_id, action=action,
            reason=code, actor=device_id or "unknown", ip=ip,
        ))
    await log_action(
        db=db, actor_type="api_client", action=f"error_{action}",
        actor_login=device_id or "unknown", entity_type="license", entity_id=lic_id,
        details={"code": code, "device_id": device_id, "ip": ip},
        success=False, request=request,
    )
    await db.commit()
```

Вызывать при:
- `LICENSE_NOT_FOUND` — только AuditLog (нет license_id)
- `LICENSE_BLOCKED` — AuditLog + LicenseAction
- `LICENSE_EXPIRED` — AuditLog + LicenseAction
- `DEVICE_MISMATCH` — AuditLog + LicenseAction (ключ активирован на другом устройстве)
- `VERSION_MISMATCH` — AuditLog + LicenseAction

Коды ошибок и HTTP-статусы:
| Ситуация | HTTP | code |
|---|---|---|
| Ключ не найден | 404 | `LICENSE_NOT_FOUND` |
| Ключ заблокирован | 403 | `LICENSE_BLOCKED` |
| Ключ истёк | 403 | `LICENSE_EXPIRED` |
| Активирован на другом устройстве | 409 | `DEVICE_MISMATCH` |
| Неверная версия ключа | 409 | `VERSION_MISMATCH` |

---

### Сценарий 5: Деактивация

`POST /api/deactivate` — уже работает. Добавить явное поле `"code": "DEACTIVATED"` в ответ:

```json
{"status": "ok", "code": "DEACTIVATED", "message": "Лицензия освобождена"}
```

Логировать в `LicenseAction(action="deactivate", actor=device_id, ip=ip)` + `log_action`.

---

### Сценарий 6: Заблокированный ключ

В `POST /api/activate` при `status == "blocked"`:
```json
{"error": "LICENSE_BLOCKED", "reason": "{{ lic.block_reason }}"}
```
Убедиться что `block_reason` передаётся в поле `reason`. Логировать через `_log_api_error`.

---

### Сценарий 7: Описание при генерации ключей

- `License.description` по умолчанию `"автоматическая генерация"` (уже в модели ✓)
- В форме выпуска лицензий у owner: поле «Описание» необязательное (убрать `required`)
- При пустом значении — подставлять `"автоматическая генерация"` на бэкенде

---

## Этап 9. Тесты

Файлы: `tests/test_api.py`, `tests/test_statuses.py`

Добавить тесты:

### test_api.py:
```python
async def test_activate_device_swap(client, db):
    """Сценарий 3: устройство переключается на новый ключ — старый освобождается"""
    # Активировать ключ1 на device_id="dev1"
    # Попытаться активировать ключ2 с тем же device_id="dev1"
    # Ожидаем: ключ1 → released, ключ2 → activated

async def test_error_logging_blocked(client, db):
    """Сценарий 4: ошибка логируется в LicenseAction"""
    # Заблокировать ключ, попробовать активировать
    # Проверить что создана запись LicenseAction с reason="LICENSE_BLOCKED"

async def test_deactivate_returns_code(client, db):
    """Сценарий 5: deactivate возвращает code=DEACTIVATED"""
```

### test_statuses.py:
```python
async def test_org_reset_license(client, db):
    """Org сбрасывает ключ: старый → is_active=False, новый ключ создан, статус not_activated"""

async def test_restore_deleted_client(client, db):
    """Восстановление soft-deleted клиента"""
```

---

## Дополнительные требования

### Права на удаление лицензии
Удаление лицензии доступно **любому** залогиненному owner'у (уже реализовано, роль не ограничивает).

---

## Итоговый чеклист

### Python:
- [ ] `app/routers/owner_web.py` — убрать excess_licenses, добавить actions_payloads, restore endpoint, quota indicator
- [ ] `app/routers/org_web.py` — GET `/licenses/{id}/history` (только чтение; reset/deactivate/delete — только owner)
- [ ] `app/routers/public_api.py` — Сценарий 3 (device swap), `_log_api_error` helper, `logo_url` в ответе, `code: "DEACTIVATED"`

### Шаблоны:
- [ ] `templates/owner/dashboard.html` — убрать кнопку «Все клиенты»
- [ ] `templates/owner/backup.html` — единый стиль base_owner.html
- [ ] `templates/owner/client_detail.html` — убрать excess_licenses, info-row, actions_payloads модалка, resizable columns, disable кнопки при квоте
- [ ] `templates/owner/client_list.html` — новые placeholder, переключатель «Показать удалённых»
- [ ] `templates/org/dashboard.html` — кнопка История (модалка); сброс/деактивация/удаление убраны
- [ ] Все шаблоны — русские названия статусов

### CSS:
- [ ] `static/styles.css` — унифицировать org- и owner-стили, org-nav

### Тесты:
- [ ] `tests/test_api.py` — Сценарий 3 (device swap), логирование ошибок, DEACTIVATED code
- [ ] `tests/test_statuses.py` — org reset, restore client

---

## Рекомендуемые коммиты

1. `refactor(owner): remove excess_licenses block, add quota indicator and actions modal`
2. `refactor(owner): restructure client info card, resizable columns, restore endpoint`
3. `feat(org): add license reset and history endpoints`
4. `feat(api): device swap scenario, error logging, logo_url in response`
5. `refactor(ui): unify owner/org styles, Russian status badges`
6. `refactor(owner): backup page to common style, dashboard cleanup`
7. `test: add device swap, error logging, org reset, client restore tests`
