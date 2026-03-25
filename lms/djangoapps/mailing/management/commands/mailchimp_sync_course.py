"""
Synchronizes a mailchimp list with the students of a course.
"""


import itertools  # noqa: I001
import logging
import math
import random
from collections import namedtuple
from itertools import chain

from django.core.management.base import BaseCommand
from mailsnake import MailSnake
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.student.models import UserProfile, unique_id_for_user

BATCH_SIZE = 15000
# If you try to subscribe with too many users at once
# the transaction times out on the mailchimp side.
SUBSCRIBE_BATCH_SIZE = 1000

log = logging.getLogger('edx.mailchimp')

FIELD_TYPES = {'EDX_ID': 'text'}


class Command(BaseCommand):
    """
    Synchronizes a mailchimp list with the students of a course.
    """
    help = 'Synchronizes a mailchimp list with the students of a course.'

    def add_arguments(self, parser):
        parser.add_argument('--key',
                            required=True,
                            help='mailchimp api key')
        parser.add_argument('--list',
                            dest='list_id',
                            required=True,
                            help='mailchimp list id')
        parser.add_argument('--course',
                            dest='course_id',
                            required=True,
                            help='edx course_id')
        parser.add_argument('--segments',
                            dest='num_segments',
                            type=int,
                            default=0,
                            help='number of static random segments to create')

    def handle(self, *args, **options):
        """Synchronizes a mailchimp list with the students of a course."""
        key = options['key']
        list_id = options['list_id']
        course_id = options['course_id']
        num_segments = options['num_segments']

        log.info('Syncronizing email list for %s', course_id)

        mailchimp = connect_mailchimp(key)

        subscribed = get_subscribed(mailchimp, list_id)
        unsubscribed = get_unsubscribed(mailchimp, list_id)
        cleaned = get_cleaned(mailchimp, list_id)
        non_subscribed = unsubscribed.union(cleaned)

        enrolled = get_enrolled_students(course_id)

        exclude = subscribed.union(non_subscribed)
        to_subscribe = get_student_data(enrolled, exclude=exclude)

        tag_names = set(chain.from_iterable(list(d.keys()) for d in to_subscribe))
        update_merge_tags(mailchimp, list_id, tag_names)

        subscribe_with_data(mailchimp, list_id, to_subscribe)

        enrolled_emails = set(enrolled.values_list('user__email', flat=True))
        non_enrolled_emails = list(subscribed.difference(enrolled_emails))

        unsubscribe(mailchimp, list_id, non_enrolled_emails)

        subscribed = subscribed.union({d['EMAIL'] for d in to_subscribe})
        make_segments(mailchimp, list_id, num_segments, subscribed)


def connect_mailchimp(api_key):
    """
    Initializes connection to the mailchimp api
    """
    mailchimp = MailSnake(api_key)
    result = mailchimp.ping()
    log.debug(result)

    return mailchimp


def verify_list(mailchimp, list_id, course_id):
    """
    Verifies that the given list_id corresponds to the course_id
    Returns boolean: whether or not course_id matches list_id
    """
    lists = mailchimp.lists(filters={'list_id': list_id})['data']

    if len(lists) != 1:
        log.error('incorrect list id')
        return False

    list_name = lists[0]['name']

    log.debug('list name: %s', list_name)

    # check that we are connecting to the correct list
    parts = course_id.replace('_', ' ').replace('/', ' ').split()
    count = sum(1 for p in parts if p in list_name)
    if count < 3:
        log.info(course_id)
        log.info(list_name)
        log.error('course_id does not match list name')
        return False

    return True


def get_student_data(students, exclude=None):
    """
    Given a QuerySet of Django users, extracts id, username, and is_anonymous data.
    Excludes any users provided in the optional `exclude` set.

    Returns a list of dictionaries for each user, where the dictionary has keys
    'EMAIL', 'FULLNAME', and 'EDX_ID'.
    """
    # To speed the query, we won't retrieve the full User object, only
    # two of its values. The namedtuple simulates the User object.
    FakeUser = namedtuple('Fake', 'id username is_anonymous')

    exclude = exclude if exclude else set()

    def make(svalue):
        """
        Given a User value entry `svalue`, extracts the student's email and fullname,
        and provides a unique id for the user.

        Returns a dictionary with keys 'EMAIL', 'FULLNAME', and 'EDX_ID'.
        """
        fake_user = FakeUser(svalue['user_id'], svalue['user__username'], lambda: True)

        entry = {
            'EMAIL': svalue['user__email'],
            'FULLNAME': svalue['name'].title(),
            'EDX_ID': unique_id_for_user(fake_user)
        }

        return entry

    fields = 'user__email', 'name', 'user_id', 'user__username'
    values = students.values(*fields)

    # TODO: Since `students` is a QuerySet, can we chain a filter here that would be more
    # performant than calling a lambda for every user?
    exclude_func = lambda s: s['user__email'] in exclude
    return [make(s) for s in values if not exclude_func(s)]


def get_enrolled_students(course_id):
    """
    Given a course_id, returns a QuerySet of all the active students
    in the course.
    """
    objects = UserProfile.objects
    course_key = CourseKey.from_string(course_id)
    students = objects.filter(user__courseenrollment__course_id=course_key,
                              user__courseenrollment__is_active=True)
    return students


def get_subscribed(mailchimp, list_id):
    """Returns a set of email addresses subscribed to `list_id`"""
    return get_members(mailchimp, list_id, 'subscribed')


def get_unsubscribed(mailchimp, list_id):
    """Returns a set of email addresses that have unsubscribed from `list_id`"""
    return get_members(mailchimp, list_id, 'unsubscribed')


def get_cleaned(mailchimp, list_id):
    """
    Returns a set of email addresses that have been cleaned from `list_id`

    These email addresses may be invalid or have caused bounces, so you don't want
    to re-add them back to the list.
    """
    return get_members(mailchimp, list_id, 'cleaned')


def get_members(mailchimp, list_id, status):
    """
    Given a mailchimp list id and a user status to filter on, returns all
    members of the mailchimp list with that status.

    Returns a set of email addresses.
    """
    mc_get_members = mailchimp.listMembers
    members = set()

    for page in itertools.count():
        response = mc_get_members(id=list_id,
                                  status=status,
                                  start=page,
                                  limit=BATCH_SIZE)
        data = response.get('data', [])

        if not data:
            break

        members.update(d['email'] for d in data)

    return members


def unsubscribe(mailchimp, list_id, emails):
    """
    Batch unsubscribe the given email addresses from the list represented
    by `list_id`
    """
    batch_unsubscribe = mailchimp.listBatchUnsubscribe
    result = batch_unsubscribe(id=list_id,
                               emails=emails,
                               send_goodbye=False,
                               delete_member=False)
    log.debug(result)


def update_merge_tags(mailchimp, list_id, tag_names):
    """
    This function is rather inscrutable. Given tag_names, which
    in this code seems to be a list of ['FULLNAME', 'EMAIL', 'EDX_ID'],
    we grab tags from the mailchimp list, then we verify tag_names has
    'FULLNAME' and 'EMAIL' present, we get more data from mailchimp, then
    sync the variables up to mailchimp using `listMergeVarAdd`.

    The purpose of this function is unclear.
    """
    mc_vars = mailchimp.listMergeVars(id=list_id)
    mc_names = {v['name'] for v in mc_vars}

    mc_merge = mailchimp.listMergeVarAdd

    tags = [v['tag'] for v in mc_vars]

    for name in tag_names:
        tag = name_to_tag(name)

        # verify FULLNAME is present
        # TODO: Why is this under the for loop? It does nothing with the loop
        # variable and seems like things would work if this was executed before or
        # after the loop.
        if 'FULLNAME' not in tags:
            result = mc_merge(id=list_id,
                              tag='FULLNAME',
                              name='Full Name',
                              options={'field_type': 'text',
                                       'public': False})
            tags.append('FULLNAME')
            log.debug(result)

        # add extra tags if not present
        if name not in mc_names and tag not in ['EMAIL', 'FULLNAME']:
            ftype = FIELD_TYPES.get(name, 'number')
            result = mc_merge(id=list_id,
                              tag=tag,
                              name=name,
                              options={'field_type': ftype,
                                       'public': False})
            tags.append(tag)
            log.debug(result)


def subscribe_with_data(mailchimp, list_id, user_data):
    """
    Given user_data in the form of a list of dictionaries for each user,
    where the dictionary has keys 'EMAIL', 'FULLNAME', and 'EDX_ID', batch
    subscribe the users to the given `list_id` via a Mailchimp api method.

    Returns None
    """
    format_entry = lambda e: {name_to_tag(k): v for k, v in e.items()}
    formated_data = list(format_entry(e) for e in user_data)

    # send the updates in batches of a fixed size
    for batch in chunk(formated_data, SUBSCRIBE_BATCH_SIZE):
        result = mailchimp.listBatchSubscribe(id=list_id,
                                              batch=batch,
                                              double_optin=False,
                                              update_existing=True)

        log.debug(
            "Added: %s Error on: %s", result['add_count'], result['error_count']
        )


def make_segments(mailchimp, list_id, count, emails):
    """
    Segments the list of email addresses `emails` into `count` segments,
    if count is nonzero.

    For unknown historical reasons, lost to the winds of time, this is done with
    a random order to the email addresses.

    First, existing 'random_' mailchimp segments are deleted.

    Then, the list of emails (the whole, large list) is shuffled.

    Finally, the shuffled emails are chunked into `count` segments and re-uploaded
    to mailchimp as 'random_'-prefixed segments.
    """
    if count > 0:
        # reset segments
        segments = mailchimp.listStaticSegments(id=list_id)
        for seg in segments:
            if seg['name'].startswith('random'):
                mailchimp.listStaticSegmentDel(id=list_id, seg_id=seg['id'])

        # shuffle and split emails
        emails = list(emails)
        random.shuffle(emails)  # Why do we do this?

        chunk_size = int(math.ceil(float(len(emails)) / count))
        chunks = list(chunk(emails, chunk_size))

        # create segments and add emails
        for seg in range(count):
            name = f'random_{seg:002}'
            seg_id = mailchimp.listStaticSegmentAdd(id=list_id, name=name)
            for batch in chunk(chunks[seg], BATCH_SIZE):
                mailchimp.listStaticSegmentMembersAdd(
                    id=list_id,
                    seg_id=seg_id,
                    batch=batch
                )


def name_to_tag(name):
    """
    Returns sanitized str `name`: no more than 10 characters,
    with spaces replaced with `_`
    """
    if len(name) > 10:
        name = name[:10]
    return name.replace(' ', '_').strip()


def chunk(elist, size):
    """
    Generator. Yields a list of size `size` of the given list `elist`,
    or a shorter list if at the end of the input.
    """
    for i in range(0, len(elist), size):
        yield elist[i:i + size]
