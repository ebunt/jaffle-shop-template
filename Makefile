YEARS ?= 6
DBT_DIR := dbt
ARGS ?=

.PHONY: help venv install gen deps seed run test build clean load

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

venv: ## Create a virtual environment with uv
	uv venv

install: ## Install project dependencies with uv
	uv pip install -q -r requirements.txt

gen: ## Generate seed data with jafgen
	cd $(DBT_DIR) && uv run jafgen $(YEARS)
	rm -rf $(DBT_DIR)/seeds/jaffle-data
	mv $(DBT_DIR)/jaffle-data $(DBT_DIR)/seeds

deps: ## Install dbt package dependencies
	uv run dbt deps --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR)

# Staging models reference the jaffle-data seeds via source(), not ref(), so
# dbt has no DAG edge between them -- the seed must load in its own
# invocation before run/test/build, or the "raw" schema won't exist yet.
SEED_CMD = uv run dbt seed --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR) --full-refresh --vars '{"load_source_data": true}'

seed: deps ## Seed the warehouse with generated data (ARGS="..." for extra dbt flags)
	$(SEED_CMD) $(ARGS)

run: deps ## Run dbt models (ARGS="..." for extra dbt flags, e.g. ARGS="-s customers")
	$(SEED_CMD)
	uv run dbt run --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR) $(ARGS)

test: deps ## Run dbt tests (ARGS="..." for extra dbt flags)
	$(SEED_CMD)
	uv run dbt test --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR) $(ARGS)

build: deps ## Run dbt build (ARGS="..." for extra dbt flags)
	$(SEED_CMD)
	uv run dbt build --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR) $(ARGS)

clean: ## Remove generated data
	rm -rf $(DBT_DIR)/jaffle-data

load: venv install gen seed clean ## Run the full venv -> install -> gen -> seed -> clean pipeline
