# The five commands a person needs. Everything runs through uv so the lockfile is honoured.
#   make install            create the environment from uv.lock (engine, UI and dev tools)
#   make test               run the whole test suite
#   make lint               ruff and mypy
#   make run IFACE=eth0     run the observer live on an interface (needs capture rights)
#   make release VERSION=x.y.z   tag and publish a GitHub release (the unit that reaches the Pi)

IFACE ?= eth0
OBSERVE ?= 60
LEARN ?= 120

.PHONY: install test lint run release

install:
	uv sync --extra ui --group dev

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy

run:
	uv run liscere-observe --iface $(IFACE) --observe $(OBSERVE) --learn $(LEARN)

release:
	@test -n "$(VERSION)" || (echo "usage: make release VERSION=x.y.z" && exit 2)
	gh release create v$(VERSION) --generate-notes --title "v$(VERSION)"
