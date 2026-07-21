FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# make runs the pipeline; git is needed for the git-hosted package in
# dbt/packages.yml (dbt-audit-helper), pulled down by `dbt deps`.
RUN apt-get update && apt-get install -y --no-install-recommends make git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock requirements.txt ./
RUN uv venv && uv pip install -q -r requirements.txt

COPY Makefile ./
COPY dbt ./dbt

ENV PATH="/app/.venv/bin:${PATH}"

# Regenerate synthetic source data on every run, then seed + run + test the
# dbt project against whatever DBT_TARGET is set (see dbt/profiles.yml).
CMD ["make", "gen", "build"]
