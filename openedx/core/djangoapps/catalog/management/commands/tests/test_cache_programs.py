"""
Test cases for cache_programs command.
"""

import json

import httpretty
import pytest
from django.core.cache import cache
from django.core.management import call_command

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangoapps.catalog.cache import (
    COURSE_PROGRAMS_CACHE_KEY_TPL,
    PATHWAY_CACHE_KEY_TPL,
    PROGRAM_CACHE_KEY_TPL,
    PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_CACHE_KEY_TPL,
    PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL,
    SITE_PATHWAY_IDS_CACHE_KEY_TPL,
    SITE_PROGRAM_UUIDS_CACHE_KEY_TPL,
)
from openedx.core.djangoapps.catalog.tests.factories import OrganizationFactory, PathwayFactory, ProgramFactory
from openedx.core.djangoapps.catalog.tests.mixins import CatalogIntegrationMixin
from openedx.core.djangoapps.catalog.utils import normalize_program_type
from openedx.core.djangoapps.site_configuration.tests.mixins import SiteMixin
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase, skip_unless_lms


@skip_unless_lms
@httpretty.activate
class TestCachePrograms(CatalogIntegrationMixin, CacheIsolationTestCase, SiteMixin):
    """
    Defines tests for the ``cache_programs`` management command.
    """
    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()

        httpretty.httpretty.reset()

        self.catalog_integration = self.create_catalog_integration()
        self.site_domain = 'testsite.com'
        self.site = self.set_up_site(
            self.site_domain,
            {
                'COURSE_CATALOG_API_URL': self.catalog_integration.get_internal_api_url().rstrip('/')
            }
        )

        self.site_domain2 = 'testsite2.com'
        self.site2 = self.set_up_site(
            self.site_domain2,
            {
                'COURSE_CATALOG_API_URL': self.catalog_integration.get_internal_api_url().rstrip('/')
            }
        )

        self.list_url = self.catalog_integration.get_internal_api_url().rstrip('/') + '/programs/'
        self.detail_tpl = self.list_url.rstrip('/') + '/{uuid}/'
        self.pathway_url = self.catalog_integration.get_internal_api_url().rstrip('/') + '/pathways/'

        self.programs = ProgramFactory.create_batch(3)
        self.programs2 = ProgramFactory.create_batch(3)
        self.pathways = PathwayFactory.create_batch(3)
        self.pathways2 = PathwayFactory.create_batch(3)
        self.child_program = ProgramFactory.create()
        self.child_program2 = ProgramFactory.create()

        self.programs[0]['curricula'][0]['programs'].append(self.child_program)
        self.programs.append(self.child_program)
        self.programs[0]['authoring_organizations'] = OrganizationFactory.create_batch(2)

        self.programs2[0]['curricula'][0]['programs'].append(self.child_program2)
        self.programs2.append(self.child_program2)
        self.programs2[0]['authoring_organizations'] = OrganizationFactory.create_batch(2)

        for pathway in self.pathways:
            self.programs += pathway['programs']

        for pathway in self.pathways2:
            self.programs2 += pathway['programs']

        self.uuids = {
            f"{self.site_domain}": [program["uuid"] for program in self.programs],
            f"{self.site_domain2}": [program["uuid"] for program in self.programs2],
        }

        # add some of the previously created programs to some pathways
        self.pathways[0]['programs'].extend([self.programs[0], self.programs[1]])
        self.pathways[1]['programs'].append(self.programs[0])

        # add some of the previously created programs to some pathways
        self.pathways2[0]['programs'].extend([self.programs2[0], self.programs2[1]])
        self.pathways2[1]['programs'].append(self.programs2[0])

    def mock_list(self, site=""):
        """ Mock the data returned by the program listing API endpoint. """
        # pylint: disable=unused-argument
        def list_callback(request, uri, headers):
            """ The mock listing callback. """
            expected = {
                'exclude_utm': ['1'],
                'status': ['active', 'retired'],
                'uuids_only': ['1']
            }
            assert request.querystring == expected
            uuids = self.uuids[self.site_domain2] if site else self.uuids[self.site_domain]
            return (200, headers, json.dumps(uuids))

        httpretty.register_uri(
            httpretty.GET,
            self.list_url,
            body=list_callback,
            content_type='application/json'
        )

    def mock_detail(self, uuid, program):
        """ Mock the data returned by the program detail API endpoint. """
        # pylint: disable=unused-argument
        def detail_callback(request, uri, headers):
            """ The mock detail callback. """
            expected = {
                'exclude_utm': ['1'],
            }
            assert request.querystring == expected

            return (200, headers, json.dumps(program))

        httpretty.register_uri(
            httpretty.GET,
            self.detail_tpl.format(uuid=uuid),
            body=detail_callback,
            content_type='application/json'
        )

    def mock_pathways(self, pathways, page_number=1, final=True):
        """ Mock the data for discovery's credit pathways endpoint. """
        def pathways_callback(request, uri, headers):  # pylint: disable=unused-argument
            """ Mocks the pathways response. """

            expected = {
                'exclude_utm': ['1'],
                'page': [str(page_number)],
                'status': ['published']
            }
            assert request.querystring == expected

            body = {
                'count': len(pathways),
                'next': None if final else 'more',  # we don't actually parse this value
                'prev': None,
                'results': pathways
            }

            return (200, headers, json.dumps(body))

        httpretty.register_uri(
            httpretty.GET,
            self.pathway_url + f'?status=published&exclude_utm=1&page={page_number}',
            body=pathways_callback,
            content_type='application/json',
            match_querystring=True,
        )

    def test_handle_domain(self):
        """
        Verify that the command argument is working correct or not.
        """
        UserFactory(username=self.catalog_integration.service_username)

        programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs2
        }

        self.mock_list(self.site2)
        self.mock_pathways(self.pathways2)

        for uuid in self.uuids[self.site_domain2]:
            program = programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        call_command('cache_programs', f'--domain={self.site_domain2}')

        cached_uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site_domain2))
        assert set(cached_uuids) == set(self.uuids[self.site_domain2])

    def test_handle_programs(self):
        """
        Verify that the command requests and caches program UUIDs and details.
        """
        # Ideally, this user would be created in the test setup and deleted in
        # the one test case which covers the case where the user is missing. However,
        # that deletion causes "OperationalError: no such table: wiki_attachmentrevision"
        # when run on Jenkins.
        UserFactory(username=self.catalog_integration.service_username)

        programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs
        }

        self.mock_list()
        self.mock_pathways(self.pathways)

        for uuid in self.uuids[self.site_domain]:
            program = programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        call_command('cache_programs')

        cached_uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert set(cached_uuids) == set(self.uuids[self.site_domain])

        program_keys = list(programs.keys())
        cached_programs = cache.get_many(program_keys)
        # Verify that the keys were all cache hits.
        assert set(cached_programs) == set(programs)

        # We can't use a set comparison here because these values are dictionaries
        # and aren't hashable. We've already verified that all programs came out
        # of the cache above, so all we need to do here is verify the accuracy of
        # the data itself.
        for key, program in cached_programs.items():
            # cached programs have a pathways field added to them, remove before comparing
            del program['pathway_ids']
            assert program == programs[key]

        # the courses in the child program's first curriculum (the active one)
        # should point to both the child program and the first program
        # in the cache.
        for course in self.child_program['curricula'][0]['courses']:
            for course_run in course['course_runs']:
                course_run_cache_key = COURSE_PROGRAMS_CACHE_KEY_TPL.format(course_run_id=course_run['key'])
                assert self.programs[0]['uuid'] in cache.get(course_run_cache_key)
                assert self.child_program['uuid'] in cache.get(course_run_cache_key)

        # for each program, assert that the program's UUID is in a cached list of
        # program UUIDS by program type and a cached list of UUIDs by authoring organization
        for program in self.programs:
            program_type = normalize_program_type(program.get('type', 'None'))
            program_type_slug = program.get('type_attrs', {}).get('slug')
            program_type_cache_key = PROGRAMS_BY_TYPE_CACHE_KEY_TPL.format(
                site_id=self.site.id, program_type=program_type
            )
            program_type_slug_cache_key = PROGRAMS_BY_TYPE_SLUG_CACHE_KEY_TPL.format(
                site_id=self.site.id, program_slug=program_type_slug
            )
            assert program['uuid'] in cache.get(program_type_cache_key)
            assert program['uuid'] in cache.get(program_type_slug_cache_key)

            for organization in program['authoring_organizations']:
                organization_cache_key = PROGRAMS_BY_ORGANIZATION_CACHE_KEY_TPL.format(
                    org_key=organization['key']
                )
                assert program['uuid'] in cache.get(organization_cache_key)

    def test_handle_pathways(self):
        """
        Verify that the command requests and caches credit pathways
        """

        UserFactory(username=self.catalog_integration.service_username)

        programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs
        }

        pathways = {
            PATHWAY_CACHE_KEY_TPL.format(id=pathway['id']): pathway for pathway in self.pathways
        }

        self.mock_list()
        self.mock_pathways(self.pathways)

        for uuid in self.uuids[self.site_domain]:
            program = programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        call_command('cache_programs')

        cached_pathway_keys = cache.get(SITE_PATHWAY_IDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        pathway_keys = list(pathways.keys())
        assert set(cached_pathway_keys) == set(pathway_keys)

        cached_pathways = cache.get_many(pathway_keys)
        assert set(cached_pathways) == set(pathways)

        # We can't use a set comparison here because these values are dictionaries
        # and aren't hashable. We've already verified that all pathways came out
        # of the cache above, so all we need to do here is verify the accuracy of
        # the data itself.
        for key, pathway in cached_pathways.items():
            # cached pathways store just program uuids instead of the full programs, transform before comparing
            pathways[key]['program_uuids'] = [program['uuid'] for program in pathways[key]['programs']]
            del pathways[key]['programs']

            assert pathway == pathways[key]

    def test_pathways_multiple_pages(self):
        """
        Verify that the command properly caches credit pathways when multiple pages are returned from its endpoint
        """
        UserFactory(username=self.catalog_integration.service_username)
        new_pathways = PathwayFactory.create_batch(40)
        for new_pathway in new_pathways:
            new_pathway['programs'] = []
        pathways = self.pathways + new_pathways

        programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs
        }

        self.mock_list()
        for uuid in self.uuids[self.site_domain]:
            program = programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        # mock 3 pages of credit pathways, starting at the last
        self.mock_pathways(pathways[40:], page_number=3, final=True)
        self.mock_pathways(pathways[20:40], page_number=2, final=False)
        self.mock_pathways(pathways[:20], page_number=1, final=False)

        call_command('cache_programs')

        pathways_dict = {
            PATHWAY_CACHE_KEY_TPL.format(id=pathway['id']): pathway for pathway in pathways
        }
        pathway_keys = list(pathways_dict.keys())

        cached_pathway_keys = cache.get(SITE_PATHWAY_IDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert set(cached_pathway_keys) == set(pathway_keys)

        cached_pathways = cache.get_many(pathway_keys)
        assert set(cached_pathways) == set(pathways_dict)

        # We can't use a set comparison here because these values are dictionaries
        # and aren't hashable. We've already verified that all pathways came out
        # of the cache above, so all we need to do here is verify the accuracy of
        # the data itself.
        for key, pathway in cached_pathways.items():
            # cached pathways store just program uuids instead of the full programs, transform before comparing
            pathways_dict[key]['program_uuids'] = [program['uuid'] for program in pathways_dict[key]['programs']]
            del pathways_dict[key]['programs']

            assert pathway == pathways_dict[key]

    def test_handle_missing_service_user(self):
        """
        Verify that the command raises an exception when run without a service
        user, and that program UUIDs are not cached.
        """
        with pytest.raises(Exception):  # noqa: B017, PT011
            call_command('cache_programs')

        cached_uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert cached_uuids is None

    def test_handle_missing_uuids(self):
        """
        Verify that the command raises an exception when it fails to retrieve
        program UUIDs.
        """
        UserFactory(username=self.catalog_integration.service_username)

        with pytest.raises(SystemExit) as context:
            call_command('cache_programs')
        assert context.value.code == 1

        cached_uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert cached_uuids == []

    def test_handle_missing_pathways(self):
        """
        Verify that the command raises an exception when it fails to retrieve pathways.
        """
        UserFactory(username=self.catalog_integration.service_username)

        programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs
        }

        self.mock_list()

        for uuid in self.uuids[self.site_domain]:
            program = programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        with pytest.raises(SystemExit) as context:
            call_command('cache_programs')
        assert context.value.code == 1

        cached_pathways = cache.get(SITE_PATHWAY_IDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert cached_pathways == []

    def test_handle_missing_programs(self):
        """
        Verify that a problem retrieving a program doesn't prevent the command
        from retrieving and caching other programs, but does cause it to exit
        with a non-zero exit code.
        """
        UserFactory(username=self.catalog_integration.service_username)

        all_programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs
        }
        partial_programs = {
            PROGRAM_CACHE_KEY_TPL.format(uuid=program['uuid']): program for program in self.programs[:2]
        }

        self.mock_list()

        for uuid in self.uuids[self.site_domain][:2]:
            program = partial_programs[PROGRAM_CACHE_KEY_TPL.format(uuid=uuid)]
            self.mock_detail(uuid, program)

        with pytest.raises(SystemExit) as context:
            call_command('cache_programs')

        assert context.value.code == 1

        cached_uuids = cache.get(SITE_PROGRAM_UUIDS_CACHE_KEY_TPL.format(domain=self.site_domain))
        assert set(cached_uuids) == set(self.uuids[self.site_domain])

        program_keys = list(all_programs.keys())
        cached_programs = cache.get_many(program_keys)
        # One of the cache keys should result in a cache miss.
        assert set(cached_programs) == set(partial_programs)

        for key, program in cached_programs.items():
            # cached programs have a pathways field added to them, remove before comparing
            del program['pathway_ids']
            assert program == partial_programs[key]
