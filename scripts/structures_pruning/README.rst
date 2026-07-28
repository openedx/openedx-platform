Structures Pruning Scripts
==========================

`This <https://github.com/openedx/edx-platform/tree/master/scripts/structures_pruning>`_ directory contains mongo db structures pruning script that is migrated from the
`tubular <https://github.com/openedx/tubular>`_ repository.


This script could be called from any automation/CD framework.

How to run the scripts
======================

Download the Scripts
--------------------

To download the scripts, you can perform a partial clone of the edx-platform repository to obtain only the required scripts. The following steps demonstrate how to achieve this. Alternatively, you may choose other utilities or libraries for the partial clone.

.. code-block:: bash

    repo_url=git@github.com:openedx/edx-platform.git
    branch=master
    directory=scripts/structures_pruning

    git clone --branch $branch --single-branch --depth=1 --filter=tree:0 $repo_url
    cd edx-platform
    git sparse-checkout init --cone
    git sparse-checkout set $directory

Create Python Virtual Environment
---------------------------------

Create a Python virtual environment using Python 3.12:

.. code-block:: bash

    python3.12 -m venv ../venv
    source ../venv/bin/activate

Install Pip Packages
--------------------

Install the required pip packages using the provided requirements file:

.. code-block:: bash

    pip install -r scripts/structures_pruning/requirements/base.txt


Execute Script
--------------

You can simply execute Python scripts with python command

.. code-block:: bash

    python scripts/structures_pruning/structures.py prune plan_file.json

Feel free to customize these steps according to your specific environment and requirements.

Run Test Cases
==============

Before running test cases, install the testing requirements:

.. code-block:: bash

    pip install -r scripts/structures_pruning/requirements/testing.txt

Run the test cases using pytest:

.. code-block:: bash

    pytest scripts/structures_pruning
