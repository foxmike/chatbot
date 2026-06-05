.PHONY: up down stop start logs ps db migrate reset-db

up:
	docker compose up -d db redis

down:
	docker compose down

stop:
	docker compose stop

start:
	docker compose start

logs:
	docker compose logs -f

ps:
	docker compose ps

db:
	docker compose exec db psql -U chatbot -d chatbot

migrate:
	alembic upgrade head

reset-db:
	@echo "This will DELETE ALL DATA and recreate the database. Type YES to confirm"; \
	read confirm; \
	if [ "$$confirm" = "YES" ]; then \
		docker compose down -v; \
		docker compose up -d db redis; \
		sleep 3; \
		alembic upgrade head; \
	else \
		echo "Aborted."; \
	fi
