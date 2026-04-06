"""
Tests for the advanced settings
"""

import unittest

import ddt

from cms.djangoapps.models.settings.course_metadata import CourseMetadata

working_config_block = {
    "teams_configuration": {
        "value": {
            "max_team_size": 4,
            "topics": [
                {
                    "max_team_size": 5,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                },
                {
                    "id": "open_managed_topic_1_id",
                    "type": "open_managed",
                    "description": "Open Managed Topic 1 desc",
                    "name": "Open Managed Topic 1 Name"
                }
            ]
        }
    }
}

config_block_negative_team_size = {
    "teams_configuration": {
        "value": {
            "max_team_size": -1,
            "topics": [
                {
                    "max_team_size": 5,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_negative_local_team_size = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "topics": [
                {
                    "max_team_size": -4,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_duplicate_id = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "topics": [
                {
                    "max_team_size": 4,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_1_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_negative_team_size_dupe_id = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "topics": [
                {
                    "max_team_size": -4,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_missing_name = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "topics": [
                {
                    "max_team_size": 4,
                    "name": "",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_extra_attribute = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "topics": [
                {
                    "max_team_size": 4,
                    "name": "Topic 1 name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed",
                    "foo": "bar"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_unrecognized_teamset_type = {
    "teams_configuration": {
        "value": {
            "max_team_size": 2,
            "team_sets": [
                {
                    "max_team_size": 4,
                    "name": "Topic 1 name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "foo",
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_no_global_max_team_size = {
    "teams_configuration": {
        "value": {
            "topics": [
                {
                    "max_team_size": 5,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
                {
                    "id": "topic_2_id",
                    "name": "Topic 2 Name",
                    "description": "Topic 2 desc"
                },
                {
                    "id": "topic_3_id",
                    "name": "Topic 3 Name",
                    "description": "Topic 3 desc"
                },
                {
                    "id": "private_topic_1_id",
                    "type": "private_managed",
                    "description": "Private Topic 1 desc",
                    "name": "Private Topic 1 Name"
                },
                {
                    "id": "private_topic_2_id",
                    "type": "private_managed",
                    "description": "Private Topic 2 desc",
                    "name": "Private Topic 2 Name"
                }
            ]
        }
    }
}

config_block_course_max_team_size = {
    "teams_configuration": {
        "value": {
            "max_team_size": 501,
            "topics": [
                {
                    "max_team_size": 500,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
            ]
        }
    }
}

config_block_teamset_max_team_size = {
    "teams_configuration": {
        "value": {
            "max_team_size": 500,
            "topics": [
                {
                    "max_team_size": 501,
                    "name": "Topic 1 Name",
                    "id": "topic_1_id",
                    "description": "Topic 1 desc",
                    "type": "public_managed"
                },
            ]
        }
    }
}


@ddt.ddt
class TeamsConfigurationTests(unittest.TestCase):
    """
    Test class for advanced settings of teams
    """

    @ddt.data(
        (working_config_block, set()),
        (config_block_negative_team_size, {'max_team_size must be greater than zero'}),
        (config_block_negative_local_team_size, {'max_team_size must be greater than zero'}),
        (config_block_duplicate_id, {'duplicate ids: topic_1_id'}),
        (
            config_block_negative_team_size_dupe_id,
            {'duplicate ids: topic_2_id', 'max_team_size must be greater than zero'}
        ),
        (config_block_missing_name, {'name attribute must not be empty'}),
        (config_block_extra_attribute, {'extra keys: foo'}),
        (config_block_unrecognized_teamset_type, {'type foo is invalid'}),
        (config_block_no_global_max_team_size, set()),
        (config_block_course_max_team_size, {'max_team_size cannot be greater than 500'}),
        (config_block_teamset_max_team_size, {'max_team_size cannot be greater than 500'})
    )
    @ddt.unpack
    def test_team_settings(self, config_block, error_message):
        result = CourseMetadata.validate_team_settings(config_block)
        self.assertEqual(len(result), len(error_message))  # noqa: PT009
        if len(error_message) > 0:
            for res in result:
                self.assertIn(res['message'], error_message)  # noqa: PT009
