How to write a celery task
==========================
.. contents::

Introduction
------------

With the celery 4.4 upgrade, routing of celery tasks was broken and all the tasks were routed to the default queues of lms and cms. So we investigated and found that celery changed the implementation style of a custom router. Previously celery needed a class based custom router and in 4.0 they introduced a new Task Router API, in which celery needs a function for routing tasks to desired queues. Hence, we updated the routing architecture of edx-platform.

``routing_key`` argument in ``@task`` decorator
-----------------------------------------------

Another change in celery’s newer version was that now adding ``routing_key`` as parameter in ``@task`` decorator stopped routing task to the matching queue with that key. This change was not included in their docs. Hence we opted for newer architecture for routing.

Dictionaries For Routing
------------------------

We have added dictionaries ``EXPLICIT_QUEUES`` in django settings for both lms and cms. If you want to route your task in any queue other than the default queue, you have to mention in that dict. For entering a new task in that dictionary you have to follow the pattern given below.
Now there is no need to mention ``routing_key`` argument in ``@task`` decorator


.. code-block::

    'lms.djangoapps.grades.tasks.compute_all_grades_for_course': {
    'queue': POLICY_CHANGE_GRADES_ROUTING_KEY},

Note: If a task has added into ``EXPLICIT_QUEUES`` dict in LMS then it will be routed only if it is triggered in LMS environment. If you want to trigger it from CMS environment too then you have to add to CMS’s ``EXPLICIT_QUEUES`` too.

