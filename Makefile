YEARS ?= 6
DBT_DIR := dbt
ARGS ?=
INFRA_DIR := infra
STACK_NAME := JaffleShopStack
AWS_REGION := us-east-1
# PATH-prepend rather than `. .venv/bin/activate`: uv's generated activate
# script isn't strict-POSIX-sh compatible (it uses zsh-only syntax that
# breaks under some /bin/sh implementations), whereas this is portable.
CDK := PATH="$$PWD/.venv/bin:$$PATH" npx aws-cdk@latest

.PHONY: help venv install gen deps seed run test build clean-data clean load \
	infra-install infra-bootstrap infra-synth infra-diff infra-deploy infra-destroy infra-run infra-logs

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

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

clean-data: ## Remove generated seed data (keeps the warehouse; used by load)
	rm -rf $(DBT_DIR)/seeds/jaffle-data

clean: clean-data ## Remove generated data, dbt artifacts, and the local warehouse
	uv run dbt clean --project-dir $(DBT_DIR) --profiles-dir $(DBT_DIR)
	rm -f jaffle_shop.duckdb

load: venv install gen seed clean-data ## Run the full venv -> install -> gen -> seed -> clean-data pipeline

infra-install: ## Create the infra venv and install CDK dependencies
	cd $(INFRA_DIR) && (test -d .venv || uv venv) && uv pip install -q -r requirements.txt

infra-bootstrap: infra-install ## One-time CDK bootstrap for this AWS account/region
	cd $(INFRA_DIR) && $(CDK) bootstrap --app "python3 app.py"

infra-synth: infra-install ## Synthesize the CloudFormation template (local only, no AWS calls)
	cd $(INFRA_DIR) && $(CDK) synth --app "python3 app.py"

infra-diff: infra-install ## Show what the next infra-deploy would change
	cd $(INFRA_DIR) && $(CDK) diff --app "python3 app.py"

infra-deploy: infra-install ## Create or update the AWS stack (builds and pushes the Docker image)
	cd $(INFRA_DIR) && $(CDK) deploy --app "python3 app.py"

infra-destroy: infra-install ## Destroy the AWS stack and all its resources
	cd $(INFRA_DIR) && $(CDK) destroy --app "python3 app.py"

infra-run: ## Manually trigger the Fargate task once, outside the schedule
	@CLUSTER=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(AWS_REGION) --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text); \
	TASKDEF=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(AWS_REGION) --query "Stacks[0].Outputs[?OutputKey=='TaskDefinitionArn'].OutputValue" --output text); \
	SUBNETS=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(AWS_REGION) --query "Stacks[0].Outputs[?OutputKey=='PublicSubnetIds'].OutputValue" --output text); \
	SG=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(AWS_REGION) --query "Stacks[0].Outputs[?OutputKey=='TaskSecurityGroupId'].OutputValue" --output text); \
	aws ecs run-task --cluster "$$CLUSTER" --task-definition "$$TASKDEF" --launch-type FARGATE --region $(AWS_REGION) \
	  --network-configuration "awsvpcConfiguration={subnets=[$$SUBNETS],securityGroups=[$$SG],assignPublicIp=ENABLED}"

infra-logs: ## Tail the Fargate task's CloudWatch logs
	@LOGGROUP=$$(aws cloudformation describe-stacks --stack-name $(STACK_NAME) --region $(AWS_REGION) --query "Stacks[0].Outputs[?OutputKey=='LogGroupName'].OutputValue" --output text); \
	aws logs tail "$$LOGGROUP" --follow --region $(AWS_REGION)
