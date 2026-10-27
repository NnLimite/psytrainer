# API PsyTrainer v1

Формальный контракт: [`backend/docs/openapi.yaml`](../backend/docs/openapi.yaml) (OpenAPI 3.1, 82 операции). Интерактивная
страница у запущенного сервера: `http://localhost:8000/docs`. Контракт сверяется с кодом тестом `tests/test_contract.py`,
а сам файл генерируется из `backend/docs/build_openapi.py` (`make openapi`).

## Общие принципы

| Аспект | Правило |
|---|---|
| Базовый URL | `/api/v1` |
| Формат | JSON UTF-8, поля `snake_case`, идентификаторы UUID v4, время RFC 3339 в UTC |
| Аутентификация | `Authorization: Bearer <JWT>`. Access-токен 15 минут, refresh-токен 30 дней в httpOnly-cookie `psy_refresh`, ротируется при каждом обновлении; повторное использование старого токена отзывает всю цепочку |
| Ошибки | `application/problem+json` (RFC 9457): `type`, `title`, `status`, `code`, `detail`, `errors[]` |
| Пагинация | `?limit=1..100` и `?cursor=`; ответ `{items, next_cursor}`. Курсор непрозрачный (внутри смещение) |
| Идемпотентность | Заголовок `Idempotency-Key` на создание сессии, кейса, оценки и платежа (24 часа) |
| Конкурентные правки | `ETag` на версии кейса, `If-Match` на `PUT` (412 при несовпадении) |
| Асинхронные операции | 202 + `Location: /jobs/{id}` + `Retry-After`; результат: `GET /jobs/{id}` и `GET /jobs/{id}/download` |
| Лимиты | `RateLimit-*` заголовки, 429 + `Retry-After`. Вход ограничен по паре IP+email |
| Стриминг | `POST /sessions/{id}/messages/stream`, SSE: `token`, `done`, `ended`, `out_of_role`, `error`; пинг `: ping` раз в 15 с. Читать через `fetch`, а не `EventSource` (нужны POST и заголовок Authorization) |

## Роли

`student`, `teacher`, `org_admin`, `platform_admin`. В таблицах ниже `public` значит «без токена», `any` — «любой авторизованный».
Чужие объекты отдают 404, а не 403.

## Коды ошибок

| HTTP | `code` |
|---|---|
| 400 | `bad_request` |
| 401 | `unauthorized`, `token_expired` |
| 403 | `forbidden`, `state_hidden` |
| 404 | `not_found` |
| 409 | `consent_required`, `session_not_active`, `session_not_ended`, `session_busy`, `version_immutable`, `version_not_published`, `case_has_sessions`, `group_has_sessions`, `previous_session_mismatch`, `duplicate_message`, `already_exists`, `last_admin`, `teacher_has_groups` |
| 412 | `precondition_failed` |
| 422 | `validation_failed`, `format_not_supported` |
| 429 | `rate_limited` |
| 502 | `llm_unavailable`, `sso_failed` |
| 503 | `not_ready` |

## Видимость данных пациента

| Данные | Студент, training | Студент, exam | Преподаватель, org_admin |
|---|---|---|---|
| `state`, `delta`, `triggers` в ответах | возвращаются | `null`; `GET …/state` даёт 403 `state_hidden` | всегда |
| История состояния | нет | нет | да |
| Модель случая | публичная карточка | публичная карточка | автор и организация: полностью |
| Оценка | сразу | после подтверждения преподавателем | да |
| `match_score` модели студента | после завершения | после завершения | да |

## Эндпоинты

### Аутентификация и доступ

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `POST /auth/register` | public | Регистрация (B2C) | готов |
| `POST /auth/login` | public | Вход по email и паролю | готов |
| `POST /auth/refresh` | public | Обновить access-токен по refresh-cookie | готов |
| `POST /auth/logout` | any | Выход, отзыв refresh-токена | готов |
| `GET /auth/sso/{provider}/start` | public | Начать вход через SSO вуза (OIDC) | готов |
| `GET /auth/sso/{provider}/callback` | public | Колбэк SSO, выдаёт сессию | готов |
| `POST /auth/password/forgot` | public | Запросить письмо для сброса пароля | готов |
| `POST /auth/password/reset` | public | Установить новый пароль по токену из письма | готов |

### Профиль, согласия, прогресс, право на удаление

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /me` | any | Текущий пользователь | готов |
| `PATCH /me` | any | Изменить профиль | готов |
| `GET /me/consents` | any | Мои согласия | готов |
| `PUT /me/consents/{kind}` | any | Дать или отозвать согласие | готов |
| `GET /me/progress` | student | Мой прогресс по навыкам | готов |
| `GET /me/subscription` | any | Моя подписка | готов |
| `POST /me/data-export` | any | Выгрузить мои данные | готов |
| `DELETE /me` | any | Удалить аккаунт и данные | готов |

### Организации, участники, приглашения, группы

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /organizations/{orgId}` | teacher, org_admin | Организация | готов |
| `PATCH /organizations/{orgId}` | org_admin | Изменить организацию | готов |
| `GET /organizations/{orgId}/members` | teacher, org_admin | Участники организации | готов |
| `POST /organizations/{orgId}/invitations` | org_admin | Пригласить пользователя | готов |
| `PATCH /organizations/{orgId}/members/{userId}` | org_admin | Изменить роль участника | готов |
| `DELETE /organizations/{orgId}/members/{userId}` | org_admin | Исключить участника | готов |
| `GET /organizations/{orgId}/subscription` | org_admin | Лицензия организации | готов |
| `GET /groups` | teacher, org_admin, student | Группы (мои или организации) | готов |
| `POST /groups` | teacher, org_admin | Создать группу | готов |
| `GET /groups/{groupId}` | teacher, org_admin, student | Группа | готов |
| `PATCH /groups/{groupId}` | teacher, org_admin | Изменить группу | готов |
| `DELETE /groups/{groupId}` | org_admin | Удалить группу | готов |
| `GET /groups/{groupId}/members` | teacher, org_admin | Состав группы | готов |
| `PUT /groups/{groupId}/members/{userId}` | teacher, org_admin | Добавить в группу (идемпотентно) | готов |
| `DELETE /groups/{groupId}/members/{userId}` | teacher, org_admin | Убрать из группы | готов |

### Справочники

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /disorders` | any | Справочник расстройств МКБ-11 и DSM-5 | готов |

### Конструктор кейсов и версии модели случая

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /cases` | teacher, org_admin, student | Каталог кейсов | готов |
| `POST /cases` | teacher, org_admin | Создать кейс (первый черновик) | готов |
| `GET /cases/{caseId}` | teacher, org_admin, student | Кейс со списком версий | готов |
| `PATCH /cases/{caseId}` | teacher, org_admin | Изменить название, доступ, архив | готов |
| `DELETE /cases/{caseId}` | teacher, org_admin | Удалить кейс (только если нет сессий) | готов |
| `POST /cases/{caseId}/clone` | teacher, org_admin | Скопировать кейс себе | готов |
| `GET /cases/{caseId}/versions` | teacher, org_admin | Версии кейса | готов |
| `POST /cases/{caseId}/versions` | teacher, org_admin | Создать новый черновик версии | готов |
| `GET /cases/{caseId}/versions/{versionNo}` | teacher, org_admin, student | Версия с полной моделью случая | готов |
| `PUT /cases/{caseId}/versions/{versionNo}` | teacher, org_admin | Заменить черновик версии | готов |
| `POST /cases/{caseId}/versions/{versionNo}/validate` | teacher, org_admin | Проверить черновик без публикации | готов |
| `POST /cases/{caseId}/versions/{versionNo}/publish` | teacher, org_admin | Опубликовать версию | готов |
| `POST /case-assist/suggestions` | teacher, org_admin | Подсказки LLM для раздела кейса | готов |

### Сессии, диалог, состояние пациента

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `POST /sessions` | student, teacher, org_admin | Начать сессию | готов |
| `GET /sessions` | student, teacher, org_admin | Список сессий | готов |
| `GET /sessions/{sessionId}` | student, teacher, org_admin | Сессия | готов |
| `POST /sessions/{sessionId}/end` | student, teacher, org_admin | Завершить сессию | готов |
| `POST /sessions/{sessionId}/messages` | student, teacher, org_admin | Реплика студента, ответ целиком | готов |
| `POST /sessions/{sessionId}/messages/stream` | student, teacher, org_admin | Реплика студента, ответ потоком SSE | готов |
| `GET /sessions/{sessionId}/turns` | student, teacher, org_admin | Транскрипт | готов |
| `GET /sessions/{sessionId}/state` | student, teacher, org_admin | Текущее состояние пациента | готов |
| `GET /sessions/{sessionId}/state/history` | teacher, org_admin | История состояния по репликам | готов |
| `PUT /sessions/{sessionId}/formulation` | student | Отправить свою модель случая | готов |
| `GET /sessions/{sessionId}/formulation` | student, teacher | Модель случая студента и совпадение с эталоном | готов |
| `GET /sessions/{sessionId}/safety-events` | teacher, org_admin | События безопасности сессии | готов |

### Рубрики и оценка сессий

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /rubrics` | teacher, org_admin, student | Рубрики | готов |
| `POST /rubrics` | teacher, org_admin | Создать рубрику | готов |
| `GET /rubrics/{rubricId}` | teacher, org_admin, student | Рубрика | готов |
| `POST /rubrics/{rubricId}/versions` | teacher, org_admin | Новая версия рубрики | готов |
| `POST /sessions/{sessionId}/assessments` | teacher, student | Запустить оценку сессии | готов |
| `GET /sessions/{sessionId}/assessments` | student, teacher | Оценки сессии | готов |
| `GET /assessments/{assessmentId}` | student, teacher | Оценка с баллами и привязкой к репликам | готов |
| `PATCH /assessments/{assessmentId}` | teacher | Поправить баллы и комментарий | готов |
| `POST /assessments/{assessmentId}/confirm` | teacher | Подтвердить оценку преподавателем | готов |
| `GET /jobs/{jobId}` | any | Статус фоновой задачи | готов |
| `GET /jobs/{jobId}/download` | any | Скачать файл результата задачи (отчёт, выгрузка данных) | готов |

### Аналитика и отчёты

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /analytics/groups/{groupId}/summary` | teacher, org_admin | Сводка по группе | готов |
| `GET /analytics/students/{userId}/progress` | teacher, org_admin | Прогресс студента | готов |
| `GET /analytics/cases/{caseId}/usage` | teacher, org_admin | Использование кейса | готов |
| `POST /reports` | teacher, org_admin | Заказать отчёт (PDF, XLSX, CSV) | готов |

### События безопасности

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /safety-events` | teacher, org_admin | События безопасности | готов |
| `PATCH /safety-events/{eventId}` | teacher, org_admin | Принять или закрыть событие | готов |

### Подписки и платежи

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `POST /billing/checkout` | any | Создать платёжную сессию | готов |
| `POST /webhooks/payments/{provider}` | public | Вебхук платёжного провайдера | готов |

### Администрирование платформы

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /admin/prompt-templates` | platform_admin | Шаблоны промптов | готов |
| `POST /admin/prompt-templates` | platform_admin | Создать версию шаблона | готов |
| `POST /admin/prompt-templates/{templateId}/activate` | platform_admin | Сделать версию активной | готов |
| `GET /admin/llm-usage` | platform_admin | Расход LLM: вызовы, токены, задержка, стоимость | готов |

### Служебные

| Метод и путь | Роли | Назначение | Статус |
|---|---|---|---|
| `GET /health` | public | Liveness | готов |
| `GET /ready` | public | Readiness: БД, Redis, LLM | готов |
