# Единая точка входа для операций проекта.
# Зависимости ставятся только через uv — не pip и не poetry.
#
# Docker-цели используют "docker build"/"docker run" напрямую, а не
# docker-compose.yml (он есть в репозитории, но не подключён к Makefile —
# см. README про "docker compose up -d" как альтернативный путь запуска).

.DEFAULT_GOAL := help

PROJECT_NAME    := tg-ollama-bot
DOCKER_IMAGE    := $(PROJECT_NAME):latest
DOCKER_CONTAINER := $(PROJECT_NAME)-container
ENV_FILE        := .env

.PHONY: help install run lint format test check \
        migrate migration migrate-rollback \
        build up-local restart-local down-local logs shell clean \
        deploy backup restore \
        status health stats

help: ## Показать список целей
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Разработка ---------------------------------------------------------------

install: ## Установить зависимости (uv sync)
	uv sync

run: ## Запустить бота локально
	uv run python -m bot.main

lint: ## Проверить код (ruff + mypy)
	uv run ruff check src/
	uv run mypy src/

format: ## Отформатировать код (black + ruff --fix)
	uv run black src/
	uv run ruff check --fix src/

test: ## Прогнать тесты с покрытием
	uv run pytest --cov=src --cov-report=term-missing

check: lint test ## Полная проверка перед коммитом

# --- База данных ----------------------------------------------------------------

migrate: ## Применить миграции
	uv run alembic upgrade head

migration: ## Создать ревизию: make migration m="описание"
	@test -n "$(m)" || { echo "Укажите описание: make migration m=\"добавил users\""; exit 1; }
	uv run alembic revision --autogenerate -m "$(m)"

migrate-rollback: ## Откатить последнюю миграцию
	uv run alembic downgrade -1

# --- Docker -----------------------------------------------------------------------

build: ## Собрать Docker-образ
	docker build -t $(DOCKER_IMAGE) .

up-local: build ## Поднять контейнер (со сборкой при необходимости)
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "Ошибка: файл $(ENV_FILE) не найден!"; \
		echo "Создайте его из .env.example"; \
		exit 1; \
	fi
	docker run -d \
		--name $(DOCKER_CONTAINER) \
		--env-file $(ENV_FILE) \
		-v $(PWD)/data:/app/data \
		--restart unless-stopped \
		$(DOCKER_IMAGE)
	@echo "Контейнер запущен: $(DOCKER_CONTAINER)"

down-local: ## Остановить контейнер
	docker stop $(DOCKER_CONTAINER) || true
	docker rm $(DOCKER_CONTAINER) || true
	@echo "Контейнер остановлен и удалён: $(DOCKER_CONTAINER)"

restart-local: down-local up-local ## Перезапустить контейнер

logs: ## Логи контейнера (follow)
	docker logs -f $(DOCKER_CONTAINER)

shell: ## Shell внутри запущенного контейнера
	docker exec -it $(DOCKER_CONTAINER) /bin/bash

# --- Прод -------------------------------------------------------------------------

deploy: build ## Деплой в прод (пример, требует доработки под реестр)
	@echo "Деплой $(DOCKER_IMAGE) в прод..."
	# docker tag $(DOCKER_IMAGE) registry.example.com/$(DOCKER_IMAGE)
	# docker push registry.example.com/$(DOCKER_IMAGE)

# --- Резервное копирование ---------------------------------------------------------

backup: ## Бэкап базы данных
	@mkdir -p backups
	@timestamp=$$(date +%Y%m%d_%H%M%S); \
	docker exec $(DOCKER_CONTAINER) sqlite3 /app/data/bot.db ".backup /app/data/backup_$$timestamp.db" && \
	docker cp $(DOCKER_CONTAINER):/app/data/backup_$$timestamp.db ./backups/ && \
	echo "Бэкап создан: ./backups/backup_$$timestamp.db"

restore: ## Восстановить базу данных из бэкапа
	@read -p "Введите имя файла бэкапа (из каталога backups/): " filename; \
	if [ -f "./backups/$$filename" ]; then \
		docker cp ./backups/$$filename $(DOCKER_CONTAINER):/app/data/restore.db && \
		docker exec $(DOCKER_CONTAINER) mv /app/data/bot.db /app/data/bot.db.old && \
		docker exec $(DOCKER_CONTAINER) mv /app/data/restore.db /app/data/bot.db && \
		echo "База восстановлена из $$filename"; \
	else \
		echo "Файл бэкапа не найден: ./backups/$$filename"; \
	fi

# --- Мониторинг --------------------------------------------------------------------

status: ## Статус контейнера
	@docker ps -f name=$(DOCKER_CONTAINER) --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

health: ## Статус healthcheck
	@docker inspect $(DOCKER_CONTAINER) --format='{{json .State.Health}}' | python -m json.tool

stats: ## Потребление ресурсов контейнером
	docker stats $(DOCKER_CONTAINER) --no-stream

# --- Прочее -------------------------------------------------------------------------

clean: down-local ## Удалить контейнеры, образ и локальную БД
	docker rmi $(DOCKER_IMAGE) || true
	rm -rf data/*.db
	@echo "Очистка завершена"
