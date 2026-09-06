# Shortcuts for the commands the README spells out. Nothing here does anything
# the README does not; it just keeps you from retyping them. Run with a venv
# active - `make` does not create one for you.

.PHONY: dev db migrate mcp mcp-http api test lint frontend clean

## dev: the whole local loop - database, migrations, MCP server over stdio
dev: db migrate mcp

## db: Postgres on 127.0.0.1:5432 (docker compose)
db:
	docker compose up -d postgres

## migrate: apply migrations through head; seeds the "self" Actor
migrate:
	cd backend && alembic upgrade head

## mcp: the MCP server over stdio (what .mcp.json launches)
mcp:
	cd backend && python -m app.mcp.server

## mcp-http: the MCP server over Streamable HTTP on loopback:8765
mcp-http:
	cd backend && python -m app.mcp.server --http --port 8765

## api: the REST API on loopback:8000 with reload
api:
	cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

## test: the SQLite suite (set AXONRELAY_TEST_POSTGRES_URL to include the Postgres parity tests)
test:
	cd backend && pytest -q

## lint: what CI runs
lint:
	cd backend && ruff check . && ruff format --check .

## frontend: the read-only dashboard on :5173, proxying /api to :8000
frontend:
	cd frontend && pnpm install && pnpm dev

## clean: stop the containers (data volume is kept)
clean:
	docker compose down

help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## //'
