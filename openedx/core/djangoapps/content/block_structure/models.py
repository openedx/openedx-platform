"""
Models used by the block structure framework.
"""


import errno
from contextlib import contextmanager
from datetime import datetime
from logging import getLogger

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.core.files.base import ContentFile
from django.db import models, transaction
from model_utils.models import TimeStampedModel

from common.djangoapps.util.storage import resolve_storage_backend
from openedx.core.djangoapps.xmodule_django.models import UsageKeyWithRunField

from . import config
from .exceptions import BlockStructureNotFound

log = getLogger(__name__)


def _create_path(directory, filename):
    """
    Returns the full path for the given directory and filename.
    """
    return f'{directory}/{filename}'


def _directory_name(data_usage_key):
    """
    Returns the directory name for the given
    data_usage_key.
    """
    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['DIRECTORY_PREFIX']
    # .. setting_default: ''
    # .. setting_description: Specifies the path in storage where block structures would be saved,
    #   for storage-backed block structure cache.
    #   For more information, check https://github.com/openedx/edx-platform/pull/14571.
    # .. setting_warnings: Depends on `BLOCK_STRUCTURES_SETTINGS['STORAGE_CLASS']`
    directory_prefix = settings.BLOCK_STRUCTURES_SETTINGS.get('DIRECTORY_PREFIX', '')

    # replace any '/' in the usage key so they aren't interpreted
    # as folder separators.
    encoded_usage_key = str(data_usage_key).replace('/', '_')
    return '{}{}'.format(  # noqa: UP032
        directory_prefix,
        encoded_usage_key,
    )


def _path_name(bs_model, _filename):
    """
    Returns path name to use for the given
    BlockStructureModel instance.
    """
    filename = datetime.utcnow().strftime('%Y-%m-%d-%H:%M:%S-%f')
    return _create_path(
        _directory_name(bs_model.data_usage_key),
        filename,
    )


def _bs_model_storage():
    """
    Get django Storage object for BlockStructureModel.
    """
    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['STORAGE_CLASS']
    # .. setting_default: None
    # .. setting_description: Specifies the storage used for storage-backed block structure cache.
    #   For more information, check https://github.com/openedx/edx-platform/pull/14571.
    # storage_class = settings.BLOCK_STRUCTURES_SETTINGS.get('STORAGE_CLASS')

    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['STORAGE_KWARGS']
    # .. setting_default: {}
    # .. setting_description: Specifies the keyword arguments needed to setup the storage, which
    #   would be used for storage-backed block structure cache.
    #   For more information, check https://github.com/openedx/edx-platform/pull/14571.
    # .. setting_warnings: Depends on `BLOCK_STRUCTURES_SETTINGS['STORAGE_CLASS']`
    storage_kwargs = settings.BLOCK_STRUCTURES_SETTINGS.get('STORAGE_KWARGS', {})

    return resolve_storage_backend(
        storage_key="block_structures_settings",
        legacy_setting_key="BLOCK_STRUCTURES_SETTINGS",
        legacy_sec_setting_keys=["STORAGE_CLASS"],
        options=storage_kwargs
    )


class CustomizableFileField(models.FileField):
    """
    Subclass of FileField that allows custom settings to not
    be serialized (hard-coded) in migrations. Otherwise,
    migrations include optional settings for storage (such as
    the storage class and bucket name); we don't want to
    create new migration files for each configuration change.
    """
    def __init__(self, *args, **kwargs):
        kwargs.update(dict(
            upload_to=_path_name,
            storage=_bs_model_storage(),
            max_length=500,  # allocate enough for base path + prefix + usage_key + timestamp in filepath
        ))
        super().__init__(*args, **kwargs)

    def deconstruct(self):  # pylint: disable=missing-function-docstring
        name, path, args, kwargs = super().deconstruct()
        del kwargs['upload_to']
        del kwargs['storage']
        del kwargs['max_length']
        return name, path, args, kwargs


@contextmanager
def _storage_error_handling(bs_model, operation, is_read_operation=False):
    """
    Helpful context manager that handles various errors
    from the backend storage.

    Typical errors at read time on configuration changes:
        IOError:
            - File not found (S3 or FS)
            - Bucket name changed (S3)
        SuspiciousOperation
            - Path mismatches when changing backends

    Other known errors:
        OSError
            - Access issues in creating files (FS)
        S3ResponseError
            - Incorrect credentials with 403 status (S3)
            - Non-existent bucket with 404 status (S3)
    """
    try:
        yield
    except Exception as error:  # pylint: disable=broad-except
        log.exception('BlockStructure: Exception %s on store %s; %s.', error.__class__, operation, bs_model)
        if isinstance(error, OSError) and error.errno in (errno.EACCES, errno.EPERM):  # pylint: disable=no-else-raise, no-member
            raise
        elif is_read_operation and isinstance(error, (IOError, SuspiciousOperation)):
            # May have been caused by one of the possible error
            # situations listed above.  Raise BlockStructureNotFound
            # so the block structure can be regenerated and restored.
            raise BlockStructureNotFound(bs_model.data_usage_key)  # pylint: disable=raise-missing-from  # noqa: B904
        else:
            raise


class BlockStructureModel(TimeStampedModel):
    """
    Model for storing Block Structure information.

    .. no_pii:
    """
    VERSION_FIELDS = [
        'data_version',
        'data_edit_timestamp',
        'transformers_schema_version',
        'block_structure_schema_version',
    ]
    UNIQUENESS_FIELDS = ['data_usage_key'] + VERSION_FIELDS

    class Meta:
        db_table = 'block_structure'

    data_usage_key = UsageKeyWithRunField(
        'Identifier of the data being collected.',
        blank=False,
        max_length=255,
        unique=True,
    )
    data_version = models.CharField(  # noqa: DJ001
        'Version of the data at the time of collection.',
        blank=True,
        null=True,
        max_length=255,
    )
    data_edit_timestamp = models.DateTimeField(
        'Edit timestamp of the data at the time of collection.',
        blank=True,
        null=True,
    )
    transformers_schema_version = models.CharField(
        'Representation of the schema version of the transformers used during collection.',
        blank=False,
        max_length=255,
    )
    block_structure_schema_version = models.CharField(
        'Version of the block structure schema at the time of collection.',
        blank=False,
        max_length=255,
    )
    data = CustomizableFileField()

    def get_serialized_data(self):
        """
        Returns the collected data for this instance.
        """
        operation = 'Read'
        with _storage_error_handling(self, operation, is_read_operation=True):
            serialized_data = self.data.read()

        self._log(self, operation, serialized_data)
        return serialized_data

    @classmethod
    def get(cls, data_usage_key):
        """
        Returns the entry associated with the given data_usage_key.
        Raises:
             BlockStructureNotFound if an entry for data_usage_key is not found.
        """
        try:
            return cls.objects.get(data_usage_key=data_usage_key)
        except cls.DoesNotExist:
            log.info('BlockStructure: Not found in table; %s.', data_usage_key)
            raise BlockStructureNotFound(data_usage_key)  # pylint: disable=raise-missing-from  # noqa: B904

    @classmethod
    def update_or_create(cls, serialized_data, data_usage_key, **kwargs):
        """
        Updates or creates the BlockStructureModel entry
        for the given data_usage_key in the kwargs,
        uploading serialized_data as the content data.
        """
        # Use an atomic transaction so the model isn't updated
        # unless the file is successfully persisted.
        with transaction.atomic():
            bs_model, created = cls.objects.update_or_create(defaults=kwargs, data_usage_key=data_usage_key)
            operation = 'Created' if created else 'Updated'

            with _storage_error_handling(bs_model, operation):
                bs_model.data.save('', ContentFile(serialized_data))

        if not created:
            cls._prune_files(data_usage_key)
        cls._log(bs_model, operation, serialized_data)
        return bs_model, created

    def __str__(self):
        """
        Returns a string representation of this model.
        """
        return ', '.join(
            f'{field_name}: {str(getattr(self, field_name))}'
            for field_name in self.UNIQUENESS_FIELDS
        )

    @classmethod
    def _prune_files(cls, data_usage_key, num_to_keep=None):
        """
        Deletes previous file versions for data_usage_key.
        """
        if num_to_keep is None:
            num_to_keep = config.num_versions_to_keep()

        try:
            all_files_by_date = sorted(cls._get_all_files(data_usage_key))
            files_to_delete = all_files_by_date[:-num_to_keep] if num_to_keep > 0 else all_files_by_date  # pylint: disable=invalid-unary-operand-type
            cls._delete_files(files_to_delete)
            log.info(
                'BlockStructure: Deleted %d out of total %d files in store; data_usage_key: %s, num_to_keep: %d.',
                len(files_to_delete),
                len(all_files_by_date),
                data_usage_key,
                num_to_keep,
            )
        except Exception:  # pylint: disable=broad-except
            log.exception('BlockStructure: Exception when deleting old files; data_usage_key: %s.', data_usage_key)

    @classmethod
    def _delete_files(cls, files):
        """
        Deletes the given files from storage.
        """
        storage = _bs_model_storage()
        list(map(storage.delete, files))

    @classmethod
    def _get_all_files(cls, data_usage_key):
        """
        Returns all filenames that exist for the given key.
        """
        directory = _directory_name(data_usage_key)
        _, filenames = _bs_model_storage().listdir(directory)
        return [
            _create_path(directory, filename)
            for filename in filenames
            if filename and not filename.startswith('.')
        ]

    @classmethod
    def _log(cls, bs_model, operation, serialized_data):
        """
        Writes log information for the given values.
        """
        log.info(
            'BlockStructure: %s in store %s at %s%s; %s, size: %d',
            operation,
            bs_model.data.storage.__class__,
            getattr(bs_model.data.storage, 'bucket_name', ''),
            getattr(bs_model.data.storage, 'location', ''),
            bs_model,
            len(serialized_data),
        )
