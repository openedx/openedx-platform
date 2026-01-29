# Do things in edx-platform
.PHONY: base-requirements check-types clean \
  compile-requirements detect_changed_source_translations dev-requirements \
  docs extract_translations \
  guides help lint-imports local-requirements migrate migrate-lms migrate-cms \
  pre-requirements pull pull_xblock_translations pull_translations push_translations \
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
	DJANGO_SETTINGS_MODULE=docs.docs_settings python manage.py lms generate_swagger --generator-class=edx_api_doc_tools.ApiSchemaGenerator -o $(SWAGGER)

extract_translations: ## extract localizable strings from sources
	i18n_tool extract --no-segment -v
	cd conf/locale/en/LC_MESSAGES && msgcat djangojs.po underscore.po -o djangojs.po

pull_plugin_translations:  ## Pull translations for edx_django_utils.plugins for both lms and cms
	python manage.py lms pull_plugin_translations --verbose $(ATLAS_OPTIONS)
	python manage.py lms compile_plugin_translations

pull_xblock_translations:  ## pull xblock translations via atlas
	python manage.py lms pull_xblock_translations --verbose $(ATLAS_OPTIONS)
	python manage.py lms compile_xblock_translations
	python manage.py cms compile_xblock_translations

clean_translations: ## Remove existing translations to prepare for a fresh pull
	# Removes core edx-platform translations but keeps config files and Esperanto (eo) test translations
	find conf/locale/ -type f \! -path '*/eo/*' \( -name '*.mo' -o -name '*.po' \) -delete
	# Removes the xblocks/plugins and js-compiled translations
	rm -rf conf/plugins-locale cms/static/js/i18n/ lms/static/js/i18n/ cms/static/js/xblock.v1-i18n/ lms/static/js/xblock.v1-i18n/

pull_translations: clean_translations  ## pull translations via atlas
	make pull_xblock_translations
	make pull_plugin_translations
	atlas pull $(ATLAS_OPTIONS) \
	    translations/edx-platform/conf/locale:conf/locale \
	    $(ATLAS_EXTRA_SOURCES)
	python manage.py lms compilemessages
	python manage.py lms compilejsi18n
	python manage.py cms compilejsi18n

detect_changed_source_translations: ## check if translation files are up-to-date
	i18n_tool changed

pre-requirements: ## install Python requirements for running pip-tools
	pip install -r requirements/pip-tools.txt

local-requirements:
# 	edx-platform installs some Python projects from within the edx-platform repo itself.
	pip install -e .

dev-requirements: pre-requirements
	@# The "$(wildcard..)" is to include private.txt if it exists, and make no mention
	@# of it if it does not.  Shell wildcarding can't do that with default options.
	pip-sync requirements/edx/development.txt $(wildcard requirements/edx/private.txt)
	make local-requirements

base-requirements: pre-requirements
	pip-sync requirements/edx/base.txt
	make local-requirements

test-requirements: pre-requirements
	pip-sync --pip-args="--exists-action=w" requirements/edx/testing.txt
	make local-requirements

requirements: dev-requirements ## install development environment requirements

# Order is very important in this list: files must appear after everything they include!
REQ_FILES = \
	requirements/edx/coverage \
	requirements/edx-sandbox/base \
	requirements/edx/base \
	requirements/edx/doc \
	requirements/edx/testing \
	requirements/edx/assets \
	requirements/edx/development \
	requirements/edx/semgrep \
	scripts/xblock/requirements \
	scripts/user_retirement/requirements/base \
	scripts/user_retirement/requirements/testing \
	scripts/structures_pruning/requirements/base \
	scripts/structures_pruning/requirements/testing

define COMMON_CONSTRAINTS_TEMP_COMMENT
# This is a temporary solution to override the real common_constraints.txt\n# In edx-lint, until the pyjwt constraint in edx-lint has been removed.\n# See BOM-2721 for more details.\n# Below is the copied and edited version of common_constraints\n
endef

COMMON_CONSTRAINTS_TXT=requirements/common_constraints.txt
.PHONY: $(COMMON_CONSTRAINTS_TXT)
$(COMMON_CONSTRAINTS_TXT):
	curl -L https://raw.githubusercontent.com/edx/edx-lint/master/edx_lint/files/common_constraints.txt > "$(@)"
	printf "$(COMMON_CONSTRAINTS_TEMP_COMMENT)" | cat - $(@) > temp && mv temp $(@)

compile-requirements: export CUSTOM_COMPILE_COMMAND=make upgrade
compile-requirements: pre-requirements ## Re-compile *.in requirements to *.txt
	@# Bootstrapping: Rebuild pip and pip-tools first, and then install them
	@# so that if there are any failures we'll know now, rather than the next
	@# time someone tries to use the outputs.
	sed 's/Django<5.0//g' requirements/common_constraints.txt > requirements/common_constraints.tmp
	mv requirements/common_constraints.tmp requirements/common_constraints.txt
	sed 's/pip<25.3//g' requirements/common_constraints.txt > requirements/common_constraints.tmp
	mv requirements/common_constraints.tmp requirements/common_constraints.txt

	pip-compile -v --allow-unsafe ${COMPILE_OPTS} -o requirements/pip-tools.txt requirements/pip-tools.in
	pip install -r requirements/pip-tools.txt

	@ export REBUILD='--rebuild'; \
	for f in $(REQ_FILES); do \
		echo ; \
		echo "== $$f ===============================" ; \
		echo "pip-compile -v $$REBUILD ${COMPILE_OPTS} -o $$f.txt $$f.in"; \
		pip-compile -v $$REBUILD ${COMPILE_OPTS} -o $$f.txt $$f.in || exit 1; \
		export REBUILD=''; \
	done

upgrade: $(COMMON_CONSTRAINTS_TXT) ## update the pip requirements files to use the latest releases satisfying our constraints
	$(MAKE) compile-requirements COMPILE_OPTS="--upgrade"

upgrade-package: ## update just one package to the latest usable release
	@test -n "$(package)" || { echo "\nUsage: make upgrade-package package=...\n"; exit 1; }
	$(MAKE) compile-requirements COMPILE_OPTS="--upgrade-package $(package)"

check-types: ## run static type-checking tests
	mypy

lint-imports:
	lint-imports

migrate-lms:
	python manage.py lms showmigrations --database default --traceback --pythonpath=.
	python manage.py lms migrate --database default --traceback --pythonpath=.

migrate-cms:
	python manage.py cms showmigrations --database default --traceback --pythonpath=.
	python manage.py cms migrate --database default --noinput --traceback --pythonpath=.

migrate: migrate-lms migrate-cms

# WARNING (EXPERIMENTAL):
# This installs the Ubuntu requirements necessary to make `pip install` and some other basic
# dev commands to pass. This is not necessarily everything needed to get a working edx-platform.
# Part of https://github.com/openedx/wg-developer-experience/issues/136
ubuntu-requirements: ## Install ubuntu 22.04 system packages needed for `pip install` to work on ubuntu.
	sudo apt install libmysqlclient-dev libxmlsec1-dev

xsslint: ## check xss for quality issuest
	python scripts/xsslint/xss_linter.py \
	--rule-totals \
	--config=scripts.xsslint_config \
	--thresholds=scripts/xsslint_thresholds.json

pycodestyle: ## check python files for quality issues
	pycodestyle .

## Re-enable --lint flag when this issue https://github.com/openedx/edx-platform/issues/35775 is resolved
pii_check: ## check django models for pii annotations
	DJANGO_SETTINGS_MODULE=cms.envs.test \
	code_annotations django_find_annotations \
		--config_file .pii_annotations.yml \
		--app_name cms \
		--coverage \
		--lint

	DJANGO_SETTINGS_MODULE=lms.envs.test \
	code_annotations django_find_annotations \
		--config_file .pii_annotations.yml \
		--app_name lms \
		--coverage \
		--lint

check_keywords: ## check django models for reserve keywords
	DJANGO_SETTINGS_MODULE=cms.envs.test \
	python manage.py cms check_reserved_keywords \
	--override_file db_keyword_overrides.yml

	DJANGO_SETTINGS_MODULE=lms.envs.test \
	python manage.py lms check_reserved_keywords \
	--override_file db_keyword_overrides.yml
