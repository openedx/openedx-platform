Monitoring by Code Owner
************************

Status
======

Superseded (DEPR: https://github.com/openedx/edx-django-utils/issues/469)

Context
=======

It is currently difficult for different teams to have team-based on-calls rotations, alerting and monitoring for various parts of the edx-platform (specifically LMS).

Decision
========

We will implement a custom attribute "code_owner" that can be used in NewRelic (or other monitoring solutions that are made pluggable).

The new custom attribute makes it simple to query NewRelic for all Transactions or TransactionErrors that are associated with requests with a specific owner.  This enables a team to quickly identify data that they own, for use in NewRelic alerts or NewRelic dashboards.

To minimize maintenance, the value of the "code_owner" attribute will be populated using the source-of-truth of ownership of various parts of edx-platform.

See `Rejected Alternatives`_ for details of the decision **not** to split the NewRelic application into multiple NewRelic applications.

Note: "owner" is a MySql reserved word, which NewRelic cautions against using, so we are using "code_owner".

Consequences
============

This attribute should be quickly available for use with custom alerts and custom dashboards.

In the future, this attribute could potentially be added to logging as well.

Rejected Alternatives
=====================

Splitting the NewRelic application
----------------------------------

The edx-platform (LMS) NewRelic application could have been split into multiple applications. This would have had the benefit of getting the out-of-the-box APM Dashboards for free.

We decided against this alternative because:

* To enable this solution, we would need to disable gunicorn instrumentation, and this instrumentation has proved to be valuable for understanding certain types of production issues.
* Splitting the app may make it more difficult to pinpoint the source of any problem that affects multiple applications.
* The application splitting depends on a slight "hack" from NewRelic, by resetting the application name for each path.

  * This "hack" goes against the grain of NewRelic's typical recommendations, so there could be unknown pitfalls.
  * The mapping of request path to owner would require additional maintenance.
  * Processing time is **not** accounted for in the NewRelic transaction time, because any processing required takes place before the NewRelic transaction gets started.
