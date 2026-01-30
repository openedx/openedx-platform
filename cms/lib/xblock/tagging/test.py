"""
Tests for the Studio Tagging XBlockAside
"""


import json
from datetime import datetime
from io import StringIO

import ddt
from django.test.client import RequestFactory
from lxml import etree
from opaque_keys.edx.asides import AsideUsageKeyV1, AsideUsageKeyV2, UsageKey
from pytz import UTC
from xblock.fields import ScopeIds
from xblock.runtime import DictKeyValueStore, KvsFieldData
from xblock.test.tools import TestRuntime

from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient
from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.views.preview import get_preview_fragment
from cms.djangoapps.xblock_config.models import StudioConfig
from cms.lib.xblock.tagging import StructuredTagsAside
from cms.lib.xblock.tagging.models import TagAvailableValues, TagCategories
from common.djangoapps.student.tests.factories import UserFactory
from xmodule.modulestore import ModuleStoreEnum  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase  # lint-amnesty, pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory  # lint-amnesty, pylint: disable=wrong-import-order


@ddt.ddt
class StructuredTagsAsideTestCase(ModuleStoreTestCase):
    """
    Base class for tests of StructuredTagsAside (tagging.py)
    """
    def setUp(self):
        """
        Preparation for the test execution
        """
        super().setUp()
        self.aside_name = 'tagging_aside'
        self.aside_tag_dif = 'difficulty'
        self.aside_tag_dif_value = 'Hard'
        self.aside_tag_dif_value2 = 'Easy'
        self.aside_tag_lo = 'learning_outcome'

        course = CourseFactory.create(default_store=ModuleStoreEnum.Type.split)
        self.course = BlockFactory.create(
            parent_location=course.location,
            category="course",
            display_name="Test course",
        )
        self.chapter = BlockFactory.create(
            parent_location=self.course.location,
            category='chapter',
            display_name="Week 1",
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.sequential = BlockFactory.create(
            parent_location=self.chapter.location,
            category='sequential',
            display_name="Lesson 1",
            publish_item=True,
            start=datetime(2015, 3, 1, tzinfo=UTC),
        )
        self.vertical = BlockFactory.create(
            parent_location=self.sequential.location,
            category='vertical',
            display_name='Subsection 1',
            publish_item=True,
            start=datetime(2015, 4, 1, tzinfo=UTC),
        )
        self.problem = BlockFactory.create(
            category="problem",
            parent_location=self.vertical.location,
            display_name="A Problem Block",
            weight=1,
            user_id=self.user.id,
            publish_item=False,
        )
        self.video = BlockFactory.create(
            parent_location=self.vertical.location,
            category='video',
            display_name='My Video',
            user_id=self.user.id
        )

        _init_data = [
            {
                'name': 'difficulty',
                'title': 'Difficulty',
                'values': ['Easy', 'Medium', 'Hard'],
            },
            {
                'name': 'learning_outcome',
                'title': 'Learning outcome',
                'values': ['Learned nothing', 'Learned a few things', 'Learned everything']
            }
        ]

        for tag in _init_data:
            category = TagCategories.objects.create(name=tag['name'], title=tag['title'])
            for val in tag['values']:
                TagAvailableValues.objects.create(category=category, value=val)

        config = StudioConfig.current()
        config.enabled = True
        config.save()

    def tearDown(self):
        TagAvailableValues.objects.all().delete()
        TagCategories.objects.all().delete()
        super().tearDown()

    def test_aside_contains_tags(self):
        """
        Checks that available_tags list is not empty
        """
        sids = ScopeIds(
            user_id="bob",
            block_type="bobs-type",
            def_id="definition-id",
            usage_id=UsageKey.from_string("block-v1:myOrg+myCourse+myRun+type@bobs-type+block@usage-id"),
        )
        key_store = DictKeyValueStore()
        field_data = KvsFieldData(key_store)
        runtime = TestRuntime(services={'field-data': field_data})
        xblock_aside = StructuredTagsAside(scope_ids=sids, runtime=runtime)
        available_tags = xblock_aside.get_available_tags()
        self.assertEqual(len(available_tags), 2, "StructuredTagsAside should contains two tag categories")

    def test_preview_html(self):
        """
        Checks that html for the StructuredTagsAside is generated correctly
        """
        request = RequestFactory().get('/dummy-url')
        request.user = UserFactory()
        request.session = {}

        # Call get_preview_fragment directly.
        context = {
            'reorderable_items': set(),
            'read_only': True
        }
        problem_html = get_preview_fragment(request, self.problem, context).content

        parser = etree.HTMLParser()
        tree = etree.parse(StringIO(problem_html), parser)

        main_div_nodes = tree.xpath('/html/body/div/section/div')
        self.assertEqual(len(main_div_nodes), 2)

        loader_div_node = main_div_nodes[0]
        self.assertIn('ui-loading', loader_div_node.get('class'))

        div_node = main_div_nodes[1]
        self.assertEqual(div_node.get('data-init'), 'StructuredTagsInit')
        self.assertEqual(div_node.get('data-runtime-class'), 'PreviewRuntime')
        self.assertEqual(div_node.get('data-block-type'), 'tagging_aside')
        self.assertEqual(div_node.get('data-runtime-version'), '1')
        self.assertIn('xblock_asides-v1', div_node.get('class'))

        select_nodes = div_node.xpath("div//select[@multiple='multiple']")
        self.assertEqual(len(select_nodes), 2)

        select_node1 = select_nodes[0]
        self.assertEqual(select_node1.get('name'), self.aside_tag_dif)

        option_nodes1 = select_node1.xpath('option')
        self.assertEqual(len(option_nodes1), 3)

        option_values1 = [opt_elem.text for opt_elem in option_nodes1]
        self.assertEqual(option_values1, ['Easy', 'Hard', 'Medium'])

        select_node2 = select_nodes[1]
        self.assertEqual(select_node2.get('name'), self.aside_tag_lo)
        self.assertEqual(select_node2.get('multiple'), 'multiple')

        option_nodes2 = select_node2.xpath('option')
        self.assertEqual(len(option_nodes2), 3)

        option_values2 = [opt_elem.text for opt_elem in option_nodes2 if opt_elem.text]
        self.assertEqual(option_values2, ['Learned a few things', 'Learned everything', 'Learned nothing'])

        # Now ensure the acid_aside is not in the result
        self.assertNotRegex(problem_html, r"data-block-type=[\"\']acid_aside[\"\']")

        # Ensure about video don't have asides
        video_html = get_preview_fragment(request, self.video, context).content
        self.assertNotRegex(video_html, "<select")

    @ddt.data(AsideUsageKeyV1, AsideUsageKeyV2)
    def test_handle_requests(self, aside_key_class):
        """
        Checks that handler to save tags in StructuredTagsAside works properly
        """
        handler_url = reverse_usage_url(
            'component_handler',
            str(aside_key_class(self.problem.location, self.aside_name)),
            kwargs={'handler': 'save_tags'}
        )

        client = AjaxEnabledTestClient()
        client.login(username=self.user.username, password=self.user_password)

        response = client.post(handler_url, json.dumps({}), content_type="application/json")
        self.assertEqual(response.status_code, 400)

        response = client.post(handler_url, json.dumps({'undefined_tag': ['undefined1', 'undefined2']}),
                               content_type="application/json")
        self.assertEqual(response.status_code, 400)

        response = client.post(handler_url, json.dumps({self.aside_tag_dif: ['undefined1', 'undefined2']}),
                               content_type="application/json")
        self.assertEqual(response.status_code, 400)

        def _test_helper_func(problem_location):
            """
            Helper function
            """
            problem = modulestore().get_item(problem_location)
            asides = problem.runtime.get_asides(problem)
            tag_aside = None
            for aside in asides:
                if isinstance(aside, StructuredTagsAside):
                    tag_aside = aside
                    break
            return tag_aside

        response = client.post(handler_url, json.dumps({self.aside_tag_dif: [self.aside_tag_dif_value]}),
                               content_type="application/json")
        self.assertEqual(response.status_code, 200)

        tag_aside = _test_helper_func(self.problem.location)
        self.assertIsNotNone(tag_aside, "Necessary StructuredTagsAside object isn't found")
        self.assertEqual(tag_aside.saved_tags[self.aside_tag_dif], [self.aside_tag_dif_value])

        response = client.post(handler_url, json.dumps({self.aside_tag_dif: [self.aside_tag_dif_value,
                                                                             self.aside_tag_dif_value2]}),
                               content_type="application/json")
        self.assertEqual(response.status_code, 200)

        tag_aside = _test_helper_func(self.problem.location)
        self.assertIsNotNone(tag_aside, "Necessary StructuredTagsAside object isn't found")
        self.assertEqual(tag_aside.saved_tags[self.aside_tag_dif], [self.aside_tag_dif_value,
                                                                    self.aside_tag_dif_value2])
