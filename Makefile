.PHONY: help install dev run test lint format docker-build docker-up docker-down docker-logs migrate shell clean

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install production dependencies
	pip install .

dev:  ## Install development dependencies
	pip install -e ".[dev]"

run:  ## Run the application locally
	uvicorn hlss.main:app --reload --host 0.0.0.0 --port 8000

test:  ## Run tests
	pytest tests/ -v --cov=hlss --cov-report=term-missing

lint:  ## Run linters
	ruff check src/
	mypy src/

format:  ## Format code
	black src/ tests/
	ruff check --fix src/

docker-build:  ## Build Docker image
	docker compose build

docker-up:  ## Start services with Docker Compose
	docker compose up -d

docker-down:  ## Stop services
	docker compose down

docker-logs:  ## View logs
	docker compose logs -f

migrate:  ## Run database migrations
	alembic upgrade head

migrate-create:  ## Create a new migration (usage: make migrate-create msg="description")
	alembic revision --autogenerate -m "$(msg)"

shell:  ## Open a Python shell with app context
	python -c "from hlss.database import *; from hlss.models import *"

clean:  ## Remove build artifacts
	rm -rf build/ dist/ *.egg-info .pytest_cache .coverage htmlcov/ .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
