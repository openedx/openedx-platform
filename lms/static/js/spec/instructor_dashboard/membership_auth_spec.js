/* global define, Membership */
define(['jquery',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/instructor_dashboard/membership'],
function($, sinon, AjaxHelpers) {
    'use strict';

    describe('Membership.AuthListWidget', function() {
        var membership, // eslint-disable-line no-unused-vars
            changeSelectedList,
            requests,
            xhrFactory;

        changeSelectedList = function(listName) {
            var i, options;
            options = document.getElementById('member-lists-selector').options;
            for (i = 0; i < options.length; i++) {
                if (options[i].value === listName) {
                    document.getElementById('member-lists-selector').selectedIndex = i;
                    $('select#member-lists-selector').trigger('change');
                    break;
                }
            }
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            var membershipMain, membershipTpl;
            membershipMain = readFixtures('js/fixtures/instructor_dashboard/membership.html');
            membershipTpl = readFixtures(
                'templates/instructor/instructor_dashboard_2/membership-list-widget.underscore'
            );
            appendSetFixtures(
                "<script type='text/template' id='membership-list-widget-tpl'>" + membershipTpl + '</script>'
                    + membershipMain
            );
            membership = new window.InstructorDashboard.sections.Membership($('#membership'));
        });

        afterEach(function() {
            requests.restore();
        });

        it('binds the ajax call and the result will be success for Group Moderator', function() {
            var data,
                url = '/courses/course-v1:edx+ed202+2017_T3/instructor/api/list_forum_members';

            data = {
                course_id: 'course-v1:edx+ed202+2017_T3',
                'Group Moderator': [{
                    email: 'verified@example.com',
                    first_name: '',
                    group_name: 'Verified',
                    last_name: '',
                    username: 'verified'
                }],
                division_scheme: 'enrollment_track'
            };
            changeSelectedList('Group Moderator');

            AjaxHelpers.expectPostRequest(requests, url, 'rolename=Group+Moderator');
            AjaxHelpers.respondWithJson(requests, data);

            expect($('.auth-list-container.active').data('rolename')).toEqual('Group Moderator');

            expect($('.request-response-error').text()).toEqual('');

            // Both buttons should be enabled
            expect($('.auth-list-container.active .add').attr('disabled')).toBe(undefined);
            expect($('.auth-list-container.active .add-field').attr('disabled')).toBe(undefined);
        });

        it('Error message is shown if user with given identifier does not exist', function() {
            var url, params;
            $('.active .add-field').val('smth');
            $('.active .add').click();
            expect(requests.length).toEqual(1);

            url = '/courses/course-v1:edx+ed202+2017_T3/instructor/api/modify_access';
            params = $.param({
                unique_student_identifier: 'smth',
                rolename: 'staff',
                action: 'allow'
            });
            AjaxHelpers.expectPostRequest(requests, url, params);

            AjaxHelpers.respondWithJson(requests, {
                unique_student_identifier: 'smth',
                userDoesNotExist: true
            });

            expect($('.request-response-error').text()).toEqual(
                "Could not find a user with username or email address 'smth'."
            );
        });

        it('When no discussions division scheme is selected error is shown and inputs are disabled', function() {
            var data,
                url = '/courses/course-v1:edx+ed202+2017_T3/instructor/api/list_forum_members';

            data = {
                course_id: 'course-v1:edx+ed202+2017_T3',
                'Group Moderator': [{
                    email: 'verified@example.com',
                    first_name: '',
                    group_name: 'Verified',
                    last_name: '',
                    username: 'verified'
                }],
                division_scheme: 'none'
            };
            changeSelectedList('Group Moderator');

            AjaxHelpers.expectPostRequest(requests, url, 'rolename=Group+Moderator');
            AjaxHelpers.respondWithJson(requests, data);

            expect($('.auth-list-container.active').data('rolename')).toEqual('Group Moderator');

            // Error message is shown.
            expect($('.request-response-error').text()).toEqual('This role requires a divided discussions scheme.');

            // Both buttons should be disabled
            expect($('.auth-list-container.active .add').attr('disabled')).toBe('disabled');
            expect($('.auth-list-container.active .add-field').attr('disabled')).toBe('disabled');
        });
    });
});
