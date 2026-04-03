"""Word cloud integration tests using mongo modulestore."""
import importlib
import json
import re
from operator import itemgetter
from unittest.mock import patch
from uuid import UUID

import pytest
from django.conf import settings
from django.test import override_settings
from xblock import plugin

from common.djangoapps.student.tests.factories import RequestFactoryNoCsrf
from xmodule import word_cloud_block
# noinspection PyUnresolvedReferences
from xmodule.tests.helpers import mock_render_template, override_descriptor_system  # pylint: disable=unused-import
from xmodule.x_module import STUDENT_VIEW

from .helpers import BaseTestXmodule


@pytest.mark.usefixtures("override_descriptor_system")
class _TestWordCloudBase(BaseTestXmodule):
    """Integration test for Word Cloud Block."""
    __test__ = False
    CATEGORY = "word_cloud"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        plugin.PLUGIN_CACHE = {}
        importlib.reload(word_cloud_block)

    def setUp(self):
        super().setUp()
        self.request_factory = RequestFactoryNoCsrf()

    def _get_users_state(self):
        """Return current state for each user:

        {username: json_state}
        """
        # check word cloud response for every user
        users_state = {}

        for user in self.users:
            if settings.USE_EXTRACTED_WORD_CLOUD_BLOCK:
                # The extracted Word Cloud XBlock uses @XBlock.json_handler, which expects a different
                # request format and url pattern
                handler_url = self.get_url('', handler_name='handle_get_state')
                response = self.clients[user.username].post(
                    handler_url,
                    data=json.dumps({}),
                    content_type='application/json',
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                )
            else:
                response = self.clients[user.username].post(self.get_url('get_state'))
            users_state[user.username] = json.loads(response.content.decode('utf-8'))

        return users_state

    def _post_words(self, words):
        """Post `words` and return current state for each user:

        {username: json_state}
        """
        users_state = {}

        for user in self.users:
            if settings.USE_EXTRACTED_WORD_CLOUD_BLOCK:
                # The extracted Word Cloud XBlock uses @XBlock.json_handler, which expects a different
                # request format and url pattern
                handler_url = self.get_url('', handler_name='handle_submit_state')
                response = self.clients[user.username].post(
                    handler_url,
                    data=json.dumps({'student_words': words}),
                    content_type='application/json',
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest',
                )
            else:
                response = self.clients[user.username].post(
                    self.get_url('submit'),
                    {'student_words[]': words},
                    HTTP_X_REQUESTED_WITH='XMLHttpRequest'
                )
            users_state[user.username] = json.loads(response.content.decode('utf-8'))

        return users_state

    def _check_response(self, response_contents, correct_jsons):
        """Utility function that compares correct and real responses."""
        for username, content in response_contents.items():
            # Used in debugger for comparing objects.
            # self.maxDiff = None

            # We should compare top_words for manually,
            # because they are unsorted.
            keys_to_compare = set(content.keys()).difference({'top_words'})
            self.assertDictEqual(
                {k: content[k] for k in keys_to_compare},
                {k: correct_jsons[username][k] for k in keys_to_compare})

            # comparing top_words:
            top_words_content = sorted(
                content['top_words'],
                key=itemgetter('text')
            )
            top_words_correct = sorted(
                correct_jsons[username]['top_words'],
                key=itemgetter('text')
            )
            self.assertListEqual(top_words_content, top_words_correct)

    def test_initial_state(self):
        """Initial state of word cloud is correct. Those state that
        is sended from server to frontend, when students load word
        cloud page.
        """
        users_state = self._get_users_state()

        assert ''.join({content['status'] for (_, content) in users_state.items()}) == 'success'

        # correct initial data:
        correct_initial_data = {
            'status': 'success',
            'student_words': {},
            'total_count': 0,
            'submitted': False,
            'top_words': {},
            'display_student_percents': False
        }

        for _, response_content in users_state.items():
            assert response_content == correct_initial_data

    def test_post_words(self):
        """Students can submit data successfully.
        Word cloud data properly updates after students submit.
        """
        input_words = [
            "small",
            "BIG",
            " Spaced ",
            " few words",
        ]

        correct_words = [
            "small",
            "big",
            "spaced",
            "few words",
        ]

        users_state = self._post_words(input_words)

        assert ''.join({content['status'] for (_, content) in users_state.items()}) == 'success'

        correct_state = {}
        for index, user in enumerate(self.users):
            correct_state[user.username] = {
                'status': 'success',
                'submitted': True,
                'display_student_percents': True,
                'student_words': {word: 1 + index for word in correct_words},
                'total_count': len(input_words) * (1 + index),
                'top_words': [
                    {
                        'text': word, 'percent': 100 / len(input_words),
                        'size': (1 + index)
                    }
                    for word in correct_words
                ]
            }

        self._check_response(users_state, correct_state)

    def test_collective_users_submits(self):
        """Test word cloud data flow per single and collective users submits.

            Makes sure that:

            1. Initial state of word cloud is correct. Those state that
            is sended from server to frontend, when students load word
            cloud page.

            2. Students can submit data successfully.

            3. Next submits produce "already voted" error. Next submits for user
            are not allowed by user interface, but technically it possible, and
            word_cloud should properly react.

            4. State of word cloud after #3 is still as after #2.
        """

        # 1.
        users_state = self._get_users_state()

        assert ''.join({content['status'] for (_, content) in users_state.items()}) == 'success'

        # 2.
        # Invcemental state per user.
        users_state_after_post = self._post_words(['word1', 'word2'])

        assert ''.join({content['status'] for (_, content) in users_state_after_post.items()}) == 'success'

        # Final state after all posts.
        users_state_before_fail = self._get_users_state()

        # 3.
        users_state_after_post = self._post_words(
            ['word1', 'word2', 'word3'])

        assert ''.join({content['status'] for (_, content) in users_state_after_post.items()}) == 'fail'

        # 4.
        current_users_state = self._get_users_state()
        self._check_response(users_state_before_fail, current_users_state)

    def test_unicode(self):
        input_words = [" this is unicode Юникод"]
        correct_words = ["this is unicode юникод"]

        users_state = self._post_words(input_words)

        assert ''.join({content['status'] for (_, content) in users_state.items()}) == 'success'

        for user in self.users:
            self.assertListEqual(
                list(users_state[user.username]['student_words'].keys()),
                correct_words)

    def test_handle_ajax_incorrect_dispatch(self):
        responses = {
            user.username: self.clients[user.username].post(
                self.get_url('whatever'),
                {},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest')
            for user in self.users
        }

        if settings.USE_EXTRACTED_WORD_CLOUD_BLOCK:
            # The extracted Word Cloud XBlock uses @XBlock.json_handler to handle AJAX requests,
            # which automatically returns a 404 for unknown requests, so there's no need to test
            # the incorrect dispatch case in this scenario.
            for username, response in responses.items():
                self.assertEqual(response.status_code, 404)
            return

        status_codes = {response.status_code for response in responses.values()}
        assert status_codes.pop() == 200

        for user in self.users:
            self.assertDictEqual(
                json.loads(responses[user.username].content.decode('utf-8')),
                {
                    'status': 'fail',
                    'error': 'Unknown Command!'
                }
            )

    @patch('xblock.utils.resources.ResourceLoader.render_django_template', side_effect=mock_render_template)
    def test_word_cloud_constructor(self, mock_render_django_template):
        """
        Make sure that all parameters extracted correctly from xml.
        """
        fragment = self.runtime.render(self.block, STUDENT_VIEW)
        expected_context = {
            'display_name': self.block.display_name,
            'instructions': self.block.instructions,
            'element_class': self.block.scope_ids.block_type,
            'num_inputs': 5,  # default value
            'submitted': False,  # default value,
        }

        if settings.USE_EXTRACTED_WORD_CLOUD_BLOCK:
            # If `USE_EXTRACTED_WORD_CLOUD_BLOCK` is enabled, the `expected_context` will be different
            # because in the extracted Word Cloud XBlock, the expected context:
            # - contains `range_num_inputs`
            # - uses `UUID` for `element_id` instead of `html_id()`
            # - does not include `ajax_url` since it uses the `@XBlock.json_handler` decorator for AJAX requests
            expected_context['range_num_inputs'] = range(5)
            uuid_str = re.search(r"UUID\('([a-f0-9\-]+)'\)", fragment.content).group(1)
            expected_context['element_id'] = UUID(uuid_str)
            mock_render_django_template.assert_called_once()
            # Remove i18n service
            fragment_content_clean = re.sub(r"\{.*?}", "{}", fragment.content)
            assert fragment_content_clean == self.runtime.render_template('templates/word_cloud.html', expected_context)
        else:
            expected_context['ajax_url'] = self.block.ajax_url
            expected_context['element_id'] = self.block.location.html_id()
            assert fragment.content == self.runtime.render_template('word_cloud.html', expected_context)


@override_settings(USE_EXTRACTED_WORD_CLOUD_BLOCK=True)
class TestWordCloudExtracted(_TestWordCloudBase):
    __test__ = True


@override_settings(USE_EXTRACTED_WORD_CLOUD_BLOCK=False)
class TestWordCloudBuiltIn(_TestWordCloudBase):
    __test__ = True
