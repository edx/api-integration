api-integration
===============

Notice: This repo will be archived in April 2021.
#######

api-integration (``edx_solutions_api_integration``) is a Django application that provides a RESTful interface to the edx-platform.


Open edX Platform Integration
-----------------------------
1. Update the version of ``api-integration`` in the appropriate requirements file (e.g. ``requirements/edx/custom.txt``).
2. Add ``edx_solutions_api_integration`` to the list of installed apps in ``common.py``.
3. Install ``edx_solutions_api_integration`` app via requirements file.

.. code-block:: bash

  $ pip install -r requirements/edx/custom.txt

4. (Optional) Run tests:

.. code-block:: bash

   $ paver test_system -s lms -t edx_solutions_api_integration

