"""Unit tests for the private course administration service."""

from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore import course_admin


SOURCE = CourseLocator(org='FBB', course='BNJjTSOB', run='2026_C1')


class CourseAdminSettingsTests(SimpleTestCase):
    """The bulk API accepts only explicit, internally consistent settings."""

    def test_dates_are_normalized_to_utc(self):
        result = course_admin.validate_settings({
            'start': '2027-01-01T09:00:00+05:00',
            'end': '2027-05-31T09:00:00+05:00',
            'self_paced': True,
            'catalog_visibility': 'about',
            'hide_source': True,
        }, require_dates=True)

        self.assertEqual(result['start'], datetime(2027, 1, 1, 4, tzinfo=timezone.utc))
        self.assertEqual(result['end'], datetime(2027, 5, 31, 4, tzinfo=timezone.utc))
        self.assertTrue(result['self_paced'])
        self.assertTrue(result['hide_source'])

    def test_rejects_unknown_settings_invalid_choices_and_non_boolean_values(self):
        invalid_payloads = (
            {'unexpected': 'value'},
            {'catalog_visibility': 'public'},
            {'mode': 'verified'},
            {'certificate_mode': 'yes'},
            {'self_paced': 'true'},
            {'hide_source': 'true'},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                course_admin.validate_settings(payload)

    def test_rejects_inconsistent_course_and_enrollment_periods(self):
        invalid_payloads = (
            {'start': '2027-02-01T00:00:00Z', 'end': '2027-01-01T00:00:00Z'},
            {'enrollment_start': '2027-03-01T00:00:00Z',
             'enrollment_end': '2027-02-01T00:00:00Z'},
            {'end': '2027-03-01T00:00:00Z', 'enrollment_end': '2027-04-01T00:00:00Z'},
        )
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                course_admin.validate_settings(payload)

    def test_service_rejects_non_admin_and_inactive_staff(self):
        for user in (
            SimpleNamespace(is_authenticated=False, is_active=True, is_staff=True),
            SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False),
            SimpleNamespace(is_authenticated=True, is_active=False, is_staff=True),
        ):
            with self.subTest(user=user), self.assertRaises(PermissionDenied):
                course_admin.preview_course_reruns(user, [], {
                    'start': '2027-01-01T00:00:00Z', 'end': '2027-02-01T00:00:00Z',
                })


class CourseAdminRunTests(SimpleTestCase):
    """Automatic run numbers work with numeric C suffixes and legacy sources."""

    def test_rerun_is_sent_to_cms_worker_without_importing_the_cms_task(self):
        settings = {
            'start': datetime(2027, 1, 1, tzinfo=timezone.utc),
            'end': datetime(2027, 5, 31, tzinfo=timezone.utc),
        }
        fields = {'display_name': 'Biology'}

        with patch.object(course_admin, 'current_app') as celery_app:
            course_admin._send_rerun_task(SOURCE, SOURCE, 7, fields, settings)

        celery_app.send_task.assert_called_once_with(
            course_admin.RERUN_COURSE_TASK_NAME,
            args=(str(SOURCE), str(SOURCE), 7, '{"display_name": "Biology"}'),
            kwargs={
                'admin_settings': {
                    'start': '2027-01-01T00:00:00+00:00',
                    'end': '2027-05-31T00:00:00+00:00',
                },
            },
            queue=course_admin.CMS_CELERY_QUEUE,
            exchange=course_admin.CMS_CELERY_EXCHANGE,
            routing_key=course_admin.CMS_CELERY_QUEUE,
        )

    def test_next_run_uses_highest_numeric_suffix_for_year_and_family(self):
        known = {
            CourseLocator(org='FBB', course='BNJjTSOB', run='2027_C2'),
            CourseLocator(org='fbb', course='bnjjtsob', run='2027_C10'),
            CourseLocator(org='FBB', course='BNJjTSOB', run='2026_C99'),
            CourseLocator(org='FBB', course='OTHER', run='2027_C50'),
            CourseLocator(org='FBB', course='BNJjTSOB', run='2021-2022_C1'),
        }

        result = course_admin.next_course_key(SOURCE, 2027, known)

        self.assertEqual(str(result), 'course-v1:FBB+BNJjTSOB+2027_C11')

    def test_certificate_generation_requires_an_active_studio_certificate(self):
        source = SimpleNamespace(id=SOURCE, certificates={'certificates': [{'is_active': False}]})
        current = {
            'self_generation_enabled': True,
            'language_specific_templates_enabled': False,
            'include_hours_of_effort': None,
        }
        with patch.object(course_admin, 'get_course_certificate_generation_settings', return_value=current):
            with self.assertRaises(ValidationError):
                course_admin._generation_values(source, {'certificate_mode': 'copy'})

            source.certificates['certificates'][0]['is_active'] = True
            result = course_admin._generation_values(source, {'certificate_mode': 'copy'})

        self.assertTrue(result['self_generation_enabled'])

    def test_shift_content_dates_updates_published_and_draft_branches_independently(self):
        source_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        published = self._block(datetime(2026, 2, 1, tzinfo=timezone.utc))
        draft = self._block(datetime(2026, 2, 3, tzinfo=timezone.utc))
        store = Mock()
        store.bulk_operations.return_value = nullcontext()
        store.get_items.side_effect = ([published], [draft])
        mixed_store = Mock()
        mixed_store._get_modulestore_for_courselike.return_value = store
        settings = {
            'start': '2027-01-01T00:00:00Z',
            'end': '2027-05-31T00:00:00Z',
            'shift_content_dates': True,
        }

        with patch.object(course_admin, '_source_course', return_value=SimpleNamespace(start=source_start)), \
                patch.object(course_admin, 'modulestore', return_value=mixed_store), \
                patch('xmodule.modulestore.split_mongo.split.SplitMongoModuleStore.update_item') as update:
            course_admin.shift_rerun_content_dates(SOURCE, SOURCE, 7, settings)

        self.assertEqual(published.start, datetime(2027, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(draft.start, datetime(2027, 2, 3, tzinfo=timezone.utc))
        self.assertEqual(update.call_count, 2)
        store._flag_publish_event.assert_called_once_with(SOURCE)

    @staticmethod
    def _block(start):
        date_field = Mock()
        date_field.is_set_on.return_value = True
        location = Mock()
        location.version_agnostic.return_value = location
        return SimpleNamespace(
            category='chapter', fields={'start': date_field}, start=start, location=location,
        )
