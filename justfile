set dotenv-load
version := `uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'`

clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage dist build src/*.egg-info

build: clean lint audit test
    uv build

format:
    uv run ruff check --select I --fix src tests
    uv run ruff format src tests

test:
    uv run pytest -n auto tests

lint: format
    uv run ruff check src tests
    uv run mypy src

audit:
    uv export --no-dev --all-extras --format requirements-txt --no-emit-project > requirements.txt
    uv run pip-audit -r requirements.txt --disable-pip
    rm requirements.txt
    uv run bandit --silent --recursive --configfile "pyproject.toml" src

sync:
    uv sync

publish: build
    git diff-index --quiet HEAD
    printf "Enter PyPI token: " && IFS= read -rs TOKEN && echo && uv publish --token "$TOKEN"
    git tag -a 'v{{version}}' -m 'v{{version}}'
    git push origin v{{version}}
