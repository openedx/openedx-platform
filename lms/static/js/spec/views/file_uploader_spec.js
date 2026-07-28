define(['backbone', 'jquery', 'sinon', 'js/views/file_uploader', 'common/js/spec_helpers/template_helpers',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'js/models/notification', 'string_utils'],
function(Backbone, $, sinon, FileUploaderView, TemplateHelpers, AjaxHelpers, NotificationModel) {
    describe('FileUploaderView', function() {
        var verifyTitle, verifyInputLabel, verifyInputTip, verifySubmitButton, verifyExtensions, verifyText,
            verifyFileUploadOption, verifyNotificationMessage, verifySubmitButtonEnabled, mimicUpload,
            respondWithSuccess, respondWithError, fileUploaderView, requests, xhrFactory,
            url = 'http://test_url/';

        verifyText = function(css, expectedText) {
            expect(fileUploaderView.$(css).text().trim()).toBe(expectedText);
        };

        verifyTitle = function(expectedTitle) { verifyText('.form-title', expectedTitle); };

        verifyInputLabel = function(expectedLabel) { verifyText('.field-label', expectedLabel); };

        verifyInputTip = function(expectedTip) { verifyText('.tip', expectedTip); };

        verifySubmitButton = function(expectedButton) { verifyText('.submit-file-button', expectedButton); };

        verifyExtensions = function(expectedExtensions) {
            var acceptAttribute = fileUploaderView.$('input.input-file').attr('accept');
            if (expectedExtensions) {
                expect(acceptAttribute).toBe(expectedExtensions);
            } else {
                expect(acceptAttribute).toBe(undefined);
            }
        };

        verifySubmitButtonEnabled = function(expectedEnabled) {
            var submitButton = fileUploaderView.$('.submit-file-button');
            if (expectedEnabled) {
                expect(submitButton).not.toHaveClass('is-disabled');
            } else {
                expect(submitButton).toHaveClass('is-disabled');
            }
        };

        verifyFileUploadOption = function(option, expectedValue) {
            expect(fileUploaderView.$('#file-upload-form').fileupload('option', option)).toBe(expectedValue);
        };

        verifyNotificationMessage = function(expectedMessage, type) {
            verifyText('.file-upload-form-result .message-' + type + ' .message-title', expectedMessage);
        };

        mimicUpload = function() {
            var param = {files: [{name: 'upload_file.txt'}]};
            fileUploaderView.$('#file-upload-form').fileupload('add', param);
            verifySubmitButtonEnabled(true);
            fileUploaderView.$('.submit-file-button').click();

            // No file will actually be uploaded because "uploaded_file.txt" doesn't actually exist.
            AjaxHelpers.expectRequest(requests, 'POST', url, new FormData());
        };

        respondWithSuccess = function(requests) {
            AjaxHelpers.respondWithJson(requests, {});
        };

        respondWithError = function(requests, errorMessage) {
            if (errorMessage) {
                AjaxHelpers.respondWithError(requests, 500, {error: errorMessage});
            } else {
                AjaxHelpers.respondWithError(requests);
            }
        };

        beforeEach(function() {
            setFixtures('<div></div>');
            TemplateHelpers.installTemplate('templates/file-upload');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/notification');
            fileUploaderView = new FileUploaderView({url: url}).render();
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('has default values', function() {
            verifyTitle('');
            verifyInputLabel('');
            verifyInputTip('');
            verifySubmitButton('Upload File');
            verifyExtensions(null);
            verifySubmitButtonEnabled(false);
        });

        it('can set text values and extensions', function() {
            fileUploaderView = new FileUploaderView({
                title: 'file upload title',
                inputLabel: 'test label',
                inputTip: 'test tip',
                submitButtonText: 'upload button text',
                extensions: '.csv,.txt'
            }).render();

            verifyTitle('file upload title');
            verifyInputLabel('test label');
            verifyInputTip('test tip');
            verifySubmitButton('upload button text');
            verifyExtensions('.csv,.txt');
        });

        it('can store upload URL', function() {
            expect(fileUploaderView.$('#file-upload-form').attr('action')).toBe(url);
        });

        it('sets autoUpload to false', function() {
            verifyFileUploadOption('autoUpload', false);
        });

        it('sets replaceFileInput to false', function() {
            verifyFileUploadOption('replaceFileInput', false);
        });

        it('handles errors with default message', function() {
            mimicUpload();
            respondWithError(requests);
            verifyNotificationMessage("Your upload of 'upload_file.txt' failed.", 'error');
        });

        it('handles errors with custom message', function() {
            fileUploaderView = new FileUploaderView({
                url: url,
                errorNotification: function(file, event, data) {
                    var message = interpolate_text("Custom error for '{file}'", {file: file});
                    return new NotificationModel({
                        type: 'customized',
                        title: message
                    });
                }
            }).render();
            mimicUpload();
            respondWithError(requests, 'server error');
            verifyNotificationMessage("Custom error for 'upload_file.txt'", 'customized');
        });

        it('handles server error message', function() {
            mimicUpload();
            respondWithError(requests, 'server error');
            verifyNotificationMessage('server error', 'error');
        });

        it('handles success with default message', function() {
            mimicUpload();
            respondWithSuccess(requests);
            verifyNotificationMessage("Your upload of 'upload_file.txt' succeeded.", 'confirmation');
        });

        it('handles success with custom message', function() {
            fileUploaderView = new FileUploaderView({
                url: url,
                successNotification: function(file, event, data) {
                    var message = interpolate_text("Custom success message for '{file}'", {file: file});
                    return new NotificationModel({
                        type: 'customized',
                        title: message
                    });
                }
            }).render();
            mimicUpload();
            respondWithSuccess(requests);
            verifyNotificationMessage("Custom success message for 'upload_file.txt'", 'customized');
        });
    });
});
