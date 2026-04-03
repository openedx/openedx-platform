"""
Tests for `backfill_course_outlines` Studio (cms) management command.
"""
from django.core.management import call_command
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.learning_sequences.api import get_course_keys_with_outlines
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,  # lint-amnesty, pylint: disable=wrong-import-order
)
from xmodule.modulestore.tests.factories import (  # lint-amnesty, pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from ....outlines import update_outline_from_modulestore


class BackfillCourseOutlinesTest(SharedModuleStoreTestCase):
    """
    Test `backfill_orgs_and_org_courses`.
    """
    def setUp(self):
        """
        Create the CourseOverviews we need for this test case.

        There's no publish signal, so we manually create the CourseOverviews.
        Without that, backfill_orgs_and_org_courses has no way to figure out
        which courses exist, which it needs in order to figure out which ones
        need backfilling.

        We can't turn on the course_published signal because if we did so, then
        the outlines would get generated automatically, and there'd be nothing
        to backfill.
        """
        super().setUp()
        CourseOverview.update_select_courses(self.course_keys, force_update=True)

    @classmethod
    def setUpClass(cls):
        """
        We set up some content here, without publish signals enabled.
        """
        super().setUpClass()
        course_run_ids = [
            "course-v1:OpenEdX+OutlineCourse+Run1",
            "course-v1:OpenEdX+OutlineCourse+Run2",
        ]
        cls.course_keys = [
            CourseKey.from_string(course_run_id) for course_run_id in course_run_ids
        ]
        for course_key in cls.course_keys:
            course = CourseFactory.create(
                org=course_key.org,
                number=course_key.course,
                run=course_key.run,
                display_name=f"Outline Backfill Test Course {course_key.run}"
            )
            with cls.store.bulk_operations(course_key):
                section = BlockFactory.create(
                    parent=course,
                    category="chapter",
                    display_name="A Section"
                )
                sequence = BlockFactory.create(
                    parent=section,
                    category="sequential",
                    display_name="A Sequence"
                )
                unit = BlockFactory.create(
                    parent=sequence,
                    category="vertical",
                    display_name="A Unit"
                )
                BlockFactory.create(
                    parent=unit,
                    category="html",
                    display_name="An HTML Block"
                )

    def test_end_to_end(self):
        """Normal invocation, it should skip only the Old Mongo course."""
        # In the beginning, we have no outlines...
        assert not get_course_keys_with_outlines().exists()

        # Run command and outlines appear for Split Mongo courses...
        call_command("backfill_course_outlines")
        course_keys_with_outlines = set(get_course_keys_with_outlines())
        assert course_keys_with_outlines == {
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run1"),
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run2"),
        }

    def test_partial(self):
        """Also works when we've manually created one in advance."""
        course_keys_with_outlines = set(get_course_keys_with_outlines())
        assert not get_course_keys_with_outlines().exists()

        # Manually create one
        update_outline_from_modulestore(
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run1")
        )
        assert set(get_course_keys_with_outlines()) == {
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run1")
        }

        # backfill command should fill in the other
        call_command("backfill_course_outlines")
        course_keys_with_outlines = set(get_course_keys_with_outlines())
        assert course_keys_with_outlines == {
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run1"),
            CourseKey.from_string("course-v1:OpenEdX+OutlineCourse+Run2"),
        }
