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
virtualenv venv
source venv/bin/activate
pip install -r ./scripts/structures_pruning/requirements/base.txt
```

Then you can use python to run the scripts, and view the integrated
help.

```bash
python scripts/structures_pruning/structures.py --help
```

# Development

For development, you can also install the testing requirements:

```bash
pip install -r scripts/structures_pruning/requirements/testing.txt
```

Run the test cases using pytest:

```bash
pytest scripts/structures_pruning
```
