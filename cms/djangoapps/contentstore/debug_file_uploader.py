""" Upload file handler to help test progress bars in uploads. """


import time

from django.core.files.uploadhandler import FileUploadHandler


class DebugFileUploader(FileUploadHandler):  # pylint: disable=missing-class-docstring
    def __init__(self, request=None):
        super().__init__(request)
        self.count = 0

    def receive_data_chunk(self, raw_data, start):
        time.sleep(1)
        self.count = self.count + len(raw_data)
        fail_at = None
        if 'fail_at' in self.request.GET:
            fail_at = int(self.request.GET.get('fail_at'))
        if fail_at and self.count > fail_at:
            raise Exception('Triggered fail')

        return raw_data

    def file_complete(self, file_size):
        return None
