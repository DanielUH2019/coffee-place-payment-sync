# coffee-sync

The Python CLI that reads a Coffee Place notebook CSV and reliably propagates each
payment to the StarHarbour Central System. Managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                   # create the venv and install deps
uv run coffee-sync ../examples/payments.csv --base-url http://localhost:9091
uv run pytest -m "not integration"        # unit tests
uv run pytest -m integration              # live end-to-end tests (needs the compose stack)
```

See the repository root `README.md` for the full project and architecture.
