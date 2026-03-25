"""
Utility methods for instructor tasks
"""


from eventtracking import tracker  # noqa: I001

from common.djangoapps.util.file import course_filename_prefix_generator
from lms.djangoapps.instructor_task.models import ReportStore

REPORT_REQUESTED_EVENT_NAME = 'edx.instructor.report.requested'

# define value to use when no task_id is provided:
UNKNOWN_TASK_ID = 'unknown-task_id'

# define values for update functions to use to return status to perform_module_state_update
UPDATE_STATUS_SUCCEEDED = 'succeeded'
UPDATE_STATUS_FAILED = 'failed'
UPDATE_STATUS_SKIPPED = 'skipped'


def upload_csv_to_report_store(rows, csv_name, course_id, timestamp, config_name='GRADES_DOWNLOAD', parent_dir=''):
    """
    Upload data as a CSV using ReportStore.

    Arguments:
        rows: CSV data in the following format (first column may be a
            header):
            [
                [row1_colum1, row1_colum2, ...],
                ...
            ]
        csv_name: Name of the resulting CSV
        course_id: ID of the course
        parent_dor: Name of the directory where the CSV file will be stored

    Returns:
        report_name: string - Name of the generated report
    """
    report_store = ReportStore.from_config(config_name)
    report_name = "{course_prefix}_{csv_name}_{timestamp_str}.csv".format(
        course_prefix=course_filename_prefix_generator(course_id),
        csv_name=csv_name,
        timestamp_str=timestamp.strftime("%Y-%m-%d-%H%M")
    )

    report_store.store_rows(course_id, report_name, rows, parent_dir)
    tracker_emit(csv_name)
    return report_name


def upload_csv_file_to_report_store(file, csv_name, course_id, timestamp, config_name='GRADES_DOWNLOAD', parent_dir=''):
    """
    Upload data as a CSV using ReportStore.

    Arguments:
        rows: CSV data in a file-like object
        csv_name: Name of the resulting CSV
        course_id: ID of the course
        parent_dor: Name of the directory where the CSV file will be stored

    Returns:
        report_name: string - Name of the generated report
    """
    report_store = ReportStore.from_config(config_name)
    report_name = "{course_prefix}_{csv_name}_{timestamp_str}.csv".format(
        course_prefix=course_filename_prefix_generator(course_id),
        csv_name=csv_name,
        timestamp_str=timestamp.strftime("%Y-%m-%d-%H%M")
    )

    report_store.store(course_id, report_name, file, parent_dir)
    tracker_emit(csv_name)
    return report_name


def upload_zip_to_report_store(file, zip_name, course_id, timestamp, config_name='GRADES_DOWNLOAD'):
    """
    Upload given file buffer as a zip file using ReportStore.
    """
    report_store = ReportStore.from_config(config_name)

    report_name = "{course_prefix}_{zip_name}_{timestamp_str}.zip".format(
        course_prefix=course_filename_prefix_generator(course_id),
        zip_name=zip_name,
        timestamp_str=timestamp.strftime("%Y-%m-%d-%H%M")
    )

    report_store.store(course_id, report_name, file)
    tracker_emit(zip_name)
    return report_name


def tracker_emit(report_name):
    """
    Emits a 'report.requested' event for the given report.
    """
    tracker.emit(REPORT_REQUESTED_EVENT_NAME, {"report_type": report_name, })
