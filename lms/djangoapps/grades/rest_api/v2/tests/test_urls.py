"""Tests for the Grades API URL names and the v1 addresses v2 supersedes."""

import ddt
from django.test import SimpleTestCase, TestCase
from django.test.utils import override_settings
from django.urls import get_resolver, resolve, reverse
from edx_rest_framework_extensions.testing import assert_error_envelope

from lms.djangoapps.grades.rest_api.v1 import gradebook_views, views
from lms.djangoapps.grades.rest_api.v2.views.unmatched import UnmatchedRouteView

COURSE_ID = 'course-v1:edX+DemoX+Demo_Course'
SUBSECTION_ID = 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@a_subsection'
LEGACY_PREFIX = '/api/grades/v1/'


class SupersededAddressTest(SimpleTestCase):
    """Every superseded address still reaches the handler it always reached."""

    def assert_resolves_to(self, path, view_class, url_name):
        """Fail unless ``path`` is served by ``view_class`` under ``url_name``."""
        match = resolve(path)
        assert match.func.view_class is view_class
        assert match.url_name == url_name

    def test_course_grade_collection(self):
        self.assert_resolves_to(f'{LEGACY_PREFIX}courses/', views.CourseGradesView, 'course_grades')

    def test_course_grade_collection_of_one_course(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}courses/{COURSE_ID}/', views.CourseGradesView, 'course_grades',
        )

    def test_both_course_grade_addresses_share_one_name(self):
        assert reverse('grades_api:v1:course_grades') == f'{LEGACY_PREFIX}courses/'
        assert reverse('grades_api:v1:course_grades', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}courses/{COURSE_ID}/'

    def test_grading_policy(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}policy/courses/{COURSE_ID}/',
            views.CourseGradingPolicy,
            'course_grading_policy',
        )

    def test_gradebook(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/',
            gradebook_views.GradebookView,
            'course_gradebook',
        )

    def test_gradebook_bulk_update(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/bulk-update',
            gradebook_views.GradebookBulkUpdateView,
            'course_gradebook_bulk_update',
        )

    def test_gradebook_grading_info(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/grading-info',
            gradebook_views.CourseGradingView,
            'course_gradebook_grading_info',
        )

    def test_subsection_grade_overrides(self):
        path = f'{LEGACY_PREFIX}subsection/{SUBSECTION_ID}/'
        self.assert_resolves_to(path, gradebook_views.SubsectionGradeView, 'course_grade_overrides')
        assert resolve(path).kwargs['subsection_id'] == SUBSECTION_ID

    def test_subsection_address_accepts_any_identifier(self):
        """The subsection identifier is captured unconstrained, slashes included."""
        path = f'{LEGACY_PREFIX}subsection/anything at all/with a slash/'
        assert resolve(path).kwargs['subsection_id'] == 'anything at all/with a slash'

    def test_section_grades_breakdown(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}section_grades_breakdown/',
            views.SectionGradesBreakdown,
            'section_grades_breakdown',
        )

    def test_submission_history(self):
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}submission_history/{COURSE_ID}/',
            views.SubmissionHistoryView,
            'submission_history',
        )

    def test_submission_history_matches_anywhere_in_the_address(self):
        """The submission-history address is unanchored, so text may precede and follow it."""
        self.assert_resolves_to(
            f'{LEGACY_PREFIX}anything-submission_history/{COURSE_ID}/and-anything-else',
            views.SubmissionHistoryView,
            'submission_history',
        )

    def test_every_superseded_name_still_reverses(self):
        assert reverse('grades_api:v1:section_grades_breakdown') == f'{LEGACY_PREFIX}section_grades_breakdown/'
        assert reverse('grades_api:v1:course_gradebook', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/'
        assert reverse('grades_api:v1:course_gradebook_bulk_update', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/bulk-update'
        assert reverse('grades_api:v1:course_gradebook_grading_info', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}gradebook/{COURSE_ID}/grading-info'
        assert reverse('grades_api:v1:course_grading_policy', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}policy/courses/{COURSE_ID}/'
        assert reverse('grades_api:v1:course_grade_overrides', kwargs={'subsection_id': SUBSECTION_ID}) == \
            f'{LEGACY_PREFIX}subsection/{SUBSECTION_ID}/'
        assert reverse('grades_api:v1:submission_history', kwargs={'course_id': COURSE_ID}) == \
            f'{LEGACY_PREFIX}submission_history/{COURSE_ID}/'


class GradeV2UrlNameTest(SimpleTestCase):
    """Every v2 route has a name of its own."""

    def test_names_are_unique(self):
        _, v2_resolver = get_resolver().namespace_dict['grade_v2']
        names = [pattern.name for pattern in v2_resolver.url_patterns]
        assert len(names) == len(set(names))
        assert all(name and name == name.lower() and '-' not in name for name in names)

    def test_every_operation_has_its_name(self):
        _, v2_resolver = get_resolver().namespace_dict['grade_v2']
        names = {pattern.name for pattern in v2_resolver.url_patterns if not pattern.name.endswith('_unmatched')}
        assert names == {
            'course_grade_list',
            'course_grade_detail',
            'gradebook_entry_list',
            'gradebook_entry_detail',
            'subsection_grade_override_list',
            'subsection_grade_detail',
            'subsection_grade_override_history_list',
            'course_grading_policy',
            'submission_history_list',
        }


class GradeV2ResolutionTest(SimpleTestCase):
    """Every v2 address resolves to its view with parsed keys, and each unmatched one to the not-found view."""

    USAGE_KEY = 'block-v1:edX+DemoX+Demo_Course+type@sequential+block@a_subsection'
    PREFIX = '/api/grade/v2/'

    def test_addresses_resolve_to_their_views(self):
        expected = {
            f'courses/{COURSE_ID}/subsection_grade_overrides/': ('SubsectionGradeOverrideViewSet', 'create'),
            f'subsection_grades/u,{self.USAGE_KEY}/': ('SubsectionGradeViewSet', 'retrieve'),
            f'subsection_grades/u,{self.USAGE_KEY}/override_history_records/': (
                'SubsectionGradeOverrideHistoryViewSet', 'list',
            ),
            f'courses/{COURSE_ID}/grading_policy/': ('CourseGradingPolicyView', None),
            f'courses/{COURSE_ID}/submission_histories/': ('SubmissionHistoryViewSet', 'list'),
        }
        for path, (view_name, action) in expected.items():
            match = resolve(self.PREFIX + path)
            assert _view_class(match).__name__ == view_name, path
            assert (getattr(match.func, 'actions', None) or {}).get('post' if action == 'create' else 'get') == action
        match = resolve(f'{self.PREFIX}subsection_grades/u,{self.USAGE_KEY}/')
        assert (str(match.kwargs['usage_key']), match.kwargs['username']) == (self.USAGE_KEY, 'u')

    def test_no_route_is_shadowed_by_a_not_found_route(self):
        kwargs = {
            'course_grade_list': {},
            'course_grade_detail': {'username': 'u', 'course_key': COURSE_ID},
            'gradebook_entry_list': {'course_key': COURSE_ID},
            'gradebook_entry_detail': {'course_key': COURSE_ID, 'username': 'u'},
            'subsection_grade_override_list': {'course_key': COURSE_ID},
            'subsection_grade_detail': {'username': 'u', 'usage_key': self.USAGE_KEY},
            'subsection_grade_override_history_list': {'username': 'u', 'usage_key': self.USAGE_KEY},
            'course_grading_policy': {'course_key': COURSE_ID},
            'submission_history_list': {'course_key': COURSE_ID},
        }
        resolved = {name: resolve(reverse(f'grade_v2:{name}', kwargs=values)) for name, values in kwargs.items()}
        assert {name: match.url_name for name, match in resolved.items()} == {name: name for name in kwargs}
        assert [name for name, match in resolved.items() if _view_class(match) is UnmatchedRouteView] == []

    def test_rejected_keys_reach_the_not_found_view(self):
        for path in (
            'courses/edX/DemoX/Demo/subsection_grade_overrides/',
            'courses/not-a-key/grading_policy/',
            'courses/not-a-key/submission_histories/',
            'subsection_grades/u,not-a-key/',
            'subsection_grades/u,not-a-key/override_history_records/',
        ):
            assert _view_class(resolve(self.PREFIX + path)).__name__ == 'UnmatchedRouteView', path


def _view_class(match):
    """Return the view class a resolved address is served by, for ViewSets and plain views alike."""
    return getattr(match.func, 'cls', None) or match.func.view_class


@ddt.ddt
@override_settings(DEBUG=False)
class UnmatchedAddressTest(TestCase):
    """Addresses under the v2 mount that name no resource get the JSON not-found error, whatever the method."""

    ADDRESSES = (
        '/api/grade/v2/',
        '/api/grade/v2/subsection_grades/',
        '/api/grade/v2/courses/',
        f'/api/grade/v2/courses/{COURSE_ID}/',
        f'/api/grade/v2/courses/{COURSE_ID}/no_such_collection/',
        f'/api/grade/v2/courses/{COURSE_ID}/gradebook_entries/u/extra/',
        '/api/grade/v2/courses/not-a-key/gradebook_entries/',
        '/api/grade/v2/courses/edX/DemoX/Demo/grading_policy/',
    )

    @ddt.data('get', 'post')
    def test_json_not_found(self, method):
        for path in self.ADDRESSES:
            response = getattr(self.client, method)(path)
            assert response['Content-Type'] == 'application/json', (method, path)
            body = assert_error_envelope(response, expected_status=404, expected_type_slug='not-found')
            assert body['instance'] == path
