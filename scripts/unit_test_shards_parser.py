import argparse
import json
import sys


def load_unit_test_shards(shard_name):
    unit_tests_json = '.github/workflows/unit-test-shards.json'
    with open(unit_tests_json) as file:
        unit_test_workflow_shards = json.load(file)
    if shard_name not in unit_test_workflow_shards:
        sys.stdout.write("Error, invalid shard name provided. please provide a valid shard name as specified in unit-test-shards.json")  # noqa: E501
    return unit_test_workflow_shards


def get_test_paths_for_shard(shard_name):
    return load_unit_test_shards(shard_name).get(shard_name).get("paths")


def get_settings_for_shard(shard_name):
    return load_unit_test_shards(shard_name).get(shard_name).get("settings")


def get_output(shard_name, output_argument):
    if output_argument == "settings":
        return get_settings_for_shard(shard_name)
    return " ".join(get_test_paths_for_shard(shard_name))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--shard-name", action="store", default="")
    parser.add_argument("--output", action="store", default="path", choices=["path", "settings"])
    argument = parser.parse_args()

    if not argument.shard_name:
        sys.exit("Error, no shard name provided. please provide a valid shard name as specified in unit-test-shards.json")  # noqa: E501

    output = get_output(argument.shard_name, argument.output)
    print(output)
