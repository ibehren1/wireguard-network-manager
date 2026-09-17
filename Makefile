SHELL := /bin/bash
COMPOSE := docker compose

.PHONY: help env build up down restart logs ps clean smoke-test

help:
	@echo "Targets:"
	@echo "  make env         - create .env from .env.example with random secrets (skips if .env exists)"
	@echo "  make build       - build the Docker image"
	@echo "  make up          - generate env (if needed), build, and start the stack"
	@echo "  make down        - stop the stack"
	@echo "  make restart     - restart the running container"
	@echo "  make logs        - tail container logs"
	@echo "  make ps          - show container status"
	@echo "  make clean       - stop the stack and delete the Mongo data volume"
	@echo "  make smoke-test  - start the stack and verify the login page responds"

env:
	@if [ -f .env ]; then \
		echo ".env already exists, skipping."; \
	else \
		cp .env.example .env; \
		SECRET_KEY=$$(python3 -c "import secrets; print(secrets.token_hex(32))"); \
		ENCRYPTION_KEY=$$(python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"); \
		ADMIN_PASSWORD=$$(python3 -c "import secrets; print(secrets.token_urlsafe(12))"); \
		sed -i.bak "s/^SECRET_KEY=.*/SECRET_KEY=$$SECRET_KEY/" .env; \
		sed -i.bak "s/^ENCRYPTION_KEY=.*/ENCRYPTION_KEY=$$ENCRYPTION_KEY/" .env; \
		sed -i.bak "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$$ADMIN_PASSWORD/" .env; \
		rm -f .env.bak; \
		echo "Generated .env (admin username: admin / password: $$ADMIN_PASSWORD)"; \
	fi

build:
	$(COMPOSE) build

up: env build
	$(COMPOSE) up -d
	@echo "Starting... app will be at http://localhost:8080"

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps

clean:
	$(COMPOSE) down -v

smoke-test: up
	@echo "Waiting for the app to respond..."
	@for i in $$(seq 1 30); do \
		code=$$(curl -4 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/auth/login || true); \
		if [ "$$code" = "200" ]; then echo "OK: login page responded 200."; exit 0; fi; \
		sleep 1; \
	done; \
	echo "FAILED: app did not respond within 30s. Check 'make logs'."; exit 1
