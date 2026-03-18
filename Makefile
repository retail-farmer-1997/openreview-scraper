PYTHON ?= python3

.PHONY: guardrails tests validate

guardrails:
	$(PYTHON) scripts/check_agent_docs.py
	$(PYTHON) scripts/check_architecture.py

tests:
	$(PYTHON) scripts/run_repo_checks.py tests

validate:
	$(PYTHON) scripts/run_repo_checks.py all
