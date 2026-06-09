define([
    'domReady', 'js/views/export', 'jquery', 'gettext'
], function(domReady, Export, $, gettext) {
    'use strict';

    return function(courselikeHomeUrl, statusUrl) {
        var $submitBtn = $('.action-export'),
            unloading = false,
            previousExport = Export.storedExport(courselikeHomeUrl);

        var onComplete = function() {
            $submitBtn.show();
        };

        var startExport = function(e) {
            e.preventDefault();
            $submitBtn.hide();
            Export.reset();
            Export.start(statusUrl).then(onComplete);
            $.ajax({
                type: 'POST',
                url: window.location.pathname,
                data: {},
                success: function(result, textStatus, xhr) {
                    if (xhr.status === 200) {
                        setTimeout(function() { Export.pollStatus(result); }, 1000);
                    } else {
                        // It could be that the user is simply refreshing the page
                        // so we need to be sure this is an actual error from the server
                        if (!unloading) {
                            $(window).off('beforeunload.import');

                            Export.reset();
                            onComplete();

                            Export.showError(gettext('Your export has failed.'));
                        }
                    }
                }
            });
        };

        $(window).on('beforeunload', function() { unloading = true; });

        // Display the status of last file upload on page load
        if (previousExport) {
            if (previousExport.completed !== true) {
                $submitBtn.hide();
            }
            Export.resume().then(onComplete);
        }

        domReady(function() {
            // export form setup
            $submitBtn.bind('click', startExport);
        });
    };
});
