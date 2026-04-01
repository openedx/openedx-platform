Reported content email notifications for moderators
===================================================


Status
------

Proposal


Context
-------

We need to provide an option for quick visibility of reported content for
moderators. We plan to implement this by adding an option to send out an
email to discussion moderators when a content (post/response/comment) is
reported. This email will be sent out when a content is reported, and stays
reported for the next 2 minutes.

The email should be sent to all roles that have discussion moderation
privileges, namely Discussion Admin, Discussion Moderator, Community TA and
Group Community TA.

Group community TAs will only receive for reported content in their own
cohort.

A toggle for this setting will be added on the edX discussions configuration
page. This toggle can be turned on or off at any point of time,
even if the course run is already in progress.


Requirements
------------

We need to have the ability to enable/disable email notifications for reported
content for a course.

When a course content is reported the email needs to be sent only if the
content remains reported for the next 2 minutes.


Decision
--------

We need to have a toggle setting for courses to have the option to configure
the email notifications for moderators. To achieve this, we will add a new
field named `reported_content_email_notifications` to the
`CourseDiscussionSettings` model and the default value for the field will be
`False`. Course Team will have the option to toggle this through the
discussions configuration page using the discussion settings endpoint.

Furthermore, we also need to handle sending out the email notification to the
moderators if the content remains reported for 2 minutes. To handle this, we
will add a Celery task with a delay of 120 seconds. The task will first check
if the content is still in the reported state, and then send an email to
moderators.

We will not retry sending a failed email attempt. A successful or failed
attempt will be logged.

Emails will be sent out from edx@news.edx.org


Changes to CourseDiscussionSettings Model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
.. code-block::

    class CourseDiscussionSettings(models.Model):
        ...
        reported_content_email_notifications=models.BooleanField(default=False)


Endpoint to toggle the notifications settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
POST ``api/discussions/v0/course/{course_id}/settings``

.. code-block::

    {
        context_key: "{course_id}",
        ...
        plugin_configuration: {
            ...
            reported_content_email_notifications: true
        }
    }
