"""
Test that changes to courses get synced into the new openedx_catalog models.
"""

import pytest

from openedx_catalog import api as catalog_api
from openedx_catalog.models_api import CatalogCourse, CourseRun

from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED,
    ModuleStoreTestCase,
    ImmediateOnCommitMixin,
)
from xmodule.modulestore.tests.factories import CourseFactory


@skip_unless_cms
class CourseOverviewSyncTestCase(ImmediateOnCommitMixin, ModuleStoreTestCase):
    """
    Test that changes to courses get synced into the new openedx_catalog models.
    """

    MODULESTORE = TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED
    ENABLED_SIGNALS = ["course_deleted", "course_published"]

    def test_courserun_creation(self) -> None:
        """
        Tests that when a course is created, the `CourseRun` record gets created.

        (Also the corresponding `CatalogCourse`.)
        """
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_key = course.location.context_key

        run = catalog_api.get_course_run(course_key)
        assert run.title == "Intro to Testing"
        assert run.course_key == course_key
        assert run.catalog_course.course_code == course_key.course
        assert run.catalog_course.org_code == course_key.org

    def test_courserun_sync(self) -> None:
        """
        Tests that when a course is updated, the catalog records get updated.

        Because the "language" of a course cannot be set in Studio before you
        create the course, when a Catalog Course has only a single run, we need
        to keep the language of the catalog course in sync with any changes to
        the language field of the course run. (Because authors necessarily
        create a new course with the default language then edit it to have the
        correct language that they actually intended to use for that [catalog]
        course.) This is in contrast with display_name (title), which can
        actually be set before creating a course.
        """
        # Create a course
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key
        run = catalog_api.get_course_run(course_id)
        assert run.title == "Intro to Testing"
        assert run.catalog_course.language_short == "en"

        # Update the course's title and language:
        course.language = "es"
        course.display_name = "Introducción a las pruebas"
        self.store.update_item(course, ModuleStoreEnum.UserID.test)

        # Check if the catalog data is updated:
        run.refresh_from_db()
        assert run.title == "Introducción a las pruebas"
        assert run.catalog_course.language_short == "es"
        # Note: for now we don't update the title of the catalog course after it has been created.
        # We _could_ decide to sync the name from run -> catalog course if there is only one run.
        assert run.catalog_course.title == "Intro to Testing"

    def test_courserun_of_many_sync(self) -> None:
        """
        Tests that when a course is updated, the catalog records get updated,
        but if there are several runs of the same course, the changes don't
        propagate to the `CatalogCourse` and only affect the `CourseRun.
        """
        # This import causes problems at top level when tests run on the LMS shard
        from cms.djangoapps.contentstore.views.course import rerun_course

        # Create a course
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key
        run = catalog_api.get_course_run(course_id)
        assert run.title == "Intro to Testing"
        assert run.catalog_course.language_short == "en"

        # re-run the course:
        new_run_course_id = rerun_course(
            self.user,
            source_course_key=course_id,
            org=course_id.org,
            number=course_id.course,
            run="newRUN",
            fields={"display_name": "Intro to Testing TEMPORARY NAME"},
            background=False,
        )

        # Update the re-run's title (display_name) and language:
        new_course = self.store.get_course(new_run_course_id)
        new_course.language = "es"
        new_course.display_name = "Introducción a las pruebas"
        self.store.update_item(new_course, self.user.id)

        # Check if the catalog data is updated correctly.
        # The original CourseRun object should be unchanged:
        run.refresh_from_db()
        assert run.title == "Intro to Testing"
        assert run.catalog_course.language_short == "en"
        # The new CourseRun object should be created:
        new_run = catalog_api.get_course_run(new_run_course_id)
        assert new_run.title == "Introducción a las pruebas"
        # Changing the language of the second run doesn't affect the lanugage of the overall catalog course (since the
        # first run is still in English)
        assert new_run.catalog_course.language_short == "en"

    def test_courserun_deletion(self) -> None:
        """
        Tests that when a course run is deleted, the corresponding CourseRun is
        deleted, and when it's the last run, the CatalogCourse is deleted too.
        """
        # This import causes problems at top level when tests run on the LMS shard
        from cms.djangoapps.contentstore.views.course import rerun_course

        # Create a course with two runs:
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id1 = course.location.context_key
        run1 = catalog_api.get_course_run(course_id1)
        # re-run the course:
        course_id2 = rerun_course(
            self.user,
            source_course_key=course_id1,
            org=course_id1.org,
            number=course_id1.course,
            run="run2",
            fields={"display_name": "ItT run2"},
            background=False,
        )
        run2 = catalog_api.get_course_run(course_id2)
        catalog_course = run1.catalog_course
        assert catalog_course == run2.catalog_course  # Same for run1 and run2

        self.store.delete_course(course_id1, ModuleStoreEnum.UserID.test)
        with pytest.raises(CourseRun.DoesNotExist):
            run1.refresh_from_db()

        # run2 should still exist:
        run2.refresh_from_db()
        assert run2.catalog_course.title == "Intro to Testing"  # The catalog course still exists and works

        # delete run 2:
        self.store.delete_course(course_id2, ModuleStoreEnum.UserID.test)
        with pytest.raises(CourseRun.DoesNotExist):
            run2.refresh_from_db()
        # With no runs left, the CatalogCourse also gets auto-deleted:
        with pytest.raises(CatalogCourse.DoesNotExist):
            catalog_course.refresh_from_db()
