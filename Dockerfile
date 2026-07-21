FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

# make runs the pipeline; git is needed for the git-hosted package in
# dbt/packages.yml (dbt-audit-helper), pulled down by `dbt deps`; gcc (via
# build-essential) is needed because mmh3 (a transitive dep of
# dbt-athena-community) has no prebuilt wheel for this platform/Python
# combo yet and compiles from source.
RUN apt-get update && apt-get install -y --no-install-recommends make git build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen

COPY Makefile ./
COPY dbt ./dbt

ENV PATH="/app/.venv/bin:${PATH}"

# Regenerate synthetic source data on every run, then seed + run + test the
# dbt project against whatever DBT_TARGET is set (see dbt/profiles.yml).
CMD ["make", "gen", "build"]
