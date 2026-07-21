FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

WORKDIR /app

COPY pyproject.toml uv.lock requirements.txt ./
RUN uv venv && uv pip install -q -r requirements.txt

COPY Makefile ./
COPY dbt ./dbt

ENV PATH="/app/.venv/bin:${PATH}"

# Regenerate synthetic source data on every run, then seed + run + test the
# dbt project against whatever DBT_TARGET is set (see dbt/profiles.yml).
CMD ["make", "gen", "build"]
