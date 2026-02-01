/* globals _, Logger, Slick, tinyMCE, InstructorDashboard, PendingInstructorTasks, createTaskListTable */

(function() {
    'use strict';

    var IntervalManager, KeywordValidator,
        createEmailContentTable, createEmailMessageViews,
        findAndAssert, pWrapper, plantInterval, plantTimeout,
        sentToFormatter, setupCopyEmailButton, subjectFormatter,
        unknownIfNullFormatter, unknownP,
        anyOf = [].indexOf || function(item) {
            var i, l;
            for (i = 0, l = this.length; i < l; i++) {
                if (i in this && this[i] === item) {
                    return i;
                }
            }
            return -1;
        };

    plantTimeout = function(ms, cb) {
        return setTimeout(cb, ms);
    };

    plantInterval = function(ms, cb) {
        return setInterval(cb, ms);
    };

    findAndAssert = function($root, selector) {
        var item, msg;
        item = $root.find(selector);
        if (item.length !== 1) {
            msg = 'Failed Element Selection';
            throw msg;
        } else {
            return item;
        }
    };

    this.statusAjaxError = function(handler) {
        return function(jqXHR, textStatus, errorThrown) { //  eslint-disable-line no-unused-vars
            return handler.apply(this, arguments);
        };
    };

    this.createTaskListTable = function($tableTasks, tasksData) {
        var $tablePlaceholder, columns, options, tableData, grid;
        $tableTasks.empty();
        options = {
            enableCellNavigation: true,
            enableColumnReorder: false,
            autoHeight: true,
            rowHeight: 100,
            forceFitColumns: true,
            autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
        };
        columns = [
            {
                id: 'task_type',
                field: 'task_type',
                /*
        Translators: a "Task" is a background process such as grading students or sending email
        */

                name: gettext('Task Type'),
                minWidth: 102
            }, {
                id: 'task_input',
                field: 'task_input',
                /*
        Translators: a "Task" is a background process such as grading students or sending email
        */

                name: gettext('Task inputs'),
                minWidth: 150
            }, {
                id: 'task_id',
                field: 'task_id',
                /*
        Translators: a "Task" is a background process such as grading students or sending email
        */

                name: gettext('Task ID'),
                minWidth: 150
            }, {
                id: 'requester',
                field: 'requester',
                /*
        Translators: a "Requester" is a username that requested a task such as sending email
        */

                name: gettext('Requester'),
                minWidth: 80
            }, {
                id: 'created',
                field: 'created',
                /*
        Translators: A timestamp of when a task (eg, sending email) was submitted appears after this
        */

                name: gettext('Submitted'),
                minWidth: 120
            }, {
                id: 'duration_sec',
                field: 'duration_sec',
                /*
        Translators: The length of a task (eg, sending email) in seconds appears this
        */

                name: gettext('Duration (sec)'),
                minWidth: 80
            }, {
                id: 'task_state',
                field: 'task_state',
                /*
        Translators: The state (eg, "In progress") of a task (eg, sending email) appears after this.
        */

                name: gettext('State'),
                minWidth: 80
            }, {
                id: 'status',
                field: 'status',
                /*
        Translators: a "Task" is a background process such as grading students or sending email
        */

                name: gettext('Task Status'),
                minWidth: 80
            }, {
                id: 'task_message',
                field: 'task_message',
                /*
        Translators: a "Task" is a background process such as grading students or sending email
        */

                name: gettext('Task Progress'),
                minWidth: 120
            }
        ];
        tableData = tasksData;
        $tablePlaceholder = $('<div/>', {
            class: 'slickgrid'
        });
        $tableTasks.append($tablePlaceholder);
        grid = new Slick.Grid($tablePlaceholder[0], tableData, columns, options);
        return grid.autosizeColumns();
    };

    subjectFormatter = function(row, cell, value) {
        var subjectText;
        if (value === null) {
            return gettext('An error occurred retrieving your email. Please try again later, and contact technical support if the problem persists.'); // eslint-disable-line max-len
        }
        subjectText = $('<span>').text(value.subject).html();
        return edx.HtmlUtils.joinHtml(edx.HtmlUtils.HTML(
            '<p><a href="#email_message_'), value.id, edx.HtmlUtils.HTML(
            '" id="email_message_'), value.id, edx.HtmlUtils.HTML('_trig">'),
        subjectText, edx.HtmlUtils.HTML('</a></p>')
        );
    };

    pWrapper = function(value) {
        return edx.HtmlUtils.joinHtml(edx.HtmlUtils.HTML('<p>'), value, edx.HtmlUtils.HTML('</p>'));
    };

    unknownP = function() {
        return pWrapper(gettext('Unknown'));
    };

    sentToFormatter = function(row, cell, value) {
        if (value === null) {
            return unknownP();
        } else {
            return pWrapper(value.join(', '));
        }
    };

    unknownIfNullFormatter = function(row, cell, value) {
        if (value === null) {
            return unknownP();
        } else {
            return pWrapper(value);
        }
    };

    createEmailContentTable = function($tableEmails, $tableEmailsInner, emailData) {
        var $tablePlaceholder, columns, options, tableData, grid;
        $tableEmailsInner.empty();
        $tableEmails.show();
        options = {
            enableCellNavigation: true,
            enableColumnReorder: false,
            autoHeight: true,
            rowHeight: 50,
            forceFitColumns: true,
            autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
        };
        columns = [
            {
                id: 'email',
                field: 'email',
                name: gettext('Subject'),
                minWidth: 80,
                cssClass: 'email-content-cell',
                formatter: subjectFormatter
            }, {
                id: 'requester',
                field: 'requester',
                name: gettext('Sent By'),
                minWidth: 80,
                maxWidth: 100,
                cssClass: 'email-content-cell',
                formatter: unknownIfNullFormatter
            }, {
                id: 'sent_to',
                field: 'sent_to',
                name: gettext('Sent To'),
                minWidth: 80,
                maxWidth: 100,
                cssClass: 'email-content-cell',
                formatter: sentToFormatter
            }, {
                id: 'created',
                field: 'created',
                name: gettext('Time Sent'),
                minWidth: 80,
                cssClass: 'email-content-cell',
                formatter: unknownIfNullFormatter
            }, {
                id: 'number_sent',
                field: 'number_sent',
                name: gettext('Number Sent'),
                minwidth: 100,
                maxWidth: 150,
                cssClass: 'email-content-cell',
                formatter: unknownIfNullFormatter
            }
        ];
        tableData = emailData;
        $tablePlaceholder = $('<div/>', {
            class: 'slickgrid'
        });
        $tableEmailsInner.append($tablePlaceholder);
        grid = Slick.Grid($tablePlaceholder[0], tableData, columns, options);
        grid.autosizeColumns();
        return $tableEmails.append($('<br/>'));
    };

    createEmailMessageViews = function($messagesWrapper, emails) {
        var $closeButton, $created, $emailContent, $emailContentHeader,
            $emailHeader, $emailWrapper, $message, $messageContent,
            $requester, $sentTo, $subject, emailId, emailInfo, interpolateHeader, i, len;
        $messagesWrapper.empty();
        for (i = 0, len = emails.length; i < len; i++) {
            emailInfo = emails[i];
            if (!emailInfo.email) {
                return;
            }
            emailId = emailInfo.email.id;
            $messageContent = $('<section>', {
                'aria-hidden': 'true',
                class: 'modal email-modal',
                id: 'email_message_' + emailId
            });
            $emailWrapper = $('<div>', {
                class: 'inner-wrapper email-content-wrapper'
            });
            $emailHeader = $('<div>', {
                class: 'email-content-header'
            });
            $emailHeader.append($('<input>', {
                type: 'button',
                name: 'copy-email-body-text',
                value: gettext('Copy Email To Editor'),
                id: 'copy_email_' + emailId
            }));
            $closeButton = $('<a>', {
                href: '#',
                class: 'close-modal'
            });
            $closeButton.append($('<i>', {
                class: 'icon fa fa-times'
            }));
            $emailHeader.append($closeButton);
            interpolateHeader = function(title, value) {
                return edx.HtmlUtils.setHtml($('<h2>', {
                    class: 'message-bold'
                }), edx.HtmlUtils.joinHtml(edx.HtmlUtils.HTML('<em>'), title, edx.HtmlUtils.HTML('</em>'), value));
            };
            $subject = interpolateHeader(gettext('Subject:'), emailInfo.email.subject);
            $requester = interpolateHeader(gettext('Sent By:'), emailInfo.requester);
            $created = interpolateHeader(gettext('Time Sent:'), emailInfo.created);
            $sentTo = interpolateHeader(gettext('Sent To:'), emailInfo.sent_to.join(', '));
            $emailHeader.append($subject);
            $emailHeader.append($requester);
            $emailHeader.append($created);
            $emailHeader.append($sentTo);
            $emailWrapper.append($emailHeader);
            $emailWrapper.append($('<hr>'));
            $emailContent = $('<div>', {
                class: 'email-content-message'
            });
            $emailContentHeader = edx.HtmlUtils.setHtml($('<h2>', {
                class: 'message-bold'
            }), edx.HtmlUtils.joinHtml(edx.HtmlUtils.HTML('<em>'), gettext('Message:'), edx.HtmlUtils.HTML('</em>')));
            $emailContent.append($emailContentHeader);
            $message = edx.HtmlUtils.setHtml($('<div>'), edx.HtmlUtils.HTML(emailInfo.email.html_message));
            $emailContent.append($message);
            $emailWrapper.append($emailContent);
            $messageContent.append($emailWrapper);
            $messagesWrapper.append($messageContent);
            $('#email_message_' + emailInfo.email.id + '_trig').leanModal({
                closeButton: '.close-modal',
                copyEmailButton: '#copy_email_' + emailId
            });
            setupCopyEmailButton(emailId, emailInfo.email.html_message, emailInfo.email.subject);
        }
    };

    setupCopyEmailButton = function(emailId, htmlMessage, subject) {
        return $('#copy_email_' + emailId).click(function() {
            var editor;
            editor = tinyMCE.get('mce_0');
            editor.setContent(htmlMessage);
            return $('#id_subject').val(subject);
        });
    };

    IntervalManager = (function() {
        function intervalManager(ms, fn) {
            this.ms = ms;
            this.fn = fn;
            this.intervalID = null;
            this.failedTries = 0;
        }

        intervalManager.prototype.start = function() {
            this.fn();
            if (this.intervalID === null) {
                this.intervalID = setInterval(this.fn, this.ms);
                return this.intervalID;
            }
            return this.intervalID;
        };

        intervalManager.prototype.stop = function() {
            clearInterval(this.intervalID);
            this.intervalID = null;
            return this.intervalID;
        };

        intervalManager.prototype.failed_retry_threshold = 5;

        intervalManager.prototype.backOff = function() {
            this.failedTries++;
            if (this.failedTries >= this.failed_retry_threshold) {
                this.stop();
            }
        };

        return intervalManager;
    }());

    this.PendingInstructorTasks = (function() {
        function PendingInstructorTasks($section) {
            var TASK_LIST_POLL_INTERVAL,
                ths = this;
            this.$section = $section;
            this.reload_running_tasks_list = function() {
                return PendingInstructorTasks.prototype.reload_running_tasks_list.apply(
                    ths, arguments
                );
            };
            this.$running_tasks_section = findAndAssert(this.$section, '.running-tasks-section');
            this.$table_running_tasks = findAndAssert(this.$section, '.running-tasks-table');
            this.$no_tasks_message = findAndAssert(this.$section, '.no-pending-tasks-message');
            if (this.$table_running_tasks.length) {
                TASK_LIST_POLL_INTERVAL = 20000;
                this.reload_running_tasks_list();
                this.task_poller = new IntervalManager(TASK_LIST_POLL_INTERVAL, function() {
                    return ths.reload_running_tasks_list();
                });
            }
        }

        PendingInstructorTasks.prototype.reload_running_tasks_list = function() {
            var listEndpoint,
                ths = this;
            listEndpoint = this.$table_running_tasks.data('endpoint');
            return $.ajax({
                type: 'POST',
                dataType: 'json',
                url: listEndpoint,
                success: function(data) {
                    if (data.tasks.length) {
                        createTaskListTable(ths.$table_running_tasks, data.tasks);
                        ths.$no_tasks_message.hide();
                        return ths.$running_tasks_section.show();
                    } else {
                        ths.$running_tasks_section.hide();
                        ths.$no_tasks_message.empty();
                        ths.$no_tasks_message.append($('<p>').text(gettext('No tasks currently running.')));
                        return ths.$no_tasks_message.show();
                    }
                },
                error: function() {
                    ths.task_poller.backOff();
                }
            });
        };

        return PendingInstructorTasks;
    }());

    KeywordValidator = (function() {
        function keywordValidator() {}

        keywordValidator.keyword_regex = /%%+[^%]+%%/g;

        keywordValidator.keywords = [
            '%%USER_ID%%', '%%USER_FULLNAME%%', '%%COURSE_DISPLAY_NAME%%', '%%COURSE_END_DATE%%'
        ];

        keywordValidator.validate_string = function(string) {
            var foundKeyword, foundKeywords, invalidKeywords, isValid,
                keywords, regexMatch, validation, i, len;
            regexMatch = string.match(KeywordValidator.keyword_regex);
            foundKeywords = regexMatch === null ? [] : regexMatch;
            invalidKeywords = [];
            isValid = true;
            keywords = KeywordValidator.keywords;
            validation = function(foundkeyword) {
                if (anyOf.call(keywords, foundkeyword) < 0) {
                    return invalidKeywords.push(foundkeyword);
                } else {
                    return invalidKeywords;
                }
            };
            for (i = 0, len = foundKeywords.length; i < len; i++) {
                foundKeyword = foundKeywords[i];
                validation(foundKeyword);
            }
            if (invalidKeywords.length !== 0) {
                isValid = false;
            }
            return {
                isValid: isValid,
                invalidKeywords: invalidKeywords
            };
        };

        return keywordValidator;
    }).call(this);

    this.ReportDownloads = (function() {
        /* Report Downloads -- links expire quickly, so we refresh every 5 mins
      */

        function ReportDownloads($section) {
            var POLL_INTERVAL,
                reportdownloads = this;
            this.$section = $section;
            this.$report_downloads_table = this.$section.find('.report-downloads-table');
            POLL_INTERVAL = 20000;
            this.downloads_poller = new InstructorDashboard.util.IntervalManager(POLL_INTERVAL, function() {
                return reportdownloads.reload_report_downloads();
            });
        }

        ReportDownloads.prototype.reload_report_downloads = function() {
            var endpoint,
                ths = this;
            endpoint = this.$report_downloads_table.data('endpoint');
            return $.ajax({
                type: 'POST',
                dataType: 'json',
                url: endpoint,
                success: function(data) {
                    if (data.downloads.length) {
                        return ths.create_report_downloads_table(data.downloads);
                    } else {
                        return false;
                    }
                },
                error: function() {
                    ths.downloads_poller.backOff();
                }
            });
        };

        ReportDownloads.prototype.create_report_downloads_table = function(reportDownloadsData) {
            var $tablePlaceholder, columns, grid, options;
            this.$report_downloads_table.empty();
            options = {
                enableCellNavigation: true,
                enableColumnReorder: false,
                forceFitColumns: true,
                height: 50,
                autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
            };
            columns = [
                {
                    id: 'link',
                    field: 'link',
                    name: gettext('File Name'),
                    toolTip: gettext('Links are generated on demand and expire within 5 minutes due to the sensitive nature of student information.'), //  eslint-disable-line max-len
                    cssClass: 'file-download-link',
                    formatter: function(row, cell, value, columnDef, dataContext) {
                        return edx.HtmlUtils.joinHtml(edx.HtmlUtils.HTML(
                            '<a rel="noopener" target="_blank" href="'), dataContext.url,
                        edx.HtmlUtils.HTML('">'), dataContext.name,
                        edx.HtmlUtils.HTML('<span class="sr-only">Opens in a new tab</span></a>'));
                    }
                }
            ];
            $tablePlaceholder = $('<div/>', {
                class: 'slickgrid'
            });
            this.$report_downloads_table.append($tablePlaceholder);
            grid = new Slick.Grid($tablePlaceholder[0], reportDownloadsData, columns, options);
            grid.onClick.subscribe(function(event) {
                var reportUrl;
                reportUrl = event.target.href;
                if (reportUrl) {
                    return Logger.log('edx.instructor.report.downloaded', {
                        report_url: reportUrl
                    });
                }
                return Logger.log('edx.instructor.report.downloaded', {
                    report_url: reportUrl
                });
            });
            return grid.autosizeColumns();
        };

        return ReportDownloads;
    }());

    if (typeof _ !== 'undefined' && _ !== null) {
        _.defaults(window, {
            InstructorDashboard: {}
        });
        window.InstructorDashboard.util = {
            plantTimeout: plantTimeout,
            plantInterval: plantInterval,
            statusAjaxError: this.statusAjaxError,
            IntervalManager: IntervalManager,
            createTaskListTable: createTaskListTable,
            createEmailContentTable: createEmailContentTable,
            createEmailMessageViews: createEmailMessageViews,
            PendingInstructorTasks: PendingInstructorTasks,
            KeywordValidator: KeywordValidator,
            ReportDownloads: this.ReportDownloads
        };
    }
}).call(this);
