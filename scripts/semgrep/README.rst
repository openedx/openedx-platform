scripts/semgrep: an isolated environment for running semgrep
##############################################################

This directory holds a standalone ``uv``-managed environment
(``pyproject.toml`` + ``uv.lock``) for the `semgrep <https://semgrep.dev/>`_
CLI, used by the ``.github/workflows/semgrep.yml`` CI job to scan ``lms``,
``cms``, ``common``, and ``openedx`` against the rules in
``test_root/semgrep/``.

It's isolated from the main application's dependency graph because semgrep's
own dependencies conflict with other root-project dependencies when resolved
together in one shared graph.

Regenerate ``uv.lock`` by running ``make compile-requirements`` or
``make upgrade`` from the repo root -- see the ``scripts/semgrep`` entry in
the root ``Makefile``'s ``compile-requirements`` target.
