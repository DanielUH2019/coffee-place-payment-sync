# coffee-sync client image, built with uv.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /work

# Install dependencies first (cached) using the lockfile, then the project itself.
COPY pyproject.toml uv.lock README.md ./
COPY coffee_sync ./coffee_sync
RUN uv sync --frozen --no-dev

# Put the project's venv on PATH so `coffee-sync` is directly invocable.
ENV PATH="/work/.venv/bin:$PATH"

# Args passed to `docker compose run client ...` become the CSV path / flags.
ENTRYPOINT ["coffee-sync"]
