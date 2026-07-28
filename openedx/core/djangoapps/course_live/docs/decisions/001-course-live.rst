Course Live Plugin
_______________

Status
======
Accepted

Context
=======

The new `Course Authoring MFE`_ includes a new UX called "Pages and Resources"
for configuring different aspects of the course experience such as progress,
wiki, teams, discussions, etc. all from one place.

This ADR describes the addition of another course app named ``Live``. The
Live app will be used to configure a video conferencing tool for the course.
The will be available as a new Tab in the course experience.
The tool will be added as an LTI integration. We will currently support only
``Zoom`` as a video conferencing provider. But other providers can also be
added later.


.. _Course Authoring MFE: https://github.com/openedx/frontend-app-course-authoring/


Decision
========

We proposed to add the course live as a plugin with the following structure

Course Live App Plugin Class
----------------------------

The app will be loaded as a plugin and added to ``pyproject.toml`` with the namespace
``openedx.course_app``.

.. code-block :: python

    class CourseLiveApp:
        app_id: str = 'live'
        name: str = 'Live'
        description: str = 'Enable in-platform video conferencing by
                            configuring live.'

        @classmethod
        def is_available(cls, course_key):
            # The app will be available for everyone
            # We will initially keep the availability behind a Waffle flag
            return True

        @classmethod
        def is_enabled(cls, course_key):
            # This will be controlled by the CourseLiveConfiguration model
            return True

        @classmethod
        def set_enabled(cls, course_key, user, enabled):
            # This will be controlled by the CourseLiveConfiguration model
            return enabled

        @classmethod
        def get_allowed_operations(cls, course_key, user):
            # Can only enable live for a course if live is configured.
            return {
                'enable': Only if configuration exists,
                'configure': True
            }
