"""
Helper API classes for calling google APIs.

DriveApi is for managing files in google drive.
"""
# NOTE: Make sure that all non-ascii text written to standard output (including
# print statements and logging) is manually encoded to bytes using a utf-8 or
# other encoding.  We currently make use of this library within a context that
# does NOT tolerate unicode text on sys.stdout, namely python 2 on Build
# Jenkins  PLAT-2287 tracks this Tech Debt..

import json
import logging
from itertools import count

import backoff
from dateutil.parser import parse
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# I'm not super happy about this since the function is protected with a leading
# underscore, but the next best thing is literally copying this ~40 line
# function verbatim.
from googleapiclient.http import MediaIoBaseUpload, _should_retry_response
from six import iteritems, text_type

from scripts.user_retirement.utils.utils import batch

LOG = logging.getLogger(__name__)

# The maximum number of requests per batch is 100, according to the google API docs.
# However, cap our number lower than that maximum to avoid throttling errors and backoff.
GOOGLE_API_MAX_BATCH_SIZE = 10

# Mimetype used for Google Drive folders.
FOLDER_MIMETYPE = 'application/vnd.google-apps.folder'

# Fields to be extracted from OAuth2 JSON token files
OAUTH2_TOKEN_FIELDS = [
    'client_id', 'client_secret', 'refresh_token',
    'token_uri', 'id_token', 'scopes', 'access_token'
]


class BatchRequestError(Exception):
    """
    Exception which indicates one or more failed requests inside of a batch request.
    """


class TriggerRetryException(Exception):
    """
    Exception which indicates one or more throttled requests inside of a batch request.
    """


class BaseApiClient:
    """
    Base API client for google services.

    To add a new service, extend this class and override these class variables:

      _api_name  (e.g. "drive")
      _api_version  (e.g. "v3")
      _api_scopes
    """
    _api_name = None
    _api_version = None
    _api_scopes = None

    def __init__(self, client_secrets_file_path, **kwargs):
        self._build_client(client_secrets_file_path, **kwargs)

    def _build_client(self, client_secrets_file_path, **kwargs):
        """
        Build the google API client, specific to a single google service.
        """
        # as_user_account is an indicator that the authentication
        # is using a user account.
        # If not true, assume a service account. Otherwise, read in the JSON
        # file, set the scope, and use the info to instantiate Credentials.
        # For more information about user account authentication, go to
        # https://google-auth.readthedocs.io/en/master/user-guide.html#user-credentials
        as_user_account = kwargs.pop('as_user_account', False)
        if not as_user_account:
            credentials = service_account.Credentials.from_service_account_file(
                client_secrets_file_path, scopes=self._api_scopes
            )
        else:
            with open(client_secrets_file_path) as fh:
                token_info = json.load(fh)
                token_info = {k: token_info.get(k) for k in OAUTH2_TOKEN_FIELDS}
                # Take the access_token field and change it to token
                token = token_info.pop('access_token', None)
                token_info['token'] = token
                # Set the scopes
                token_info['scopes'] = self._api_scopes
                credentials = Credentials(**token_info)
        self._client = build(self._api_name, self._api_version, credentials=credentials, **kwargs)
        LOG.info("Client built.")

    def _batch_with_retry(self, requests):
        """
        Send the given Google API requests in a single batch requests, and retry only requests that are throttled.

        Args:
            requests (list of googleapiclient.http.HttpRequest): The requests to send.

        Returns:
            dict mapping of request object to response
        """

        # Mapping of request object to the corresponding response.
        responses = {}

        # This is our working "request queue".  Initially, populate the request queue with all the given requests.
        try_requests = []
        try_requests.extend(requests)

        # This is the queue of requests that are to be retried, populated by the batch callback function.
        retry_requests = []

        # Generate arbitrary (but unique in this batch request) IDs for each request, so that we can recall the
        # corresponding response within a batch response.
        request_object_to_request_id = dict(zip(
            requests,
            (text_type(n) for n in count()),
        ))
        # Create a flipped mapping for convenience.
        request_id_to_request_object = {v: k for k, v in iteritems(request_object_to_request_id)}

        def batch_callback(request_id, response, exception):  # pylint: disable=unused-argument,missing-docstring
            """
            Handle individual responses in the batch request.
            """
            request_object = request_id_to_request_object[request_id]
            if exception:
                if _should_retry_google_api(exception):
                    LOG.error(u'Request throttled, adding to the retry queue: {}'.format(exception).encode('utf-8'))
                    retry_requests.append(request_object)
                else:
                    # In this case, probably nothing can be done, so we just give up on this particular request and
                    # do not include it in the responses dict.
                    LOG.error(u'Error processing request {}'.format(request_object).encode('utf-8'))
                    LOG.error(text_type(exception).encode('utf-8'))
            else:
                responses[request_object] = response
                LOG.info(u'Successfully processed request {}.'.format(request_object).encode('utf-8'))

        # Retry on API throttling at the HTTP request level.
        @backoff.on_exception(
            backoff.expo,
            HttpError,
            max_time=600,  # 10 minutes
            giveup=lambda e: not _should_retry_google_api(e),
            on_backoff=lambda details: _backoff_handler(details),  # pylint: disable=unnecessary-lambda
        )
        # Retry on API throttling at the BATCH ITEM request level.
        @backoff.on_exception(
            backoff.expo,
            TriggerRetryException,
            max_time=600,  # 10 minutes
            on_backoff=lambda details: _backoff_handler(details),  # pylint: disable=unnecessary-lambda
        )
        def func():
            """
            Core function which constitutes the retry loop.  It has no inputs or outputs, only side-effects which
            populates the `responses` variable within the scope of _batch_with_retry().
            """
            # Construct a new batch request object containing the current iteration of requests to "try".
            batch_request = self._client.new_batch_http_request(callback=batch_callback)  # pylint: disable=no-member
            for request_object in try_requests:
                batch_request.add(
                    request_object,
                    request_id=request_object_to_request_id[request_object]
                )

            # Empty the retry queue in preparation of filling it back up with requests that need to be retried.
            del retry_requests[:]

            # Send the batch request.  If the API responds with HTTP 403 or some other retryable error, we should
            # immediately retry this function func() with the same requests in the try_requests queue.  If the response
            # is HTTP 200, we *still* may raise TriggerRetryException and retry a subset of requests if some, but not
            # all requests need to be retried.
            batch_request.execute()

            # If the API throttled some requests, batch_callback would have populated the retry queue.  Reset the
            # try_requests queue and indicate to backoff that there are requests to retry.
            if retry_requests:
                del try_requests[:]
                try_requests.extend(retry_requests)
                raise TriggerRetryException()

        # func()'s side-effect is that it indirectly calls batch_callback which populates the responses dict.
        func()
        return responses


def _backoff_handler(details):
    """
    Simple logging handler for when timeout backoff occurs.
    """
    LOG.info('Trying again in {wait:0.1f} seconds after {tries} tries calling {target}'.format(**details))


def _should_retry_google_api(exc):
    """
    General logic for determining if a google API response is retryable.

    Args:
        exc (googleapiclient.errors.HttpError): The exception thrown by googleapiclient.

    Returns:
        bool: True if the caller should retry the API call.
    """
    retry = False
    if hasattr(exc, 'resp') and exc.resp:  # bizarre and disappointing that sometimes `resp` doesn't exist.
        retry = _should_retry_response(exc.resp.status, exc.content)
    return retry


class DriveApi(BaseApiClient):
    """
    Google Drive API client.
    """
    _api_name = 'drive'
    _api_version = 'v3'
    _api_scopes = [
        # basic file read-write functionality.
        # 'https://www.googleapis.com/auth/drive.file',
        # Full read write functionality
        'https://www.googleapis.com/auth/drive',
        # additional scope for being able to see folders not owned by this account.
        'https://www.googleapis.com/auth/drive.metadata',
    ]

    @backoff.on_exception(
        backoff.expo,
        HttpError,
        max_time=600,  # 10 minutes
        giveup=lambda e: not _should_retry_google_api(e),
        on_backoff=lambda details: _backoff_handler(details),  # pylint: disable=unnecessary-lambda
    )
    def create_file_in_folder(self, folder_id, filename, file_stream, mimetype):
        """
        Creates a new file in the specified folder.

        Args:
            folder_id (str): google resource ID for the drive folder to put the file into.
            filename (str): name of the uploaded file.
            file_stream (file-like/stream): contents of the file to upload.
            mimetype (str): mimetype of the given file.

        Returns: file ID (str).

        Throws:
            googleapiclient.errors.HttpError:
                For some non-retryable 4xx or 5xx error.  See the full list here:
                https://developers.google.com/drive/api/v3/handle-errors
        """
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
        }
        media = MediaIoBaseUpload(file_stream, mimetype=mimetype)
        uploaded_file = self._client.files().create(  # pylint: disable=no-member
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        LOG.info(u'File uploaded: ID="{}", name="{}"'.format(uploaded_file.get('id'), filename).encode('utf-8'))
        return uploaded_file.get('id')

    # NOTE: Do not decorate this function with backoff since it already calls retryable methods.
    def delete_files(self, file_ids):
        """
        Delete multiple files forever, bypassing the "trash".

        This function takes advantage of request batching to reduce request volume.

        Args:
            file_ids (list of str): list of IDs for files to delete.

        Returns: nothing

        Throws:
            BatchRequestError:
                One or more files could not be deleted (could even mean the file does not exist).
        """
        if len(set(file_ids)) != len(file_ids):
            raise ValueError('duplicates detected in the file_ids list.')

        # mapping of request object to the new comment resource returned in the response.
        responses = {}

        # process the list of file ids in batches of size GOOGLE_API_MAX_BATCH_SIZE.
        for file_ids_batch in batch(file_ids, batch_size=GOOGLE_API_MAX_BATCH_SIZE):
            request_objects = []
            for file_id in file_ids_batch:
                request_objects.append(self._client.files().delete(fileId=file_id))  # pylint: disable=no-member

            # this generic helper function will handle the retry logic
            responses_batch = self._batch_with_retry(request_objects)

            responses.update(responses_batch)

        if len(responses) != len(file_ids):
            raise BatchRequestError('Error deleting one or more files/folders.')

    def delete_files_older_than(self, top_level, delete_before_dt, mimetype=None, prefix=None):
        """
        Delete all files beneath a given top level folder that are older than a certain datetime.
        Optionally, specify a file mimetype and a filename prefix.

        Args:
            top_level (str): ID of top level folder.
            delete_before_dt (datetime.datetime): Datetime to use for file age. All files created before this datetime
                will be permanently deleted. Should be timezone offset-aware.
            mimetype (str): Mimetype of files to delete. If not specified, all non-folders will be found.
            prefix (str): Filename prefix - only files started with this prefix will be deleted.
        """
        LOG.info("Walking files...")
        all_files = self.walk_files(
            top_level, 'id, name, createdTime', mimetype
        )
        LOG.info("Files walked. {} files found before filtering.".format(len(all_files)))
        file_ids_to_delete = []
        for file in all_files:
            if (not prefix or file['name'].startswith(prefix)) and parse(file['createdTime']) < delete_before_dt:
                file_ids_to_delete.append(file['id'])
        if file_ids_to_delete:
            LOG.info("{} files remaining after filtering.".format(len(file_ids_to_delete)))
            self.delete_files(file_ids_to_delete)

    @backoff.on_exception(
        backoff.expo,
        HttpError,
        max_time=600,  # 10 minutes
        giveup=lambda e: not _should_retry_google_api(e),
        on_backoff=lambda details: _backoff_handler(details),  # pylint: disable=unnecessary-lambda
    )
    def walk_files(self, top_folder_id, file_fields='id, name', mimetype=None, recurse=True):
        """
        List all files of a particular mimetype within a given top level folder, traversing all folders recursively.

        This function may make multiple HTTP requests depending on how many pages the response contains.  The default
        page size for the python google API client is 100 items.

        Args:
            top_folder_id (str): ID of top level folder.
            file_fields (str): Comma-separated list of metadata fields to return for each folder/file.
                For a full list of file metadata fields, see https://developers.google.com/drive/api/v3/reference/files
            mimetype (str): Mimetype of files to find. If not specified, all items will be returned, including folders.
            recurse (bool): True to recurse into all found folders for items, False to only return top-level items.

        Returns: List of dicts, where each dict contains file metadata and each dict key corresponds to fields
            specified in the `file_fields` arg.

        Throws:
            googleapiclient.errors.HttpError:
                For some non-retryable 4xx or 5xx error.  See the full list here:
                https://developers.google.com/drive/api/v3/handle-errors
        """
        # Sent to list() call and used only for sending the pageToken.
        extra_kwargs = {}
        # Cumulative list of file metadata dicts for found files.
        results = []
        # List of IDs of all visited folders.
        visited_folders = []
        # List of IDs of all found files.
        found_ids = []
        # List of folder IDs remaining to be listed.
        folders_to_visit = [top_folder_id]
        # Mimetype part of file-listing query.
        mimetype_clause = ""
        if mimetype:
            # Return both folders and the specified mimetype.
            mimetype_clause = "( mimeType = '{}' or mimeType = '{}') and ".format(FOLDER_MIMETYPE, mimetype)

        while folders_to_visit:
            current_folder = folders_to_visit.pop()
            LOG.info("Current folder: {}".format(current_folder))
            visited_folders.append(current_folder)
            extra_kwargs = {}

            while True:
                resp = self._client.files().list(  # pylint: disable=no-member
                    q="{}'{}' in parents".format(mimetype_clause, current_folder),
                    fields='nextPageToken, files({})'.format(
                        file_fields + ', mimeType, parents'
                    ),
                    **extra_kwargs
                ).execute()
                page_results = resp.get('files', [])

                LOG.info("walk_files: Returned %s results.", len(page_results))

                # Examine returned results to separate folders from non-folders.
                for result in page_results:
                    LOG.info(u"walk_files: Result: {}".format(result).encode('utf-8'))
                    # Folders contain files - and get special treatment.
                    if result['mimeType'] == FOLDER_MIMETYPE:
                        if recurse and result['id'] not in visited_folders:
                            # Add any undiscovered folders to the list of folders to check.
                            folders_to_visit.append(result['id'])
                    # Determine if this result is a file to return.
                    if result['id'] not in found_ids and (not mimetype or result['mimeType'] == mimetype):
                        found_ids.append(result['id'])
                        # Return only the fields specified in file_fields.
                        results.append({k.strip(): result.get(k.strip(), None) for k in file_fields.split(',')})

                LOG.info("walk_files: %s files found and %s folders to check.", len(results), len(folders_to_visit))

                if page_results and 'nextPageToken' in resp and resp['nextPageToken']:
                    # Only call for more result pages if results were actually returned -and
                    # a nextPageToken is returned.
                    extra_kwargs['pageToken'] = resp['nextPageToken']
                else:
                    break
        return results

    # NOTE: Do not decorate this function with backoff since it already calls retryable methods.
    def create_comments_for_files(self, file_ids_and_content, fields='id'):
        """
        Create comments for files.

        This function is NOT idempotent.  It will blindly create the comments it was asked to create, regardless of the
        existence of other identical comments.

        Args:
            file_ids_and_content (list of tuple(str, str)): list of (file_id, content) tuples.
            fields (str): comma separated list of fields to describe each comment resource in the response.

        Returns: dict mapping of file_id to comment resource (dict).  The contents of the comment resources are dictated
            by the `fields` arg.

        Throws:
            googleapiclient.errors.HttpError:
                For some non-retryable 4xx or 5xx error.  See the full list here:
                https://developers.google.com/drive/api/v3/handle-errors
            BatchRequestError:
                One or more files resulted in an error when adding comments.
        """
        file_ids, _ = zip(*file_ids_and_content)
        if len(set(file_ids)) != len(file_ids):
            raise ValueError('Duplicates detected in the file_ids_and_content list.')

        # Mapping of file_id to the new comment resource returned in the response.
        responses = {}

        # Process the list of file IDs in batches of size GOOGLE_API_MAX_BATCH_SIZE.
        for file_ids_and_content_batch in batch(file_ids_and_content, batch_size=GOOGLE_API_MAX_BATCH_SIZE):
            request_objects_to_file_id = {}
            for file_id, content in file_ids_and_content_batch:
                request_object = self._client.comments().create(  # pylint: disable=no-member
                    fileId=file_id,
                    body={u'content': content},
                    fields=fields
                )
                request_objects_to_file_id[request_object] = file_id

            # This generic helper function will handle the retry logic
            responses_batch = self._batch_with_retry(request_objects_to_file_id.keys())

            # Transform the mapping FROM request objects -> comment resource TO file IDs -> comment resources.
            responses_batch = {
                request_objects_to_file_id[request_object]: resp
                for request_object, resp in responses_batch.items()
            }
            responses.update(responses_batch)

        if len(responses) != len(file_ids_and_content):
            raise BatchRequestError('Error creating comments for one or more files/folders.')

        return responses

    # NOTE: Do not decorate this function with backoff since it already calls retryable methods.
    def list_permissions_for_files(self, file_ids, fields='emailAddress, role'):
        """
        List permissions for files.

        Args:
            file_ids (list of str): list of Drive file IDs for which to list permissions.
            fields (str): comma separated list of fields to describe each permissions resource in the response.

        Returns: dict mapping of file_id to permission resource list (list of dict).  The contents of the permission
            resources are dictated by the `fields` arg.

        Throws:
            googleapiclient.errors.HttpError:
                For some non-retryable 4xx or 5xx error.  See the full list here:
                https://developers.google.com/drive/api/v3/handle-errors
            BatchRequestError:
                One or more files resulted in an error when having their permissions listed.
        """

        if len(set(file_ids)) != len(file_ids):
            raise ValueError('duplicates detected in the file_ids list.')

        # mapping of file_id to the new comment resource returned in the response.
        responses = {}

        # process the list of file ids in batches of size GOOGLE_API_MAX_BATCH_SIZE.
        for file_ids_batch in batch(file_ids, batch_size=GOOGLE_API_MAX_BATCH_SIZE):
            request_objects_to_file_id = {}
            for file_id in file_ids_batch:
                request_object = self._client.permissions().list(  # pylint: disable=no-member
                    fileId=file_id,
                    fields='permissions({})'.format(fields)
                )
                request_objects_to_file_id[request_object] = file_id

            # this generic helper function will handle the retry logic
            responses_batch = self._batch_with_retry(request_objects_to_file_id.keys())

            # transform the mapping from request objects -> response dicts to file ids -> permissions resource lists.
            responses_batch_transformed = {}
            for request_object, resp in responses_batch.items():
                permissions = None
                if resp and 'permissions' in resp:
                    permissions = resp['permissions']
                responses_batch_transformed[request_objects_to_file_id[request_object]] = permissions

            responses.update(responses_batch_transformed)

        if len(responses) != len(file_ids):
            raise BatchRequestError('Error listing permissions for one or more files/folders.')

        return responses
