set shell := ["bash", "-uc"]

stack_name := "JaffleShopStack"
# infra-deploy et al defer to app.py's own region resolution
# (CDK_DEFAULT_REGION env var, else "us-east-1"); infra-run/infra-logs call
# `aws` directly instead, which needs an explicit --region, so resolve it
# the same way here (falling back through AWS config before "us-east-1",
# since CDK_DEFAULT_REGION is normally only set by the cdk CLI itself, not
# ambient in a plain shell).
aws_region := `echo "${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"`
# PATH-prepend rather than `. .venv/bin/activate`: uv's generated activate
# script isn't strict-POSIX-sh compatible (it uses zsh-only syntax that
# breaks under just's embedded shell), whereas this is portable.
cdk := 'PATH="' + justfile_directory() + '/.venv/bin:$PATH" npx aws-cdk@latest'

venv:
    uv venv

install:
    uv pip install -q -r requirements.txt

gen years="6":
    cd dbt && uv run jafgen {{years}} && rm -rf seeds/jaffle-data && mv jaffle-data seeds

deps:
    uv run dbt deps --project-dir dbt --profiles-dir dbt

seed *args: deps
    uv run dbt seed --project-dir dbt --profiles-dir dbt --full-refresh --vars '{"load_source_data": true}' {{args}}

# Staging models reference the jaffle-data seeds via source(), not ref(),
# so dbt has no DAG edge between them -- the seed must load in its own
# invocation before run/test/build, or the "raw" schema won't exist yet.
_ensure-seeded: deps
    uv run dbt seed --project-dir dbt --profiles-dir dbt --full-refresh --vars '{"load_source_data": true}'

run *args: _ensure-seeded
    uv run dbt run --project-dir dbt --profiles-dir dbt {{args}}

test *args: _ensure-seeded
    uv run dbt test --project-dir dbt --profiles-dir dbt {{args}}

build *args: _ensure-seeded
    uv run dbt build --project-dir dbt --profiles-dir dbt {{args}}

source-freshness *args: _ensure-seeded
    uv run dbt source freshness --project-dir dbt --profiles-dir dbt {{args}}

clean-data:
    rm -rf dbt/seeds/jaffle-data

clean: clean-data
    uv run dbt clean --project-dir dbt --profiles-dir dbt
    rm -f jaffle_shop.duckdb

load: venv install gen seed clean-data

infra-install:
    cd infra/cdk && (test -d .venv || uv venv) && uv pip install -q -r requirements.txt

infra-bootstrap: infra-install
    cd infra/cdk && {{cdk}} bootstrap --app "python3 app.py"

infra-synth: infra-install
    cd infra/cdk && {{cdk}} synth --app "python3 app.py"

infra-diff: infra-install
    cd infra/cdk && {{cdk}} diff --app "python3 app.py"

infra-deploy: infra-install
    cd infra/cdk && {{cdk}} deploy --app "python3 app.py"

infra-destroy: infra-install
    cd infra/cdk && {{cdk}} destroy --app "python3 app.py"

infra-run:
    #!/usr/bin/env bash
    set -euo pipefail
    CLUSTER=$(aws cloudformation describe-stacks --stack-name {{stack_name}} --region {{aws_region}} --query "Stacks[0].Outputs[?OutputKey=='ClusterName'].OutputValue" --output text)
    TASKDEF=$(aws cloudformation describe-stacks --stack-name {{stack_name}} --region {{aws_region}} --query "Stacks[0].Outputs[?OutputKey=='TaskDefinitionArn'].OutputValue" --output text)
    SUBNETS=$(aws cloudformation describe-stacks --stack-name {{stack_name}} --region {{aws_region}} --query "Stacks[0].Outputs[?OutputKey=='PublicSubnetIds'].OutputValue" --output text)
    SG=$(aws cloudformation describe-stacks --stack-name {{stack_name}} --region {{aws_region}} --query "Stacks[0].Outputs[?OutputKey=='TaskSecurityGroupId'].OutputValue" --output text)
    aws ecs run-task --cluster "$CLUSTER" --task-definition "$TASKDEF" --launch-type FARGATE --region {{aws_region}} \
      --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}"

infra-logs:
    #!/usr/bin/env bash
    set -euo pipefail
    LOGGROUP=$(aws cloudformation describe-stacks --stack-name {{stack_name}} --region {{aws_region}} --query "Stacks[0].Outputs[?OutputKey=='LogGroupName'].OutputValue" --output text)
    aws logs tail "$LOGGROUP" --follow --region {{aws_region}}
