User Retirement Scripts
=======================

`This <https://github.com/openedx/edx-platform/tree/master/scripts/user_retirement>`_ directory contains python scripts which are migrated from the `tubular <https://github.com/openedx/tubular/tree/master/scripts>`_ respository.
These scripts are intended to drive the user retirement workflow which involves handling the deactivation or removal of user accounts as part of the platform's management process.

These scripts could be called from any automation/CD framework.

How to run the scripts
======================

Download the Scripts
--------------------

To download the scripts, you can perform a partial clone of the edx-platform repository to obtain only the required scripts. The following steps demonstrate how to achieve this. Alternatively, you may choose other utilities or libraries for the partial clone.

.. code-block:: bash

    repo_url=git@github.com:openedx/edx-platform.git
    branch=master
    directory=scripts/user_retirement

    git clone --branch $branch --single-branch --depth=1 --filter=tree:0 $repo_url
    cd edx-platform
    git sparse-checkout init --cone
    git sparse-checkout set $directory

Install Dependencies
--------------------

With `uv <https://docs.astral.sh/uv/>`_ (recommended)::

    uv sync --project scripts/user_retirement --frozen

Prefix commands below with ``uv run --project scripts/user_retirement`` to
run them in that environment, or activate it directly with
``source scripts/user_retirement/.venv/bin/activate``.

Without uv, create a virtual environment using Python 3.12 and install from
the compatibility export instead::

    python3.12 -m venv ../venv
    source ../venv/bin/activate
    pip install -r scripts/user_retirement/requirements/base.txt

In-depth Documentation and Configuration Steps
----------------------------------------------

For in-depth documentation and essential configurations follow these docs

`Documentation <https://docs.openedx.org/projects/edx-platform/en/latest/references/docs/scripts/user_retirement/docs/index.html>`_

`Configuration Docs <https://docs.openedx.org/projects/edx-platform/en/latest/references/docs/scripts/user_retirement/docs/driver_setup.html>`_


Execute Script
--------------

Execute the following shell command to establish entry points for the scripts

.. code-block:: bash

    chmod +x scripts/user_retirement/entry_points.sh
    source scripts/user_retirement/entry_points.sh

To retire a specific learner, you can use the provided example script:

.. code-block:: bash

    retire_one_learner.py \
    --config_file=src/config.yml \
    --username=user1

Make sure to replace ``src/config.yml`` with the actual path to your configuration file and ``user1`` with the actual username.

You can also execute Python scripts directly using the file path:

.. code-block:: bash

    python scripts/user_retirement/retire_one_learner.py \
    --config_file=src/config.yml \
    --username=user1

Feel free to customize these steps according to your specific environment and requirements.

Run Test Cases
==============

With uv::

    uv run --project scripts/user_retirement --group test pytest scripts/user_retirement

Without uv, install the testing requirements and run pytest directly::

    pip install -r scripts/user_retirement/requirements/testing.txt
    pytest scripts/user_retirement
