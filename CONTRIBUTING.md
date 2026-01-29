# Contributing

Thanks for helping improve Agent Sessions. This project aims to stay minimal, fast to run locally, and safe to share.

## Development setup

With `uv`:

```bash
uv venv
uv pip install -e ".[dev]"
```

With `pip`:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Running checks

```bash
pytest           # tests
ruff check .     # lint
ruff format .    # format (run ruff format --check . in CI)
pyright          # type check
```

## Fixtures and test data

Please only add synthetic or anonymized fixtures. Do not commit real transcripts, usernames, project names, file paths, or secrets. If you need to add a new provider fixture, replace any identifiable content with placeholder values that preserve structure and timestamps.

## Support / Questions

Open a GitHub issue or discussion for help. For security concerns, see `SECURITY.md`.

## Releases

Versioning is SemVer-ish: we use `MAJOR.MINOR.PATCH`, but `0.x` releases may include breaking changes while the API stabilizes.

Release steps:

1. Update `CHANGELOG.md` with notable changes.
2. Bump the version in `pyproject.toml`.
3. Tag the release: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. Create a GitHub release from the tag.
5. Optional: publish to PyPI later if desired.
