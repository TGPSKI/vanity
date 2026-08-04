PYTHON   ?= python3
VANITY ?= PYTHONPATH=src $(PYTHON) -m vanity
TARGET   ?=
BACKEND  ?= git-xet
JOBS     ?= 1
LOG_DIR  ?= logs
HACK     ?=

ifneq (,$(wildcard .env))
include .env
export
endif

.PHONY: help list add status doctor fetch fetch-bg verify remove \
	config install compile test lint dev-check

help:
	@printf '%s\n' \
		'vanity commands:' \
		'  make list                  show known models, sets, and aliases' \
		'  make add REPO=<org/name>   onboard a model from Hugging Face' \
		'  make config                show where registry, library, and state live' \
		'  make status                show local fetch state' \
		'  make doctor                check git-xet/git-lfs/HF_TOKEN readiness' \
		'  make fetch TARGET=<key>    fetch a model key, set name, registry file stem, or "all"' \
		'  make fetch-bg TARGET=<key> fetch a model key, set name, file stem, or "all" in background' \
		'                             with logs' \
		'  make verify [TARGET=<t>]   verify models (defaults to "all" if unset)' \
		'  make remove TARGET=<key>   remove a local model directory (refuses TARGET=all)' \
		'' \
		'targets:' \
		'  TARGET   - model key, alias, set name, registry file stem, or "all"' \
		'  BACKEND  - fetch backend (git-xet, git-lfs); default: git-xet' \
		'  JOBS     - parallel download workers (default: 1)' \
		'' \
		'src/ (vanity package) commands:' \
		'  make install               editable-install vanity (pip install -e .)' \
		'  make compile               syntax-check every module under src/' \
		'  make test                  run the unittest suite in tests/' \
		'  make lint                  ruff check (dev-time only; not a runtime dep)' \
		'  make dev-check             compile + test (pre-commit sanity check)'

list:
	$(VANITY) list

add:
	@test -n "$(REPO)" || { echo 'set REPO=<org/name> (e.g. make add REPO=BAAI/bge-m3)'; exit 1; }
	$(VANITY) add $(REPO) $(if $(FILE),--file $(FILE),)

config:
	$(VANITY) config

status:
	$(VANITY) status

doctor:
	$(VANITY) doctor

fetch:
	@test -n "$(TARGET)" || { echo "set TARGET=<model|set|file|all> (see: make list)"; exit 1; }
	$(VANITY) fetch $(TARGET) --backend $(BACKEND) --jobs $(JOBS)

fetch-bg:
	@test -n "$(TARGET)" || { echo "set TARGET=<model|set|file|all> (see: make list)"; exit 1; }
	@mkdir -p $(LOG_DIR)
	@$(VANITY) fetch $(TARGET) --backend $(BACKEND) --jobs $(JOBS) > $(LOG_DIR)/fetch-$(TARGET).log 2>&1 & echo $$! > $(LOG_DIR)/fetch-$(TARGET).pid
	@printf 'pid: %s\nlog: %s\n' "$$(cat $(LOG_DIR)/fetch-$(TARGET).pid)" $(LOG_DIR)/fetch-$(TARGET).log

verify:
	$(VANITY) verify $(if $(TARGET),$(TARGET),all)

remove:
	@test -n "$(TARGET)" && [ "$(TARGET)" != "all" ] || { echo "refuse remove with no TARGET or TARGET=all"; exit 1; }
	$(VANITY) remove $(TARGET) --yes

# --- src/ (vanity package) ---

install:
	$(PYTHON) -m pip install -e .

compile:
	$(PYTHON) -m compileall -q src

# PYTHONPATH=src so the suite runs from a clone with nothing installed --
# without it this only passes on a machine that happens to have vanity
# pip-installed, which is exactly the case CI does not have.
test:
	PYTHONPATH=src $(PYTHON) -m unittest discover -s tests

lint:
	@command -v ruff >/dev/null || { echo 'ruff not installed: pip install ruff'; exit 1; }
	ruff check src tests

dev-check: compile test

