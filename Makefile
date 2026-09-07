# Do things in edx-platform
.PHONY: base-requirements check-types clean \
  compile-requirements detect_changed_source_translations dev-requirements \
  docs extract_translations \
  guides help lint-imports local-requirements migrate migrate-lms migrate-cms \
  pull pull_xblock_translations pull_translations push_translations \
  requirements shell swagger \
  technical-docs test-requirements ubuntu-requirements upgrade-package upgrade

# Careful with mktemp syntax: it has to work on Mac and Ubuntu, which have differences.
PRIVATE_FILES := $(shell mktemp -u /tmp/private_files.XXXXXX)

help: ## display this help message
	@echo "Please use \`make <target>' where <target> is one of"
	@grep '^[a-zA-Z]' $(MAKEFILE_LIST) | sort | awk -F ':.*?## ' 'NF==2 {printf "\033[36m  %-25s\033[0m %s\n", $$1, $$2}'

clean: ## archive and delete most git-ignored files
	@# Remove all the git-ignored stuff, but save and restore things marked
	@# by start-noclean/end-noclean. Include Makefile in the tarball so that
	@# there's always at least one file even if there are no private files.
	sed -n -e '/start-noclean/,/end-noclean/p' < .gitignore > /tmp/private-files
	-tar cf $(PRIVATE_FILES) Makefile `git ls-files --exclude-from=/tmp/private-files --ignored --others`
	-git clean -fdX
	tar xf $(PRIVATE_FILES)
	rm $(PRIVATE_FILES)

SWAGGER = docs/lms-openapi.yaml

docs: swagger guides technical-docs ## build the documentation for this repository
	$(MAKE) -C docs html

swagger: ## generate the swagger.yaml file
	DJANGO_SETTINGS_MODULE=docs.docs_settings uv run python manage.py lms generate_swagger --generator-class=edx_api_doc_tools.ApiSchemaGenerator -o $(SWAGGER)

extract_translations: ## extract localizable strings from sources
	uv run i18n_tool extract --no-segment -v
	cd conf/locale/en/LC_MESSAGES && msgcat djangojs.po underscore.po -o djangojs.po

pull_plugin_translations:  ## Pull translations for edx_django_utils.plugins for both lms and cms
	uv run python manage.py lms pull_plugin_translations --verbose $(ATLAS_OPTIONS)
	uv run python manage.py lms compile_plugin_translations

pull_xblock_translations:  ## pull xblock translations via atlas
	uv run python manage.py lms pull_xblock_translations --verbose $(ATLAS_OPTIONS)
	uv run python manage.py lms compile_xblock_translations
	uv run python manage.py cms compile_xblock_translations

clean_translations: ## Remove existing translations to prepare for a fresh pull
	# Removes core edx-platform translations but keeps config files and Esperanto (eo) test translations
	find conf/locale/ -type f \! -path '*/eo/*' \( -name '*.mo' -o -name '*.po' \) -delete
	# Removes the xblocks/plugins and js-compiled translations
	rm -rf conf/plugins-locale cms/static/js/i18n/ lms/static/js/i18n/ cms/static/js/xblock.v1-i18n/ lms/static/js/xblock.v1-i18n/

pull_translations: clean_translations  ## pull translations via atlas
	make pull_xblock_translations
	make pull_plugin_translations
	uv run atlas pull $(ATLAS_OPTIONS) \
	    translations/edx-platform/conf/locale:conf/locale \
	    $(ATLAS_EXTRA_SOURCES)
	uv run python manage.py lms compilemessages
	uv run python manage.py lms compilejsi18n
	uv run python manage.py cms compilejsi18n

detect_changed_source_translations: ## check if translation files are up-to-date
	uv run i18n_tool changed

local-requirements: ## no-op; kept for backwards compatibility -- uv sync handles this now
	@echo "WARNING: 'make local-requirements' is a no-op post uv migration. Please update your code."

dev-requirements: ## install development environment requirements
	uv sync --group development --group ci --frozen

base-requirements: ## install only production/runtime dependencies
	uv sync --no-default-groups --group bundled --frozen

test-requirements: ## install production dependencies plus the testing group (used by CI and tox)
	uv sync --no-default-groups --group testing --frozen

requirements: dev-requirements ## install development environment requirements

compile-requirements: ## Regenerate uv.lock for the root project and all uv sub-projects
	uv run --no-project --isolated --with edx-lint edx_lint write_uv_constraints pyproject.toml
	uv lock ${UV_LOCK_OPTS}

	@# Lock every uv-managed sub-project (each has its own pyproject.toml + uv.lock,
	@# independent of the root project's dependency graph) before exporting any
	@# compat file below, so a failure in one halts the whole target before
	@# anything downstream of it is (re)generated.
	@for d in requirements/edx-sandbox scripts/xblock scripts/semgrep scripts/user_retirement scripts/structures_pruning; do \
		echo ; \
		echo "== $$d ===============================" ; \
		uv run --no-project --isolated --with edx-lint edx_lint write_uv_constraints $$d/pyproject.toml && \
		(cd $$d && uv lock ${UV_LOCK_OPTS}) \
		|| exit 1; \
	done

	@# --- Everything below is DEPR-tracked compatibility-export scaffolding for
	@# external tooling (e.g. Tutor's Dockerfile) that still does
	@# `pip install -r requirements/edx/<name>.txt` directly instead of using uv.
	@# These are GENERATED FILES -- see the header comment in each for what
	@# regenerates them. Remove this whole section (and the scripts/*/requirements/
	@# *.txt targets it writes) once external consumers have migrated to `uv sync`:
	@# https://github.com/openedx/public-engineering/issues/552
	@mkdir -p requirements/edx
	@{ \
		echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
		echo "# Compatibility export of [project.dependencies] plus the 'bundled' group"; \
		echo "# (optional third-party add-ons installed by default) for tools that still"; \
		echo "# 'pip install -r requirements/edx/base.txt' directly instead of using uv."; \
		echo "# Source of truth: [project.dependencies] / [dependency-groups].bundled in pyproject.toml / uv.lock."; \
		uv export --frozen --no-hashes --no-default-groups --group bundled --no-emit-project; \
	} > requirements/edx/base.txt
	@{ \
		echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
		echo "# Compatibility export of the 'assets' dependency-group for tools that still"; \
		echo "# 'pip install -r requirements/edx/assets.txt' directly instead of using uv."; \
		echo "# Source of truth: [dependency-groups].assets in pyproject.toml / uv.lock."; \
		uv export --frozen --no-hashes --only-group assets --no-emit-project; \
	} > requirements/edx/assets.txt
	@{ \
		echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
		echo "# Compatibility export of the 'development' and 'ci' dependency-groups for"; \
		echo "# tools that still 'pip install -r requirements/edx/development.txt'"; \
		echo "# directly instead of using uv."; \
		echo "# Source of truth: [dependency-groups].development / .ci in pyproject.toml / uv.lock."; \
		uv export --frozen --no-hashes --group development --group ci --no-emit-project; \
	} > requirements/edx/development.txt

	@# requirements/edx-sandbox, scripts/xblock: single compat export, no dependency-groups.
	@{ \
		echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
		echo "# Compatibility export for anyone still 'pip install -r requirements/edx-sandbox/base.txt'"; \
		echo "# directly instead of using uv. Source of truth: requirements/edx-sandbox/pyproject.toml / uv.lock."; \
		(cd requirements/edx-sandbox && uv export --frozen --no-hashes --no-emit-project); \
	} > requirements/edx-sandbox/base.txt
	@{ \
		echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
		echo "# Compatibility export for anyone still 'pip install -r scripts/xblock/requirements.txt'"; \
		echo "# directly instead of using uv. Source of truth: scripts/xblock/pyproject.toml / uv.lock."; \
		(cd scripts/xblock && uv export --frozen --no-hashes --no-emit-project); \
	} > scripts/xblock/requirements.txt

	@# scripts/user_retirement and scripts/structures_pruning: base + testing (test group) compat exports.
	@for d in scripts/user_retirement scripts/structures_pruning; do \
		{ \
			echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
			echo "# Compatibility export for anyone still 'pip install -r $$d/requirements/base.txt'"; \
			echo "# directly instead of using uv. Source of truth: $$d/pyproject.toml / uv.lock."; \
			(cd $$d && uv export --frozen --no-hashes --no-emit-project); \
		} > $$d/requirements/base.txt && \
		{ \
			echo "# GENERATED FILE, DO NOT EDIT DIRECTLY."; \
			echo "# Compatibility export for anyone still 'pip install -r $$d/requirements/testing.txt'"; \
			echo "# directly instead of using uv. Source of truth: $$d/pyproject.toml (test group) / uv.lock."; \
			(cd $$d && uv export --frozen --no-hashes --group test --no-emit-project); \
		} > $$d/requirements/testing.txt \
		|| exit 1; \
	done

upgrade: ## update all dependencies (uv.lock for the root project and all uv sub-projects) to the latest releases satisfying our constraints
	$(MAKE) compile-requirements UV_LOCK_OPTS="--upgrade"

upgrade-package: ## update just one package to the latest usable release
	@test -n "$(package)" || { echo "\nUsage: make upgrade-package package=...\n"; exit 1; }
	$(MAKE) compile-requirements UV_LOCK_OPTS="--upgrade-package $(package)"

check-types: ## run static type-checking tests
	uv run mypy

lint-imports:
	uv run lint-imports

migrate-lms:
	uv run python manage.py lms showmigrations --database default --traceback --pythonpath=.
	uv run python manage.py lms migrate --database default --traceback --pythonpath=.

migrate-cms:
	uv run python manage.py cms showmigrations --database default --traceback --pythonpath=.
	uv run python manage.py cms migrate --database default --noinput --traceback --pythonpath=.

migrate: migrate-lms migrate-cms

# WARNING (EXPERIMENTAL):
# This installs the Ubuntu requirements necessary to make `pip install` and some other basic
# dev commands to pass. This is not necessarily everything needed to get a working edx-platform.
# Part of https://github.com/openedx/wg-developer-experience/issues/136
ubuntu-requirements: ## Install ubuntu 22.04 system packages needed for `pip install` to work on ubuntu.
	sudo apt install libmysqlclient-dev libxmlsec1-dev

xsslint: ## check xss for quality issuest
	uv run python scripts/xsslint/xss_linter.py \
	--rule-totals \
	--config=scripts.xsslint_config \
	--thresholds=scripts/xsslint_thresholds.json

ruff: ## check python files with ruff
	uv run ruff check .

## Re-enable --lint flag when this issue https://github.com/openedx/edx-platform/issues/35775 is resolved
pii_check: ## check django models for pii annotations
	DJANGO_SETTINGS_MODULE=cms.envs.test \
	uv run code_annotations django_find_annotations \
		--config_file .pii_annotations.yml \
		--coverage \
		--lint

	DJANGO_SETTINGS_MODULE=lms.envs.test \
	uv run code_annotations django_find_annotations \
		--config_file .pii_annotations.yml \
		--coverage \
		--lint

check_keywords: ## check django models for reserve keywords
	DJANGO_SETTINGS_MODULE=cms.envs.test \
	uv run python manage.py cms check_reserved_keywords \
	--override_file db_keyword_overrides.yml

	DJANGO_SETTINGS_MODULE=lms.envs.test \
	uv run python manage.py lms check_reserved_keywords \
	--override_file db_keyword_overrides.yml
