.PHONY: help dev seed test clean

help:
	@echo "Shaha Finlease - Speech Analytics & Compliance Prototype"
	@echo ""
	@echo "Available targets:"
	@echo "  make dev      - Start both backend and frontend servers with docker-compose"
	@echo "  make seed     - Reset database and seed with test data"
	@echo "  make test     - Run all tests (backend pytest + frontend vitest)"
	@echo "  make clean    - Stop containers and remove volumes"
	@echo ""

dev:
	docker-compose up --build

seed:
	cd backend && python scripts/seed_db.py

test:
	cd backend && pytest
	cd frontend && npm run test

clean:
	docker-compose down -v
