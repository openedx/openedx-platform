# pylint: disable=django-not-configured
"""
Script to process pytest warnings output by pytest-json-report plugin and output it as a html
"""

import argparse
import io  # pylint: disable=unused-import  # noqa: F401
import itertools
import json
import os
import re
from collections import Counter
from xml.sax.saxutils import escape

from write_to_html import HtmlOutlineWriter  # noqa pylint: disable=import-error,useless-suppression

columns = [
    "message",
    "category",
    "filename",
    "lineno",
    "high_location",
    "label",
    "num",
    "deprecated",
]
columns_index_dict = {key: index for index, key in enumerate(columns)}


def separate_warnings_by_location(warnings_data):
    """
    Warnings originate from multiple locations, this function takes in list of warning objects
    and separates them based on their filename location
    """

    # first create regex for each n file location
    warnings_locations = {
        r".*/python\d\.\d/site-packages/.*\.py": "python",  # noqa pylint: disable=W1401
        r".*/edx-platform/lms/.*\.py": "lms",  # noqa pylint: disable=W1401
        r".*/edx-platform/openedx/.*\.py": "openedx",  # noqa pylint: disable=W1401
        r".*/edx-platform/cms/.*\.py": "cms",  # noqa pylint: disable=W1401
        r".*/edx-platform/common/.*\.py": "common",  # noqa pylint: disable=W1401
    }

    # separate into locations flow:
    #  - iterate through each wanring_object, see if its filename matches any regex in warning locations.
    #  - If so, change high_location index on warnings_object to location name
    for warnings_object in warnings_data:
        warning_origin_located = False
        for key in warnings_locations:
            if (
                re.search(key, warnings_object[columns_index_dict["filename"]])
                is not None
            ):
                warnings_object[
                    columns_index_dict["high_location"]
                ] = warnings_locations[key]
                warning_origin_located = True
                break
        if not warning_origin_located:
            warnings_object[columns_index_dict["high_location"]] = "other"
    return warnings_data


def convert_warning_dict_to_list(warning_dict):
    """
    converts our data dict into our defined list based on columns defined at top of this file
    """
    output = []
    for column in columns:
        if column in warning_dict:
            output.append(warning_dict[column])
        else:
            output.append(None)
    output[columns_index_dict["num"]] = 1
    return output


def read_warning_data(dir_path):
    """
    During test runs in jenkins, multiple warning json files are output. This function finds all files
    and aggregates the warnings in to one large list
    """
    dir_path = os.path.expanduser(dir_path)
    # find all files that exist in given directory
    files_in_dir = [
        f for f in os.listdir(dir_path) if os.path.isfile(os.path.join(dir_path, f))
    ]
    warnings_files = []

    # TODO(jinder): currently this is hard-coded in, maybe create a constants file with info
    # THINK(jinder): but creating file for one constant seems overkill
    warnings_file_name_regex = (
        r"pytest_warnings_?[\w.-]*\.json"  # noqa pylint: disable=W1401
    )

    # iterate through files_in_dir and see if they match our know file name pattern
    for temp_file in files_in_dir:
        if re.search(warnings_file_name_regex, temp_file) is not None:
            warnings_files.append(temp_file)

    # go through each warning file and aggregate warnings into warnings_data
    warnings_data = []
    for temp_file in warnings_files:
        with open(os.path.expanduser(dir_path + "/" + temp_file), "r") as read_file:  # noqa: UP015
            json_input = json.load(read_file)
            if "warnings" in json_input:
                data = [
                    convert_warning_dict_to_list(warning_dict)
                    for warning_dict in json_input["warnings"]
                ]
                warnings_data.extend(data)
            else:
                print(temp_file)
    return warnings_data


def compress_similar_warnings(warnings_data):
    """
    find all warnings that are exactly the same, count them, and return set with count added to each warning
    """
    tupled_data = [tuple(data) for data in warnings_data]
    test_counter = Counter(tupled_data)
    output = [list(value) for value in test_counter.keys()]
    for data_object in output:
        data_object[columns_index_dict["num"]] = test_counter[tuple(data_object)]
    return output


def canonical_message(msg):
    """
    Remove noise from a warning message.

    The "same" warning can produce different messages because of data in
    the message.  This returns a new message with the data converted to
    placeholders.
    """
    hex = r"[0-9a-fA-F]"
    # Temp files are test_Abcd123.csv etc...
    msg = re.sub(r"\btest_\w{7}\.", "test_TMP.", msg)
    # Guids, SHA hashes, and numbers in general get replaced.
    msg = re.sub(rf"\b{hex}{{8}}-{hex}{{4}}-{hex}{{4}}-{hex}{{4}}-{hex}{{12}}\b", "GUID", msg)
    msg = re.sub(rf"\b{hex}{{32}}\b", "SHA", msg)
    msg = re.sub(r"\b\d+(\.\d+)*\b", "#", msg)
    return msg


def process_warnings_json(dir_path):
    """
    Master function to process through all warnings and output a dict

    dict structure:
    {
        location: [{warning text: {file_name: warning object}}]
    }

    flow:
        - Aggregate data from all warning files
        - Separate warnings by deprecated vs non deprecated(has word deprecate in it)
        - Further categorize warnings
        - Return output
    Possible Error/enhancement: there might be better ways to separate deprecates vs
        non-deprecated warnings
    """
    warnings_data = read_warning_data(dir_path)
    for warnings_object in warnings_data:
        warnings_object[columns_index_dict["deprecated"]] = bool(
            "deprecated" in warnings_object[columns_index_dict["message"]]
        )
        warnings_object[columns_index_dict["message"]] = canonical_message(warnings_object[columns_index_dict["message"]])
    warnings_data = separate_warnings_by_location(warnings_data)
    compressed_warnings_data = compress_similar_warnings(warnings_data)
    return compressed_warnings_data


def group_and_sort_by_sumof(data, group, sort_by):
    """
    Group and sort data.

    Return
        List of tuples. Each tuple has:
        - Group key
        - Iterable of warnings that belongs to that group
        - Count of warnings that belong to that group
    """
    sorted_data = sorted(data, key=lambda x: x[columns.index(group)])
    groups_by = itertools.groupby(sorted_data, lambda x: x[columns_index_dict[group]])
    temp_list_to_sort = []
    for key, generator in groups_by:
        value = list(generator)
        temp_list_to_sort.append((key, value, sum([item[columns_index_dict[sort_by]] for item in value])))
    # sort by count
    return sorted(temp_list_to_sort, key=lambda x: -x[2])


def write_html_report(warnings_data, html_path):
    """
    converts from list of lists data to our html
    """
    html_path = os.path.expanduser(html_path)
    if "/" in html_path:
        location_of_last_dir = html_path.rfind("/")
        dir_path = html_path[:location_of_last_dir]
        os.makedirs(dir_path, exist_ok=True)
    with open(html_path, "w") as fout:
        html_writer = HtmlOutlineWriter(fout)
        category_sorted_by_count = group_and_sort_by_sumof(
            warnings_data, "category", "num"
        )
        for category, group_in_category, category_count in category_sorted_by_count:
            # xss-lint: disable=python-wrap-html
            html = '<span class="count">{category}, count: {count}</span> '.format(  # noqa: UP032
                category=escape(category), count=category_count
            )
            html_writer.start_section(html, klass="category")
            locations_sorted_by_count = group_and_sort_by_sumof(
                group_in_category, "high_location", "num"
            )

            for (
                location,
                group_in_location,
                location_count,
            ) in locations_sorted_by_count:
                # xss-lint: disable=python-wrap-html
                html = '<span class="count">{location}, count: {count}</span> '.format(  # noqa: UP032
                    location=escape(location), count=location_count
                )
                html_writer.start_section(html, klass="location")
                message_group_sorted_by_count = group_and_sort_by_sumof(
                    group_in_location, "message", "num"
                )
                for (
                    message,
                    message_group,
                    message_count,
                ) in message_group_sorted_by_count:
                    # xss-lint: disable=python-wrap-html
                    html = '<span class="count">{warning_text}, count: {count}</span> '.format(  # noqa: UP032
                        warning_text=escape(message), count=message_count
                    )
                    html_writer.start_section(html, klass="warning_text")
                    # warnings_object[location][warning_text] is a list
                    for warning in message_group:
                        # xss-lint: disable=python-wrap-html
                        html = '<span class="count">{warning_file_path}</span> '.format(
                            warning_file_path=warning[columns_index_dict["filename"]]
                        )
                        html_writer.start_section(html, klass="warning")
                        # xss-lint: disable=python-wrap-html
                        html = '<p class="lineno">lineno: {lineno}</p> '.format(
                            lineno=warning[columns_index_dict["lineno"]]
                        )
                        html_writer.write(html)
                        # xss-lint: disable=python-wrap-html
                        html = '<p class="num">num_occur: {num}</p> '.format(
                            num=warning[columns_index_dict["num"]]
                        )
                        html_writer.write(html)

                        html_writer.end_section()
                    html_writer.end_section()
                html_writer.end_section()
            html_writer.end_section()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process and categorize pytest warnings and output html report."
    )
    parser.add_argument("--dir-path", default="test_root/log")
    parser.add_argument("--html-path", default="test_html.html")
    args = parser.parse_args()
    data_output = process_warnings_json(args.dir_path)
    write_html_report(data_output, args.html_path)
