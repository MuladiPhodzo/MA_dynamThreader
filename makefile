# Detect OS (Windows_NT for PowerShell/CMD, else assume Unix)
ifeq ($(OS),Windows_NT)
	PYTHONPATH_SET = set PYTHONPATH=src/main/python &&
	RM = rmdir /S /Q
else
	PYTHONPATH_SET = PYTHONPATH=src/main/python
	RM = rm -rf
endif

# Variables
PYTEST_FLAGS = -v --disable-warnings --asyncio-mode=auto
COV_FLAGS = --cov=src/main/python/advisor --cov-report=term-missing --cov-report=html

# ------------------------------
# Run Tests
# ------------------------------
test:
	$(PYTHONPATH_SET) pytest $(PYTEST_FLAGS)

# ------------------------------
# Run Tests with Coverage
# ------------------------------
coverage:
	$(PYTHONPATH_SET) pytest $(PYTEST_FLAGS) $(COV_FLAGS)
	@echo "📊 Coverage report available in 'htmlcov/index.html'"

# ------------------------------
# Clean Build Artifacts
# ------------------------------
clean:
	-$(RM) build dist __pycache__ .pytest_cache *.spec htmlcov .coverage
	find . -type d -name "__pycache__" -exec $(RM) {} + || true

# ------------------------------
# Code Quality
# ------------------------------
format:
	black src/ tests

lint:
	flake8 src tests

typecheck:
	mypy src

# ------------------------------
# Build and Packaging
# ------------------------------
build: clean test
	pyinstaller MA_DynamAdvisor.spec

run:
	python src/main/python/advisor/MA_DynamAdvisor.py

install:
	pip install -r requirements.txt

update-deps:
	pip install --upgrade -r requirements.txt

package: build
	cd dist && zip -r MA_DynamAdvisor.zip MA_DynamAdvisor.exe README.md

release: lint test build package
	@echo "🚀 Release build completed!"

