# gov_bot_tg

Dockerized stack: **FastAPI + Celery + Postgres (pgvector) + Redis + Telegram bot**.

## Services
- `api` — HTTP API (`:8000`)
- `worker` — Celery worker (`worker.tasks`)
- `bot` — Telegram polling bot
- `postgres` — pgvector-enabled PostgreSQL
- `redis` — Celery broker/result backend

## Quick start

1. Copy env and fill values:
   ```bash
   cp .env.example .env
   ```
   Required real values:
   - `OPENAI_API_KEY` (optional for degraded mode, but recommended)
   - `TELEGRAM_BOT_TOKEN` (for bot)
   - `ADMIN_TOKEN` (for admin endpoints)

2. Build and run:
   ```bash
   docker compose up -d --build
   ```

3. Check health:
   ```bash
   curl -s localhost:8000/health
   ```
   Expected:
   ```json
   {"status":"ok"}
   ```

## DB init
Run once (idempotent):

```bash
curl -s -X POST localhost:8000/admin/init-db \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json"
```

## Ingest document

### Sync mode
```bash
curl -s -X POST "localhost:8000/admin/ingest?sync=true" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://zakon.rada.gov.ua/laws/show/2657-12#Text"}'
```

### Async mode
```bash
TASK_ID=$(curl -s -X POST "localhost:8000/admin/ingest" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://zakon.rada.gov.ua/laws/show/2657-12#Text"}' | jq -r .task_id)

curl -s "localhost:8000/admin/task/${TASK_ID}" \
  -H "X-Admin-Token: ${ADMIN_TOKEN}"
```

## Ask question

```bash
curl -s -X POST localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "question":"Що регулює цей закон?",
    "user_external_id":123456,
    "max_citations":4
  }'
```

Expected JSON fields: `answer`, `citations`, `chat_id`, `need_more_info`, `questions`.

## Telegram bot
Bot starts automatically with compose if `TELEGRAM_BOT_TOKEN` is set.

Commands:
- `/start` — reset context and intro
- `/menu` — main menu
- `/back` — previous screen
- `/cancel` — cancel current action
- `/newchat` — start a new question
- `/help` — help

`chat_id` from API response is persisted in Telegram `user_data`, so next user messages continue same chat context.


### Conversation telemetry tables

The DB now stores additional consultation telemetry:
- `conversation_turns` — question/answer snapshots with `need_more_info` + clarification questions count
- `audit_logs` — lightweight API events for diagnostics


### Batch ingest (for scaling the DB)

You can enqueue multiple URLs in one call:

```bash
curl -s -X POST "localhost:8000/admin/ingest-batch" \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -d '{"urls":["https://zakon.rada.gov.ua/laws/show/435-15","https://zakon.rada.gov.ua/laws/show/2341-14"]}'
```

Use `/admin/task/{task_id}` to poll async batch status.


## Useful diagnostics

```bash
docker compose ps
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f bot
```

## How to verify it works (checklist)

- [ ] `docker compose up -d --build` finished without crashes
- [ ] `GET /health` returns `{"status":"ok"}`
- [ ] `POST /admin/init-db` succeeds with admin token
- [ ] `POST /admin/ingest?sync=true` ingests zakon.rada URL
- [ ] `POST /admin/ingest` returns `task_id`
- [ ] `GET /admin/task/{id}` becomes `ready=true, successful=true`
- [ ] `POST /chat` returns `answer`, `citations`, `chat_id`
- [ ] `docker compose logs worker` includes successful task execution
- [ ] `docker compose logs bot` confirms polling started
