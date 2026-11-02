edx-sandbox: a Python environment for sandboxed execution with CodeJail
#######################################################################

The requirements in this directory describe a Python environment separate from
the general edx-platform environment. When correctly configured with
`CodeJail <https://github.com/openedx/codejail>`_, edx-platform can use
it to execute untrusted code, particularly instructor-authored Python code
within ``<script type="loncapa/python">`` ProblemBlock tags.

Files in this directory
***********************

base.in
=======

This is the current set of requirements or the edx-sandbox environment, and it
is used to generate the ``.txt`` files described below. These requirements
share some constraints with the general edx-platform requirements (via
``../constraints.txt``), but otherwise, they are completely separate.

Installing the edx-sandbox environment from this file is **unsupported** and
**unstable**, because the packages are not pinned.

base.txt
========

These are the latest requirement pins for edx-sandbox. They are regularly
updated with the latest compatible versions of each package.

Installing the edx-sandbox environment from this file is **supported** yet
**unstable**. Breaking package upgrades and Python langugae upgrades will
regularly land directly in base.txt.

releases/
=========

Starting with Quince, every named Open edX release adds one of these files.
They contain the requirement pins corresponding to ``base.txt`` at the time of
each release.

Installing the edx-sandbox environment from the *latest* release file is
**supported** and **stable**. Installing the edx-sandbox environment from
*older* release files is **unsupported** yet **stable**.

When migrating from one release file to a newer one, be aware of which Python
versions are supported as well as breaking changes in newer packages versions.
You may need to edit the instructor-authored Python code in your platform in
order for it to remain compatible. The edx-platform maintenance team will do their
best to make note of these changes below and in the Open edX release notes.

releases/quince.txt
-------------------

* Frozen between the Quince and Redwood releases
* Supports only Python 3.8

releases/redwood.txt
--------------------

* Frozen at the time of the Redwood release
* Supports Python 3.8 and Python 3.11
* BREAKING CHANGE: SciPy is upgraded from 1.7.3 to 1.10.1 (`SciPy changelog`_)
* BREAKING CHANGE: NumPy is upgraded from 1.22.4 to 1.24.4
  (`NumPy changelog`_)
* These upgrades prepare edx-sandbox for the Python 3.12 update in Sumac.

releases/sumac.txt
------------------

* Frozen at the time of the Sumac release
* BREAKING CHANGE: Drops support for Python 3.8 (`Python changelog`_)
* Supports Python 3.11 and Python 3.12

.. _Python changelog: https://docs.python.org/3.11/whatsnew/changelog.html
.. _SciPy changelog: https://docs.scipy.org/doc/scipy/release.html
.. _NumPy changelog: https://numpy.org/doc/stable/release.html

releases/teak.txt
------------------

* Frozen at the time of the Teak release
* Supports Python 3.11 and Python 3.12
* SciPy is upgraded from 1.14.1 to 1.15.2

.. _SciPy changelog: https://docs.scipy.org/doc/scipy/release.html

releases/ulmo.txt
------------------

* Frozen at the time of the Ulmo release
* Supports Python 3.11 and Python 3.12
* SciPy is upgraded from 1.15.2 to 1.16.3

.. _SciPy changelog: https://docs.scipy.org/doc/scipy/release.html
