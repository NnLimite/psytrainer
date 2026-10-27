# Фронтенд

Сюда кладите ваши компоненты на TSX. Приложение пока не создано: заготовка ниже описывает, как подключить его к бэкенду.

## Создать проект

```bash
npm create vite@latest . -- --template react-ts
npm i
```

## Типы и клиент из контракта

```bash
npx openapi-typescript ../backend/docs/openapi.yaml -o src/api/schema.d.ts
npm i openapi-fetch
```

После изменения контракта (`make openapi`) команду нужно повторить: типы всегда совпадут с бэкендом.

## Предлагаемая структура

```
frontend/src/
├── api/               # клиент (openapi-fetch) и schema.d.ts; обновление access-токена по 401
├── features/
│   ├── auth/          # вход, регистрация по приглашению, SSO, сброс пароля
│   ├── cases/         # каталог, конструктор (ETag/If-Match), проверка и публикация
│   ├── session/       # чат со стримингом, панель состояния пациента, завершение
│   ├── debrief/       # разбор: баллы с привязкой к репликам, модель случая студента
│   ├── teacher/       # группы, аналитика, оценки, события безопасности
│   ├── org/           # организация, участники, приглашения
│   └── settings/      # профиль, согласия, выгрузка и удаление данных, подписка
├── components/        # ваши существующие компоненты
└── routes/
```

## Что нужно знать про API

- **Токены.** Access-токен живёт 15 минут и хранится в памяти. Refresh-токен лежит в httpOnly-cookie `psy_refresh`:
  запросы к `/auth/*` отправляйте с `credentials: "include"`. При 401 `token_expired` вызовите `POST /auth/refresh` и повторите запрос.
- **Стриминг.** `EventSource` не умеет POST и заголовки, поэтому читайте поток через `fetch` и `ReadableStream`,
  разбирая строки `data: {...}`. Строки `: ping` игнорируйте. События: `token`, `done`, `ended`, `out_of_role`, `error`.
- **Режим exam.** В ответах `state` равен `null`, панель состояния прячьте.
- **Редактирование кейса.** Берите `ETag` из `GET /cases/{id}/versions/{n}`, отправляйте его в `If-Match` при `PUT`;
  на 412 перечитайте версию.
- **Адрес API.** `http://localhost:8000/api/v1`. В dev настройте CORS (`PSY_CORS_ORIGINS`) на адрес фронтенда.

## Что уже написано

`src/api/client.ts` (токены, refresh, ошибки, SSE), `features/auth`, `features/cases/Catalog`, `features/session`,
`features/settings`, `features/landing`, `app/App.tsx` (маршруты). Нужны зависимости: `npm i react-router-dom`;
адрес API задаётся `VITE_API_URL` (по умолчанию `http://localhost:8000/api/v1`). Точка входа: `<App />` в `main.tsx`.
Дополнительно: `features/debrief` (оценка и разбор), `features/cases/Constructor` (конструктор с ETag, проверкой и публикацией), `features/teacher` (группы, аналитика, события безопасности, участники и приглашения), `features/billing` (тариф).
Не написаны: список и создание рубрик, редактор модели случая студента, отчёты и их скачивание, SSO-кнопка, подтверждение и правка оценок преподавателем, список сессий.
