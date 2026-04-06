"""Tests for util.db module."""

from io import StringIO

import ddt
from django.core.management import call_command
from django.db.transaction import TransactionManagementError, atomic
from django.test import TestCase, TransactionTestCase
from django.test.utils import override_settings

from common.djangoapps.util.db import enable_named_outer_atomic, generate_int_id, outer_atomic


def do_nothing():
    """Just return."""
    return


class TransactionManagersTestCase(TransactionTestCase):
    """
    Tests outer_atomic.
    """

    def test_outer_atomic_nesting(self):
        """
        Test that outer_atomic raises an error if it is nested inside
        another atomic.
        """
        outer_atomic()(do_nothing)()  # pylint: disable=not-callable

        with atomic():
            atomic()(do_nothing)()  # pylint: disable=not-callable

        with outer_atomic():
            atomic()(do_nothing)()  # pylint: disable=not-callable

        with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
            with atomic():
                outer_atomic()(do_nothing)()  # pylint: disable=not-callable

        with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
            with outer_atomic():
                outer_atomic()(do_nothing)()  # pylint: disable=not-callable

    def test_named_outer_atomic_nesting(self):
        """
        Test that a named outer_atomic raises an error only if nested in
        enable_named_outer_atomic and inside another atomic.
        """
        outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable

        with atomic():
            outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable

        with enable_named_outer_atomic('abc'):

            outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable  # Not nested.

            with atomic():
                outer_atomic(name='pqr')(do_nothing)()  # pylint: disable=not-callable  # Not enabled.

            with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
                with atomic():
                    outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable

        with enable_named_outer_atomic('abc', 'def'):

            outer_atomic(name='def')(do_nothing)()  # pylint: disable=not-callable  # Not nested.

            with atomic():
                outer_atomic(name='pqr')(do_nothing)()  # pylint: disable=not-callable  # Not enabled.

            with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
                with atomic():
                    outer_atomic(name='def')(do_nothing)()  # pylint: disable=not-callable

            with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
                with outer_atomic():
                    outer_atomic(name='def')(do_nothing)()  # pylint: disable=not-callable

            with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
                with atomic():
                    outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable

            with self.assertRaisesRegex(TransactionManagementError, 'Cannot be inside an atomic block.'):  # noqa: PT027
                with outer_atomic():
                    outer_atomic(name='abc')(do_nothing)()  # pylint: disable=not-callable


@ddt.ddt
class GenerateIntIdTestCase(TestCase):
    """Tests for `generate_int_id`"""
    @ddt.data(10)
    def test_no_used_ids(self, times):
        """
        Verify that we get a random integer within the specified range
        when there are no used ids.
        """
        minimum = 1
        maximum = times
        for __ in range(times):
            assert generate_int_id(minimum, maximum) in list(range(minimum, (maximum + 1)))

    @ddt.data(10)
    def test_used_ids(self, times):
        """
        Verify that we get a random integer within the specified range
        but not in a list of used ids.
        """
        minimum = 1
        maximum = times
        used_ids = {2, 4, 6, 8}
        for __ in range(times):
            int_id = generate_int_id(minimum, maximum, used_ids)
            assert int_id in list(set(range(minimum, (maximum + 1))) - used_ids)


class MigrationTests(TestCase):
    """
    Tests for migrations.
    """

    @override_settings(MIGRATION_MODULES={})
    def test_migrations_are_in_sync(self):
        """
        Tests that the migration files are in sync with the models.
        If this fails, you needs to run the Django command makemigrations.

        The test is set up to override MIGRATION_MODULES to ensure migrations are
        enabled for purposes of this test regardless of the overall test settings.

        TODO: Find a general way of handling the case where if we're trying to
        make a migrationless release that'll require a separate migration
        release afterwards, this test doesn't fail.
        """
        out = StringIO()
        call_command("makemigrations", dry_run=True, verbosity=3, stdout=out)
        output = out.getvalue()
        assert 'No changes detected' in output
