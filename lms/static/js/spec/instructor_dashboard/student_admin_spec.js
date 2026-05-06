/* globals _, interpolate_text, statusAjaxError, PendingInstructorTasks, createTaskListTable */
define(['jquery', 'sinon', 'js/instructor_dashboard/student_admin', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers'],
    function($, sinon, StudentAdmin, AjaxHelpers) {
        // 'js/instructor_dashboard/student_admin'

        'use strict';

        describe('edx.instructor_dashboard.student_admin.StudentAdmin', function() {
            var studentadmin, dashboardApiUrl, uniqStudentIdentifier, alertMsg, requests, xhrFactory;

            beforeEach(function() {
                loadFixtures('js/fixtures/instructor_dashboard/student_admin.html');
                window.InstructorDashboard = {};
                window.InstructorDashboard.util = {
                    statusAjaxError: statusAjaxError,
                    PendingInstructorTasks: PendingInstructorTasks,
                    createTaskListTable: createTaskListTable
                };
                studentadmin = new window.StudentAdmin($('#student_admin'));
                dashboardApiUrl = '/courses/PU/FSc/2014_T4/instructor/api';
                uniqStudentIdentifier = 'test@example.com';
                alertMsg = '';
                spyOn(window, 'alert').and.callFake(function(message) {
                    alertMsg = message;
                });
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('initiates resetting of entrance exam when button is clicked', function() {
                var successMessage = gettext("Entrance exam attempts is being reset for student '{student_id}'.");
                var fullSuccessMessage = interpolate_text(successMessage, {
                    student_id: uniqStudentIdentifier
                });
                var url = dashboardApiUrl + '/reset_student_attempts_for_entrance_exam';

                // Verify that the client contacts the server to start instructor task
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    delete_module: false
                });

                studentadmin.$btn_reset_entrance_exam_attempts.click();
                // expect error to be shown since student identifier is not set
                expect(studentadmin.$request_err_ee.text()).toEqual(
                    gettext('Please enter a student email address or username.')
                );

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_reset_entrance_exam_attempts.click();

                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate a success response from the server
                AjaxHelpers.respondWithJson(requests, {
                    message: fullSuccessMessage
                });
                expect(alertMsg).toEqual(fullSuccessMessage);
            });

            it('shows an error when resetting of entrance exam fails', function() {
                var url = dashboardApiUrl + '/reset_student_attempts_for_entrance_exam';
                // Verify that the client contacts the server to start instructor task
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    delete_module: false
                });
                var errorMessage = gettext("Error resetting entrance exam attempts for student '{student_id}'. Make sure student identifier is correct."); //  eslint-disable-line max-len
                var fullErrorMessage = interpolate_text(errorMessage, {
                    student_id: uniqStudentIdentifier
                });

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_reset_entrance_exam_attempts.click();

                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests, 400, {});

                expect(studentadmin.$request_err_ee.text()).toEqual(fullErrorMessage);
            });

            it('initiates rescoring of the entrance exam when the button is clicked', function() {
                var successMessage = gettext("Started entrance exam rescore task for student '{student_id}'."
                    + " Click the 'Show Task Status' button to see the status of the task."); //  eslint-disable-line max-len
                var fullSuccessMessage = interpolate_text(successMessage, {
                    student_id: uniqStudentIdentifier
                });
                var url = dashboardApiUrl + '/rescore_entrance_exam';

                // Verify that the client contacts the server to start instructor task
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    only_if_higher: false
                });

                studentadmin.$btn_rescore_entrance_exam.click();
                // expect error to be shown since student identifier is not set
                expect(studentadmin.$request_err_ee.text()).toEqual(
                    gettext('Please enter a student email address or username.')
                );

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_rescore_entrance_exam.click();

                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate a success response from the server
                AjaxHelpers.respondWithJson(requests, {
                    message: fullSuccessMessage
                });
                expect(alertMsg).toEqual(fullSuccessMessage);
            });

            it('shows an error when entrance exam rescoring fails', function() {
                var url = dashboardApiUrl + '/rescore_entrance_exam';
                // Verify that the client contacts the server to start instructor task
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    only_if_higher: false
                });
                var errorMessage = gettext(
                    "Error starting a task to rescore entrance exam for student '{student_id}'."
                    + ' Make sure that entrance exam has problems in it and student identifier is correct.'
                );
                var fullErrorMessage = interpolate_text(errorMessage, {
                    student_id: uniqStudentIdentifier
                });

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_rescore_entrance_exam.click();

                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests, 400, {});

                expect(studentadmin.$request_err_ee.text()).toEqual(fullErrorMessage);
            });

            it('initiates skip entrance exam when button is clicked', function() {
                var successMessage = "This student ('{student_id}') will skip the entrance exam.";
                var fullSuccessMessage = interpolate_text(successMessage, {
                    student_id: uniqStudentIdentifier
                });
                var url = dashboardApiUrl + '/mark_student_can_skip_entrance_exam';

                studentadmin.$btn_skip_entrance_exam.click();
                // expect error to be shown since student identifier is not set
                expect(studentadmin.$request_err_ee.text()).toEqual(
                    gettext("Enter a student's username or email address.")
                );

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_skip_entrance_exam.click();
                // Verify that the client contacts the server to start instructor task
                AjaxHelpers.expectRequest(requests, 'POST', url, $.param({
                    unique_student_identifier: uniqStudentIdentifier
                }));

                // Simulate a success response from the server
                AjaxHelpers.respondWithJson(requests, {
                    message: fullSuccessMessage
                });
                expect(alertMsg).toEqual(fullSuccessMessage);
            });

            it('shows an error when skip entrance exam fails', function() {
                var url = dashboardApiUrl + '/mark_student_can_skip_entrance_exam';
                var errorMessage = "An error occurred. Make sure that the student's username or email address is correct and try again."; //  eslint-disable-line max-len
                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_skip_entrance_exam.click();

                AjaxHelpers.expectRequest(requests, 'POST', url, $.param({
                    unique_student_identifier: uniqStudentIdentifier
                }));

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests, 400, {});

                expect(studentadmin.$request_err_ee.text()).toEqual(errorMessage);
            });

            it('initiates delete student state for entrance exam when button is clicked', function() {
                var successMessage = gettext("Entrance exam state is being deleted for student '{student_id}'.");
                var fullSuccessMessage = interpolate_text(successMessage, {
                    student_id: uniqStudentIdentifier
                });
                var url = dashboardApiUrl + '/reset_student_attempts_for_entrance_exam';

                // Verify that the client contacts the server to start instructor task
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    delete_module: true
                });
                studentadmin.$btn_delete_entrance_exam_state.click();
                // expect error to be shown since student identifier is not set
                expect(studentadmin.$request_err_ee.text()).toEqual(
                    gettext('Please enter a student email address or username.')
                );

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_delete_entrance_exam_state.click();

                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate a success response from the server
                AjaxHelpers.respondWithJson(requests, {
                    message: fullSuccessMessage
                });
                expect(alertMsg).toEqual(fullSuccessMessage);
            });

            it('shows an error when delete student state for entrance exam fails', function() {
                var url = dashboardApiUrl + '/reset_student_attempts_for_entrance_exam';
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier,
                    delete_module: true
                });
                var errorMessage = gettext("Error deleting entrance exam state for student '{student_id}'. "
                    + 'Make sure student identifier is correct.'); //  eslint-disable-line max-len
                var fullErrorMessage = interpolate_text(errorMessage, {
                    student_id: uniqStudentIdentifier
                });
                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_delete_entrance_exam_state.click();
                // Verify that the client contacts the server to start instructor task
                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests, 400, {});

                expect(studentadmin.$request_err_ee.text()).toEqual(fullErrorMessage);
            });

            it('initiates listing of entrance exam task history when button is clicked', function() {
                var url = dashboardApiUrl + '/list_entrance_exam_instructor_tasks';

                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier
                });
                studentadmin.$btn_entrance_exam_task_history.click();
                // expect error to be shown since student identifier is not set
                expect(studentadmin.$request_err_ee.text()).toEqual(
                    gettext("Enter a student's username or email address.")
                );

                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_entrance_exam_task_history.click();
                // Verify that the client contacts the server to start instructor task
                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate a success response from the server
                AjaxHelpers.respondWithJson(requests, {
                    tasks: [
                        {
                            status: 'Incomplete',
                            task_type: 'rescore_problem',
                            task_id: '9955d413-eac1-441f-978d-27c60dd1c946',
                            created: '2015-02-19T10:59:01+00:00',
                            task_input: '{"entrance_exam_url": "i4x://PU/FSc/chapter/d2204197cce443c4a0d5c852d4e7f638", "student": "audit"}', //  eslint-disable-line max-len
                            duration_sec: 'unknown',
                            task_message: 'No status information available',
                            requester: 'staff',
                            task_state: 'QUEUING'
                        }
                    ]
                });
                expect($('.entrance-exam-task-history-table')).toBeVisible();
            });

            it('shows an error when listing entrance exam task history fails', function() {
                var url = dashboardApiUrl + '/list_entrance_exam_instructor_tasks';
                var params = $.param({
                    unique_student_identifier: uniqStudentIdentifier
                });
                var errorMessage = gettext("Error getting entrance exam task history for student '{student_id}'. "
                    + 'Make sure student identifier is correct.');
                var fullErrorMessage = interpolate_text(errorMessage, {
                    student_id: uniqStudentIdentifier
                });
                studentadmin.$field_exam_grade.val(uniqStudentIdentifier);
                studentadmin.$btn_entrance_exam_task_history.click();
                // Verify that the client contacts the server to start instructor task
                AjaxHelpers.expectPostRequest(requests, url, params);

                // Simulate an error response from the server
                AjaxHelpers.respondWithError(requests, 400, {});

                expect(studentadmin.$request_err_ee.text()).toEqual(fullErrorMessage);
            });
        });
    });
