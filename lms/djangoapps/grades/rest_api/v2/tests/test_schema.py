"""Tests for how the Grades API v2 appears in the generated LMS schema."""

import re

from django.test import SimpleTestCase
from drf_spectacular.drainage import GENERATOR_STATS
from drf_spectacular.generators import SchemaGenerator

API_PREFIX = '/api/grade/v2/'
LEGACY_PREFIX = '/api/grades/v1/'
#: The one generator warning tolerated on v2: the platform's default authentication
#: classes have no schema extension.
TOLERATED_WARNING = re.compile(
    r"could not resolve authenticator <class 'openedx\.core\.djangolib\.default_auth_classes\."
    r"Default(Jwt|Session)Authentication'>"
)
ANSI_CODE = re.compile(r'\x1b\[[0-9;]*m')
PAGE_FIELDS = {'count', 'num_pages', 'current_page', 'start', 'next', 'previous', 'results'}
ERROR_REF = '#/components/schemas/ErrorResponse'

#: The operation id of every v2 operation.
OPERATION_IDS = {
    ('/api/grade/v2/course_grades/', 'get'): 'course_grades_list',
    ('/api/grade/v2/course_grades/{username},{course_key}/', 'get'): 'course_grades_retrieve',
    ('/api/grade/v2/courses/{course_key}/gradebook_entries/', 'get'): 'gradebook_entries_list',
    ('/api/grade/v2/courses/{course_key}/gradebook_entries/{username}/', 'get'): 'gradebook_entries_retrieve',
    ('/api/grade/v2/courses/{course_key}/subsection_grade_overrides/', 'post'): 'subsection_grade_overrides_create',
    ('/api/grade/v2/subsection_grades/{username},{usage_key}/', 'get'): 'subsection_grades_retrieve',
    ('/api/grade/v2/subsection_grades/{username},{usage_key}/override_history_records/', 'get'): (
        'subsection_grade_override_history_records_list'
    ),
    ('/api/grade/v2/courses/{course_key}/grading_policy/', 'get'): 'course_grading_policy_retrieve',
    ('/api/grade/v2/courses/{course_key}/submission_histories/', 'get'): 'submission_histories_list',
}
OPERATION_ID = re.compile(r'^[a-z]+(_[a-z]+)*$')

#: The v1 operations the schema marks deprecated, one per v1 route.
LEGACY_OPERATIONS = {
    ('/api/grades/v1/courses/', 'get'),
    ('/api/grades/v1/courses/{course_id}/', 'get'),
    ('/api/grades/v1/policy/courses/{course_id}/', 'get'),
    ('/api/grades/v1/gradebook/{course_id}/', 'get'),
    ('/api/grades/v1/gradebook/{course_id}/bulk-update', 'post'),
    ('/api/grades/v1/gradebook/{course_id}/grading-info', 'get'),
    ('/api/grades/v1/subsection/{subsection_id}/', 'get'),
    ('/api/grades/v1/section_grades_breakdown/', 'get'),
    ('/api/grades/v1/submission_history/{course_id}/', 'get'),
}


class GradeSchemaTest(SimpleTestCase):
    """The LMS schema, generated with the service's own hooks, publishes v2 and deprecates v1."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        GENERATOR_STATS._warn_cache.clear()  # pylint: disable=protected-access
        GENERATOR_STATS._error_cache.clear()  # pylint: disable=protected-access
        cls.schema = SchemaGenerator().get_schema(request=None, public=True)
        cls.paths = cls.schema['paths']
        cls.messages = [
            ANSI_CODE.sub('', message)
            for message in [*GENERATOR_STATS._warn_cache, *GENERATOR_STATS._error_cache]  # pylint: disable=protected-access
        ]

    def operation(self, path, method='get'):
        return self.paths[path][method]

    def resolve(self, schema):
        """Follow a component reference."""
        if '$ref' in schema:
            return self.schema['components']['schemas'][schema['$ref'].rsplit('/', 1)[-1]]
        return schema

    def response_schema(self, path, status_code='200'):
        content = self.operation(path)['responses'][status_code]['content']['application/json']
        return self.resolve(content['schema'])

    def v2_operations(self):
        return [
            (path, method, operation)
            for path, item in self.paths.items() if path.startswith(API_PREFIX)
            for method, operation in item.items()
        ]

    def test_v2_operations_published(self):
        assert {(path, method) for path, method, _ in self.v2_operations()} == {
            ('/api/grade/v2/course_grades/', 'get'),
            ('/api/grade/v2/course_grades/{username},{course_key}/', 'get'),
            ('/api/grade/v2/courses/{course_key}/gradebook_entries/', 'get'),
            ('/api/grade/v2/courses/{course_key}/gradebook_entries/{username}/', 'get'),
            ('/api/grade/v2/courses/{course_key}/subsection_grade_overrides/', 'post'),
            ('/api/grade/v2/subsection_grades/{username},{usage_key}/', 'get'),
            ('/api/grade/v2/subsection_grades/{username},{usage_key}/override_history_records/', 'get'),
            ('/api/grade/v2/courses/{course_key}/grading_policy/', 'get'),
            ('/api/grade/v2/courses/{course_key}/submission_histories/', 'get'),
        }

    def test_every_v2_operation_is_tagged(self):
        assert {path: operation['tags'] for path, _, operation in self.v2_operations()} == {
            '/api/grade/v2/course_grades/': ['openedx-platform-sdk'],
            '/api/grade/v2/course_grades/{username},{course_key}/': ['openedx-platform-sdk'],
            '/api/grade/v2/courses/{course_key}/gradebook_entries/': ['grade'],
            '/api/grade/v2/courses/{course_key}/gradebook_entries/{username}/': ['grade'],
            '/api/grade/v2/courses/{course_key}/subsection_grade_overrides/': ['grade'],
            '/api/grade/v2/subsection_grades/{username},{usage_key}/': ['grade'],
            '/api/grade/v2/subsection_grades/{username},{usage_key}/override_history_records/': ['grade'],
            '/api/grade/v2/courses/{course_key}/grading_policy/': ['openedx-platform-sdk'],
            '/api/grade/v2/courses/{course_key}/submission_histories/': ['grade'],
        }

    def test_sdk_operations_have_explicit_operation_ids(self):
        assert {
            operation['operationId'] for _, _, operation in self.v2_operations()
            if operation['tags'] == ['openedx-platform-sdk']
        } == {'course_grades_list', 'course_grades_retrieve', 'course_grading_policy_retrieve'}

    def test_operation_ids_are_unique(self):
        ids = [operation['operationId'] for _, _, operation in self.v2_operations()]
        assert len(ids) == len(set(ids))

    def test_every_operation_id_is_the_declared_one(self):
        assert {(path, method): operation['operationId'] for path, method, operation in self.v2_operations()} == (
            OPERATION_IDS
        )
        assert [name for name in OPERATION_IDS.values() if not OPERATION_ID.match(name)] == []

    def test_v2_generates_no_warning_but_the_default_authentication_one(self):
        v2_messages = [message for message in self.messages if '/grades/rest_api/v2/' in message]
        assert len(v2_messages) == 14, v2_messages
        assert [message for message in v2_messages if not TOLERATED_WARNING.search(message)] == []

    def test_unmatched_routes_are_not_published(self):
        assert not [path for path in self.paths if 'unmatched' in path]

    def test_every_legacy_operation_is_deprecated(self):
        legacy = {
            (path, method) for path, item in self.paths.items() if path.startswith(LEGACY_PREFIX)
            for method, operation in item.items() if operation.get('deprecated') is True
        }
        assert legacy == LEGACY_OPERATIONS

    def test_no_v2_operation_is_deprecated(self):
        assert [path for path, _, operation in self.v2_operations() if 'deprecated' in operation] == []

    def test_every_documented_error_references_the_error_component(self):
        for path, method, operation in self.v2_operations():
            for status_code, response in operation['responses'].items():
                if status_code.startswith(('4', '5')):
                    assert response['content']['application/json']['schema'] == {'$ref': ERROR_REF}, (
                        path, method, status_code,
                    )

    def test_course_grade_operations(self):
        list_operation = self.operation('/api/grade/v2/course_grades/')
        detail_operation = self.operation('/api/grade/v2/course_grades/{username},{course_key}/')
        assert (list_operation['operationId'], list_operation['tags']) == (
            'course_grades_list', ['openedx-platform-sdk'],
        )
        assert (detail_operation['operationId'], detail_operation['tags']) == (
            'course_grades_retrieve', ['openedx-platform-sdk'],
        )
        assert set(list_operation['responses']) == {'200', '400', '401', '403'}
        assert set(detail_operation['responses']) == {'200', '400', '401', '403', '404'}

    def test_course_grade_list_is_the_page_envelope(self):
        body = self.response_schema('/api/grade/v2/course_grades/')
        assert set(body['properties']) == PAGE_FIELDS
        row = self.resolve(body['properties']['results']['items'])
        assert set(row['properties']) == {
            'username', 'course_key', 'email', 'passed', 'percent', 'letter_grade', 'section_breakdown',
        }
        assert row['properties']['letter_grade']['nullable'] is True
        assert 'section_breakdown' not in row['required']

    def test_course_grade_list_parameters(self):
        parameters = {
            parameter['name']: parameter
            for parameter in self.operation('/api/grade/v2/course_grades/')['parameters']
        }
        assert set(parameters) == {'course_key', 'username', 'ordering', 'view', 'page', 'page_size'}
        assert parameters['view']['schema']['enum'] == ['full']

    def test_course_grade_detail_path_parameters(self):
        parameters = {
            parameter['name']: parameter['in']
            for parameter in self.operation('/api/grade/v2/course_grades/{username},{course_key}/')['parameters']
        }
        assert parameters == {'username': 'path', 'course_key': 'path', 'view': 'query'}

    def test_gradebook_list_is_the_page_envelope(self):
        body = self.response_schema('/api/grade/v2/courses/{course_key}/gradebook_entries/')
        assert set(body['properties']) == PAGE_FIELDS
        row = self.resolve(body['properties']['results']['items'])
        assert set(row['properties']) == {
            'username', 'full_name', 'email', 'external_user_key', 'percent', 'section_breakdown',
        }
        # The minimal view keeps only the username and percent of each row.
        assert set(row['required']) == {'username', 'percent'}
        section = self.resolve(row['properties']['section_breakdown']['items'])
        assert 'usage_key' in section['properties']
        assert 'module_id' not in section['properties']
        assert section['properties']['label']['nullable'] is True

    def test_gradebook_list_parameters(self):
        operation = self.operation('/api/grade/v2/courses/{course_key}/gradebook_entries/')
        parameters = {parameter['name']: parameter['in'] for parameter in operation['parameters']}
        assert parameters == {
            'course_key': 'path',
            'user_contains': 'query',
            'username_contains': 'query',
            'cohort_id': 'query',
            'enrollment_mode': 'query',
            'excluded_course_roles': 'query',
            'assignment_usage_key': 'query',
            'assignment_grade_min': 'query',
            'assignment_grade_max': 'query',
            'course_grade_min': 'query',
            'course_grade_max': 'query',
            'ordering': 'query',
            'view': 'query',
            'page': 'query',
            'page_size': 'query',
        }
        assert set(operation['responses']) == {'200', '400', '401', '403', '404'}

    def test_gradebook_filter_parameter_types(self):
        operation = self.operation('/api/grade/v2/courses/{course_key}/gradebook_entries/')
        parameters = {parameter['name']: parameter for parameter in operation['parameters']}
        roles = parameters['excluded_course_roles']
        assert (roles['schema'], roles['style'], roles['explode']) == (
            {'type': 'array', 'items': {'type': 'string'}}, 'form', True,
        )
        assert roles['description'].startswith('Leave out learners who hold this role in the course.')
        assert parameters['cohort_id']['schema'] == {'type': 'integer'}
        assert parameters['assignment_usage_key']['schema'] == {'type': 'string'}
        assert parameters['assignment_usage_key']['description'] == (
            'Key of the subsection that assignment_grade_min and assignment_grade_max apply to.'
        )

    def test_gradebook_detail_parameters(self):
        operation = self.operation('/api/grade/v2/courses/{course_key}/gradebook_entries/{username}/')
        parameters = {parameter['name']: parameter['in'] for parameter in operation['parameters']}
        assert parameters == {'course_key': 'path', 'username': 'path', 'view': 'query'}

    def test_override_request_body_differs_from_its_response(self):
        operation = self.operation('/api/grade/v2/courses/{course_key}/subsection_grade_overrides/', 'post')
        request = self.resolve(operation['requestBody']['content']['application/json']['schema'])
        response = self.resolve(operation['responses']['200']['content']['application/json']['schema'])
        assert operation['requestBody']['required'] is True
        assert list(operation['requestBody']['content']) == ['application/json']
        assert list(request['properties']) == ['overrides']
        item = self.resolve(request['properties']['overrides']['items'])
        assert set(item['required']) == {'username', 'usage_key'}
        assert item['properties']['comment']['maxLength'] == 300
        assert response != request
        assert set(self.resolve(response['properties']['overrides']['items'])['properties']) == {
            'username', 'usage_key', 'earned_all_override', 'possible_all_override',
            'earned_graded_override', 'possible_graded_override', 'comment',
        }
        assert set(operation['responses']) == {'200', '400', '401', '403', '404', '500'}

    def test_override_examples(self):
        operation = self.operation('/api/grade/v2/courses/{course_key}/subsection_grade_overrides/', 'post')
        request_example = operation['requestBody']['content']['application/json']['examples']['TwoOverrides']
        assert [item['username'] for item in request_example['value']['overrides']] == ['learner_one', 'learner_two']
        error_example = operation['responses']['400']['content']['application/json']['examples']['InvalidItems']
        assert list(error_example['value']['errors']) == ['overrides[0].username', 'overrides[1].usage_key']
        assert error_example['value']['type'] == 'https://docs.openedx.org/errors/validation'

    def test_subsection_grade_operation(self):
        body = self.response_schema('/api/grade/v2/subsection_grades/{username},{usage_key}/')
        assert set(body['properties']) == {'username', 'usage_key', 'course_key', 'original_grade', 'override'}
        assert body['properties']['override']['nullable'] is True
        parameters = {
            parameter['name']: parameter['in']
            for parameter in self.operation('/api/grade/v2/subsection_grades/{username},{usage_key}/')['parameters']
        }
        assert parameters == {'username': 'path', 'usage_key': 'path'}

    def test_override_history_is_the_page_envelope(self):
        path = '/api/grade/v2/subsection_grades/{username},{usage_key}/override_history_records/'
        body = self.response_schema(path)
        assert set(body['properties']) == PAGE_FIELDS
        record = self.resolve(body['properties']['results']['items'])
        assert 'history_user_id' not in record['properties']
        assert record['properties']['history_user']['nullable'] is True
        parameters = {parameter['name'] for parameter in self.operation(path)['parameters']}
        assert parameters == {'username', 'usage_key', 'ordering', 'page', 'page_size'}

    def test_grading_policy_operation(self):
        path = '/api/grade/v2/courses/{course_key}/grading_policy/'
        body = self.response_schema(path)
        assert set(body['properties']) == {
            'grade_cutoffs', 'assignment_types', 'subsections', 'grades_frozen', 'bulk_management_enabled',
        }
        assert set(body['required']) == {'grade_cutoffs', 'assignment_types'}
        subsection = self.resolve(body['properties']['subsections']['items'])
        assert subsection['properties']['display_name']['nullable'] is True
        parameters = {parameter['name']: parameter for parameter in self.operation(path)['parameters']}
        assert set(parameters) == {'course_key', 'view', 'graded_only'}
        assert parameters['view']['schema']['enum'] == ['minimal']

    def test_submission_histories_are_the_page_envelope(self):
        path = '/api/grade/v2/courses/{course_key}/submission_histories/'
        body = self.response_schema(path)
        assert set(body['properties']) == PAGE_FIELDS
        parameters = {parameter['name'] for parameter in self.operation(path)['parameters']}
        assert parameters == {'course_key', 'username', 'ordering', 'view', 'page', 'page_size'}
        assert '429' in self.operation(path)['responses']

    def test_serializer_components_are_namespaced(self):
        assert {
            'grade_v2.CourseGrade',
            'grade_v2.CourseGradeSection',
            'grade_v2.GradebookEntry',
            'grade_v2.GradebookSection',
            'grade_v2.SubsectionGradeOverrideRequest',
            'grade_v2.SubsectionGradeOverrideList',
            'grade_v2.SubsectionGrade',
            'grade_v2.SubsectionGradeOverrideHistoryRecord',
            'grade_v2.CourseGradingPolicy',
            'grade_v2.SubmissionHistory',
        } <= set(self.schema['components']['schemas'])
