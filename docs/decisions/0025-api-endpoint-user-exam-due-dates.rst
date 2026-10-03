ADR: API Endpoint for User Exam Due Dates in the Mobile App
###########################################################

Status
******

Proposed

Context
*******

To improve the Open edX mobile experience, we need to provide learners with a fast, accurate list of upcoming exam and assignment due dates across all of their enrolled courses. This is critical for enabling mobile-first learning workflows, proactive planning, and push notifications.

The current approach, which relies on ``get_course_date_blocks()`` and ``modulestore`` traversal, is inefficient, especially for multi-course scenarios. Furthermore, the Open edX community has committed to limiting ``modulestore`` usage in runtime systems (as per ADR 0011) and modularizing content and date management.

Decision
********

We will reuse and extend the ``edx-when`` application as the unified backend for both course-level and user-level due dates.

Key Implementation Details
==========================

* **Data Storage**:
    * Use ``edx-when.ContentDate`` to store canonical due dates published from course metadata.
    * Use ``edx-when.UserDate`` to store user-specific overrides, including personalized schedules (self-paced) and instructor-granted extensions.
* **Service Layer**: Implement a dedicated Python service layer within ``edx-when`` to resolve dates. This service will merge ``ContentDate`` and ``UserDate`` records and apply visibility filtering. This ensures the logic is reusable by the LMS, background tasks, and the REST API.
* **API Endpoint**: Create a new REST API endpoint (proposed at ``/api/edx_when/v1/user-dates/``) that returns a merged, filtered, and sorted list of upcoming due dates for the authenticated user across all enrollments.
* **Encapsulation**: The REST API will reside within ``edx-when`` to keep it co-located with the models, provided that it does not require circular dependencies on ``edx-platform``.

Scope Limitations
=================

* **CCX Courses**: Custom Courses for edX (CCX) are excluded from this implementation due to their unique override structure and management complexity.

Consequences
************

Positive
========

* **Performance**: Replaces slow runtime block traversal with indexed database queries.
* **Consistency**: Centralizes date management into a single canonical system for LMS, MFE, and Mobile.
* **Scalability**: Reduces the load on the ``modulestore`` and aligns with the platform's long-term goal of removing MongoDB dependencies.
* **Maintainability**: Avoids the creation of redundant data models for "exam dates" specifically.

Negative/Risks
==============

* **Dependency Management**: We must ensure ``edx-when`` does not import code from ``edx-platform`` to maintain clean encapsulation. If complex platform logic is required, a thin wrapper API may need to be hosted in ``edx-platform`` instead.

Rejected Alternatives
*********************

1. **Continue Using XBlock Metadata**
   * *Description*: Store all start, due, and grace period fields in ``modulestore``.
   * *Reason for Rejection*: No support for user-level overrides without course republishing; cannot support dynamic scheduling or extensions effectively.

2. **Use Only the 'Schedules' App**
   * *Description*: Centralize all date logic through ``schedules.Schedule``.
   * *Reason for Rejection*: Not designed for block-level (exam-specific) due dates; would require significant refactoring of upstream APIs.

3. **Compute Dates On-the-Fly at Runtime**
   * *Description*: Dynamically calculate due dates based on enrollment and pacing without persistence.
   * *Reason for Rejection*: Degrades performance at scale and provides no audit trail for extension history.

4. **Separate Microservice**
   * *Description*: Introduce a new service for exam dates.
   * *Reason for Rejection*: Adds unnecessary operational overhead and duplicates functionality already available in ``edx-when``.
