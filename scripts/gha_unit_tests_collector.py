import argparse
import json
import sys


def get_all_unit_test_shards():
    unit_tests_json = '.github/workflows/unit-test-shards.json'
    with open(unit_tests_json) as file:
        unit_test_workflow_shards = json.load(file)

    return unit_test_workflow_shards


def update_unit_test_modules(module_name, shard_config, unit_test_modules):
    is_cms_shard_path = shard_config['paths'][0].startswith('cms')

    if is_cms_shard_path and module_name == "cms":
        unit_test_modules.update(shard_config.get('paths'))
    elif not is_cms_shard_path and module_name != "cms":
        unit_test_modules.update(shard_config.get('paths'))
    return unit_test_modules


def get_unit_test_modules(module_name="lms"):
    unit_test_modules = set()
    all_unit_test_shards = get_all_unit_test_shards()
    for shard_name, shard_config in all_unit_test_shards.items():  # noqa: B007
        unit_test_modules = update_unit_test_modules(module_name, shard_config, unit_test_modules)
    return unit_test_modules


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cms-only", action="store_true", default="")
    parser.add_argument("--lms-only", action="store_true", default="")
    argument = parser.parse_args()

    if not argument.cms_only and not argument.lms_only:
        print("Please specify --cms-only or --lms-only")
        sys.exit(1)

    modules = get_unit_test_modules("cms") if argument.cms_only else get_unit_test_modules("lms")
    paths_output = ' '.join(modules)
    print(paths_output)
