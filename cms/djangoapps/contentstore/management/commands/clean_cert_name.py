"""
A single-use management command that provides an interactive way to remove
erroneous certificate names.
"""


from collections import namedtuple

from django.core.management.base import BaseCommand

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

Result = namedtuple("Result", ["course_key", "cert_name_short", "cert_name_long", "should_clean"])


class Command(BaseCommand):
    """
    A management command that provides an interactive way to remove erroneous cert_name_long and
    cert_name_short course attributes across both the Split and Mongo modulestores.
    """
    help = 'Allows manual clean-up of invalid cert_name_short and cert_name_long entries on CourseBlocks'

    def _mongo_results(self):
        """
        Return Result objects for any mongo-modulestore backend course that has
        cert_name_short or cert_name_long set.
        """
        # N.B. This code breaks many abstraction barriers. That's ok, because
        # it's a one-time cleanup command.
        # pylint: disable=protected-access
        mongo_modulestore = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.mongo)
        old_mongo_courses = mongo_modulestore.collection.find({
            "_id.category": "course",
            "$or": [
                {"metadata.cert_name_short": {"$exists": 1}},
                {"metadata.cert_name_long": {"$exists": 1}},
            ]
        }, {
            "_id": True,
            "metadata.cert_name_short": True,
            "metadata.cert_name_long": True,
        })

        return [
            Result(
                mongo_modulestore.make_course_key(
                    course['_id']['org'],
                    course['_id']['course'],
                    course['_id']['name'],
                ),
                course['metadata'].get('cert_name_short'),
                course['metadata'].get('cert_name_long'),
                True
            ) for course in old_mongo_courses
        ]

    def _split_results(self):
        """
        Return Result objects for any split-modulestore backend course that has
        cert_name_short or cert_name_long set.
        """
        # N.B. This code breaks many abstraction barriers. That's ok, because
        # it's a one-time cleanup command.
        # pylint: disable=protected-access
        split_modulestore = modulestore()._get_modulestore_by_type(ModuleStoreEnum.Type.split)
        active_version_collection = split_modulestore.db_connection.course_index
        structure_collection = split_modulestore.db_connection.structures

        branches = active_version_collection.aggregate([{
            '$group': {
                '_id': 1,
                'draft': {'$push': '$versions.draft-branch'},
                'published': {'$push': '$versions.published-branch'}
            }
        }, {
            '$project': {
                '_id': 1,
                'branches': {'$setUnion': ['$draft', '$published']}
            }
        }])['result'][0]['branches']

        structures = list(
            structure_collection.find({
                '_id': {'$in': branches},
                'blocks': {'$elemMatch': {
                    '$and': [
                        {"block_type": "course"},
                        {'$or': [
                            {'fields.cert_name_long': {'$exists': True}},
                            {'fields.cert_name_short': {'$exists': True}}
                        ]}
                    ]
                }}
            }, {
                '_id': True,
                'blocks.fields.cert_name_long': True,
                'blocks.fields.cert_name_short': True,
            })
        )

        structure_map = {struct['_id']: struct for struct in structures}
        structure_ids = [struct['_id'] for struct in structures]

        split_mongo_courses = list(active_version_collection.find({
            '$or': [
                {"versions.draft-branch": {'$in': structure_ids}},
                {"versions.published": {'$in': structure_ids}},
            ]
        }, {
            'org': True,
            'course': True,
            'run': True,
            'versions': True,
        }))

        for course in split_mongo_courses:
            draft = course['versions'].get('draft-branch')
            if draft in structure_map:
                draft_fields = structure_map[draft]['blocks'][0].get('fields', {})
            else:
                draft_fields = {}

            published = course['versions'].get('published')
            if published in structure_map:
                published_fields = structure_map[published]['blocks'][0].get('fields', {})
            else:
                published_fields = {}

            for fields in (draft_fields, published_fields):
                for field in ('cert_name_short', 'cert_name_long'):
                    if field in fields:
                        course[field] = fields[field]

        return [
            Result(
                split_modulestore.make_course_key(
                    course['org'],
                    course['course'],
                    course['run'],
                ),
                course.get('cert_name_short'),
                course.get('cert_name_long'),
                True
            ) for course in split_mongo_courses
        ]

    def _display(self, results):
        """
        Render a list of Result objects as a nicely formatted table.
        """
        headers = ["Course Key", "cert_name_short", "cert_name_short", "Should clean?"]
        col_widths = [
            max(len(str(result[col])) for result in results + [headers])
            for col in range(len(results[0]))
        ]
        id_format = f"{{:>{len(str(len(results)))}}} |"
        col_format = "| {{:>{}}} |"

        self.stdout.write(id_format.format(""), ending='')
        for header, width in zip(headers, col_widths):  # noqa: B905
            self.stdout.write(col_format.format(width).format(header), ending='')

        self.stdout.write('')

        for idx, result in enumerate(results):
            self.stdout.write(id_format.format(idx), ending='')
            for col, width in zip(result, col_widths):  # noqa: B905
                self.stdout.write(col_format.format(width).format(str(col)), ending='')
            self.stdout.write("")

    def _commit(self, results):
        """
        For each Result in ``results``, if ``should_clean`` is True, remove cert_name_long
        and cert_name_short from the course and save in the backing modulestore.
        """
        for result in results:
            if not result.should_clean:
                continue
            course = modulestore().get_course(result.course_key)
            del course.cert_name_short
            del course.cert_name_long
            modulestore().update_item(course, ModuleStoreEnum.UserID.mgmt_command)

    def handle(self, *args, **options):

        results = self._mongo_results() + self._split_results()

        self.stdout.write("Type the index of a row to toggle whether it will be cleaned, "
                          "'commit' to remove all cert_name_short and cert_name_long values "
                          "from any rows marked for cleaning, or 'quit' to quit.")

        while True:
            self._display(results)
            command = input("<index>|commit|quit: ").strip()

            if command == 'quit':
                return
            elif command == 'commit':
                self._commit(results)
                return
            elif command == '':
                continue
            else:
                index = int(command)
                results[index] = results[index]._replace(should_clean=not results[index].should_clean)
