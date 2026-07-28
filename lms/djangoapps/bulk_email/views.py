"""
Views to support bulk email functionalities like opt-out.
"""


import logging

from django.contrib.auth.models import User  # pylint: disable=imported-auth-user
from django.http import Http404
from eventtracking import tracker
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

from common.djangoapps.edxmako.shortcuts import render_to_response
from lms.djangoapps.bulk_email.models import Optout
from lms.djangoapps.discussion.notification_prefs.views import UsernameCipher, UsernameDecryptionException
from openedx.core.lib.courses import get_course_by_id

log = logging.getLogger(__name__)


def opt_out_email_updates(request, token, course_id):
    """
    A view that let users opt out of any email updates.

    This meant is meant to be the target of an opt-out link or button.
    The `token` parameter must decrypt to a valid username.
    The `course_id` is the string course key of any course.

    Raises a 404 if there are any errors parsing the input.
    """
    try:
        username = UsernameCipher().decrypt(token).decode("utf-8")
        user = User.objects.get(username=username)
        course_key = CourseKey.from_string(course_id)
        course = get_course_by_id(course_key, depth=0)
    except UnicodeDecodeError:
        raise Http404("base64url")  # pylint: disable=raise-missing-from  # noqa: B904
    except UsernameDecryptionException as exn:
        raise Http404(str(exn))  # pylint: disable=raise-missing-from  # noqa: B904
    except User.DoesNotExist:
        raise Http404("username")  # pylint: disable=raise-missing-from  # noqa: B904
    except InvalidKeyError:
        raise Http404("course")  # pylint: disable=raise-missing-from  # noqa: B904

    unsub_check = request.POST.get('unsubscribe', False)
    context = {
        'course': course,
        'unsubscribe': unsub_check
    }

    if request.method == 'GET':
        return render_to_response('bulk_email/confirm_unsubscribe.html', context)

    if request.method == 'POST' and unsub_check:
        Optout.objects.get_or_create(user=user, course_id=course_key)
        log.info(
            "User %s (%s) opted out of receiving emails from course %s",
            user.username,
            user.email,
            course_id,
        )

    event_name = 'edx.bulk_email.opt_out'
    event_data = {
        "username": user.username,
        "user_email": user.email,
        "user_id": user.id,
        "course_id": course_id,
    }
    with tracker.get_tracker().context(event_name, event_data):
        tracker.emit(
            event_name,
            event_data
        )

    return render_to_response('bulk_email/unsubscribe_success.html', context)
