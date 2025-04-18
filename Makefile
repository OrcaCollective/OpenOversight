export UID=$(shell id -u)

default: build start create_db populate test stop clean

.PHONY: build
build: ## Build containers
	docker-compose build

.PHONY: build_with_version
build_with_version: create_empty_secret
	docker-compose build --build-arg MAKE_PYTHON_VERSION=$(PYTHON_VERSION)

.PHONY: test_with_version
test_with_version: build_with_version
	touch OpenOversight/tests/coverage.xml
	docker compose run --rm web-test pytest --cov=OpenOversight --cov-report xml:OpenOversight/tests/coverage.xml --doctest-modules -n 4 --dist=loadfile -v OpenOversight/tests/

.PHONY: start
start: build ## Run containers
	docker-compose up -d

.PHONY: create_db
create_db: start
	@until docker-compose exec postgres psql -h localhost -U openoversight -c '\l' postgres &>/dev/null; do \
		echo "Postgres is unavailable - sleeping..."; \
		sleep 1; \
	done
	@echo "Postgres is up"
	## Creating database
	docker compose run --rm web python ./create_db.py

.PHONY: create_db_diagram
create_db_diagram:
	# Create new dot file showing current version of schema
	eralchemy -i postgresql://openoversight:terriblepassword@localhost/openoversight-dev -o database/schema.new.dot
	# Sort new version of schema file
	sort database/schema.new.dot -o schema.new.dot.sorted
	# Create old schema file if it does not exist and then sort it
	touch database/schema.dot
	sort database/schema.dot -o schema.dot.sorted
	# Create a new diagram if there are changes, otherwise clean up files
	@if diff schema.dot.sorted schema.new.dot.sorted > /dev/null 2>&1; then \
		echo 'No schema changes detected!'; \
		rm database/schema.new.dot; \
	else \
		echo 'Detected schema changes, making new DB relationship diagram!'; \
		mv database/schema.new.dot database/schema.dot; \
		dot -Tpng -o database/database_relationships.png -Grankdir=TB -Kdot database/schema.dot; \
	fi
	# Remove all sorted files
	rm schema.dot.sorted schema.new.dot.sorted

.PHONY: dev
dev: create_empty_secret build start create_db populate

.PHONY: populate
populate: create_db ## Build and run containers
	@until docker-compose exec postgres psql -h localhost -U openoversight -c '\l' postgres &>/dev/null; do \
		echo "Postgres is unavailable - sleeping..."; \
		sleep 1; \
	done
	@echo "Postgres is up"
	## Populate database with test data
	docker-compose run --rm web python ../test_data.py -p

.PHONY: testlocal
testlocal:
	pytest

.PHONY: test
test: start ## Run tests
	if [ -z "$(name)" ]; then \
		ENV=testing docker-compose run --rm web pytest --cov --doctest-modules -n auto --dist=loadfile -v OpenOversight/tests/; \
	else \
		ENV=testing docker-compose run --rm web pytest --cov --doctest-modules -v OpenOversight/tests/ -k $(name); \
	fi

.PHONY: lint
lint:
	docker-compose run --no-deps --rm web /bin/bash -c 'flake8; mypy app --config="../mypy.ini"'

.PHONY: stop
stop: ## Stop containers
	docker-compose stop

.PHONY: clean
clean: clean_assets stop ## Remove containers
	docker compose rm -f

.PHONY: clean_all
clean_all: clean stop ## Wipe database
	docker-compose down -v

.PHONY: docs
docs: ## Build project documentation in live reload for editing
	make -C docs/ clean && sphinx-autobuild docs/ docs/_build/html

.PHONY: help
help: ## Print this message and exit
	@printf "OpenOversight: Makefile for development, documentation and testing.\n"
	@printf "Subcommands:\n\n"
	@awk 'BEGIN {FS = ":.*?## "} /^[0-9a-zA-Z_-]+:.*?## / {printf "\033[36m%s\033[0m : %s\n", $$1, $$2}' $(MAKEFILE_LIST) \
		| sort \
		| column -s ':' -t

.PHONY: attach
attach:
	docker-compose exec postgres psql -h localhost -U openoversight openoversight-dev

.PHONY: create_empty_secret
create_empty_secret: ## This is needed to make sure docker doesn't create an empty directory, or delete that directory first
	touch service_account_key.json || \
	(echo "Need to delete that empty directory first"; \
	 sudo rm -d service_account_key.json/; \
	 touch service_account_key.json)
