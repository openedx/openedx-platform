"""HTTP authorization, signed previews and selection rules for the private course console."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.test import RequestFactory, SimpleTestCase, override_settings
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore.views import course_admin as views


SOURCE = 'course-v1:FBB+BNJjTSOB+2026_C1'
TARGET = 'course-v1:FBB+BNJjTSOB+2026_C2'
OTHER = 'course-v1:FBB+OTHER+2026_C1'
DATES = {'start': '2026-09-01T00:00:00Z', 'end': '2026-12-31T00:00:00Z'}


def course(run='2026_C1', org='FBB', number='BNJjTSOB', name='Biotechnology', start=None):
    """Create an inventory row without a database or a Mongo course."""
    return {
        'key': CourseLocator(org=org, course=number, run=run), 'name': name,
        'start': start, 'end': None, 'enrollment_start': None, 'enrollment_end': None,
        'faculty': 'Biology', 'visibility': 'both',
    }


@override_settings(COURSE_ADMIN_MAX_BATCH_SIZE=500)
class CourseAdminViewTests(SimpleTestCase):
    """Exercise real Django decorators, requests and signatures with service boundaries mocked."""

    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()
        self.user = SimpleNamespace(pk=10, id=10, is_authenticated=True, is_active=True, is_staff=True)
        self.source = CourseLocator.from_string(SOURCE)
        self.preview_rows = [{
            'source_course_id': SOURCE, 'course_id': TARGET, 'state': 'preview', 'message': 'Ready',
        }]
        self.inventory = self.enterContext(patch.object(views, '_courses', return_value=[course()]))
        self.validate = self.enterContext(patch.object(
            views.service, 'validate_settings', side_effect=lambda values, **kwargs: dict(values),
        ))
        self.preview = self.enterContext(patch.object(
            views.service, 'preview_course_reruns', return_value=self.preview_rows,
        ))
        self.queue = self.enterContext(patch.object(views.service, 'queue_course_reruns', return_value=[]))
        self.save = self.enterContext(patch.object(views.service, 'save_course_settings', return_value=[]))
        self.states = self.enterContext(patch.object(views.CourseRerunState, 'objects'))
        self.modes = self.enterContext(patch.object(views.CourseMode, 'objects'))
        self.modes.filter.return_value.order_by.return_value = []
        self.generation = self.enterContext(patch.object(
            views, 'get_self_generation_enabled_for_courses', return_value={},
        ))

    def request(self, method='post', payload=None, user=None, csrf=True, **extra):
        """Build a request with a real, matched CSRF cookie and masked header token."""
        if method == 'post':
            request = self.factory.post('/course-admin/action/', json.dumps(payload),
                                        content_type='application/json', **extra)
        else:
            request = getattr(self.factory, method)('/course-admin/', payload or {}, **extra)
        request.user = user or self.user
        if csrf:
            token = get_token(request)
            request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META['CSRF_COOKIE']
            request.META['HTTP_X_CSRFTOKEN'] = token
        return request

    def action(self, action='preview', **values):
        payload = {'action': action, 'course_ids': [SOURCE], 'settings': dict(DATES)}
        payload.update(values)
        return views.course_admin_action(self.request(payload=payload))

    @staticmethod
    def body(response):
        return json.loads(response.content)

    def token(self, **changes):
        data = {'user_id': self.user.pk, 'course_ids': [SOURCE], 'settings': dict(DATES),
                'targets': {SOURCE: TARGET}}
        data.update(changes)
        return signing.dumps(data, salt=views.PREVIEW_SALT)

    def test_every_endpoint_rejects_anonymous_nonstaff_and_inactive_admins(self):
        for endpoint in (views.course_admin_page, views.course_admin_action, views.course_admin_status):
            for overrides in ({'is_authenticated': False}, {'is_staff': False}, {'is_active': False}):
                with self.subTest(endpoint=endpoint.__name__, user=overrides):
                    user = SimpleNamespace(**(vars(self.user) | overrides))
                    response = endpoint(self.request(payload={}, user=user, csrf=False))
                    self.assertEqual(response.status_code, 403)
                    self.assertIn('no-store', response['Cache-Control'])
        self.inventory.assert_not_called()
        self.validate.assert_not_called()
        self.preview.assert_not_called()
        self.queue.assert_not_called()
        self.save.assert_not_called()
        self.states.find_all.assert_not_called()

    def test_read_endpoints_reject_post_and_write_endpoint_rejects_get(self):
        for endpoint in (views.course_admin_page, views.course_admin_status):
            with self.subTest(endpoint=endpoint.__name__):
                self.assertEqual(endpoint(self.request(payload={})).status_code, 405)
        self.assertEqual(views.course_admin_action(self.request(method='get')).status_code, 405)
        self.validate.assert_not_called()

    def test_action_requires_csrf_for_each_operation(self):
        for operation in ('preview', 'rerun', 'save'):
            with self.subTest(operation=operation):
                request = self.request(payload={'action': operation}, csrf=False)
                self.assertEqual(views.course_admin_action(request).status_code, 403)
        self.validate.assert_not_called()

    def test_action_rejects_wrong_csrf_and_untrusted_origin(self):
        request = self.request(payload={'action': 'save'})
        request.META['HTTP_X_CSRFTOKEN'] = 'x' * 64
        self.assertEqual(views.course_admin_action(request).status_code, 403)
        request = self.request(payload={'action': 'save'}, HTTP_ORIGIN='https://untrusted.example')
        self.assertEqual(views.course_admin_action(request).status_code, 403)
        self.validate.assert_not_called()

    def test_preview_is_read_only_and_signs_exact_selection_settings_and_targets(self):
        response = self.action()
        self.assertEqual(response.status_code, 200)
        body = self.body(response)
        self.assertEqual(body['results'], self.preview_rows)
        decoded = signing.loads(body['preview_token'], salt=views.PREVIEW_SALT)
        self.assertEqual(decoded, {'user_id': self.user.pk, 'course_ids': [SOURCE],
                                   'settings': DATES, 'targets': {SOURCE: TARGET}})
        self.preview.assert_called_once_with(self.user, [self.source], DATES)
        self.validate.assert_called_once_with(DATES, require_dates=True)
        self.queue.assert_not_called()
        self.save.assert_not_called()
        self.assertIn('no-store', response['Cache-Control'])

    def test_rerun_forwards_preview_mapping_and_repeated_token_unchanged(self):
        token = self.body(self.action())['preview_token']
        for _ in range(2):
            self.assertEqual(self.action('rerun', preview_token=token).status_code, 200)
        self.assertEqual(self.queue.call_count, 2)
        for call in self.queue.call_args_list:
            self.assertEqual(call.args, (self.user, [self.source], DATES))
            self.assertEqual(call.kwargs, {'expected_course_ids': {SOURCE: TARGET}})
        # The service owns reservation idempotency; the HTTP layer must not recompute a new target.
        self.assertEqual(self.preview.call_count, 1)

    def test_rerun_rejects_missing_corrupted_and_expired_tokens(self):
        with patch('django.core.signing.time.time', return_value=1000):
            expired = self.token()
        for token in (None, '', 'corrupt', self.token() + 'x', expired):
            with self.subTest(token=token):
                self.assertEqual(self.action('rerun', preview_token=token).status_code, 400)
        self.queue.assert_not_called()

    def test_rerun_rejects_cross_user_changed_settings_changed_ids_and_partial_preview(self):
        variants = (
            {'user_id': 20}, {'settings': dict(DATES, catalog_visibility='none')},
            {'course_ids': [OTHER]}, {'targets': {}}, {'targets': {SOURCE: TARGET, OTHER: OTHER}},
        )
        for changes in variants:
            with self.subTest(changes=changes):
                response = self.action('rerun', preview_token=self.token(**changes))
                self.assertEqual(response.status_code, 400)
        self.queue.assert_not_called()

    def test_preview_with_one_failed_course_cannot_be_submitted_as_a_complete_batch(self):
        self.preview.return_value = self.preview_rows + [{
            'source_course_id': OTHER, 'course_id': None, 'state': 'failed', 'message': 'Missing',
        }]
        ids = [SOURCE, OTHER]
        response = self.action(course_ids=ids)
        self.assertEqual(response.status_code, 200)
        response = self.action('rerun', course_ids=ids, preview_token=self.body(response)['preview_token'])
        self.assertEqual(response.status_code, 400)
        self.queue.assert_not_called()

    def test_explicit_selection_deduplicates_ids_and_preserves_order(self):
        self.assertEqual(self.action(course_ids=[SOURCE, OTHER, SOURCE]).status_code, 200)
        self.assertEqual(self.preview.call_args.args[1], [self.source, CourseLocator.from_string(OTHER)])

    def test_all_matching_selects_server_inventory_beyond_one_display_page(self):
        self.inventory.return_value = [course(number=f'course{index}') for index in range(60)]
        response = self.action(all_matching=True, filters={'latest': False}, course_ids=[OTHER])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.preview.call_args.args[1]), 60)
        self.assertNotIn(CourseLocator.from_string(OTHER), self.preview.call_args.args[1])

    def test_changed_all_matching_inventory_invalidates_preview(self):
        token = self.body(self.action(all_matching=True, filters={}))['preview_token']
        self.inventory.return_value.append(course(number='NEW'))
        response = self.action('rerun', all_matching=True, filters={}, preview_token=token)
        self.assertEqual(response.status_code, 400)
        self.queue.assert_not_called()

    @override_settings(COURSE_ADMIN_MAX_BATCH_SIZE=1)
    def test_empty_and_oversized_selections_rejected_before_service_action(self):
        for identifiers in ([], [SOURCE, OTHER]):
            with self.subTest(identifiers=identifiers):
                self.assertEqual(self.action(course_ids=identifiers).status_code, 400)
        self.inventory.return_value = [course(), course(number='OTHER')]
        self.assertEqual(self.action(all_matching=True).status_code, 400)
        self.preview.assert_not_called()

    def test_malformed_payload_settings_filters_and_identifiers_rejected(self):
        variants = (
            {'action': 'delete'}, {'settings': []}, {'course_ids': SOURCE}, {'course_ids': [1]},
            {'course_ids': ['not-a-course']}, {'course_ids': ['FBB/BNJjTSOB/2026_C1']},
            {'course_ids': ['library-v1:FBB+LIB']}, {'all_matching': 'true'},
            {'all_matching': True, 'filters': []}, {'all_matching': True, 'filters': {'year': '26'}},
        )
        for values in variants:
            with self.subTest(values=values):
                self.assertEqual(self.action(**values).status_code, 400)
        request = self.factory.post('/course-admin/action/', '{', content_type='application/json')
        request.user = self.user
        request._dont_enforce_csrf_checks = True  # Test JSON decoding independently of CSRF tests above.
        self.assertEqual(views.course_admin_action(request).status_code, 400)
        self.assertEqual(views.course_admin_action(self.request(payload=[])).status_code, 400)
        self.preview.assert_not_called()
        self.queue.assert_not_called()
        self.save.assert_not_called()

    def test_service_settings_validation_errors_return_400_without_mutation(self):
        self.validate.side_effect = ValidationError(['Invalid start date', 'Invalid visibility'])
        response = self.action('save', settings={'start': 'bad', 'catalog_visibility': 'public'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid start date', self.body(response)['error'])
        self.save.assert_not_called()

    def test_save_accepts_partial_settings_without_preview_and_rejects_empty_settings(self):
        response = self.action('save', settings={'catalog_visibility': 'none'})
        self.assertEqual(response.status_code, 200)
        self.validate.assert_called_with({'catalog_visibility': 'none'}, require_dates=False)
        self.save.assert_called_once_with(self.user, [self.source], {'catalog_visibility': 'none'})
        self.assertEqual(self.action('save', settings={}).status_code, 400)
        self.assertEqual(self.save.call_count, 1)
        self.queue.assert_not_called()

    def test_page_paginates_and_sets_csrf_cookie(self):
        self.inventory.return_value = [course(number=f'course{index}') for index in range(60)]
        self.states.find_all.return_value.order_by.return_value = []
        with patch.object(views, 'render_to_response', return_value=HttpResponse('console')) as render, \
                patch.object(views, '_display_course', side_effect=lambda row, modes, generation: {
                    'id': str(row['key']),
                }), \
                patch.object(views, 'reverse', return_value='/endpoint/'):
            response = views.course_admin_page(self.request(method='get', payload={'page': '2'}))
        self.assertEqual(response.status_code, 200)
        context = render.call_args.args[1]
        self.assertEqual((context['total'], context['num_pages'], context['page']), (60, 2, 2))
        self.assertEqual(len(context['courses']), 10)
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertTrue(context['csrf_token'])

    def test_page_rejects_invalid_filters_before_inventory_read(self):
        response = views.course_admin_page(self.request(method='get', payload={'year': 'bad'}))
        self.assertEqual(response.status_code, 400)
        self.inventory.assert_not_called()

    def test_status_returns_persisted_state_and_unknown_without_task_backend(self):
        state = SimpleNamespace(source_course_key=self.source, course_key=CourseLocator.from_string(TARGET),
                                state='failed')
        self.states.find_all.return_value = [state]
        response = views.course_admin_status(self.request(
            method='get', payload={'course_ids': ','.join((TARGET, OTHER))},
        ))
        self.assertEqual(response.status_code, 200)
        rows = self.body(response)['results']
        self.assertEqual([(row['course_id'], row['state']) for row in rows], [(TARGET, 'failed'), (OTHER, 'unknown')])
        self.queue.assert_not_called()
        self.states.find_all.assert_called_once_with(
            course_key__in=[CourseLocator.from_string(TARGET), CourseLocator.from_string(OTHER)],
        )

    def test_status_rejects_missing_invalid_and_oversized_ids(self):
        for value in ('', 'invalid', ','.join([SOURCE] * 51)):
            with self.subTest(value=value):
                response = views.course_admin_status(self.request(method='get', payload={'course_ids': value}))
                self.assertEqual(response.status_code, 400)
        self.states.find_all.assert_not_called()


class CourseAdminFilterTests(SimpleTestCase):
    """Selection preserves course families and supports historical run identifiers."""

    def test_latest_compares_numeric_run_and_keeps_organizations_separate(self):
        rows = [course('2026_C2'), course('2026_C10'), course('2025_C20'), course('2026_C1', org='kaznu')]
        result = views.filter_courses(rows, views.parse_filters({}))
        self.assertEqual({str(row['key']) for row in result}, {
            'course-v1:FBB+BNJjTSOB+2026_C10', 'course-v1:kaznu+BNJjTSOB+2026_C1',
        })

    def test_legacy_runs_remain_selectable_and_use_start_for_latest(self):
        rows = [course('C3', start=datetime(2021, 10, 1, tzinfo=timezone.utc)),
                course('2022-2023_C1', start=datetime(2022, 11, 7, tzinfo=timezone.utc)),
                course('2021C2', start=datetime(2021, 1, 25, tzinfo=timezone.utc))]
        selected = views.filter_courses(rows, views.parse_filters({}))
        self.assertEqual(selected[0]['key'].run, '2022-2023_C1')
        selected = views.filter_courses(rows, views.parse_filters({'run': 'C3'}))
        self.assertEqual(selected[0]['key'].run, 'C3')
        self.assertEqual(len(views.filter_courses(rows, views.parse_filters({'latest': False}))), 3)

    def test_filters_apply_before_latest_and_match_literals_case_insensitively(self):
        rows = [course('2025_C1'), course('2026_C1'), course('2025_C2', org='OTHER')]
        selected = views.filter_courses(rows, views.parse_filters({'year': '2025', 'org': 'fbb', 'q': 'biology'}))
        self.assertEqual([str(row['key']) for row in selected], ['course-v1:FBB+BNJjTSOB+2025_C1'])
        self.assertEqual(views.filter_courses(rows, views.parse_filters({'q': '.*'})), [])

    def test_filter_validation_bounds_and_normalization(self):
        for values in ([], {'q': ['x']}, {'q': 'x' * 256}, {'org': None}, {'year': '0000'},
                       {'year': '20266'}, {'latest': 'yes'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                views.parse_filters(values)
        normalized = views.parse_filters({'q': ' Biology ', 'latest': 'false'})
        self.assertEqual(normalized['q'], 'Biology')
        self.assertFalse(normalized['latest'])
