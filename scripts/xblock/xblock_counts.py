import argparse
import csv
import json
import os
import sys
from datetime import datetime

import requests

# Keys for the CSV and JSON interpretation
PAGINATION_KEY = 'pagination'
NUM_PAGES_KEY = 'num_pages'
NEXT_PAGE_URL_KEY = 'next'
RESULTS_KEY = 'results'
BLOCKS_URL_KEY = 'blocks_url'
BLOCK_ROOT_KEY = 'root'
BLOCKS_KEY = 'blocks'
BLOCK_COUNTS_KEY = 'block_counts'
COURSE_NAME_KEY = 'name'
COURSE_ID_KEY = 'course_id'
COURSE_START_KEY = 'start'
COURSE_END_KEY = 'end'


def monthdelta(date, delta):
    """
    Method to get a delta of Months from a provided datetime

    From this StackOverflow response:
    http://stackoverflow.com/questions/3424899/whats-the-simplest-way-to-subtract-a-month-from-a-date-in-python

    Arguments:
        date datetime: Date to be modified
        delta int: delta value

    Returns:
        datetime: The datetime with the month delta applied
    """
    m, y = (date.month + delta) % 12, date.year + (date.month + delta - 1) // 12
    if not m:
        m = 12
    d = min(date.day, [31,
                       29 if y % 4 == 0 and not y % 400 == 0
                       else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return date.replace(day=d, month=m, year=y)


def _get_course_data_summary(auth_token, months_restriction, xblock_type_set, api_root, course_count=None):
    """
    Gets the course summary data from the Course Blocks API and returns a list of data objects
    summarizing each courses xBlock usage

    Arguments
        auth_token (str): Authentication token for the API
        months_restriction (int): Restriction on the number of months to go back
        xblock_type_set (set): A set of Strings containing the xBlocks types to be counted

    Returns:
        list: a list of data objects summarizing each courses xBlock usage
    """
    # Get the Course list
    response = requests.get(api_root + '/api/courses/v1/courses/')
    json_result = response.json()
    num_courses = 0
    num_pages = 1

    if PAGINATION_KEY in json_result and NUM_PAGES_KEY in json_result[PAGINATION_KEY]:
        num_pages = json_result[PAGINATION_KEY][NUM_PAGES_KEY]
        num_courses = json_result[PAGINATION_KEY]['count']

    course_summary_data = []
    block_type_url = _get_block_count_url_string(xblock_type_set)

    if course_count is None:
        course_count = num_courses

    course_count_limit = False
    total_courses = 0
    # Look through all pages and courses
    while num_pages > 0 and not course_count_limit:
        if RESULTS_KEY in json_result:
            results_list = json_result[RESULTS_KEY]
            for course in results_list:
                course_data = _get_course_data(auth_token, course, block_type_url,
                                               months_restriction=months_restriction)
                if course_data is not None:
                    course_summary_data.append(course_data)

                if total_courses == course_count:
                    course_count_limit = True
                    break
                total_courses += 1
        num_pages -= 1

        # get the url for the next "page" in the pagenated course data and update the json_result
        page_data = json_result.get(PAGINATION_KEY, None)
        if page_data is not None:
            next_page = page_data.get('next', '')
            if not next_page:
                break
            json_result = requests.get(next_page).json()

        # print to update the screen for status
        sys.stdout.write('.')
        sys.stdout.flush()
    print('Processed %d courses' % total_courses)  # noqa: UP031
    return course_summary_data


def _get_course_data(auth_token, course, block_type_url, months_restriction=None):
    """
    Collects the course data for the provided course data

    Arguments:
        auth_token (str): Authentication token for the API
        course (dict): Dictionary containing the JSON data for the given course

    Returns:
        dict: Dictionary containing the general Course information or None if date restriction is applied and course is
        older than restriction
            {
                name: 'Name of course',
                course_id: 'Course ID',
                start: 'Start date of course',
                course_end: 'End date of course',
                block_counts: Dictionary containing block counts,
                blocks_url: Url to retrieve the Blocks data,
            }
    """
    course_data = {}
    start_time_str = course.get(COURSE_START_KEY, '')
    if start_time_str:
        if months_restriction is not None:
            start_time = datetime.strptime(start_time_str, '%Y-%m-%dT%H:%M:%SZ')
            date_restriction = monthdelta(datetime.now(), -months_restriction)
            if start_time < date_restriction:
                return None
        course_data[COURSE_START_KEY] = start_time_str
    course_data[COURSE_NAME_KEY] = course.get(COURSE_NAME_KEY, '')
    course_data[COURSE_ID_KEY] = course.get(COURSE_ID_KEY, '')
    course_data[COURSE_END_KEY] = course.get(COURSE_END_KEY, '')
    if BLOCKS_URL_KEY in course:
        blocks_url = course.get(BLOCKS_URL_KEY, '')
        block_counts = _get_course_block_counts(auth_token, blocks_url + block_type_url)
        course_data[BLOCK_COUNTS_KEY] = block_counts
        course_data[BLOCKS_URL_KEY] = blocks_url
    return course_data


def _get_block_types_from_json_file(xblock_json_file):
    """
    Retrieves the block types from the provided xBlock configuration JSON file

    Arguments:
        xblock_json_file (str): The name of the xBlock configuration file

    :return:
        set: A set of strings for all the types that are available in the configuration file
    """
    if not os.path.isfile(xblock_json_file):
        print('xBlock configuration file does not exist: %s' % xblock_json_file)  # noqa: UP031
        sys.exit(2)
    with open(xblock_json_file) as json_file:
        type_set = set()
        try:
            json_data = json.loads(json_file.read())
        except ValueError as e:
            print('xBlock configuration file does not match the expected layout and is '  # noqa: UP031
                  'missing "data" list: %s' % xblock_json_file)
            sys.exit(str(e))
        if 'data' in json_data:
            xblock_type_list = json_data['data']
            for xblock in xblock_type_list:
                type_set.add(xblock['name'])
            return type_set
        else:
            print('xBlock configuration file does not match the expected layout and is '  # noqa: UP031
                  'missing "data" list: %s' % xblock_json_file)
            sys.exit(2)


def _get_block_count_url_string(xblock_type_set):
    """
    Build the string from the xBlock type set to append to the Block url for block_count types

    Arguments:
        xblock_type_set (set): A set of strings for all the block types

    Returns:
        str: The portion to append to the block url
    """
    block_url = ''
    if len(xblock_type_set) > 0:
        block_url += '&all_blocks=true&block_counts='
        for index, block_type in enumerate(xblock_type_set):
            block_url += block_type
            if index < len(xblock_type_set) - 1:
                block_url += ','
    return block_url


def _get_course_block_counts(auth_token, block_url):
    """
    Get the block counts for a given block_url

    Arguments:
        auth_token (str): The Authentication token to access the API
        block_url (str): The respective url for a Courses xBlock data

    Returns:
        dict: A dictionary containing the Block counts
    """
    headers = {'Authorization': f'Bearer {auth_token}'}

    response = requests.get(block_url, headers=headers)
    if response.status_code != 200:
        print(f"url {block_url} returned status code {response.status_code}")
        return {}
    response_json = response.json()

    if BLOCK_ROOT_KEY in response_json and BLOCKS_KEY in response_json:
        root_val = response_json[BLOCK_ROOT_KEY]
        counts = response_json[BLOCKS_KEY][root_val][BLOCK_COUNTS_KEY]
        return counts
    return {}


def _get_block_summary_totals(course_data):
    """
    Totals the xBlock types included in the course data and returns those counts by type

    Arguments:
        course_data (list of dicts): a list of course_data objects

    Returns:
        dict: containing the total number of blocks by type
            {
                <block_type>: <count>,
                ...
            }
        dict: containing the total unique courses for a block type
    """
    block_summary_counts = {}
    unique_course_counts = {}

    for course in course_data:
        block_counts = course.get(BLOCK_COUNTS_KEY)
        for count_label, value in block_counts.items():
            unique = 0
            if value > 0:
                unique = 1
            if count_label in block_summary_counts:
                block_summary_counts[count_label] += value
                unique_course_counts[count_label] += unique
            else:
                block_summary_counts[count_label] = value
                unique_course_counts[count_label] = unique

    return block_summary_counts, unique_course_counts


def write_block_summary_report(course_data):
    """
    Generate a CSV file containing a summary of the xBlock usage

    Arguments:
        course_data (list of dicts): a list of course_data objects

    Returns:
        Nothing
    """
    (block_summary_counts, unique_course_counts) = _get_block_summary_totals(course_data)

    # Open and start writing the data into the CSV
    with open('xblock_summary_counts.csv', 'wb') as csvfile:
        summary_writer = csv.writer(csvfile, delimiter=',',
                                    quotechar='"', quoting=csv.QUOTE_MINIMAL)
        summary_writer.writerow(['XBLOCK_NAME', 'UNIQUE_COURSES', 'NUM_TOTAL_INSTANCES'])
        for block_type in sorted(block_summary_counts):
            block_count = block_summary_counts.get(block_type)
            summary_writer.writerow([block_type, str(unique_course_counts[block_type]), str(block_count)])
        csvfile.close()


def write_course_block_detail_report(course_data):
    """
    Generate a CSV file containing the detailed information about the xBlocks available per course

    Arguments:
        course_data (list of dicts): a list of course_data objects

    Returns:
        Nothing
    """
    with open('xblock_course_detail.csv', 'wb') as csvfile:
        detail_writer = csv.writer(
            csvfile,
            delimiter=',',
            quotechar='"',
            quoting=csv.QUOTE_ALL
        )
        detail_writer.writerow(['XBLOCK_TYPE_NAME', 'COURSE_NAME', 'COURSE_ID', 'COURSE_START', 'COURSE_END', 'NUM_XBLOCK_INSTANCES'])
        for course in course_data:
            for block_type, count in course.get(BLOCK_COUNTS_KEY, []).items():
                if count > 0:
                    detail_writer.writerow([
                        block_type,
                        course.get(COURSE_NAME_KEY, '').encode('utf-8'),
                        course.get(COURSE_ID_KEY, ''),
                        course.get(COURSE_START_KEY, ''),
                        course.get(COURSE_END_KEY, ''),
                        str(count)
                    ])
        csvfile.close()


def get_access_token(username, password, oauth2_client_id, api_root):
    """
    Get the Access token using the provided credentials

    Arguments:
        username (str): a string containing the username to log in
        password (str): a string containing the password for the username

    Returns:
        str: Authentication token
    """
    response = requests.post(
        api_root + '/oauth2/access_token/',
        data={
            'client_id': oauth2_client_id,
            'grant_type': 'password',
            'username': username,
            'password': password
        },
    )
    return json.loads(response.text).get('access_token', None)

if __name__ == "__main__":
    # Get username and password from command line arguments
    username = None
    password = None
    months_restriction = 12
    xblock_json_file = 'xblock_studio_configuration.json'
    api_root = 'https://courses.edx.org'
    course_count_limit = None

    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--username', required=True, help='User name for destination')
    parser.add_argument('-p', '--password', required=True, help='Password for the provided username')
    parser.add_argument('-c', '--clientid', required=True, help='OAuth2 Client ID for the destination')
    parser.add_argument('-a', '--api_root', help='The root of the api that the script is being run against',
                        default=api_root)
    parser.add_argument('-m', '--month', type=int, help='The months to go back when collecting course data '
                                                        '(Default 12 months)')
    parser.add_argument('-x', '--xblock_config', type=str, help='The xBlock configuration JSON file containing all the'
                                                                'xBlock types', default=xblock_json_file)
    parser.add_argument('-n', '--course_count', type=int, help='The number of courses that will be retrieved')
    args = parser.parse_args()
    username = args.username
    password = args.password
    oauth2_client_id = args.clientid
    if args.xblock_config:
        xblock_json_file = args.xblock_config
    if args.month:
        months_restriction = args.month
    if args.api_root:
        api_root = args.api_root
    if args.course_count:
        course_count_limit = args.course_count

    start_time = datetime.now()
    # Get User access token
    token = get_access_token(username, password, oauth2_client_id, api_root)
    if token is None:
        print('Failed to retrieve user token for user: %s ' % username)  # noqa: UP031
        sys.exit(2)

    # Collect course data and write CSV reports
    xblock_type_set = _get_block_types_from_json_file(xblock_json_file)
    course_data = _get_course_data_summary(token, months_restriction, xblock_type_set, api_root,
                                           course_count=course_count_limit)
    if len(course_data) > 0:
        write_block_summary_report(course_data)
        write_course_block_detail_report(course_data)
    print(f'Start time: {str(start_time)} Total run time: {str(datetime.now() - start_time)}')
