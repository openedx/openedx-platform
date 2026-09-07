Requirements/dependencies
#########################

The main application's Python dependencies are declared in the root
``pyproject.toml`` (``[project.dependencies]`` for runtime deps,
``[dependency-groups]`` for testing/development/doc/assets tooling,
and ``[tool.edx_lint].uv_constraints`` for repo-specific version pins) and
locked in the root ``uv.lock``, managed with `uv`_.

This ``requirements/`` directory now holds ``edx-sandbox``, its own
standalone ``uv``-managed project (``pyproject.toml`` + ``uv.lock``) for
Codejail's isolated sandbox environment, plus ``requirements/edx/*.txt`` --
generated compatibility exports of the main app's dependencies (regenerated
by ``make compile-requirements``, not hand-edited) for external tools like
Tutor/Devstack that still do ``pip install -r requirements/edx/base.txt``
directly instead of using ``uv sync``.

The four standalone script directories at the repo root (``scripts/xblock``,
``scripts/user_retirement``, ``scripts/structures_pruning``, ``scripts/semgrep``)
each have their own ``pyproject.toml`` + ``uv.lock`` too, independent of both
the main app and each other.

All of these are manipulated using the Makefile targets below in a Linux
environment (to match our build and deploy systems); for developers on Mac,
this can be achieved by using the GitHub workflows or by running Make targets
from inside devstack's lms-shell or another Linux environment.

.. _uv: https://docs.astral.sh/uv/

If you don't have write permissions to openedx/edx-platform, you'll need to run these workflows on a fork.

Keeping extra personal packages installed
=========================================

The old ``requirements/edx/private.in``/``private.txt`` mechanism (an
uncommitted, git-ignored pair of files letting you keep extra personal
packages installed alongside the official requirements, surviving a
``pip-sync``) has no direct successor file, but the same need is covered by
``uv sync``'s ``--inexact`` flag: ``uv pip install <package>`` your extra
tool once, then pass ``--inexact`` on subsequent ``uv sync`` calls (e.g.
``uv sync --inexact``) to keep it from being removed as an "extraneous"
package.

Workflows and Makefile targets
******************************

Add a dependency
================

To add a Python dependency, add it to ``[project.dependencies]`` (or the
appropriate ``[dependency-groups]`` entry) in ``pyproject.toml``, push that up
to a branch, and then use the `compile-python-requirements.yml workflow <https://github.com/openedx/edx-platform/actions/workflows/compile-python-requirements.yml>`_ to run ``make compile-requirements`` against your branch. This will ensure ``uv.lock`` is updated with any transitive dependencies and will ping you on a PR for updating your branch.

Upgrade just one dependency
===========================

Want to upgrade just *one* dependency without pulling in other upgrades? You can `run the upgrade-one-python-dependency.yml workflow <https://github.com/openedx/edx-platform/actions/workflows/upgrade-one-python-dependency.yml>`_ to have a pull request made against a branch of your choice.

Or, if you need to do it locally, you can use the ``upgrade-package`` make target directly. For example, you could run ``make upgrade-package package=ecommerce``.

If your dependency is pinned in ``[tool.edx_lint].uv_constraints`` (in ``pyproject.toml``), you'll need to enter an explicit version number in the appropriate field when running the workflow; this will include an update to that constraint in the resulting PR.

Downgrade a dependency
======================

If you instead need to surgically *downgrade* a dependency:

1. Add an exact-match or max-version constraint to ``[tool.edx_lint].uv_constraints`` in ``pyproject.toml`` with a comment explaining why (and ideally a ticket or issue link). Here's what it might look like::

     # frobulator 2.x has breaking API changes; see https://github.com/openedx/edx-platform/issue/1234567 for fixing it
     "frobulator<2.0.0",

2. After pushing that up to a branch, use the `compile-python-requirements.yml workflow <https://github.com/openedx/edx-platform/actions/workflows/compile-python-requirements.yml>`_ to run ``make compile-requirements`` against your branch.

Upgrade all dependencies
========================

 You can use the `upgrade-requirements Github Workflow <https://github.com/openedx/edx-platform/actions/workflows/upgrade-python-requirements.yml>`_ to make a PR that upgrades as many packages as possible to newer versions. This is a wrapper around ``make upgrade`` and is run on a schedule to keep dependencies up to date.

Inconsistent dependencies
*************************

You might be directed to this section if a PR check for consistent dependencies has failed.

Did you run ``make upgrade`` or ``make compile-requirements`` on a Mac directly?
================================================================================

Some packages have different dependencies on Mac vs. Linux. Usually this is not relevant in production (they generally have to do with desktop integrations of developer tools) but this does cause "churn" and make it harder to review PRs when dependencies are alternatingly recompiled on Mac and Linux. As edx-platform runs on Linux, we want to ensure that dependencies are compiled for that platform.

Solutions for Mac users:

- Use the workflow described in `Upgrading just one dependency`_.
- You can run ``make lms-shell`` in devstack to get a Linux environment for more complicated operations.

Did you hand-edit the .txt files?
=================================

Hand-editing the .txt requirements files often leads to dependency conflicts, failed deployments, or outages. It's easy to forget to update all the locations where a requirement appears, and it's often not feasible to track down all of the transitive dependencies of the package you want to upgrade.

Luckily, we have simple runbooks for upgrading or downgrading a single package, which are the most common cases:

- `Upgrading just one dependency`_
- `Downgrading a dependency`_

Is there an unpinned git dependency?
====================================

If the diff relates to a dependency that is installed from git rather than from PyPI, check ``[project.dependencies]`` in ``pyproject.toml`` for a direct reference (``name @ git+https://...@TAG-OR-SHA``) that has failed to pin a specific commit. We want to have as few of these dependencies as possible, as they're a maintenance and performance problem.

Help, I didn't change any dependencies, and this is still failing!
==================================================================

It's possible that someone introduced an inconsistency on the master branch, in which case please submit a new PR off of master after running ``make compile-requirements`` (but see notes above for Mac users). Or perhaps your branch was made while there was such an inconsistency, in which case please rebase onto master or merge down from master to your branch.
