"""
Mock implementation of the Learner Home.
Returns statically authored JSON data
"""
# pylint: disable=line-too-long

import json
from os import path

from rest_framework.generics import RetrieveAPIView
from rest_framework.response import Response

LEARNER_HOME_DIR = "/edx/app/edxapp/edx-platform/lms/djangoapps/learner_home/mock"
MOCK_DATA_FILE = "mock_data.json"


class InitializeView(RetrieveAPIView):
    """Returns static JSON authored in MOCK_DATA_FILE"""

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        with open(path.join(LEARNER_HOME_DIR, MOCK_DATA_FILE), "r") as mock_data_file:

            # Edit me to change response data
            mock_data = json.load(mock_data_file)
        return Response(mock_data)
