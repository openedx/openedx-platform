# Structures Pruning Scripts

This directory contains the MongoDB structures pruning script that was
migrated from the [tubular](https://github.com/openedx/tubular)
repository.

This can be useful to keep the MongoDB `modulestore.structures`
collection size in check. This script could be called from any
automation/CD framework.

# Quick start

> [!NOTE]
> See [How to Use the Structures Pruning Script](https://docs.openedx.org/en/latest/site_ops/how-tos/use-the-structures-pruning-script.html) for a complete guide to running this in production.

Some quick steps for setting this up to run. A shallow clone of
openedx-platform is recommended because the repository is very large.

```bash
git clone --depth=1 https://github.com/openedx/openedx-platform.git
cd openedx-platform
```

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync --project scripts/structures_pruning --frozen
```

Or, without uv:

```bash
virtualenv venv
source venv/bin/activate
pip install -r ./scripts/structures_pruning/requirements/base.txt
```

Then you can use python to run the scripts, and view the integrated
help.

```bash
uv run --project scripts/structures_pruning python scripts/structures_pruning/structures.py --help
```

(or, without uv and with the virtualenv above activated: `python scripts/structures_pruning/structures.py --help`)

# Development

With uv:

```bash
uv run --project scripts/structures_pruning --group test pytest scripts/structures_pruning
```

Or, without uv, install the testing requirements and run pytest directly:

```bash
pip install -r scripts/structures_pruning/requirements/testing.txt
pytest scripts/structures_pruning
```
