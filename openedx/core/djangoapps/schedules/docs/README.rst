Dynamic Pacing Schedules
========================

The Schedules app allows course teams to automatically email learners in
self-paced courses. The emails are designed to keep learners engaged
with a course. Learners receive these messages at important milestones
in their learning.

The author of a self-paced course opts learners into one of two
“Schedule Experiences”. Learners either receive Weekly Course Highlight
Messages, or a combination of Recurring Nudges and Upgrade Reminders.

The app can send all three message types “out of the box”

-  Recurring Nudges
-  Upgrade Reminders
-  Weekly Course Highlight Messages

Recurring Nudges encourage learners to return to self-paced courses at
regular intervals. The app sends nudges three days and ten days after a
learner enrolls in a course.

Upgrade Reminders ask learners to purchase their course’s Verified
certificate. The reminders are sent two days before their course’s upgrade
deadline, or two days before the course’s end date (whichever date occurs
sooner).

Weekly Course Highlight Messages tell learners what to look forward to in the
coming week of a course. Course authors provide “section highlights” when
authoring a course in Studio. The app sends a weekly email with these
highlights listed in the message.

The app introduces a Schedule object to the edX codebase. Learners receive
Schedules when they enroll in self-paced courses. The app uses Schedules to
determine which learners to message.

In the future, the Schedules app may be extended or changed to support more
complicated communication patterns. For example, something that takes into
account the user's progress through the course.

Glossary
--------

-  **Schedule**: Stores the day a learner enrolls in a course and the
   learner's "upgrade deadline". This information allows us to personalize
   learners' experiences in self-paced courses so that events happen relative to
   the learner's schedule, not the course's schedule.

-  **Schedule Experience**: Defines the set of emails that a learner
   receives. The two Schedule Experiences are:

   -  “Recurring Nudge and Upgrade Reminder”
   -  “Course Updates”

-  **Upgrade Deadline**: The date before which a learner is encouraged to
   purchase a verified certificate. By default, a Schedule imposes a "soft"
   upgrade deadline (meaning, a suggested, but not final, date) 21 days from
   when a learner enrolled in a course. A self-paced course imposes a "hard"
   upgrade deadline that is the course-wide expiration date for upgrading on the
   course. A Schedule uses whichever date is earlier.

-  **Course Update**: We refer to "Weekly Course Highlight Messages" as "Course
   Updates" in the code. In contexts outside of this app, Course Updates refer
   to bulk-emails manually sent out by course instructors and for the blocks of
   text that course instructors can add to the top of the course outline. We
   plan on removing this term from this app's code to avoid confusion.

-  **Section**: From our
   `documentation <https://docs.openedx.org/en/latest/educators/concepts/open_edx_platform/about_course_sections.html>`__,
   “A section is the topmost category in your course. A section can
   represent a time period in your course, a chapter, or another
   organizing principle. A section contains one or more subsections.”
   For the purposes of Weekly Section Highlights Messages, we assume
   that a section contains a week’s worth of learning material.

-  **Weekly Section Highlights**: A list of topics that learners will
   encounter in a section of a course. Course authors enter section
   highlights in the Studio UI.

-  **Resolver**: A Python class that identifies which learners to
   message and sends them emails. We create a Resolver subclass to
   manage each message type. An “UpgradeReminderResolver” sends Upgrade
   Reminder messages, a “RecurringNudgeResolver” sends Recurring Nudge
   messages, and a “CourseUpdateResolver” sends Weekly Section Highlight
   Messages.

-  **MessageType**: A Python class that represents a kind of email
   message. It specifies the Django template the app uses the render the
   email. `MessageType is an ACE
   concept <https://edx-ace.readthedocs.io/en/latest/modules.html#edx_ace.message.MessageType>`__.

-  **Task**: A
   `Celery <http://docs.celeryproject.org/en/latest/index.html>`__
   asynchronous class/function that is run in a separate process from
   the main Django LMS process. In the app, a task is created to email
   groups of learners. We bin learners to distribute the amount of work
   each task performs. We email each bin's worth of learners in a task.

-  **Bin**: In the Schedules app, we divide the learners we are emailing
   into N “bins” (by default, N is 24). We do this to evenly distribute
   the number of emails each task must send.

-  **Email Backend**: An external service that ACE will use to deliver emails.
   For now, ACE only supports `Sailthru <http://www.sailthru.com/>`__ as an
   email backend.


An Overview of edX's Dynamic Pacing System
------------------------------------------

.. image:: images/system_diagram.png


Running the Management Commands
-------------------------------

There are three management commands in the Schedules app. Each command sends a
message type: ``send_recurring_nudge``; ``send_upgrade_reminder``; and
``send_course_update``.

You must specify the Site for which you are sending emails in the command:

::

    ./manage.py lms send_recurring_nudge example.com

Make sure to specify your development environment with the “settings”
flag. For example, if you are running a command in docker devstack, you
can use:

::

    ./manage.py lms --settings devstack send_recurring_nudge example.com

You can override the “current date” when running a command. The app will run,
using the date you specify as its "today":

::

    ./manage.py lms --settings devstack send_recurring_nudge example.com --date 2017-11-13

If the app is paired with Sailthru, you can override which email addresses the
app sends to. The app will send all emails to the address you specify:

::

    ./manage.py lms --settings devstack send_recurring_nudge example.com --override-recipient-email developer@example.com

These management commands are meant to be run daily. We schedule them to
run automatically in a Jenkins job. You can use a similar automation
tool, like “cron”, to schedule a daily run of the app.

Configuring A.C.E.
------------------

These instructions assume you have already setup an Open edX instance or
are running devstack. See the `Open edX Developer’s
Guide <https://docs.openedx.org/en/latest/developers/references/developer_guide/index.html>`__
for information on setting them up.

The Schedule app relies on ACE. When live, ACE sends emails to users
through Sailthru. You can instead configure ACE to write emails
to the local filesystem, which can be useful for debugging.

File Back-end
~~~~~~~~~~~~~

Edit the ``lms/envs/common.py`` or ``lms/envs/private.py``\ and
add/change the following:

.. code:: python

    ACE_CHANNEL_SAILTHRU_DEBUG = True

By default, your devstack should be configured to use the ``file_email``
ACE channel. This ACE channel saves the emails to
``/path/to/your/devstack/src/ace_messages/*.html`` on your host machine
(the host path corresponds to ``/edx/src/ace_messages/`` in your devstack docker
container). To view the emails, open the saved files in your browser.

Sailthru Back-end
~~~~~~~~~~~~~~~~~

To configure ACE to send emails to users’ email addresses, add a
`Sailthru <http://www.sailthru.com/>`__ back-end configuration. See the
`edx-ace
documentation <https://edx-ace.readthedocs.io/en/latest/getting_started.html#sailthruemailchannel-settings>`__
for instructions on setting up a Sailthru API key and secret.

Make sure to add the following settings in either ``lms/envs/common.py``
or ``lms/envs/private.py``:

.. code:: python

    ACE_CHANNEL_SAILTHRU_DEBUG = False
    ACE_ENABLED_CHANNEL = ['sailthru_email']
    ACE_ENABLED_POLICIES = ['bulk_email_optout']
    ACE_CHANNEL_SAILTHRU_TEMPLATE_NAME = '<insert_sailthru_template_name_here>'

Django Settings
---------------

These settings populate links in the emails to external
social media, marketing websites, app stores, etc.

Edit the ``lms/envs/common.py`` or ``lms/envs/private.py`` and
add/change the following:

.. code:: python

    MKTG_URLS = {
        'ROOT': '<insert_lms_url_here>',
    }
    SOCIAL_MEDIA_FOOTER_URLS = {
        'tumblr': '<insert_tumblr_url_here>',
        'reddit': '<insert_reddit_url_here>',
        'twitter': '<insert_twitter_url_here>',
        'google_plus': '<insert_google_plus_url_here>',
        'youtube': '<insert_youtube_url_here>',
        'linkedin': '<insert_linkedin_url_here>',
        'meetup': '<insert_meetup_url_here>',
        'facebook': '<insert_facebook_url_here>',
    }
    MOBILE_STORE_URLS = {
        'google': '<insert_play_store_url_here>',
        'apple': '<insert_app_store_url_here>',
    }
    CONTACT_MAILING_ADDRESS = '<insert_physical_address_here>'

Configuration Flags
-------------------

Configuring Schedule Creation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Configuring Upgrade Deadline on Schedule
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The upgrade reminder message type depends on there being a date in the
``upgrade_deadline`` field of the Schedule model. Up-sell messaging will
also be added to the recurring nudge and course updates message types
when an upgrade deadline date is present.

DynamicUpgradeDeadlineConfiguration models
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In order to enable filling in the ``upgrade_deadline`` field of new
Schedule models created, you must create and enable one of the
following:

-  A DynamicUpgradeDeadlineConfiguration toggles the feature for all
   courses globally.
-  A OrgDynamicUpgradeDeadlineConfiguration toggles the feature for all
   courses in a particular organization.
-  A CourseDynamicUpgradeDeadlineConfiguration toggles the feature for a
   particular course.

The CourseDynamicUpgradeDeadlineConfiguration takes precedence over the
OrgDynamicUpgradeDeadlineConfiguration which takes precedence over the
global DynamicUpgradeDeadlineConfiguration.

The “deadline days” field specifies how many days from the day of the
learner’s enrollment will be their soft upgrade deadline on the Schedule
model.

Verified Course Mode
^^^^^^^^^^^^^^^^^^^^

The ``upgrade_deadline`` will only be filled for a course if it has a
verified course mode. To add a verified course mode to a course, go to
``<lms_url>/admin/course_modes/coursemode/`` and add a course mode
linked with the course with the "Mode" equal to "verified".

Configuring Email Sending
~~~~~~~~~~~~~~~~~~~~~~~~~

.. scheduleconfig-1:

ScheduleConfig
^^^^^^^^^^^^^^

The ScheduleConfig model at
``<lms_url>/admin/schedules/scheduleconfig/`` also has fields which
configure enqueueing and delivering emails per message type:

-  ``enqueue_*``: allows sending email tasks of this message type to
   celery.
-  ``deliver_*``: allows delivering emails through ACE for this message
   type.

Configuring a Learner’s Schedule
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Emails will only be sent to learners who have Schedule ``start_date``\ s
or ``upgrade_deadline``\ s and ScheduleExperience that match the
criteria for the message type.

Recurring Nudge
^^^^^^^^^^^^^^^

-  Learners must have the ScheduleExperience type of "Recurring Nudge
   and Upgrade Reminder".
-  Their Schedule ``start_date`` must be 3 or 10 days before the current
   date.

Upgrade Reminder
^^^^^^^^^^^^^^^^

-  Learners must have the ScheduleExperience type of “Recurring Nudge
   and Upgrade Reminder”.
-  Their Schedule ``upgrade_deadline`` must be 2 days after the current
   date.

Course Update
^^^^^^^^^^^^^

-  Learners must have the ScheduleExperience type of “Course Updates”.
-  Their Schedule ``start_date`` must be 7, 14, or any increment of 7
   days up to 77 days before the current date.

Analytics
~~~~~~~~~

To track the performance of these communications, there is an integration setup
with Google Analytics and Segment. When a message is sent a Segment event is
emitted that contains the unique message identifier and a bunch of other data
about the message that was sent. When a user opens an email, an invisible
tracking pixel is rendered that records an event in Google Analytics. When a
user clicks a link in the email,
`UTM parameters <https://en.wikipedia.org/wiki/UTM_parameters>`__ are included
in the query string which allow Google Analytics to know that the traffic was
driven to the LMS by that email.

Using these three pieces of information you can track many key metrics.
Specifically: you can monitor the number of messages sent, the ratio of messages
opened to messages sent, and the ratio of links clicked in messages to the
messages opened. These help you answer a few key questions: How many people
am I reaching? How many people are opening my messages? How many people are
persuaded to actually come back to my site after reading my message?

You can also filter Google Analytics to compare the behavior of the users
coming to your platform from these emails relative to other sources of traffic.

Enabling Tracking
^^^^^^^^^^^^^^^^^

-  In either your site configuration or django settings set
   ``GOOGLE_ANALYTICS_TRACKING_ID`` to your Google Analytics tracking ID. This
   will look something like UA-XXXXXXX-X
-  In your django settings set ``LMS_SEGMENT_KEY`` to your Segment project
   write key.

Emitted Events
^^^^^^^^^^^^^^

The segment event that is emitted when a message is sent is named
"edx.bi.email.sent" and contains the following information:

-  ``send_uuid`` uniquely identifies this batch of emails that are being sent to
   many learners.
-  ``uuid`` uniquely identifies this particular message being sent to exactly
   one learner.
-  ``site`` is the site that the email was sent for.
-  ``app_label`` will always be "schedules" for the emails sent from here.
-  ``name`` will be the name of the message that was sent: recurringnudge_day3,
   recurringnudge_day10, upgradereminder, or courseupdate.
- ``primary_course_id`` identifies the primary course discussed in the email if
  the email was sent on behalf of several courses.
- ``language`` is the language the email was translated into.
- ``course_ids`` is a list of all courses that this email was sent on behalf of.
  This can be truncated if the list of courses is long.
- ``num_courses`` is the actual number of courses covered by this message. This
  may differ from the course_ids list if the list was truncated.

The Google Analytics event that is emitted when a learner opens an email has
the following properties:

-  ``action`` is "edx.bi.email.opened"
-  ``category`` is "email"
-  ``label`` is the primary_course_id described above
-  ``campaign source`` is "schedules"
-  ``campaign medium`` is "email"
-  ``campaign name`` is the name of the message that was sent:
   recurringnudge_day3, recurringnudge_day10, upgradereminder,
   or courseupdate.
-  ``campaign content`` is the unique identifier for the message

When the user clicks a link in the email the following UTM parameters are
included in the URL:

-  ``campaign source`` is "schedules"
-  ``campaign medium`` is "email"
-  ``campaign name`` is the name of the message that was sent:
   recurringnudge_day3, recurringnudge_day10, upgradereminder,
   or courseupdate.
-  ``campaign content`` is the unique identifier for the message
-  ``campaign term`` is the primary_course_id described above

Litmus
------

When designing email templates, it is important to test the rendered emails in a
variety of email clients to ensure that they render correctly. EdX uses a tool
called `Litmus <http://litmus.com/>`__ for this process.

To begin using Litmus, follow these steps:

1. Make sure that ACE is configured to use Sailthru (see instructions above).
2. Go to the `Litmus checklist page <https://litmus.com/checklist>`__ and start
   a new checklist.
3. The checklist will provide you with an email address to which you will send
   a test email.
4. Send an email. Use one of the management commands with the
   `--override-recipient-email` flag. Use the Litmus email you got in step 3
   as the flag value.

::

    ./manage.py lms --settings devstack send_recurring_nudge example.com --override-recipient-email PUT-LITMUS-ADDRESS-HERE

Using the Litmus Browser Extenstion to test emails saved as local files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Configure your devstack to use the "file_email" channel for ACE (see
   instructions above).
2. Install the Litmus `chrome browser extension
   <https://chrome.google.com/webstore/detail/litmus/makmhllelncgkglnpaipelogkekggpio>`__.
3. Send an email by running the management command. This should save the email
   to a file.
4. Open the saved file in chrome on your host. It should be in
   `/path/to/your/devstack/src/ace_messages/*.html`.
5. Open the Litmus extension.
6. When you regenerate emails, you can easily refresh the previews in Litmus.
