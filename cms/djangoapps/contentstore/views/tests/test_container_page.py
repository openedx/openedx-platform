"""
Unit tests for the container page.
"""


import datetime
import re
from unittest.mock import Mock, patch
from urllib.parse import quote

from django.http import Http404
from django.test.client import RequestFactory
from django.urls import reverse
from edx_toggles.toggles.testutils import override_waffle_flag
from pytz import UTC

import cms.djangoapps.contentstore.views.component as views
from cms.djangoapps.contentstore import toggles
from cms.djangoapps.contentstore.tests.test_libraries import LibraryTestCase
from xmodule.modulestore import ModuleStoreEnum  # pylint: disable=wrong-import-order
from xmodule.modulestore.django import modulestore  # pylint: disable=wrong-import-order
from xmodule.modulestore.tests.factories import (  # pylint: disable=wrong-import-order
    BlockFactory,
    CourseFactory,
)

from .utils import StudioPageTestCase


class ContainerPageTestCase(StudioPageTestCase, LibraryTestCase):
    """
    Unit tests for the container page.
    """

    container_view = 'container_preview'
    reorderable_child_view = 'reorderable_container_child_preview'

    def setUp(self):
        super().setUp()
        self.vertical = self._create_block(self.sequential, 'vertical', 'Unit')
        self.html = self._create_block(self.vertical, "html", "HTML")
        self.child_container = self._create_block(self.vertical, 'split_test', 'Split Test')
        self.child_vertical = self._create_block(self.child_container, 'vertical', 'Child Vertical')
        self.video = self._create_block(self.child_vertical, "video", "My Video")
        self.store = modulestore()

        past = datetime.datetime(1970, 1, 1, tzinfo=UTC)
        future = datetime.datetime.now(UTC) + datetime.timedelta(days=1)
        self.released_private_vertical = self._create_block(
            parent=self.sequential, category='vertical', display_name='Released Private Unit',
            start=past)
        self.unreleased_private_vertical = self._create_block(
            parent=self.sequential, category='vertical', display_name='Unreleased Private Unit',
            start=future)
        self.released_public_vertical = self._create_block(
            parent=self.sequential, category='vertical', display_name='Released Public Unit',
            start=past)
        self.unreleased_public_vertical = self._create_block(
            parent=self.sequential, category='vertical', display_name='Unreleased Public Unit',
            start=future)
        self.store.publish(self.unreleased_public_vertical.location, self.user.id)
        self.store.publish(self.released_public_vertical.location, self.user.id)
        self.store.publish(self.vertical.location, self.user.id)

    def test_container_html(self):
        assets_url = reverse(
            'assets_handler', kwargs={'course_key_string': str(self.child_container.location.course_key)}
        )
        self._test_html_content(
            self.child_container,
            expected_section_tag=(
                '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '  # noqa: UP032
                'data-locator="{0}" data-course-key="{0.course_key}" data-course-assets="{1}">'.format(
                    self.child_container.location, assets_url
                )
            ),
            expected_breadcrumbs=(
                '<li class="nav-item">\\s*<a href="/course/{course}{section_parameters}">Week 1<\\/a>.*'
                '<a href="/course/{course}{subsection_parameters}">Lesson 1</a>'
            ).format(
                course=re.escape(str(self.course.id)),
                section_parameters=re.escape('?show={}'.format(quote(
                    str(self.chapter.location).encode()
                ))),
                subsection_parameters=re.escape('?show={}'.format(quote(
                    str(self.sequential.location).encode()
                ))),
            ),
        )

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_container_on_container_html(self):
        """
        Create the scenario of an xblock with children (non-vertical) on the container page.
        This should create a container page that is a child of another container page.
        """
        draft_container = self._create_block(self.child_container, "wrapper", "Wrapper")
        self._create_block(draft_container, "html", "Child HTML")

        def test_container_html(xblock):
            assets_url = reverse(
                'assets_handler', kwargs={'course_key_string': str(draft_container.location.course_key)}
            )
            self._test_html_content(
                xblock,
                expected_section_tag=(
                    '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '  # noqa: UP032
                    'data-locator="{0}" data-course-key="{0.course_key}" data-course-assets="{1}">'.format(
                        draft_container.location, assets_url
                    )
                ),
                expected_breadcrumbs=(
                    '<a href="/course/{course}{subsection_parameters}">Lesson 1</a>.*'
                    '<a href="/container/{unit_parameters}">Unit</a>.*'
                ).format(
                    course=re.escape(str(self.course.id)),
                    unit_parameters=re.escape(str(self.vertical.location)),
                    subsection_parameters=re.escape('?show={}'.format(quote(
                        str(self.sequential.location).encode()
                    ))),
                ),
            )

        # Test the draft version of the container
        test_container_html(draft_container)

        # Now publish the unit and validate again
        self.store.publish(self.vertical.location, self.user.id)
        draft_container = self.store.get_item(draft_container.location)
        test_container_html(draft_container)

    def _test_html_content(self, xblock, expected_section_tag, expected_breadcrumbs):
        """
        Get the HTML for a container page and verify the section tag is correct
        and the breadcrumbs trail is correct.
        """
        html = self.get_page_html(xblock)
        self.assertIn(expected_section_tag, html)  # noqa: PT009
        self.assertRegex(html, re.compile(expected_breadcrumbs, re.DOTALL))  # noqa: PT009

    def test_public_container_preview_html(self):
        """
        Verify that a public xblock's container preview returns the expected HTML.
        """
        published_unit = self.store.publish(self.vertical.location, self.user.id)
        published_child_container = self.store.get_item(self.child_container.location)
        published_child_vertical = self.store.get_item(self.child_vertical.location)
        self.validate_preview_html(published_unit, self.container_view)
        self.validate_preview_html(published_child_container, self.container_view)
        self.validate_preview_html(published_child_vertical, self.reorderable_child_view)

    def test_library_page_preview_html(self):
        """
        Verify that a library xblock's container (library page) preview returns the expected HTML.
        """
        # Add some content to library.
        self._add_simple_content_block()
        self.validate_preview_html(self.library, self.container_view, can_reorder=False, can_move=False)

    def test_library_content_preview_html(self):
        """
        Verify that a library content block container page preview returns the expected HTML.
        """
        # Library content block is only supported in split courses.
        with modulestore().default_store(ModuleStoreEnum.Type.split):
            course = CourseFactory.create()

        # Add some content to library
        self._add_simple_content_block()

        # Create a library content block
        lc_block = self._add_library_content_block(course, self.lib_key)
        self.assertEqual(len(lc_block.children), 0)  # noqa: PT009

        # Refresh children to be reflected in lc_block
        lc_block = self._upgrade_and_sync(lc_block)
        self.assertEqual(len(lc_block.children), 1)  # noqa: PT009

        self.validate_preview_html(
            lc_block,
            self.container_view,
            can_add=False,
            can_reorder=False,
            can_move=False,
            can_edit=True,
            can_duplicate=False,
            can_delete=False
        )

    def test_draft_container_preview_html(self):
        """
        Verify that a draft xblock's container preview returns the expected HTML.
        """
        self.validate_preview_html(self.vertical, self.container_view)
        self.validate_preview_html(self.child_container, self.container_view)
        self.validate_preview_html(self.child_vertical, self.reorderable_child_view)

    def _create_block(self, parent, category, display_name, **kwargs):
        """
        creates a block in the module store, without publishing it.
        """
        return BlockFactory.create(
            parent=parent,
            category=category,
            display_name=display_name,
            publish_item=False,
            user_id=self.user.id,
            **kwargs
        )

    def test_public_child_container_preview_html(self):
        """
        Verify that a public container rendered as a child of the container page returns the expected HTML.
        """
        empty_child_container = self._create_block(self.vertical, 'split_test', 'Split Test 1')
        published_empty_child_container = self.store.publish(empty_child_container.location, self.user.id)
        self.validate_preview_html(published_empty_child_container, self.reorderable_child_view, can_add=False)

    def test_draft_child_container_preview_html(self):
        """
        Verify that a draft container rendered as a child of the container page returns the expected HTML.
        """
        empty_child_container = self._create_block(self.vertical, 'split_test', 'Split Test 1')
        self.validate_preview_html(empty_child_container, self.reorderable_child_view, can_add=False)

    @patch(
        'cms.djangoapps.contentstore.views.component.render_to_response',
        Mock(return_value=Mock(status_code=200, content=''))
    )
    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_container_page_with_valid_and_invalid_usage_key_string(self):
        """
        Check that invalid 'usage_key_string' raises Http404.
        """
        request = RequestFactory().get('foo')
        request.user = self.user
        request.LANGUAGE_CODE = 'en'

        # Check for invalid 'usage_key_strings'
        self.assertRaises(  # noqa: PT027
            Http404, views.container_handler,
            request,
            usage_key_string='i4x://InvalidOrg/InvalidCourse/vertical/static/InvalidContent',
        )

        # Check 200 response if 'usage_key_string' is correct
        response = views.container_handler(
            request=request,
            usage_key_string=str(self.vertical.location)
        )
        self.assertEqual(response.status_code, 200)  # noqa: PT009


class ContainerEmbedPageTestCase(ContainerPageTestCase):  # pylint: disable=test-inherits-tests
    """
    Unit tests for the container embed page.
    """

    def test_container_html(self):
        assets_url = reverse(
            'assets_handler', kwargs={'course_key_string': str(self.child_container.location.course_key)}
        )
        self._test_html_content(
            self.child_container,
            expected_section_tag=(
                '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '  # noqa: UP032
                'data-locator="{0}" data-course-key="{0.course_key}" data-course-assets="{1}">'.format(
                    self.child_container.location, assets_url
                )
            ),
        )

    @override_waffle_flag(toggles.LEGACY_STUDIO_UNIT_EDITOR, True)
    def test_container_on_container_html(self):
        """
        Create the scenario of an xblock with children (non-vertical) on the container page.
        This should create a container page that is a child of another container page.
        """
        draft_container = self._create_block(self.child_container, "wrapper", "Wrapper")
        self._create_block(draft_container, "html", "Child HTML")

        def test_container_html(xblock):
            assets_url = reverse(
                'assets_handler', kwargs={'course_key_string': str(draft_container.location.course_key)}
            )
            self._test_html_content(
                xblock,
                expected_section_tag=(
                    '<section class="wrapper-xblock level-page is-hidden studio-xblock-wrapper" '  # noqa: UP032
                    'data-locator="{0}" data-course-key="{0.course_key}" data-course-assets="{1}">'.format(
                        draft_container.location, assets_url
                    )
                ),
            )

        # Test the draft version of the container
        test_container_html(draft_container)

        # Now publish the unit and validate again
        self.store.publish(self.vertical.location, self.user.id)
        draft_container = self.store.get_item(draft_container.location)
        test_container_html(draft_container)

    def _test_html_content(self, xblock, expected_section_tag):  # pylint: disable=arguments-differ
        """
        Get the HTML for a container page and verify the section tag is correct
        and the breadcrumbs trail is correct.
        """
        html = self.get_page_html(xblock)
        self.assertIn(expected_section_tag, html)  # noqa: PT009
