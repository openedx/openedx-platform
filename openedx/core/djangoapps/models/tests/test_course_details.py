"""
Tests for CourseDetails
"""


import datetime
from django.test import override_settings
import pytest
import ddt
from zoneinfo import ZoneInfo

from django.conf import settings
from xmodule.modulestore import ModuleStoreEnum
from xmodule.data import CertificatesDisplayBehaviors
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory

from openedx.core.djangoapps.models.course_details import ABOUT_ATTRIBUTES, CourseDetails

EXAMPLE_CERTIFICATE_AVAILABLE_DATE = datetime.date(2020, 1, 1)


@ddt.ddt
class CourseDetailsTestCase(ModuleStoreTestCase):
    """
    Tests the first course settings page (course dates, overview, etc.).
    """
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.course = CourseFactory.create(default_enrollment_start=True)

    @ddt.data(True, False)
    def test_virgin_fetch(self, should_have_default_enroll_start):
        features = settings.FEATURES.copy()
        features['CREATE_COURSE_WITH_DEFAULT_ENROLLMENT_START_DATE'] = should_have_default_enroll_start

        with override_settings(FEATURES=features):
            course = CourseFactory.create(default_enrollment_start=should_have_default_enroll_start)
            details = CourseDetails.fetch(course.id)
            wrong_enrollment_start_msg = (
                'enrollment_start not copied into'
                if should_have_default_enroll_start
                else f'enrollment_start date somehow initialized {str(details.enrollment_start)}'
            )
            assert details.org == course.location.org, 'Org not copied into'
            assert details.course_id == course.location.course, 'Course_id not copied into'
            assert details.run == course.location.run, 'Course run not copied into'
            assert details.course_image_name == course.course_image
            assert details.start_date.tzinfo is not None
            assert details.end_date is None, ('end date somehow initialized ' + str(details.end_date))
            assert details.enrollment_start == course.enrollment_start, wrong_enrollment_start_msg
            assert details.enrollment_end is None,\
                ('enrollment_end date somehow initialized ' + str(details.enrollment_end))
            assert details.certificate_available_date is None,\
                ('certificate_available_date date somehow initialized ' + str(details.certificate_available_date))
            assert details.syllabus is None, ('syllabus somehow initialized' + str(details.syllabus))
            assert details.intro_video is None, ('intro_video somehow initialized' + str(details.intro_video))
            assert details.effort is None, ('effort somehow initialized' + str(details.effort))
            assert details.language is None, ('language somehow initialized' + str(details.language))
            assert not details.self_paced

    def test_update_and_fetch(self):
        jsondetails = CourseDetails.fetch(self.course.id)
        jsondetails.syllabus = "<a href='foo'>bar</a>"
        # encode - decode to convert date fields and other data which changes form
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).syllabus ==\
                   jsondetails.syllabus, 'After set syllabus'
            jsondetails.short_description = "Short Description"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).short_description ==\
                   jsondetails.short_description, 'After set short_description'
            jsondetails.overview = "Overview"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).overview ==\
                   jsondetails.overview, 'After set overview'
            jsondetails.intro_video = "intro_video"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).intro_video ==\
                   jsondetails.intro_video, 'After set intro_video'
            jsondetails.about_sidebar_html = "About Sidebar HTML"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user)\
                .about_sidebar_html == jsondetails.about_sidebar_html, 'After set about_sidebar_html'
            jsondetails.effort = "effort"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).effort ==\
                   jsondetails.effort, 'After set effort'
            jsondetails.self_paced = True
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).self_paced ==\
                   jsondetails.self_paced
            jsondetails.start_date = datetime.datetime(2010, 10, 1, 0, tzinfo=ZoneInfo("UTC"))
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).start_date ==\
                   jsondetails.start_date
            jsondetails.end_date = datetime.datetime(2011, 10, 1, 0, tzinfo=ZoneInfo("UTC"))
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).end_date ==\
                   jsondetails.end_date
            jsondetails.certificate_available_date = datetime.datetime(2010, 10, 1, 0, tzinfo=ZoneInfo("UTC"))
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user)\
                .certificate_available_date == jsondetails.certificate_available_date
            jsondetails.course_image_name = "an_image.jpg"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).course_image_name ==\
                   jsondetails.course_image_name
            jsondetails.banner_image_name = "an_image.jpg"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).banner_image_name ==\
                   jsondetails.banner_image_name
            jsondetails.video_thumbnail_image_name = "an_image.jpg"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user)\
                .video_thumbnail_image_name == jsondetails.video_thumbnail_image_name
            jsondetails.language = "hr"
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).language ==\
                   jsondetails.language
            jsondetails.learning_info = ["test", "test"]
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).learning_info ==\
                   jsondetails.learning_info

            jsondetails.instructor_info = {
                "instructors": [
                    {
                        "name": "test",
                        "title": "test",
                        "organization": "test",
                        "image": "test",
                        "bio": "test"
                    }
                ]
            }
            assert CourseDetails.update_from_json(self.course.id, jsondetails.__dict__, self.user).instructor_info ==\
                   jsondetails.instructor_info

    def test_toggle_pacing_during_course_run(self):
        self.course.start = datetime.datetime.now(ZoneInfo("UTC"))
        self.store.update_item(self.course, self.user.id)

        details = CourseDetails.fetch(self.course.id)
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            updated_details = CourseDetails.update_from_json(
                self.course.id,
                dict(details.__dict__, self_paced=True),
                self.user
            )
        assert not updated_details.self_paced

    @ddt.data(*ABOUT_ATTRIBUTES)
    def test_fetch_about_attribute(self, attribute_name):
        attribute_value = 'test_value'
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            CourseDetails.update_about_item(self.course, attribute_name, attribute_value, self.user.id)
        assert CourseDetails.fetch_about_attribute(self.course.id, attribute_name) == attribute_value

    def test_fetch_about_attribute_error(self):
        attribute_name = 'not_an_about_attribute'
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            CourseDetails.update_about_item(self.course, attribute_name, 'test_value', self.user.id)
        with pytest.raises(ValueError):
            CourseDetails.fetch_about_attribute(self.course.id, attribute_name)

    def test_fetch_video(self):
        video_value = 'test_video_id'
        with self.store.branch_setting(ModuleStoreEnum.Branch.draft_preferred, self.course.id):
            CourseDetails.update_about_video(self.course, video_value, self.user.id)
        assert CourseDetails.fetch_youtube_video_id(self.course.id) == video_value
        video_url = CourseDetails.fetch_video_url(self.course.id)
        self.assertRegex(video_url, fr'http://.*{video_value}')

    @ddt.data(
        (
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.END,
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.END_WITH_DATE
        ),
        (
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.END_WITH_DATE,
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.END_WITH_DATE
        ),
        (
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.EARLY_NO_INFO,
            None,
            CertificatesDisplayBehaviors.EARLY_NO_INFO
        ),
        (
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            "invalid_option",
            EXAMPLE_CERTIFICATE_AVAILABLE_DATE,
            CertificatesDisplayBehaviors.END_WITH_DATE
        ),
        (
            None,
            CertificatesDisplayBehaviors.END,
            None,
            CertificatesDisplayBehaviors.END
        ),
        (
            None,
            CertificatesDisplayBehaviors.END_WITH_DATE,
            None,
            CertificatesDisplayBehaviors.END
        ),
        (
            None,
            CertificatesDisplayBehaviors.EARLY_NO_INFO,
            None,
            CertificatesDisplayBehaviors.EARLY_NO_INFO
        ),
        (
            None,
            "invalid_option",
            None,
            CertificatesDisplayBehaviors.END
        ),
    )
    @ddt.unpack
    def test_validate_certificate_settings(self, stored_date, stored_behavior, expected_date, expected_behavior):
        assert CourseDetails.validate_certificate_settings(
            stored_date, stored_behavior
        ) == (expected_date, expected_behavior)
