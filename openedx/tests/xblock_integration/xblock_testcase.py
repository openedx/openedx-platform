"""This file defines a testing framework for XBlocks. This framework
is designed to be independent of the edx-platform, to allow:

1. The tests to move into the XBlock repositories.
2. The tests to work in xblock-sdk and other runtimes.

This is a prototype. We reserve the right to change the APIs at any
point, and expect to do so a few times before freezing.

At this point, we support:

1. Python unit testing
2. Event publish testing
3. Testing multiple students
4. Testing multiple XBlocks on the same page.

We have spec'ed out how to do acceptance testing, but have not
implemented it yet. We have not spec'ed out JavaScript testing,
but believe it is important.

We do not intend to spec out XBlock/edx-platform integration testing
in the immediate future. This is best built as traditional
edx-platform tests for now.

We also do not plan to work on regression testing (taking live data
and replaying it) for now, but also believe it is important to do so
either in this framework or another.

Our next steps would be to:
* Finish this framework
* Have an appropriate test to make sure those tests are likely
  running for standard XBlocks (e.g. assert those entry points
  exist)
* Move more blocks out of the platform, and more tests into the
  blocks themselves.
"""


import collections
import json
import sys
import unittest
from datetime import datetime, timedelta
import html

from unittest import mock
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from django.conf import settings
from django.urls import reverse
from xblock.plugin import Plugin

import xmodule.services
from xmodule.modulestore.tests.django_utils import SharedModuleStoreTestCase
from xmodule.modulestore.tests.factories import CourseFactory, BlockFactory

from lms.djangoapps.courseware.tests.helpers import LoginEnrollmentTestCase


class XBlockEventTestMixin:
    """Mixin for easily verifying that events were published during a
    test.

    To do:
    * Evaluate patching runtime.emit instead of log_event
    * Evaluate using @mulby's event compare library

    By design, we capture all published events. We provide two functions:
    1. assert_no_events_published verifies that no events of a
       given search specification were published.
    2. assert_event_published verifies that an event of a given search
        specification was published.

    The Mongo event tests in cohorts have nice examplars for
    how such functionality might look.

    In the future, we would like to expand both search
    specifications. This is built in the edX event tracking acceptance
    tests, but is built on top of Mongo. We would also like to have
    nice error messages. This is in the edX event tracking tests, but
    would require a bit of work to tease out of the platform and make
    work in this context. We would also like to provide access to
    events for downstream consumers.

    Good things to look at as developing the code:
    * Gabe's library for parsing events. This is nice.
    * This is originally based on a cleanup of the EventTestMixin. We
      could work to converge those in some sensible way.
    """
    def setUp(self):
        """
        We patch runtime.publish to capture all XBlock events sent during
        the test.

        This is a little bit ugly -- it's all dynamic -- so we patch
        __init__ for the system runtime to capture the
        dynamically-created publish, and catch whatever is being
        passed into it.

        """
        super().setUp()
        saved_init = xmodule.services.EventPublishingService.__init__

        def patched_init(runtime_self, user, course_id, track_function, **kwargs):
            """
            Swap out track_function in the __init__
            """
            old_track_function = track_function

            def new_track_function(event_type, event):
                """
                Log the event, and call the original track_function.
                """
                self.events.append({"event": event, "event_type": event_type})
                old_track_function(event_type, event)
            track_function = new_track_function
            return saved_init(runtime_self, user, course_id, track_function, **kwargs)

        self.events = []
        publish_service = "xmodule.services.EventPublishingService.__init__"
        patcher = mock.patch(publish_service, patched_init)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_no_events_published(self, event_type):
        """
        Ensures no events of a given type were published since the last
        event related assertion.

        We are relatively specific since things like implicit HTTP
        events almost always do get omitted, and new event types get
        added all the time. This is not useful without a filter.

        """
        for event in self.events:
            assert event['event_type'] != event_type

    def assert_event_published(self, event_type, event_fields=None):
        """
        Verify that an event was published with the given parameters.

        We can verify that specific event fields are set using the
        optional search parameter.
        """
        if not event_fields:
            event_fields = {}
        for event in self.events:
            if event['event_type'] == event_type:
                found = True
                for field in event_fields:
                    if field not in event['event']:
                        found = False
                    elif event_fields[field] != event['event'][field]:
                        found = False
                if found:
                    return
        assert {'event_type': event_type, 'event': event_fields} in self.events

    def reset_published_events(self):
        """
        Reset the mock tracker in order to forget about old events.
        """
        self.events = []


class GradePublishTestMixin:
    '''
    This checks whether a grading event was correctly published. This
    puts basic plumbing in place, but we would like to:

    * Add search parameters. Is it for the right block? The right user? This
      only handles the case of one block/one user right now.
    * Check end-to-end. We would like to see grades in the database, not just
      look for emission. Looking for emission may still be helpful if there
      are multiple events in a test.

    This is a bit of work since we need to do a lot of translation
    between XBlock and edx-platform identifiers (e.g. url_name and
    usage key).

    We could also use the runtime.publish logic above, now that we have it.

    '''
    def setUp(self):
        '''
        Hot-patch the grading emission system to capture grading events.
        '''
        super().setUp()

        def capture_score(user_id, usage_key, score, max_score):
            '''
            Hot-patch which stores scores in a local array instead of the
            database.

            Note that to make this generic, we'd need to do both.
            '''
            self.scores.append({'student': user_id,
                                'usage': usage_key,
                                'score': score,
                                'max_score': max_score})
            # Shim a return time, defaults to 1 hour before now
            return datetime.now().replace(tzinfo=ZoneInfo("UTC")) - timedelta(hours=1)

        self.scores = []
        patcher = mock.patch("lms.djangoapps.grades.signals.handlers.set_score", capture_score)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_grade(self, grade):
        '''
        Confirm that the last grade set was equal to grade.

        HACK: In the future, this should take a user ID and a block url_name.
        '''
        assert grade == self.scores[(- 1)]['score']


class XBlockScenarioTestCaseMixin:
    '''
    This allows us to have test cases defined in JSON today, and in OLX
    someday.

    Until we do OLX, we're very restrictive in structure. One block
    per sequence, essentially.
    '''
    @classmethod
    def setUpClass(cls):
        """
        Create a set of pages with XBlocks on them. For now, we restrict
        ourselves to one block per learning sequence.
        """
        super().setUpClass()

        cls.course = CourseFactory.create(
            display_name='XBlock_Test_Course'
        )
        cls.scenario_urls = {}
        cls.xblocks = {}
        with cls.store.bulk_operations(cls.course.id, emit_signals=False):
            for chapter_config in cls.test_configuration:
                chapter = BlockFactory.create(
                    parent_location=cls.course.location,
                    display_name="ch_" + chapter_config['urlname'],
                    category='chapter'
                )
                section = BlockFactory.create(
                    parent=chapter,
                    display_name="sec_" + chapter_config['urlname'],
                    category='sequential'
                )
                unit = BlockFactory.create(
                    parent=section,
                    display_name='unit_' + chapter_config['urlname'],
                    category='vertical'
                )

                if len(chapter_config['xblocks']) > 1:
                    raise NotImplementedError(
                        """We only support one block per page. """
                        """We will do more with OLX+learning """
                        """sequence cleanups."""
                    )

                for xblock_config in chapter_config['xblocks']:
                    xblock = BlockFactory.create(
                        parent=unit,
                        category=xblock_config['blocktype'],
                        display_name=xblock_config['urlname'],
                        **xblock_config.get("parameters", {})
                    )
                    cls.xblocks[xblock_config['urlname']] = xblock

                scenario_url = reverse('render_xblock', args=[str(section.location)])

                cls.scenario_urls[chapter_config['urlname']] = scenario_url


class XBlockStudentTestCaseMixin:
    '''
    Creates a default set of students for XBlock tests
    '''
    student_list = [
        {'email': 'alice@test.edx.org', 'password': 'Password1234'},
        {'email': 'bob@test.edx.org', 'password': 'Password1234'},
        {'email': 'eve@test.edx.org', 'password': 'Password1234'},
    ]

    def setUp(self):
        """
        Create users accounts. The first three, we give helpful names
        to. If there are any more, we auto-generate number IDs. We
        intentionally use slightly different conventions for different
        users, so we exercise more corner cases, but we could
        standardize if this is more hassle than it's worth.
        """
        super().setUp()
        for idx, student in enumerate(self.student_list):
            username = f"u{idx}"
            self._enroll_user(username, student['email'], student['password'])
        self.select_student(0)

    def _enroll_user(self, username, email, password):
        '''
        Create and activate a user account.
        '''
        self.create_account(username, email, password)
        self.activate_user(email)
        self.login(email, password)
        self.enroll(self.course, verify=True)

    def select_student(self, user_id):
        """
        Select a current user account
        """
        # If we don't have enough users, add a few more...
        for newuser_id in range(len(self.student_list), user_id):
            username = f"user_{newuser_id}"
            email = f"user_{newuser_id}@example.edx.org"
            password = "12345"
            self._enroll_user(username, email, password)
            self.student_list.append({'email': email, 'password': password})

        email = self.student_list[user_id]['email']
        password = self.student_list[user_id]['password']

        # ... and log in as the appropriate user
        self.login(email, password)


class XBlockTestCase(XBlockStudentTestCaseMixin,
                     XBlockScenarioTestCaseMixin,
                     XBlockEventTestMixin,
                     GradePublishTestMixin,
                     SharedModuleStoreTestCase,
                     LoginEnrollmentTestCase,
                     Plugin):
    """
    Class for all XBlock-internal test cases (as opposed to
    integration tests).
    """
    test_configuration = None  # Children must override this!

    entry_point = 'xblock.test.v0'

    @classmethod
    def setUpClass(cls):
        '''
        Unless overridden, we create two student users and one staff
        user. We create the course hierarchy based on the OLX defined
        in the XBlock test class. Until we can deal with OLX, that
        actually will come from a list.
        '''
        # Nose runs setUpClass methods even if a class decorator says to skip
        # the class: https://github.com/nose-devs/nose/issues/946
        # So, skip the test class here if we are not in the LMS.
        if settings.ROOT_URLCONF != 'lms.urls':
            raise unittest.SkipTest('Test only valid in lms')
        super().setUpClass()

    def get_handler_url(self, handler, xblock_name=None):
        """
        Get url for the specified xblock handler
        """
        return reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': str(
                self.course.id.make_usage_key('done', xblock_name)
            ),
            'handler': handler,
            'suffix': ''
        })

    def ajax(self, function, block_urlname, json_data):
        '''
        Call a json_handler in the XBlock. Return the response as
        an object containing response code and JSON.
        '''
        url = self._get_handler_url(function, block_urlname)
        resp = self.client.post(url, json.dumps(json_data), '')
        ajax_response = collections.namedtuple('AjaxResponse',
                                               ['data', 'status_code'])
        try:
            ajax_response.data = json.loads(resp.content.decode('utf-8'))
        except ValueError:
            print("Invalid JSON response")
            print("(Often a redirect if e.g. not logged in)")
            print("Could not load JSON from AJAX call", file=sys.stderr)
            print("Status:", resp.status_code, file=sys.stderr)
            print("URL:", url, file=sys.stderr)
            print("Block", block_urlname, file=sys.stderr)
            print("Response", repr(resp.content), file=sys.stderr)
            raise
        ajax_response.status_code = resp.status_code
        return ajax_response

    def _get_handler_url(self, handler, xblock_name=None):
        """
        Get url for the specified xblock handler
        """
        xblock_type = None
        for scenario in self.test_configuration:
            for block in scenario["xblocks"]:
                if block["urlname"] == xblock_name:
                    xblock_type = block["blocktype"]

        key = str(self.course.id.make_usage_key(xblock_type, xblock_name))
        return reverse('xblock_handler', kwargs={
            'course_id': str(self.course.id),
            'usage_id': key,
            'handler': handler,
            'suffix': ''
        })

    def extract_block_html(self, content, urlname):
        '''This will extract the HTML of a rendered XBlock from a
        page. This should be simple. This should just be (in lxml):
            usage_id = self.xblocks[block_urlname].scope_ids.usage_id
            encoded_id = usage_id.replace(";_", "/")
        Followed by:
            page_xml = defusedxml.ElementTree.parse(StringIO(response_content))
            page_xml.find("//[@data-usage-id={usage}]".format(usage=encoded_id))
        or
            soup_html = BeautifulSoup(response_content, 'html.parser')
            soup_html.find(**{"data-usage-id": encoded_id})

        Why isn't it? Well, the blocks are stored in a rather funky
        way in learning sequences. Ugh. Easy enough, populate the
        course with just verticals. Well, that doesn't work
        either. The whole test infrastructure populates courses with
        Studio AJAX calls, and Studio has broken support for anything
        other than course/sequence/vertical/block.

        So until we either fix Studio to support most course
        structures, fix learning sequences to not have HTML-in-JS
        (which causes many other problems as well -- including
        user-facing bugs), or fix the test infrastructure to
        create courses from OLX, we're stuck with this little hack.
        '''
        usage_id = self.xblocks[urlname].scope_ids.usage_id
        # First, we get out our <div>
        soup_html = BeautifulSoup(markup=content, features="lxml")
        xblock_html = str(soup_html.find(id="seq_contents_0"))
        # Now, we get out the text of the <div>
        try:
            escaped_html = xblock_html.split('<')[1].split('>')[1]
        except IndexError:
            print("XBlock page could not render", file=sys.stderr)
            print("(Often, a redirect if e.g. not logged in)", file=sys.stderr)
            print("URL Name:", repr(urlname), file=sys.stderr)
            print("Usage ID", repr(usage_id), file=sys.stderr)
            print("Content", repr(content), file=sys.stderr)
            print("Split 1", repr(xblock_html.split('<')), file=sys.stderr)
            print("Dice 1:", repr(xblock_html.split('<')[1]), file=sys.stderr)
            print("Split 2", repr(xblock_html.split('<')[1].split('>')), file=sys.stderr)
            print("Dice 2", repr(xblock_html.split('<')[1].split('>')[1]), file=sys.stderr)
            raise
        # Finally, we unescape the contents
        decoded_html = html.unescape(escaped_html).strip()

        return decoded_html

    def render_block(self, block_urlname):
        '''
        Return a rendering of the XBlock.

        We should include data, but with a selector dropping
        the rest of the HTML around the block.
        '''
        section = self._containing_section(block_urlname)
        html_response = collections.namedtuple('HtmlResponse',
                                               ['status_code',
                                                'content',
                                                'debug'])
        url = self.scenario_urls[section]
        response = self.client.get(url)

        html_response.status_code = response.status_code
        response_content = response.content.decode('utf-8')
        html_response.content = self.extract_block_html(
            response_content,
            block_urlname
        )
        # We return a little bit of metadata helpful for debugging.
        # What is in this is not a defined part of the API contract.
        html_response.debug = {'url': url,
                               'section': section,
                               'block_urlname': block_urlname}
        return html_response

    def _containing_section(self, block_urlname):
        '''
        For a given block, return the parent section
        '''
        for section in self.test_configuration:
            blocks = section["xblocks"]
            for block in blocks:
                if block['urlname'] == block_urlname:
                    return section['urlname']
        raise Exception("Block not found " + block_urlname)

    def assertXBlockScreenshot(self, block_urlname, rendering=None):
        '''
        As in Bok Choi, but instead of a CSS selector, we pass a
        block_id. We may want to be able to pass an optional selector
        for picking a subelement of the block.

        This confirms status code, and that the screenshot is
        identical.

        To do: Implement
        '''
        raise NotImplementedError("We need Ben's help to finish this")
