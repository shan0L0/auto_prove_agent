.PHONY: clean clean-runs clean-cache clean-output help

RUNS_DIR := runs
WORKSPACES_DIR := $(RUNS_DIR)/workspaces
LEAN_WORKSPACE_DIR := lean_workspace
OUTPUT_DIR := output

help:
	@echo "Targets:"
	@echo "  make clean        Remove run logs, copied workspaces, Lean temp files, and Python/test caches"
	@echo "  make clean-runs   Remove only runs/ contents, including runs/workspaces"
	@echo "  make clean-lean   Remove Lean temp attempts and Lake build cache in lean_workspace/"
	@echo "  make clean-cache  Remove Python/test cache files"
	@echo "  make clean-output Remove exported output/ results"

clean: clean-runs clean-lean clean-cache

clean-runs:
	@echo "Cleaning $(RUNS_DIR)/, including $(WORKSPACES_DIR)/"
	@mkdir -p "$(RUNS_DIR)" "$(WORKSPACES_DIR)"
	@find "$(RUNS_DIR)" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
	@mkdir -p "$(WORKSPACES_DIR)"

clean-lean:
	@echo "Cleaning $(LEAN_WORKSPACE_DIR)/ temporary Lean files"
	@if [ -d "$(LEAN_WORKSPACE_DIR)" ]; then \
		find "$(LEAN_WORKSPACE_DIR)" -mindepth 1 -maxdepth 1 -type d -name "auto_prove_*" -exec rm -rf {} +; \
		rm -rf "$(LEAN_WORKSPACE_DIR)/.lake/build"; \
	fi

clean-cache:
	@echo "Cleaning Python and test caches"
	@find . -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" \) -prune -exec rm -rf {} +
	@find . -type f \( -name "*.pyc" -o -name "*.pyo" -o -name ".coverage" \) -delete

clean-output:
	@echo "Cleaning $(OUTPUT_DIR)/"
	@mkdir -p "$(OUTPUT_DIR)"
	@find "$(OUTPUT_DIR)" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
