define([
    'domReady',
    '../views/import',
    'jquery',
    'gettext',
    'jQuery-File-Upload/js/jquery.fileupload',
    'jquery.cookie',
    '../../../../cms/js/main'
], function(domReady, Import, $, gettext) {
    'use strict';

    const IMPORTABLE_FILE_TYPES = /\.tar\.gz$|\.zip$/;

    return {
        Import: function(feedbackUrl) {
            var dbError = gettext('There was an error while importing the new course to our database.'),
                $bar = $('.progress-bar'),
                $fill = $('.progress-fill'),
                $submitBtn = $('.submit-button'),
                $chooseBtn = $('.view-import .choose-file-button'),
                defaults = [
                    gettext('There was an error during the upload process.') + '\n',
                    gettext('There was an error while unpacking the file.') + '\n',
                    gettext('There was an error while verifying the file you submitted.') + '\n',
                    dbError + '\n'
                ],
                unloading = false,
                previousImport = Import.storedImport(),
                file,
                onComplete = function() {
                    $bar.hide();
                    $chooseBtn
                        .find('.copy').text(gettext('Choose new file')).end()
                        .show();
                },
                showImportSubmit = function() {
                    var filepath = $(this).val(),
                        msg;

                    if (IMPORTABLE_FILE_TYPES.test(filepath)) {
                        $('.error-block').hide();
                        $('.file-name').text($(this).val().replace('C:\\fakepath\\', ''));
                        $('.file-name-block').show();
                        $chooseBtn.hide();
                        $submitBtn.show();
                        $('.progress').show();
                    } else {
                        msg = gettext('File format not supported. Please upload a file with a {ext} extension.')
                            .replace('{ext}', '<code>tar.gz or zip</code>');

                        $('.error-block').text(msg).show();
                    }
                };

            $(window).on('beforeunload', function() { unloading = true; });

            // Display the status of last file upload on page load
            if (previousImport) {
                $('.file-name-block')
                    .find('.file-name')
                    .text(previousImport.file.name)
                    .end()
                    .show();

                if (previousImport.completed !== true) {
                    $chooseBtn.hide();
                }

                Import.resume().then(onComplete);
            }

            $('#fileupload').fileupload({
                dataType: 'json',
                type: 'POST',
                maxChunkSize: 20 * 1000000, // 20 MB
                autoUpload: false,
                add: function(e, data) {
                    Import.reset();
                    $submitBtn.unbind('click');

                    file = data.files[0];

                    if (IMPORTABLE_FILE_TYPES.test(file.name)) {
                        $submitBtn.click(function(event) {
                            event.preventDefault();

                            Import.start(
                                file.name,
                                feedbackUrl.replace('fillerName', file.name)
                            ).then(onComplete);

                            $submitBtn.hide();
                            data.submit().complete(function(result, textStatus, xhr) {
                                var serverMsg, errMsg, stage;
                                if (xhr.status !== 200) {
                                    try {
                                        serverMsg = $.parseJSON(result.responseText) || {};
                                    } catch (err) {
                                        return;
                                    }

                                    errMsg = serverMsg.hasOwnProperty('ErrMsg') ? serverMsg.ErrMsg : '';

                                    if (serverMsg.hasOwnProperty('Stage')) {
                                        stage = Math.abs(serverMsg.Stage);
                                        Import.cancel(defaults[stage] + errMsg, stage);
                                    } else if (!unloading) {
                                        // It could be that the user is simply refreshing the page
                                        // so we need to be sure this is an actual error from the server
                                        $(window).off('beforeunload.import');

                                        Import.reset();
                                        onComplete();

                                        alert(gettext('Your import has failed.') + '\n\n' + errMsg); // eslint-disable-line max-len, no-alert
                                    }
                                }
                            });
                        });
                    } else {
                        // Can't fix this lint error without major structural changes, which I'm not comfortable
                        // doing given this file's test coverage
                        data.files = []; // eslint-disable-line no-param-reassign
                    }
                },

                progressall: function(e, data) {
                    // eslint-disable-next-line no-mixed-operators
                    var percentInt = data.loaded / data.total * 100,
                        percentVal = parseInt(percentInt, 10) + '%',
                        doneAt;
                    // Firefox makes ProgressEvent.loaded equal ProgressEvent.total only
                    // after receiving a response from the server (see Mozilla bug 637002),
                    // so for Firefox we jump the gun a little.
                    if (navigator.userAgent.toLowerCase().indexOf('firefox') > -1) {
                        doneAt = 95;
                    } else {
                        doneAt = 99;
                    }
                    if (percentInt >= doneAt) {
                        $bar.hide();

                        // Start feedback with delay so that current stage of
                        // import properly updates in session
                        setTimeout(function() { Import.pollStatus(); }, 3000);
                    } else {
                        $bar.show();
                        $fill.width(percentVal).text(percentVal);
                    }
                },
                sequentialUploads: true,
                notifyOnError: false
            });

            domReady(function() {
                // import form setup
                $('.view-import .file-input').bind('change', showImportSubmit);
                $('.view-import .choose-file-button, .view-import .choose-file-button-inline')
                    .bind('click', function(e) {
                        e.preventDefault();
                        $('.view-import .file-input').click();
                    });
            });
        }
    };
});
