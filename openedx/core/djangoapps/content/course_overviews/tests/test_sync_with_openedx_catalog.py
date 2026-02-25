"""
Test that changes to courses get synced into the new openedx_catalog models.
"""

from openedx_catalog import api as catalog_api

from cms.djangoapps.contentstore.views.course import rerun_course
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED,
    ModuleStoreTestCase,
    ImmediateOnCommitMixin,
)
from xmodule.modulestore.tests.factories import CourseFactory


class CourseOverviewSyncTestCase(ImmediateOnCommitMixin, ModuleStoreTestCase):
    """
    Test that changes to courses get synced into the new openedx_catalog models.
    """

    MODULESTORE = TEST_DATA_ONLY_SPLIT_MODULESTORE_DRAFT_PREFERRED
    ENABLED_SIGNALS = ["course_published"]

    def test_courserun_creation(self) -> None:
        """
        Tests that when a course is created, the `CourseRun` record gets created.

        (Also the corresponding `CatalogCourse`.)
        """
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key

        run = catalog_api.get_course_run(course_id)
        assert run.display_name == "Intro to Testing"
        assert run.course_id == course_id
        assert run.catalog_course.course_code == course_id.course
        assert run.catalog_course.org_code == course_id.org

    def test_courserun_sync(self) -> None:
        """
        Tests that when a course is updated, the catalog records get updated.

        Because the "language" of a course cannot be set in Studio before you
        create the course, when a Catalog Course has only a single run, we need
        to keep the language of the catalog course in sync with any changes to
        the language field of the course run. (Because authors necessarily
        create a new course with the default language then edit it to have the
        correct language that they actually intended to use for that [catalog]
        course.) This is in contrast with display_name, which can actually be
        set before creating a course.
        """
        # Create a course
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key
        run = catalog_api.get_course_run(course_id)
        assert run.display_name == "Intro to Testing"
        assert run.catalog_course.language_short == "en"

        # Update the course's display_name and language:
        course.language = "es"
        course.display_name = "Introducción a las pruebas"
        self.store.update_item(course, ModuleStoreEnum.UserID.test)

        # Check if the catalog data is updated:
        run.refresh_from_db()
        assert run.display_name == "Introducción a las pruebas"
        assert run.catalog_course.language_short == "es"
        # Note: for now we don't update the display_name of the catalog course after it has been created.
        # We _could_ decide to sync the name from run -> catalog course if there is only one run.
        assert run.catalog_course.display_name == "Intro to Testing"

    def test_courserun_sync(self) -> None:
        """
        Tests that when a course is updated, the catalog records get updated.

        Because the "language" of a course cannot be set in Studio before you
        create the course, when a Catalog Course has only a single run, we need
        to keep the language of the catalog course in sync with any changes to
        the language field of the course run. (Because authors necessarily
        create a new course with the default language then edit it to have the
        correct language that they actually intended to use for that [catalog]
        course.) This is in contrast with display_name, which can actually be
        set before creating a course.
        """
        # Create a course
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key
        run = catalog_api.get_course_run(course_id)
        assert run.display_name == "Intro to Testing"
        assert run.catalog_course.language_short == "en"

        # Update the course's display_name and language:
        course.language = "es"
        course.display_name = "Introducción a las pruebas"
        self.store.update_item(course, ModuleStoreEnum.UserID.test)

        # Check if the catalog data is updated:
        run.refresh_from_db()
        assert run.display_name == "Introducción a las pruebas"
        assert run.catalog_course.language_short == "es"
        # Note: for now we don't update the display_name of the catalog course after it has been created.
        # We _could_ decide to sync the name from run -> catalog course if there is only one run.
        assert run.catalog_course.display_name == "Intro to Testing"

    def test_courserun_of_many_sync(self) -> None:
        """
        Tests that when a course is updated, the catalog records get updated,
        but if there are several runs of the same course, the changes don't
        propagate to the `CatalogCourse` and only affect the `CourseRun.
        """
        # Create a course
        course = CourseFactory.create(display_name="Intro to Testing", emit_signals=True)
        course_id = course.location.context_key
        run = catalog_api.get_course_run(course_id)
        assert run.display_name == "Intro to Testing"
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

        # Update the re-run's display_name and language:
        new_course = self.store.get_course(new_run_course_id)
        new_course.language = "es"
        new_course.display_name = "Introducción a las pruebas"
        self.store.update_item(new_course, self.user.id)

        # Check if the catalog data is updated correctly.
        # The original CourseRun object should be unchanged:
        run.refresh_from_db()
        assert run.display_name == "Intro to Testing"
        assert run.catalog_course.language_short == "en"
        # The new CourseRun object should be created:
        new_run = catalog_api.get_course_run(new_run_course_id)
        assert new_run.display_name == "Introducción a las pruebas"
        # Changing the language of the second run doesn't affect the lanugage of the overall catalog course (since the
        # first run is still in English)
        assert new_run.catalog_course.language_short == "en"
