Ensure GET is Idempotent
========================

:Status: Accepted
:Date: 2026-03-31
:Deciders: API Working Group

Context
=======

Some Open edX endpoints use ``GET`` requests that mutate **domain state** as a
side-effect — for example, firing openedx-events, triggering Django signals, or
directly creating/updating transactional records (e.g. enrollments, first-access
markers). This violates REST safety/idempotency expectations and can break
caching/proxy behavior and automated clients/agents.

Note that not every write that occurs during a ``GET`` handler is a violation. Pure
analytics writes — such as emitting a ``tracker.emit`` event, recording a Segment
event, or incrementing a read counter in a dedicated analytics store — do *not*
mutate transactional domain state and are treated differently in this ADR (see
Decision below).

Decision
========

**Domain state** is defined as any data that lives in the transactional domain model
and drives application behavior — for example: enrollments, grades, user profile
fields, course access records, or any other database records that affect what a user
can see or do. Writes to domain state from a ``GET`` handler make the request
non-idempotent in ways that are difficult to audit and can cause unpredictable
behavior when responses are cached or requests are retried.

**Non-domain writes** — such as emitting pure analytics events (``tracker.emit``,
Segment), writing to a dedicated read-analytics store, or updating a read-count
counter — do *not* modify domain state. These are explicitly **permitted** inside
``GET`` handlers, subject to the constraint that the response content must not depend
on them and no openedx-events or Django signals are involved.

1. Treat ``GET`` as strictly read-only with respect to **domain state**: a ``GET`` handler
   must not create, update, or delete records that are part of the transactional domain model
   (e.g. enrollments, grades, user profile fields).
2. Move domain-state-mutating side-effects out of ``GET`` handlers:

   * **openedx-events and Django signals must not be fired from ``GET`` handlers.**
     These are the primary concern: signal receivers may perform writes, trigger
     downstream workflows, or update domain state in ways that are invisible to the
     ``GET`` handler itself.
   * Create explicit write endpoints (``POST``, ``PUT``, ``PATCH``) for state changes,
     including any side-effects that need to emit openedx-events or Django signals.
   * Simple telemetry writes to a **separate analytics store** (e.g. ``tracker.emit``,
     Segment events, read-count increments) are acceptable inside a ``GET`` handler
     **provided** the response content does not depend on them and no openedx-events
     or Django signals are involved. These writes do not need to be moved to
     async pipelines unless there is a specific performance or reliability reason to do so.

3. Add regression tests to ensure ``GET`` handlers do not modify domain state.
4. Document exceptions (if any) and provide migration notes for clients.

Relevance in edx-platform
=========================

* **openedx-events and Django signals on read**: The primary concern is ``GET``
  handlers that fire openedx-events (e.g. ``COURSE_ENROLLMENT_CREATED``,
  ``STUDENT_REGISTRATION_COMPLETED``) or Django signals (e.g. ``post_save``,
  ``m2m_changed``) as a side-effect. Receivers of these events/signals can trigger
  domain-state mutations that are invisible to the ``GET`` handler, making the
  request non-idempotent in ways that are difficult to audit. These must be moved
  to explicit write endpoints.
* **GET used with side-effects**: Various views use ``@require_GET`` while
  triggering writes (e.g. tracking, first-access, or logging). Discussion views
  (``lms/djangoapps/discussion/views.py``) use ``@require_GET`` for thread/topic
  listing; any implicit domain-state mutation on read should be moved to separate
  endpoints or async events.
* **Legacy analytics on read**: ``common/djangoapps/student`` and courseware code
  sometimes emit pure analytics events (e.g. ``tracker.emit``, streak updates) in
  code paths triggered by GET. Pure telemetry that does not affect domain state and
  does not involve openedx-events or Django signals may remain, but anything that
  can cause downstream domain writes must be decoupled.

Code example
============

**Anti-pattern (GET that fires an openedx-event):**

.. code-block:: python

   @require_GET
   def get_enrollment(request, course_id):
       # BAD: firing an openedx-event from a GET handler; receivers may
       # perform domain-state writes invisible to this handler.
       COURSE_ENROLLMENT_CHANGED.send_event(
           enrollment=EnrollmentData(user=request.user, course_key=course_id)
       )
       return JsonResponse(fetch_enrollment_data(...))

**Preferred: read-only GET + explicit write endpoint for state-changing events**

.. code-block:: python

   @require_GET
   def get_enrollment(request, course_id):
       # GOOD: pure read, no signals or openedx-events fired
       return Response(EnrollmentSerializer(fetch_enrollment_data(...)).data)

   @require_POST
   def track_enrollment_event(request, course_id):
       # Explicit write endpoint; openedx-event fired safely on POST
       COURSE_ENROLLMENT_CHANGED.send_event(
           enrollment=EnrollmentData(user=request.user, course_key=course_id)
       )
       return Response(status=204)

Consequences
============

* Pros

  * REST-compliant behavior; safer automated consumption (AI agents, integrations).
  * Predictable caching/proxy semantics.
  * Prevents unintended downstream side-effects from read operations (e.g. duplicate
    event emissions when a response is served from cache without hitting the handler).

* Cons / Costs

  * Requires refactoring legacy courseware/analytics endpoints that currently fire
    openedx-events or Django signals on read.
  * Potential behavior changes for internal systems that relied on implicit GET-triggered
    events or signals.

Implementation Notes
====================

* Inventory endpoints with GET side-effects, paying particular attention to those
  that fire openedx-events or Django signals.
* For each, define a read-only GET representation and a separate write/track endpoint
  (or async event emission) if needed.

References
==========

* "Non-Idempotent GET Requests" recommendation in the Open edX REST API standardization notes.
* `openedx-events <https://github.com/openedx/openedx-events>`_ — Open edX architectural events.
