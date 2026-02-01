/* globals _ */

(function() {
    'use strict';

    var Extensions;

    Extensions = (function() {
        function extensions($section) {
            var $gridDisplay,
                ext = this;
            this.$section = $section;
            this.$section.data('wrapper', this);
            this.$change_due_date = this.$section.find("input[name='change-due-date']");
            this.$reset_due_date = this.$section.find("input[name='reset-due-date']");
            this.$show_unit_ext = this.$section.find("input[name='show-unit-extensions']");
            this.$show_student_ext = this.$section.find("input[name='show-student-extensions']");
            this.$section.find('.request-response').hide();
            this.$section.find('.request-response-error').hide();
            $gridDisplay = this.$section.find('.data-display');
            this.$grid_text = $gridDisplay.find('.data-display-text');
            this.$grid_table = $gridDisplay.find('.data-display-table');
            this.$change_due_date.click(function() {
                var sendData;
                ext.clear_display();
                ext.$student_input = ext.$section.find("#set-extension input[name='student']");
                ext.$url_input = ext.$section.find("#set-extension select[name='url']");
                ext.$due_datetime_input = ext.$section.find("#set-extension input[name='due_datetime']");
                ext.$reason_input = ext.$section.find("#set-extension input[name='reason']");

                sendData = {
                    student: ext.$student_input.val(),
                    url: ext.$url_input.val(),
                    due_datetime: ext.$due_datetime_input.val(),
                    reason: ext.$reason_input.val()
                };
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: ext.$change_due_date.data('endpoint'),
                    data: sendData,
                    success: function(data) {
                        return ext.display_response('set-extension', data);
                    },
                    error: function(xhr) {
                        return ext.fail_with_error('set-extension', 'Error changing due date', xhr);
                    }
                });
            });
            this.$reset_due_date.click(function() {
                var sendData;
                ext.clear_display();
                ext.$student_input = ext.$section.find("#reset-extension input[name='student']");
                ext.$url_input = ext.$section.find("#reset-extension select[name='url']");
                ext.$reason_input = ext.$section.find("#reset-extension input[name='reason']");

                sendData = {
                    student: ext.$student_input.val(),
                    url: ext.$url_input.val(),
                    reason: ext.$reason_input.val()
                };
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: ext.$reset_due_date.data('endpoint'),
                    data: sendData,
                    success: function(data) {
                        return ext.display_response('reset-extension', data);
                    },
                    error: function(xhr) {
                        return ext.fail_with_error('reset-extension', 'Error reseting due date', xhr);
                    }
                });
            });
            this.$show_unit_ext.click(function() {
                var sendData, url;
                ext.clear_display();
                ext.$grid_table.text('Loading');
                ext.$url_input = ext.$section.find("#view-granted-extensions select[name='url']");
                url = ext.$show_unit_ext.data('endpoint');
                sendData = {
                    url: ext.$url_input.val()
                };
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    data: sendData,
                    error: function(xhr) {
                        return ext.fail_with_error('view-granted-extensions', 'Error getting due dates', xhr);
                    },
                    success: function(data) {
                        return ext.display_grid(data);
                    }
                });
            });
            this.$show_student_ext.click(function() {
                var sendData, url;
                ext.clear_display();
                ext.$grid_table.text('Loading');
                url = ext.$show_student_ext.data('endpoint');
                ext.$student_input = ext.$section.find("#view-granted-extensions input[name='student']");
                sendData = {
                    student: ext.$student_input.val()
                };
                return $.ajax({
                    type: 'POST',
                    dataType: 'json',
                    url: url,
                    data: sendData,
                    error: function(xhr) {
                        return ext.fail_with_error('view-granted-extensions', 'Error getting due dates', xhr);
                    },
                    success: function(data) {
                        return ext.display_grid(data);
                    }
                });
            });
        }

        extensions.prototype.onClickTitle = function() {};

        extensions.prototype.fail_with_error = function(id, msg, xhr) {
            var $taskError, $taskResponse, data,
                message = msg;
            $taskError = this.$section.find('#' + id + ' .request-response-error');
            $taskResponse = this.$section.find('#' + id + ' .request-response');
            this.clear_display();
            data = $.parseJSON(xhr.responseText);
            message += ': ' + data.error;
            $taskResponse.empty();
            $taskError.empty();
            $taskError.text(message);
            return $taskError.show();
        };

        extensions.prototype.display_response = function(id, data) {
            var $taskError, $taskResponse;
            $taskError = this.$section.find('#' + id + ' .request-response-error');
            $taskResponse = this.$section.find('#' + id + ' .request-response');
            $taskError.empty().hide();
            $taskResponse.empty().text(data);
            return $taskResponse.show();
        };

        extensions.prototype.display_grid = function(data) {
            var $tablePlaceholder, col, columns, gridData, options, grid;
            this.clear_display();
            this.$grid_text.text(data.title);
            options = {
                enableCellNavigation: true,
                enableColumnReorder: false,
                forceFitColumns: true,
                autosizeColsMode: window.Slick.GridAutosizeColsMode.IgnoreViewport
            };
            columns = (function() {
                var i, len, ref, results;
                ref = data.header;
                results = [];
                for (i = 0, len = ref.length; i < len; i++) {
                    col = ref[i];
                    results.push({
                        id: col,
                        field: col,
                        name: col
                    });
                }
                return results;
            }());
            gridData = data.data;
            $tablePlaceholder = $('<div/>', {
                class: 'slickgrid',
                style: 'min-height: 400px'
            });
            this.$grid_table.append($tablePlaceholder);
            grid = new window.Slick.Grid($tablePlaceholder[0], gridData, columns, options);
            return grid.autosizeColumns();
        };

        extensions.prototype.clear_display = function() {
            this.$grid_text.empty();
            this.$grid_table.empty();
            this.$section.find('.request-response-error').empty().hide();
            return this.$section.find('.request-response').empty().hide();
        };

        return extensions;
    }());

    _.defaults(window, {
        InstructorDashboard: {}
    });

    _.defaults(window.InstructorDashboard, {
        sections: {}
    });

    _.defaults(window.InstructorDashboard.sections, {
        Extensions: Extensions
    });
}).call(this);
