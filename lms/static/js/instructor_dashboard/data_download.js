/* globals _ */

(function() {
    'use strict';

    var DataDownload, DataDownloadCertificate, PendingInstructorTasks, ReportDownloads, statusAjaxError;

    statusAjaxError = function() {
        return window.InstructorDashboard.util.statusAjaxError.apply(this, arguments);
    };

    PendingInstructorTasks = function() {
        return window.InstructorDashboard.util.PendingInstructorTasks;
    };

    ReportDownloads = function() {
        return window.InstructorDashboard.util.ReportDownloads;
    };

    DataDownloadCertificate = (function() {
        function InstructorDashboardDataDownloadCertificate($container) {
            var dataDownloadCert = this;
            this.$container = $container;
            this.$list_issued_certificate_table_btn = this.$container.find("input[name='issued-certificates-list']");
            this.$list_issued_certificate_csv_btn = this.$container.find("input[name='issued-certificates-csv']");
            this.$certificate_display_table = this.$container.find('.certificate-data-display-table');
            this.$certificates_request_err = this.$container.find('.issued-certificates-error.request-response-error');
            this.$list_issued_certificate_table_btn.click(function() {
                var url = dataDownloadCert.$list_issued_certificate_table_btn.data('endpoint');
                dataDownloadCert.clear_ui();
                dataDownloadCert.$certificate_display_table.text(gettext('Loading data...'));
                return $.ajax({
                    type: 'POST',
                    url: url,
                    error: function() {
                        dataDownloadCert.clear_ui();
                        dataDownloadCert.$certificates_request_err.text(
                            gettext('Error getting issued certificates list.')
                        );
                        return $('.issued_certificates .issued-certificates-error.msg-error').css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        var $tablePlaceholder, columns, feature, gridData, options, grid;
                        dataDownloadCert.clear_ui();
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
                        gridData = data.certificates;
                        $tablePlaceholder = $('<div/>', {
                            class: 'slickgrid'
                        });
                        dataDownloadCert.$certificate_display_table.append($tablePlaceholder);
                        grid = new window.Slick.Grid($tablePlaceholder[0], gridData, columns, options);
                        return grid.autosizeColumns();
                    }
                });
            });
            this.$list_issued_certificate_csv_btn.click(function() {
                dataDownloadCert.clear_ui();
                location.href = dataDownloadCert.$list_issued_certificate_csv_btn.data('endpoint') + '?csv=true';
            });
        }

        InstructorDashboardDataDownloadCertificate.prototype.clear_ui = function() {
            this.$certificate_display_table.empty();
            this.$certificates_request_err.empty();
            return $('.issued-certificates-error.msg-error').css({
                display: 'none'
            });
        };

        return InstructorDashboardDataDownloadCertificate;
    }());

    DataDownload = (function() {
        function InstructorDashboardDataDownload($section) {
            var dataDownloadObj = this;
            this.$section = $section;
            this.$section.data('wrapper', this);
            this.ddc = new DataDownloadCertificate(this.$section.find('.issued_certificates'));
            this.$list_studs_btn = this.$section.find("input[name='list-profiles']");
            this.$list_studs_csv_btn = this.$section.find("input[name='list-profiles-csv']");
            this.$proctored_exam_csv_btn = this.$section.find("input[name='proctored-exam-results-report']");
            this.$survey_results_csv_btn = this.$section.find("input[name='survey-results-report']");
            this.$list_may_enroll_csv_btn = this.$section.find("input[name='list-may-enroll-csv']");
            this.$list_learners_not_activated_csv_btn = this.$section.find("input[name='list-learners-not-activated-csv']");
            this.$list_problem_responses_csv_input = this.$section.find("input[name='problem-location']");
            this.$list_problem_responses_csv_btn = this.$section.find("input[name='list-problem-responses-csv']");
            this.$list_anon_btn = this.$section.find("input[name='list-anon-ids']");
            this.$grade_config_btn = this.$section.find("input[name='dump-gradeconf']");
            this.$calculate_grades_csv_btn = this.$section.find("input[name='calculate-grades-csv']");
            this.$problem_grade_report_csv_btn = this.$section.find("input[name='problem-grade-report']");
            this.$async_report_btn = this.$section.find("input[class='async-report-btn']");
            this.$download = this.$section.find('.data-download-container');
            this.$download_display_text = this.$download.find('.data-display-text');
            this.$download_request_response_error = this.$download.find('.request-response-error');
            this.$reports = this.$section.find('.reports-download-container');
            this.$download_display_table = this.$reports.find('.profile-data-display-table');
            this.$reports_request_response = this.$reports.find('.request-response');
            this.$reports_request_response_error = this.$reports.find('.request-response-error');
            this.report_downloads = new (ReportDownloads())(this.$section);
            this.instructor_tasks = new (PendingInstructorTasks())(this.$section);
            this.clear_display();
            this.$list_anon_btn.click(function() {
                var url = dataDownloadObj.$list_anon_btn.data('endpoint');
                var errorMessage = gettext('Error generating anonymous IDs. Please try again.');
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$proctored_exam_csv_btn.click(function() {
                var url = dataDownloadObj.$proctored_exam_csv_btn.data('endpoint');
                var errorMessage = gettext('Error generating proctored exam results. Please try again.');
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$survey_results_csv_btn.click(function() {
                var url = dataDownloadObj.$survey_results_csv_btn.data('endpoint');
                var errorMessage = gettext('Error generating survey results. Please try again.');
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$list_studs_csv_btn.click(function() {
                var url = dataDownloadObj.$list_studs_csv_btn.data('endpoint') + '/csv';
                var errorMessage = gettext('Error generating student profile information. Please try again.');
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$list_studs_btn.click(function() {
                var url = dataDownloadObj.$list_studs_btn.data('endpoint');
                dataDownloadObj.clear_display();
                dataDownloadObj.$download_display_table.text(gettext('Loading'));
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function() {
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$download_request_response_error.text(
                            gettext('Error getting student list.')
                        );
                        return dataDownloadObj.$download_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        var $tablePlaceholder, columns, feature, gridData, options, grid;
                        dataDownloadObj.clear_display();
                        options = {
                            enableCellNavigation: true,
                            enableColumnReorder: false,
                            autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
                        };
                        columns = data.queried_features.map(function(feature) {
                            return {
                                id: feature,
                                field: feature,
                                name: data.feature_names[feature],
                                resizable: false,
                            };
                        });
                        gridData = data.students;
                        $tablePlaceholder = $('<div/>', {
                            class: 'slickgrid'
                        });
                        dataDownloadObj.$download_display_table.append($tablePlaceholder);
                        grid = new window.Slick.Grid($tablePlaceholder[0], gridData, columns, options);
                        return grid.autosizeColumns();
                    }
                });
            });
            this.$list_problem_responses_csv_btn.click(function() {
                var url = dataDownloadObj.$list_problem_responses_csv_btn.data('endpoint');
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    data: {
                        problem_location: dataDownloadObj.$list_problem_responses_csv_input.val()
                    },
                    error: function(error) {
                        dataDownloadObj.$reports_request_response_error.text(
                            JSON.parse(error.responseText)
                        );
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$list_may_enroll_csv_btn.click(function() {
                var url = dataDownloadObj.$list_may_enroll_csv_btn.data('endpoint');
                var errorMessage = gettext('Error generating list of students who may enroll. Please try again.');
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$list_learners_not_activated_csv_btn.click(function() {
                var url = dataDownloadObj.$list_learners_not_activated_csv_btn.data('endpoint');
                var errorMessage = gettext('Error generating list of inactive students who are enrolled in a course. Please try again.');
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        }
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
            this.$grade_config_btn.click(function() {
                var url = dataDownloadObj.$grade_config_btn.data('endpoint');
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function() {
                        dataDownloadObj.clear_display();
                        dataDownloadObj.$download_request_response_error.text(
                            gettext('Error retrieving grading configuration.')
                        );
                        return dataDownloadObj.$download_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.clear_display();
                        return edx.HtmlUtils.setHtml(
                            dataDownloadObj.$download_display_text, edx.HtmlUtils.HTML(data.grading_config_summary));
                    }
                });
            });
            this.$async_report_btn.click(function(e) {
                var url = $(e.target).data('endpoint');
                var errorMessage = '';
                dataDownloadObj.clear_display();
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    error: function(error) {
                        if (error.responseText) {
                            errorMessage = JSON.parse(error.responseText);
                        } else if (e.target.name === 'calculate-grades-csv') {
                            errorMessage = gettext('Error generating grades. Please try again.');
                        } else if (e.target.name === 'problem-grade-report') {
                            errorMessage = gettext('Error generating problem grade report. Please try again.');
                        } else if (e.target.name === 'export-ora2-data') {
                            errorMessage = gettext('Error generating ORA data report. Please try again.');
                        }
                        dataDownloadObj.$reports_request_response_error.text(errorMessage);
                        return dataDownloadObj.$reports_request_response_error.css({
                            display: 'block'
                        });
                    },
                    success: function(data) {
                        dataDownloadObj.$reports_request_response.text(data.status);
                        return $('.msg-confirm').css({
                            display: 'block'
                        });
                    }
                });
            });
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

        InstructorDashboardDataDownload.prototype.clear_display = function() {
            this.$download_display_text.empty();
            this.$download_display_table.empty();
            this.$download_request_response_error.empty();
            this.$reports_request_response.empty();
            this.$reports_request_response_error.empty();
            $('.msg-confirm').css({
                display: 'none'
            });
            return $('.msg-error').css({
                display: 'none'
            });
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
        DataDownload: DataDownload
    });
}).call(this);
