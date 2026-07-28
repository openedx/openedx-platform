/**
 * Course export-related js.
 */
define([
    'jquery', 'underscore', 'gettext', 'moment', 'common/js/components/views/feedback_prompt',
    'edx-ui-toolkit/js/utils/html-utils', 'jquery.cookie'
], function($, _, gettext, moment, PromptView, HtmlUtils) {
    'use strict';

    /** ******** Private properties *************************************** */

    var COOKIE_NAME = 'lastexport';

    var STAGE = {
        PREPARING: 0,
        EXPORTING: 1,
        COMPRESSING: 2,
        SUCCESS: 3
    };

    var STATE = {
        READY: 1,
        IN_PROGRESS: 2,
        SUCCESS: 3,
        ERROR: 4
    };

    var courselikeHomeUrl;
    var current = {stage: 0, state: STATE.READY, downloadUrl: null};
    var deferred = null;
    var statusUrl = null;
    var successUnixDate = null;
    var timeout = {id: null, delay: 1000};
    var $dom = {
        downloadLink: $('#download-exported-button'),
        stages: $('ol.status-progress').children(),
        successStage: $('.item-progresspoint-success'),
        wrapper: $('div.wrapper-status')
    };

    /** ******** Private functions **************************************** */

    /**
     * Makes Export feedback status list visible
     *
     */
    var displayFeedbackList = function() {
        $dom.wrapper.removeClass('is-hidden');
    };

    /**
     * Updates the Export feedback status list
     *
     * @param {string} [currStageMsg=''] The message to show on the
     *   current stage (for now only in case of error)
     */
    var updateFeedbackList = function(currStageMsg) {
        var $checkmark, $curr, $prev, $next;
        var date, stageMsg, time;

        $checkmark = $dom.successStage.find('.icon');
        stageMsg = currStageMsg || '';

        function completeStage(stage) {
            $(stage)
                .removeClass('is-not-started is-started')
                .addClass('is-complete');
        }

        function errorStage(stage) {
            if (!$(stage).hasClass('has-error')) {
                stageMsg = HtmlUtils.joinHtml(
                    HtmlUtils.HTML('<p class="copy error">'),
                    stageMsg,
                    HtmlUtils.HTML('</p>')
                );
                $(stage)
                    .removeClass('is-started')
                    .addClass('has-error')
                    .find('p.copy')
                    .hide()
                    .after(HtmlUtils.ensureHtml(stageMsg).toString());
            }
        }

        function resetStage(stage) {
            $(stage)
                .removeClass('is-complete is-started has-error')
                .addClass('is-not-started')
                .find('p.error')
                .remove()
                .end()
                .find('p.copy')
                .show();
        }

        switch (current.state) {
        case STATE.READY:
            _.map($dom.stages, resetStage);

            break;

        case STATE.IN_PROGRESS:
            $prev = $dom.stages.slice(0, current.stage);
            $curr = $dom.stages.eq(current.stage);

            _.map($prev, completeStage);
            $curr.removeClass('is-not-started').addClass('is-started');

            break;

        case STATE.SUCCESS:
            date = moment(successUnixDate).utc().format('MM/DD/YYYY');
            time = moment(successUnixDate).utc().format('HH:mm');

            _.map($dom.stages, completeStage);

            $dom.successStage
                .find('.item-progresspoint-success-date')
                .text('(' + date + ' at ' + time + ' UTC)');

            break;

        case STATE.ERROR:
            // Make all stages up to, and including, the error stage 'complete'.
            $prev = $dom.stages.slice(0, current.stage + 1);
            $curr = $dom.stages.eq(current.stage);
            $next = $dom.stages.slice(current.stage + 1);

            _.map($prev, completeStage);
            _.map($next, resetStage);
            errorStage($curr);

            break;

        default:
            // Invalid state, don't change anything
            return;
        }

        if (current.state === STATE.SUCCESS) {
            $checkmark.removeClass('fa-square-o').addClass('fa-check-square-o');
            $dom.downloadLink.attr('href', current.downloadUrl);
        } else {
            $checkmark.removeClass('fa-check-square-o').addClass('fa-square-o');
            $dom.downloadLink.attr('href', '#');
        }
    };

    /**
     * Sets the Export in the "error" status.
     *
     * Immediately stops any further polling from the server.
     * Displays the error message at the list element that corresponds
     * to the stage where the error occurred.
     *
     * @param {string} msg Error message to display.
     * @param {int} [stage=current.stage] Stage of export process at which error occurred.
     */
    var error = function(msg, stage) {
        current.stage = Math.abs(stage || current.stage); // Could be negative
        current.state = STATE.ERROR;

        clearTimeout(timeout.id);
        updateFeedbackList(msg);

        deferred.resolve();
    };

    /**
     * Stores in a cookie the current export data
     *
     * @param {boolean} [completed=false] If the export has been completed or not
     */
    var storeExport = function(completed) {
        $.cookie(COOKIE_NAME, JSON.stringify({
            statusUrl: statusUrl,
            date: moment().valueOf(),
            completed: completed || false
        }), {path: window.location.pathname});
    };

    /** ******** Public functions ***************************************** */

    var CourseExport = {
        /**
         * Fetches the previous stored export
         *
         * @param {string} contentHomeUrl the full URL to the course or library being exported
         * @return {JSON} the data of the previous export
         */
        storedExport: function(contentHomeUrl) {
            var storedData = JSON.parse($.cookie(COOKIE_NAME) || null);
            if (storedData) {
                successUnixDate = storedData.date;
            }
            if (contentHomeUrl) {
                courselikeHomeUrl = contentHomeUrl;
            }
            return storedData;
        },

        /**
         * Sets the Export on the "success" status
         *
         * If it wasn't already, marks the stored export as "completed",
         * and updates its date timestamp
         */
        success: function() {
            current.state = STATE.SUCCESS;

            if (this.storedExport().completed !== true) {
                storeExport(true);
            }

            updateFeedbackList();

            deferred.resolve();
        },

        /**
         * Entry point for server feedback
         *
         * Checks for export status updates every `timeout` milliseconds,
         * and updates the page accordingly.
         *
         * @param {int} [stage=0] Starting stage.
         */
        pollStatus: function(data) {
            var editUnitUrl = null,
                msg = data;
            if (current.state !== STATE.IN_PROGRESS) {
                return;
            }

            current.stage = data.ExportStatus || STAGE.PREPARING;

            if (current.stage === STAGE.SUCCESS) {
                current.downloadUrl = data.ExportOutput;
                this.success();
            } else if (current.stage < STAGE.PREPARING) { // Failed
                if (data.ExportError) {
                    msg = data.ExportError;
                }
                if (msg.raw_error_msg) {
                    editUnitUrl = msg.edit_unit_url;
                    msg = msg.raw_error_msg;
                }
                error(msg);
                this.showError(editUnitUrl, msg);
            } else { // In progress
                updateFeedbackList();

                $.getJSON(statusUrl, function(result) {
                    timeout.id = setTimeout(function() {
                        this.pollStatus(result);
                    }.bind(this), timeout.delay);
                }.bind(this));
            }
        },

        /**
         * Resets the Export internally and visually
         *
         */
        reset: function() {
            current.stage = STAGE.PREPARING;
            current.state = STATE.READY;
            current.downloadUrl = null;

            clearTimeout(timeout.id);
            updateFeedbackList();
        },

        /**
         * Show last export status from server and start sending requests
         * to the server for status updates
         *
         * @return {jQuery promise}
         */
        resume: function() {
            deferred = $.Deferred();
            statusUrl = this.storedExport().statusUrl;

            $.getJSON(statusUrl, function(data) {
                current.stage = data.ExportStatus;
                current.downloadUrl = data.ExportOutput;

                displayFeedbackList();
                current.state = STATE.IN_PROGRESS;
                this.pollStatus(data);
            }.bind(this));

            return deferred.promise();
        },

        /**
         * Show a dialog giving further information about the details of an export error.
         *
         * @param {string} editUnitUrl URL of the unit in which the error occurred, if known
         * @param {string} errMsg Detailed error message
         */
        showError: function(editUnitUrl, errMsg) {
            var action,
                dialog,
                msg = '';
            if (editUnitUrl) {
                dialog = new PromptView({
                    title: gettext('There has been an error while exporting.'),
                    message: gettext('There has been a failure to export to XML at least one component. '
                        + 'It is recommended that you go to the edit page and repair the error before attempting '
                        + 'another export. Please check that all components on the page are valid and do not display '
                        + 'any error messages.'),
                    intent: 'error',
                    actions: {
                        primary: {
                            text: gettext('Correct failed component'),
                            click: function(view) {
                                view.hide();
                                document.location = editUnitUrl;
                            }
                        },
                        secondary: {
                            text: gettext('Return to Export'),
                            click: function(view) {
                                view.hide();
                            }
                        }
                    }
                });
            } else {
                msg += gettext('Your course could not be exported to XML. There is not enough information to '
                    + 'identify the failed component. Inspect your course to identify any problematic components '
                    + 'and try again.');
                action = gettext('Take me to the main course page');
                msg += ' ' + gettext('The raw error message is:') + ' ' + errMsg;
                dialog = new PromptView({
                    title: gettext('There has been an error with your export.'),
                    message: msg,
                    intent: 'error',
                    actions: {
                        primary: {
                            text: action,
                            click: function(view) {
                                view.hide();
                                document.location = courselikeHomeUrl;
                            }
                        },
                        secondary: {
                            text: gettext('Cancel'),
                            click: function(view) {
                                view.hide();
                            }
                        }
                    }
                });
            }

            // The CSS animation for the dialog relies on the 'js' class
            // being on the body. This happens after this JavaScript is executed,
            // causing a 'bouncing' of the dialog after it is initially shown.
            // As a workaround, add this class first.
            $('body').addClass('js');
            dialog.show();
        },

        /**
         * Starts the exporting process.
         * Makes status list visible and starts showing export progress.
         *
         * @param {string} url The full URL to use to query the server
         *     about the export status
         * @return {jQuery promise}
         */
        start: function(url) {
            current.state = STATE.IN_PROGRESS;
            deferred = $.Deferred();

            statusUrl = url;

            storeExport();
            displayFeedbackList();
            updateFeedbackList();

            return deferred.promise();
        }
    };

    return CourseExport;
});
