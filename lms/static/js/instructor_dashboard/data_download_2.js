/* globals _, DataDownloadV2, PendingInstructorTasks, ReportDownloads */

(function() {
    'use strict';

    // eslint-disable-next-line no-unused-vars
    var DataDownloadV2, PendingInstructorTasks, ReportDownloads, statusAjaxError;

    statusAjaxError = function() {
        return window.InstructorDashboard.util.statusAjaxError.apply(this, arguments);
    };

    PendingInstructorTasks = function() {
        return window.InstructorDashboard.util.PendingInstructorTasks;
    };

    ReportDownloads = function() {
        return window.InstructorDashboard.util.ReportDownloads;
    };

    DataDownloadV2 = (function() {
        function InstructorDashboardDataDownload($section) {
            var dataDownloadObj = this;
            this.$section = $section;
            this.$section.data('wrapper', this);
            this.$list_problem_responses_csv_input = this.$section.find("input[name='problem-location']");
            this.$download_display_text = $('.data-display-text');
            this.$download_request_response_error = $('.request-response-error');
            this.$download_display_table = $('.profile-data-display-table');
            this.$reports_request_response = $('.request-response');
            this.$reports_request_response_error = $('.request-response-error');
            this.report_downloads = new (ReportDownloads())(this.$section);
            this.instructor_tasks = new (PendingInstructorTasks())(this.$section);
            this.$download_report = $('.download-report');
            this.$gradeReportDownload = $('.grade-report-download');
            this.$report_type_selector = $('.report-type');
            this.$selection_informations = $('.selectionInfo');
            this.$data_display_table = $('.data-display-table-holder');
            this.$downloadProblemReport = $('#download-problem-report');
            this.$tabSwitch = $('.data-download-nav .btn-link');
            this.$selectedSection = $('#' + this.$tabSwitch.first().attr('data-section'));
            this.$learnerStatus = $('.learner-status');

            this.ERROR_MESSAGES = {
                ORADataReport: gettext('Error generating ORA data report. Please try again.'),
                problemGradeReport: gettext('Error generating problem grade report. Please try again.'),
                profileInformation: gettext('Error generating student profile information. Please try again.'),
                surveyResultReport: gettext('Error generating survey results. Please try again.'),
                proctoredExamResults: gettext('Error generating proctored exam results. Please try again.'),
                learnerWhoCanEnroll: gettext('Error generating list of students who may enroll. Please try again.'),
                viewCertificates: gettext('Error getting issued certificates list.')
            };

            /**
             * Removes text error/success messages and tables from UI
             */
            this.clear_display = function() {
                this.$download_display_text.empty();
                this.$download_display_table.empty();
                this.$download_request_response_error.empty();
                this.$reports_request_response.empty();
                this.$reports_request_response_error.empty();
                this.$data_display_table.empty();
                $('.msg-confirm').css({
                    display: 'none'
                });
                return $('.msg-error').css({
                    display: 'none'
                });
            };

            this.clear_display();

            /**
             * Show and hide selected tab data
             */
            this.$tabSwitch.click(function(event) {
                var selectedSection = '#' + $(this).attr('data-section');
                event.preventDefault();
                $('.data-download-nav .btn-link').removeClass('active-section');
                $('section.tab-data').hide();
                $(selectedSection).show();
                $(this).addClass('active-section');

                $(this).find('select').trigger('change');
                dataDownloadObj.$selectedSection = $(selectedSection);

                dataDownloadObj.clear_display();
            });

            this.$tabSwitch.first().click();

            /**
             * on change of report select update show and hide related descriptions
             */
            this.$report_type_selector.change(function() {
                var selectedOption = dataDownloadObj.$report_type_selector.val();
                dataDownloadObj.$selection_informations.each(function(index, elem) {
                    if ($(elem).hasClass(selectedOption)) {
                        $(elem).show();
                    } else {
                        $(elem).hide();
                    }
                });
                dataDownloadObj.clear_display();
            });

            this.selectedOption = function() {
                return dataDownloadObj.$selectedSection.find('select').find('option:selected');
            };

            /**
             * On click download button get selected option and pass it to handler function.
             */
            this.downloadReportClickHandler = function() {
                var selectedOption = dataDownloadObj.selectedOption();
                var errorMessage = dataDownloadObj.ERROR_MESSAGES[selectedOption.val()];

                if (selectedOption.data('directdownload')) {
                    location.href = selectedOption.data('endpoint') + '?csv=true';
                } else if (selectedOption.data('datatable')) {
                    dataDownloadObj.renderDataTable(selectedOption);
                } else {
                    dataDownloadObj.downloadCSV(selectedOption, errorMessage, false);
                }
            };
            this.$download_report.click(dataDownloadObj.downloadReportClickHandler);

            /**
             * Call data endpoint and invoke buildDataTable to render Table UI.
             * @param selected option element from report selector to get data-endpoint.
             * @param errorMessage Error message in case endpoint call fail.
             */
            this.renderDataTable = function(selected, errorMessage) {
                var url = selected.data('endpoint');
                dataDownloadObj.clear_display();
                dataDownloadObj.$data_display_table.text(gettext('Loading data...'));
                return $.ajax({
                    type: 'POST',
                    url: url,
                    error: function(error) {
                        dataDownloadObj.OnError(error, errorMessage);
                    },
                    success: function(data) {
                        dataDownloadObj.buildDataTable(data);
                    }
                });
            };

            this.$downloadProblemReport.click(function() {
                var data = {problem_location: dataDownloadObj.$list_problem_responses_csv_input.val()};
                dataDownloadObj.downloadCSV($(this), false, data);
            });

            this.$gradeReportDownload.click(function() {
                var errorMessage = gettext('Error generating grades. Please try again.');
                var data = {verified_learners_only: dataDownloadObj.$learnerStatus.val()};
                dataDownloadObj.downloadCSV($(this), errorMessage, data);
            });

            /**
             * Call data endpoint and render success/error message on dashboard UI.
             */
            this.downloadCSV = function(selected, errorMessage, postData) {
                var url = selected.data('endpoint');
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    data: postData,
                    error: function(error) {
                        dataDownloadObj.OnError(error, errorMessage);
                    },
                    success: function(data) {
                        if (data.grading_config_summary) {
                            edx.HtmlUtils.setHtml(
                                dataDownloadObj.$download_display_text, edx.HtmlUtils.HTML(data.grading_config_summary));
                        } else {
                            dataDownloadObj.$reports_request_response.text(data.status);
                            $('.msg-confirm').css({display: 'block'});
                        }
                    }
                });
            };

            this.OnError = function(error, errorMessage) {
                dataDownloadObj.clear_display();
                if (error.responseText) {
                    // eslint-disable-next-line no-param-reassign
                    errorMessage = JSON.parse(error.responseText);
                }
                dataDownloadObj.$download_request_response_error.text(errorMessage);
                return dataDownloadObj.$download_request_response_error.css({
                    display: 'block'
                });
            };
            /**
             * render data table on dashboard UI with given data.
             */
            this.buildDataTable = function(data) {
                var $tablePlaceholder, columns, feature, gridData, options, grid;
                dataDownloadObj.clear_display();
                options = {
                    enableCellNavigation: true,
                    enableColumnReorder: false,
                    forceFitColumns: true,
                    rowHeight: 35,
                    autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
                };
                columns = (function() {
                    var i, len, ref, results;
                    ref = data.queried_features;
                    results = [];
                    for (i = 0, len = ref.length; i < len; i++) {
                        feature = ref[i];
                        results.push({
                            id: feature,
                            field: feature,
                            name: data.feature_names[feature]
                        });
                    }
                    return results;
                }());
                gridData = data.hasOwnProperty('students') ? data.students : data.certificates;
                $tablePlaceholder = $('<div/>', {
                    class: 'slickgrid'
                });
                dataDownloadObj.$download_display_table.append($tablePlaceholder);
                grid = new window.Slick.Grid($tablePlaceholder[0], gridData, columns, options);
                return grid.autosizeColumns();
            };
        }

        InstructorDashboardDataDownload.prototype.onClickTitle = function() {
            this.clear_display();
            this.instructor_tasks.task_poller.start();
            return this.report_downloads.downloads_poller.start();
        };

        InstructorDashboardDataDownload.prototype.onExit = function() {
            this.instructor_tasks.task_poller.stop();
            return this.report_downloads.downloads_poller.stop();
        };
        return InstructorDashboardDataDownload;
    }());

    _.defaults(window, {
        InstructorDashboard: {}
    });

    _.defaults(window.InstructorDashboard, {
        sections: {}
    });

    _.defaults(window.InstructorDashboard.sections, {
        DataDownloadV2: DataDownloadV2
    });
}).call(this);
