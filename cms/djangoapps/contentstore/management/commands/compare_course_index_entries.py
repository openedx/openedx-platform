"""A Command to determine if the Mongo active_versions and Django course_index tables are out of sync"""
import logging

from django.conf import settings
from django.core.management.base import BaseCommand
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.split_modulestore_django.models import SplitModulestoreCourseIndex
from xmodule.modulestore.split_mongo.mongo_connection import DjangoFlexPersistenceBackend, MongoPersistenceBackend

logger = logging.getLogger(__name__)


class Command(BaseCommand):

    """
    A Command to determine if the Mongo active_versions and Django course_index tables are out of sync with one another.
    """

    def handle(self, *args, **options):

        courses = SplitModulestoreCourseIndex.objects.values('course_id')
        course_ids = [x['course_id'] for x in courses if isinstance(x['course_id'], CourseLocator)]
        out_of_sync = {}

        mongostore = MongoPersistenceBackend(**settings.CONTENTSTORE['DOC_STORE_CONFIG'])
        msqlstore = DjangoFlexPersistenceBackend(**settings.CONTENTSTORE['DOC_STORE_CONFIG'])

        def compare_entries(mongo_entry, msql_entry, course_id):
            draft = False
            published = False
            if mongo_entry["draft-branch"] != msql_entry["draft-branch"]:
                draft = True
            if mongo_entry["published-branch"] != msql_entry["published-branch"]:
                published = True
            if draft | published:
                out_of_sync[str(course_id)] = (
                    mongo_entry["draft-branch"],
                    mongo_entry["published-branch"],
                    msql_entry["draft-branch"],
                    msql_entry["published-branch"],
                    mongostore.get_course_index(key=course_id)['last_update'],
                    msqlstore.get_course_index(key=course_id)['last_update'],
                )

        for course_id in sorted(course_ids):
            mongo_entry = mongostore.get_course_index(key=course_id)['versions']
            msql_entry = msqlstore.get_course_index(key=course_id)['versions']
            compare_entries(mongo_entry, msql_entry, course_id)

        print(out_of_sync)
        for course in out_of_sync:
            print('*' * 8)
            print(f'{course} is out of sync in course_index tables')
            print(f'MONGO draft     id: {out_of_sync[course][0]}')
            print(f'MONGO published id: {out_of_sync[course][1]}')
            print(f'MYSQL draft     id: {out_of_sync[course][2]}')
            print(f'MYSQL published id: {out_of_sync[course][3]}')
        print('*' * 8)
        print(f'The number of out-of-sync courses is : {len(out_of_sync)}')
