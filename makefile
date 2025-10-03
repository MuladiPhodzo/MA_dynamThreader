# Detect OS (Windows_NT for PowerShell/CMD, else assume Unix)
ifeq ($(OS),Windows_NT)
	PYTHONPATH_SET = set PYTHONPATH=src/main/python &&
	RM = rmdir /S /Q
else
	PYTHONPATH_SET = PYTHONPATH=src/main/python
	RM = rm -rf
endif

# Run tests
test:
	$(PYTHONPATH_SET) pytest -v --disable-warnings

# Clean build artifacts (pyc, cache, dist, build)
clean:
	-$(RM) build dist __pycache__ .pytest_cache *.spec

# Format code using black
format:
	black src/ tests

# Lint with flake8 (or pylint if you prefer)
lint:
	flake8 src tests

# Type checking with mypy
typecheck:
	mypy src

# Build standalone executable with pyinstaller
build: clean test
	pyinstaller --noconsole --onefile \
		--icon=src/main/python/advisor/money_robot_Q94_icon.ico \
		src/main/python/advisor/RunAdvisorBot.py

# Run the bot directly (useful for dev without rebuilding)
run:
	python src/main/python/advisor/RunAdvisorBot.py

# Install dependencies
install:
	pip install -r requirements.txt

# Update dependencies (pip-tools recommended if you use it)
update-deps:
	pip install --upgrade -r requirements.txt

# Package zip for distribution (e.g. for testers)
package: build
	cd dist && zip -r advisor_bot.zip RunAdvisorBot.exe README.md

# Full pipeline: test -> lint -> build -> package
release: lint test build package
	@echo "🚀 Release build completed!"
