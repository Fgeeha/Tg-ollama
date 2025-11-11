.PHONY: help build run stop clean test logs shell dev install lint format check migrate

# Variables
PROJECT_NAME = tg-ollama-bot
DOCKER_IMAGE = $(PROJECT_NAME):latest
DOCKER_CONTAINER = $(PROJECT_NAME)-container
ENV_FILE = .env

# Default target
help: ## Show this help message
	@echo 'Usage: make [target]'
	@echo ''
	@echo 'Available targets:'
	@awk 'BEGIN {FS = ":.*##"; printf "\033[36m\033[0m"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Development

install: ## Install dependencies using Poetry
	poetry install

dev: ## Run bot in development mode
	poetry run python -m src.bot.main

lint: ## Run linting checks
	poetry run ruff check src/
	poetry run mypy src/

format: ## Format code with black
	poetry run black src/
	poetry run ruff check --fix src/

test: ## Run tests with coverage
	poetry run pytest --cov=src --cov-report=term-missing

check: lint test ## Run all checks (lint + test)

##@ Docker

build: ## Build Docker image
	docker build -t $(DOCKER_IMAGE) .

run: build ## Start container (builds if needed)
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "Error: $(ENV_FILE) file not found!"; \
		echo "Please create it from .env.example"; \
		exit 1; \
	fi
	docker run -d \
		--name $(DOCKER_CONTAINER) \
		--env-file $(ENV_FILE) \
		-v $(PWD)/data:/app/data \
		--restart unless-stopped \
		$(DOCKER_IMAGE)
	@echo "Container started: $(DOCKER_CONTAINER)"

stop: ## Stop container
	docker stop $(DOCKER_CONTAINER) || true
	docker rm $(DOCKER_CONTAINER) || true
	@echo "Container stopped and removed: $(DOCKER_CONTAINER)"

restart: stop run ## Restart container

logs: ## View container logs
	docker logs -f $(DOCKER_CONTAINER)

shell: ## Open shell in running container
	docker exec -it $(DOCKER_CONTAINER) /bin/bash

clean: stop ## Remove containers and images
	docker rmi $(DOCKER_IMAGE) || true
	rm -rf data/*.db
	@echo "Cleanup complete"

##@ Database

migrate: ## Run database migrations
	poetry run alembic upgrade head

migrate-create: ## Create new migration
	@read -p "Enter migration name: " name; \
	poetry run alembic revision --autogenerate -m "$$name"

migrate-rollback: ## Rollback last migration
	poetry run alembic downgrade -1

##@ Production

deploy: build ## Deploy to production (example)
	@echo "Deploying $(DOCKER_IMAGE) to production..."
	# Add your deployment commands here
	# docker tag $(DOCKER_IMAGE) registry.example.com/$(DOCKER_IMAGE)
	# docker push registry.example.com/$(DOCKER_IMAGE)

backup: ## Backup database
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	docker exec $(DOCKER_CONTAINER) sqlite3 /app/data/bot.db ".backup /app/data/backup_$$timestamp.db" && \
	docker cp $(DOCKER_CONTAINER):/app/data/backup_$$timestamp.db ./backups/ && \
	echo "Backup created: ./backups/backup_$$timestamp.db"

restore: ## Restore database from backup
	@read -p "Enter backup filename (from backups/ directory): " filename; \
	if [ -f "./backups/$$filename" ]; then \
		docker cp ./backups/$$filename $(DOCKER_CONTAINER):/app/data/restore.db && \
		docker exec $(DOCKER_CONTAINER) mv /app/data/bot.db /app/data/bot.db.old && \
		docker exec $(DOCKER_CONTAINER) mv /app/data/restore.db /app/data/bot.db && \
		echo "Database restored from $$filename"; \
	else \
		echo "Backup file not found: ./backups/$$filename"; \
	fi

##@ Monitoring

status: ## Check container status
	@docker ps -f name=$(DOCKER_CONTAINER) --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

health: ## Check health status
	@docker inspect $(DOCKER_CONTAINER) --format='{{json .State.Health}}' | python -m json.tool

stats: ## Show container resource usage
	docker stats $(DOCKER_CONTAINER) --no-stream
