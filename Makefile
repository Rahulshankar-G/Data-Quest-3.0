.PHONY: up down logs test migrate seed frontend-install frontend-dev api-dev

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

test:
	python -m unittest discover -s tests -v

migrate:
	alembic upgrade head

seed:
	python -m src.adpilot.seed_cli

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

api-dev:
	uvicorn api:app --reload --host 127.0.0.1 --port 8000
