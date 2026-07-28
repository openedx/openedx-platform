""" Management command to update courses' search index """


import logging
from datetime import date, datetime
from textwrap import dedent
from time import time

from django.conf import settings
from django.core.management import BaseCommand, CommandError
from elasticsearch import exceptions
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator
from search.search_engine_base import SearchEngine

from cms.djangoapps.contentstore.courseware_index import CourseAboutSearchIndexer, CoursewareSearchIndexer
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order

from .prompt import query_yes_no


class Command(BaseCommand):
    """
    Command to re-index courses

    Examples:

        ./manage.py reindex_course <course_id_1> <course_id_2> ... - reindexes courses with provided keys
        ./manage.py reindex_course --all --warning - reindexes all available courses with quieter logging
        ./manage.py reindex_course --setup - reindexes all courses for devstack setup
    """
    help = dedent(__doc__)
    CONFIRMATION_PROMPT = "Re-indexing all courses might be a time consuming operation. Do you want to continue?"

    def add_arguments(self, parser):
        parser.add_argument('course_ids',
                            nargs='*',
                            metavar='course_id')
        parser.add_argument('--all',
                            action='store_true',
                            help='Reindex all courses')
        parser.add_argument('--active',
                            action='store_true',
                            help='Reindex active courses only')
        parser.add_argument('--from_inclusion_date',
                            action='store_true',
                            help='Reindex courses with a start date greater than COURSEWARE_SEARCH_INCLUSION_DATE'
                            )
        parser.add_argument('--setup',
                            action='store_true',
                            help='Reindex all courses on developers stack setup')
        parser.add_argument('--warning',
                            action='store_true',
                            help='Reduce logging to a WARNING level of output for progress tracking'
                            )

    def _parse_course_key(self, raw_value):
        """ Parses course key from string """
        try:
            result = CourseKey.from_string(raw_value)
        except InvalidKeyError:
            raise CommandError("Invalid course_key: '%s'." % raw_value)  # pylint: disable=raise-missing-from  # noqa: B904, UP031

        if not isinstance(result, CourseLocator):
            raise CommandError(f"Argument {raw_value} is not a course key")

        return result

    def handle(self, *args, **options):  # pylint: disable=too-many-statements
        """
        By convention set by Django developers, this method actually executes command's actions.
        So, there could be no better docstring than emphasize this once again.
        """
        course_ids = options['course_ids']
        all_option = options['all']
        active_option = options['active']
        inclusion_date_option = options['from_inclusion_date']
        setup_option = options['setup']
        readable_option = options['warning']
        index_all_courses_option = all_option or setup_option

        course_option_flag_option = index_all_courses_option or active_option or inclusion_date_option

        if (not course_ids and not course_option_flag_option) or (course_ids and course_option_flag_option):
            raise CommandError((  # noqa: UP034
                "reindex_course requires one or more <course_id>s"
                " OR the --all, --active, --setup, or --from_inclusion_date flags."
            ))

        store = modulestore()

        if readable_option:
            logging.disable(level=logging.INFO)
            logging.warning('Reducing logging to WARNING level for easier progress tracking')

        if index_all_courses_option:
            if setup_option:
                index_names = (CoursewareSearchIndexer.INDEX_NAME, CourseAboutSearchIndexer.INDEX_NAME)
                for index_name in index_names:
                    try:
                        searcher = SearchEngine.get_search_engine(index_name)
                    except exceptions.ElasticsearchException as exc:
                        logging.exception('Search Engine error - %s', exc)
                        return

                    # Legacy Elasticsearch engine
                    if hasattr(searcher, '_es'):  # pylint: disable=protected-access
                        index_exists = searcher._es.indices.exists(index=index_name)  # pylint: disable=protected-access

                        index_mapping = searcher._es.indices.get_mapping(  # pylint: disable=protected-access
                            index=index_name,
                        ) if index_exists else {}

                        if index_exists and index_mapping:
                            return

            # if reindexing is done during devstack setup step, don't prompt the user
            if setup_option or query_yes_no(self.CONFIRMATION_PROMPT, default="no"):
                # in case of --setup or --all, get the list of course keys from all courses
                # that are stored in the modulestore
                course_keys = [course.id for course in modulestore().get_courses()]
            else:
                return
        elif active_option:
            # in case of --active, we get the list of course keys from all courses
            # that are stored in the modulestore and filter out the non-active
            all_courses = modulestore().get_courses()

            today = date.today()
            # We keep the courses that has a start date and either don't have an end date
            # or the end date is not in the past.
            active_courses = filter(lambda course: course.start
                                    and (not course.end or course.end.date() >= today),
                                    all_courses)
            course_keys = list(map(lambda course: course.id, active_courses))

            logging.warning(f'Selected {len(course_keys)} active courses over a total of {len(all_courses)}.')
        elif inclusion_date_option:
            # in case of --from_inclusion_date, we get the list of course keys from all courses
            # that are stored in modulestore and filter out courses with a start date less than
            # the settings defined COURSEWARE_SEARCH_INCLUSION_DATE
            all_courses = modulestore().get_courses()

            inclusion_date = datetime.strptime(
                settings.FEATURES.get('COURSEWARE_SEARCH_INCLUSION_DATE', '2020-01-01'),
                '%Y-%m-%d'
            )

            # We keep the courses that has a start date and the start date is greater than the inclusion date
            active_courses = filter(lambda course: course.start and (course.start >= inclusion_date), all_courses)
            course_keys = list(map(lambda course: course.id, active_courses))

        else:
            # in case course keys are provided as arguments
            course_keys = list(map(self._parse_course_key, course_ids))

        total = len(course_keys)
        logging.warning(f'Reindexing {total} courses...')
        start = time()

        count = 0
        success = 0
        errors = []

        for course_key in course_keys:
            try:
                count += 1
                CoursewareSearchIndexer.do_course_reindex(store, course_key)
                success += 1
                if count % 10 == 0 or count == total:
                    t = time() - start
                    remaining = total - success - len(errors)
                    logging.warning(f'{success} courses reindexed in {t:.1f} seconds. {remaining} remaining...')
            except Exception as exc:  # pylint: disable=broad-except
                errors.append(course_key)
                logging.exception('Error indexing course %s due to the error: %s.', course_key, exc)

        t = time() - start
        logging.warning(f'{success} of {total} courses reindexed successfully. Total running time: {t:.1f} seconds.')
        if errors:
            logging.warning('Reindex failed for %s courses:', len(errors))
            for course_key in errors:
                logging.warning(course_key)
