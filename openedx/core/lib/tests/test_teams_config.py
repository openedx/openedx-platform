"""
Tests for Course Teams configuration.
"""


import ddt
from django.test import TestCase

from ..teams_config import DEFAULT_COURSE_RUN_MAX_TEAM_SIZE, MANAGED_TEAM_MAX_TEAM_SIZE, TeamsConfig, TeamsetConfig


@ddt.ddt
class TeamsConfigTests(TestCase):
    """
    Test cases for `TeamsConfig` functions.
    """
    @ddt.data(
        None,
        "not-a-dict",
        {},
        {"max_team_size": 5},
        {"team_sets": []},
        {"team_sets": "not-a-list"},
        {"team_sets": ["not-a-dict"]},
        {"topics": None, "random_key": 88},
        {
            "max_team_size": 5,
            "topics": [
                {
                    "id": "bananas",
                    "max_team_size": 10,
                    "type": "private_managed",
                },
                {
                    "id": "apples",
                    "max_team_size": 6,
                    "type": "open",
                },
            ],
            "enabled": False
        }
    )
    def test_disabled_team_configs(self, data):
        """
        Test that configuration that doesn't specify any valid team-sets
        or specifically defines enabled as False is considered disabled
        """
        teams_config = TeamsConfig(data)
        assert not teams_config.is_enabled

    INPUT_DATA_1 = {
        "max_team_size": 5,
        "topics": [
            {
                "id": "bananas",
                "max_team_size": 10,
                "type": "private_managed",
            },
            {
                "id": "bokonism",
                "name": "BOKONISM",
                "description": "Busy busy busy",
                "type": "open",
                "max_team_size": 2,
            },
            {
                # Clusters with duplicate IDs should be dropped.
                "id": "bananas",
                "name": "All about Bananas",
                "description": "Not to be confused with bandanas",
            },

        ],
    }

    OUTPUT_DATA_1 = {
        "enabled": True,
        "max_team_size": 5,
        "team_sets": [
            {
                "id": "bananas",
                "name": "bananas",
                "description": "",
                "max_team_size": 10,
                "type": "private_managed",
                "user_partition_id": None,
            },
            {
                "id": "bokonism",
                "name": "BOKONISM",
                "description": "Busy busy busy",
                "max_team_size": 2,
                "type": "open",
                "user_partition_id": None,
            },
        ]
    }

    INPUT_DATA_2 = {
        "team_sets": [
            {
                # Team-set should be dropped due to lack of ID.
                "name": "Assignment about existence",
            },
            {
                # Team-set should be dropped due to invalid ID.
                "id": ["not", "a", "string"],
                "name": "Assignment about strings",
            },
            {
                # Team-set should be dropped due to invalid ID.
                "id": "the character & cannot be in an ID",
                "name": "Assignment about ampersands",
            },
            {
                # All fields invalid except ID;
                # Team-set will exist but have all fallbacks.
                "id": "_1. How quickly daft-jumping zebras vex",
                "name": {"assignment", "about", "zebras"},
                "description": object(),
                "max_team_size": -1000,
                "type": "matrix",
                "extra_key": "Should be ignored",
            },
            [
                # Team-set should be dropped because it's not a dict.
                "this", "isn't", "a", "valid", "team-set"
            ],
        ],
    }

    OUTPUT_DATA_2 = {
        "enabled": True,
        "max_team_size": DEFAULT_COURSE_RUN_MAX_TEAM_SIZE,
        "team_sets": [
            {
                "id": "_1. How quickly daft-jumping zebras vex",
                "name": "_1. How quickly daft-jumping zebras vex",
                "description": "",
                "max_team_size": None,
                "type": "open",
                "user_partition_id": None,
            },
        ],
    }
    INPUT_DATA_3 = {}
    OUTPUT_DATA_3 = {
        # When starting with a default blank config, there are no teamsets configured, and as such, teamsets is
        # disabled, so after processing the config the "enabled" field should be set to False.
        "enabled": False,
        "max_team_size": DEFAULT_COURSE_RUN_MAX_TEAM_SIZE,
        "team_sets": [],
    }
    INPUT_DATA_4 = {
        "team_sets": [dict(id="test-teamset", name="test", description="test")]
    }
    OUTPUT_DATA_4 = {
        # If teamsets are provided, but a value for "enabled" isn't, then the presence of teamsets indicates that
        # teams should be considered enabled, and the "enabled" field should be set to True.
        "enabled": True,
        "max_team_size": DEFAULT_COURSE_RUN_MAX_TEAM_SIZE,
        "team_sets": [
            dict(
                id="test-teamset",
                name="test",
                description="test",
                type="open",
                max_team_size=None,
                user_partition_id=None
            ),
        ],
    }

    @ddt.data(
        (INPUT_DATA_1, OUTPUT_DATA_1),
        (INPUT_DATA_2, OUTPUT_DATA_2),
        (INPUT_DATA_3, OUTPUT_DATA_3),
        (INPUT_DATA_4, OUTPUT_DATA_4),
    )
    @ddt.unpack
    def test_teams_config_round_trip(self, input_data, expected_output_data):
        """
        Test that when we load some config data,
        it is cleaned in the way we expect it to be.
        """
        teams_config = TeamsConfig(input_data)
        actual_output_data = teams_config.cleaned_data
        self.assertDictEqual(actual_output_data, expected_output_data)  # noqa: PT009

    @ddt.data(
        (None, None, "open", DEFAULT_COURSE_RUN_MAX_TEAM_SIZE),
        (None, None, "public_managed", MANAGED_TEAM_MAX_TEAM_SIZE),
        (None, 6666, "open", 6666),
        (None, 6666, "public_managed", MANAGED_TEAM_MAX_TEAM_SIZE),
        (1812, None, "open", 1812),
        (1812, None, "public_managed", MANAGED_TEAM_MAX_TEAM_SIZE),
        (1812, 6666, "open", 6666),
        (1812, 6666, "public_managed", MANAGED_TEAM_MAX_TEAM_SIZE),
    )
    @ddt.unpack
    def test_calc_max_team_size(
            self,
            course_run_max_team_size,
            teamset_max_team_size,
            teamset_type,
            expected_max_team_size,
    ):
        """
        Test that a team set's max team size is calculated as expected.
        """
        teamset_data = {"id": "teamset-1", "name": "Team size testing team-set"}
        teamset_data["max_team_size"] = teamset_max_team_size
        teamset_data["type"] = teamset_type
        config_data = {
            "max_team_size": course_run_max_team_size,
            "team_sets": [teamset_data],
        }
        config = TeamsConfig(config_data)
        assert config.calc_max_team_size("teamset-1") == expected_max_team_size

    def test_teams_config_string(self):
        """
        Assert that teams configs can be reasonably stringified.
        """
        config = TeamsConfig({})
        assert str(config) == "Teams configuration for 0 team-sets"

    def test_teamset_config_string(self):
        """
        Assert that team-set configs can be reasonably stringified.
        """
        config = TeamsetConfig({"id": "omlette-du-fromage"})
        assert str(config) == "omlette-du-fromage"

    def test_teams_config_repr(self):
        """
        Assert that the developer-friendly repr isn't broken.
        """
        config = TeamsConfig({"team_sets": [{"id": "hedgehogs"}], "max_team_size": 987})
        config_repr = repr(config)
        assert isinstance(config_repr, str)

        # When repr() fails, it doesn't always throw an exception.
        # Instead, it puts error messages in the repr.
        assert 'Error' not in config_repr

        # Instead of checking the specific string,
        # just make sure important info is there.
        assert 'TeamsetConfig' in config_repr
        assert 'TeamsConfig' in config_repr
        assert '987' in config_repr
        assert 'open' in config_repr
        assert 'hedgehogs' in config_repr

    def test_teamset_int_id(self):
        """
        Assert integer teaset IDs are treated as strings,
        for backwards compatibility.
        """
        teamset = TeamsetConfig({"id": 5})
        assert teamset.teamset_id == "5"
