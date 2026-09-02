# Plan: Re-organize ReadTheDocs Tree

## Before / After (sidebar toctree)

```
BEFORE                                 AFTER
---------------------------------      ---------------------------------
"How-To" Guides          <- top       References
  How to write a celery task            ...existing items...
  How To Use the REST API               docs - App ADR index  <- moved, renamed
References                              docs - App index      <- moved, renamed
  ...existing items...                  docs - tree           <- title changed
  docs        <- auto-generated         Python Docstrings
  Python Docstrings                   Concepts and "How-To" Guides <- renamed
Concepts and Guides      <- top          extension_points
  extension_points                       testing/testing
  testing/testing                        frontend/*
  frontend/*                             rest_apis
  rest_apis                              TinyMCE Plugins      <- moved
TinyMCE (Visual Text/HTML                How to write a celery task  <- moved
  Editor) Plugins        <- top          How To Use the REST API     <- moved
App-Level Architecture
  Decision Records       <- top (no children)
App-Level Documentation  <- top (no children)
```

(Full TinyMCE title: "TinyMCE (Visual Text/HTML Editor) Plugins")

Note: `decisions/index.rst` has no valid RST title (`# edx-platform Technical
Decisions` lacks an underline), so Sphinx drops its toctree children entirely.
The numbered decisions (0000-0026) are not in the sidebar. `app_decisions` appears
because `docs/index.rst` also references it directly via an inline `:doc:` link in
the "App Documentation" grid card. Fixing `decisions/index.rst` is out of scope here.

---

## Context

The docs tree has two structural issues to fix:
1. How-To Guides and TinyMCE plugins are top-level sections, but belong under Concepts.
2. App-Level Documentation and App-Level ADRs are top-level items, but belong under References.

Generated files are produced during the Sphinx `builder-inited` hook (`docs/conf.py` ->
`on_init()` -> `docs/repository_docs.py`). They must not be edited statically.

---

## Changes

### 1. `docs/repository_docs.py`

**a. `_create_index_rst_file()`** -- add optional `title` parameter so the root
`docs/references/docs/index.rst` can have a custom title instead of just "docs":
```python
def _create_index_rst_file(self, directory_path, title=None):
    ...
    if title is None:
        title = directory_name
    file_content = f"""{title}\n{len(title) * '='}\n\n..."""
```

**b. `build_rst_docs()`** -- accept and forward `root_title`:
```python
def build_rst_docs(self, root_title=None):
    os.makedirs(self.build_path, exist_ok=True)
    self._create_index_rst_file(self.build_path, title=root_title)
    ...
```

**c. `build_apps_index()`** -- output moves to `docs/references/` directly. Update:
- Title: `"docs - App index"`
- `:doc:` links: relative from `docs/references/` -> `docs/{rel}/index`

**d. `build_decisions_index()`** -- same path move. Update:
- Title: `"docs - App ADR index"`
- Intro sentence: `'Links to per-app Architecture Decision Records (ADR) directories, supplementing the top-level'`
- Cross-ref to repo-wide decisions: `../decisions/0001-courses-in-lms`
- `:doc:` links: relative from `docs/references/` -> `docs/{rel_from_root}/index`

### 2. `docs/conf.py` (`on_init`)

```python
repo_docs.build_rst_docs(root_title='docs - tree')
repo_docs.build_apps_index(root / 'docs' / 'references' / 'app_index.rst')
repo_docs.build_decisions_index(root / 'docs' / 'references' / 'app_adr_index.rst')
```
Old paths `docs/apps/` and `docs/decisions/app_decisions.rst` are removed.

No changes needed to `docs/references/index.rst` -- its existing `*` glob picks up
the new files automatically; `docs/index` and `docstrings/index` are already there.

### 3. Move how-to files; delete `docs/how-tos/`

Physically move:
- `docs/how-tos/celery.rst` -> `docs/concepts/celery.rst`
- `docs/how-tos/use_the_api.rst` -> `docs/concepts/use_the_api.rst`

Delete `docs/how-tos/` entirely (its `index.rst` was a bare glob toctree with no content).

### 4. `docs/concepts/index.rst`

- Rename title: `Concepts and Guides` -> `Concepts and "How-To" Guides`
- Add to toctree (flat -- no subsection wrapper):
  ```
  ../extensions/tinymce_plugins
  celery
  use_the_api
  ```

### 5. `docs/decisions/index.rst`

Remove `app_decisions` from the toctree (it is no longer generated at that path).

### 6. `docs/index.rst`

**Hidden toctree** -- remove these entries (now included via other sections):
- `how-tos/index` (now under concepts)
- `extensions/tinymce_plugins` (now under concepts)
- `apps/index` (no longer generated at this path)

**Grid cards** (independent of toctree navigation, but `:doc:` links inside them are
validated by Sphinx and must point to existing files):
- Remove **"How-tos"** card (merged into Concepts).
- Rename **"Concepts"** card -> **"Concepts and How-To Guides"**.
- Remove the `:doc:` line for `extensions/tinymce_plugins` from **"Hooks and Extensions"** (external link stays).
- **"App Documentation"** card stays, but update its `:doc:` references to new paths:
  - `:doc:`decisions/app_decisions`` -> `:doc:`references/app_adr_index``
  - `:doc:`apps/index`` -> `:doc:`references/app_index``
  - `button-ref:: apps/index` -> `button-ref:: references/app_index`

### 7. `docs/redirects.txt`

The repo uses `sphinxext.rediraffe` for internal page moves (see existing entries).
Add two entries for the moved how-tos:

```
"how-tos/celery.rst" "concepts/celery.rst"
"how-tos/use_the_api.rst" "concepts/use_the_api.rst"
```

(`redirects` dict in `conf.py` is for external URL redirects only.)

---

## Verification

```
cd docs && make html
```
- "Concepts and How-To Guides" section contains TinyMCE and the how-to pages (flat).
- References sidebar shows `docs - App ADR index`, `docs - App index`, `docs - tree`
  as sibling items alongside the existing references.
- No orphan-document Sphinx warnings.
- "App Documentation" grid card on the homepage links correctly to the new paths.
