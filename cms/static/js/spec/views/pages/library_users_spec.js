define([
    'jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'common/js/spec_helpers/view_helpers',
    'js/factories/manage_users_lib', 'common/js/components/utils/view_utils'
],
function($, sinon, AjaxHelpers, ViewHelpers, ManageUsersFactory, ViewUtils) {
    'use strict';

    var requests, xhrFactory;

    describe('Library Instructor Access Page', function() {
        var changeRoleUrl = 'dummy_change_role_url/@@EMAIL@@';
        var team_member_fixture = readFixtures('team-member.underscore');

        function setRole(email, role) {
            var $user_li = $('li.user-item[data-email="' + email + '"]');
            var $role_action = $('li.action-role a.make-' + role, $user_li);
            expect($role_action).toBeVisible();
            $role_action.click();
        }

        function getUrl(email) {
            return changeRoleUrl.replace('@@EMAIL@@', email);
        }

        describe('read-write access', function() {
            var mockHTML = readFixtures('mock/mock-manage-users-lib.underscore');

            beforeEach(function(done) {
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
                ViewHelpers.installMockAnalytics();
                setFixtures(mockHTML);
                appendSetFixtures($('<script>', {id: 'team-member-tpl', type: 'text/template'}).text(team_member_fixture));
                ManageUsersFactory(
                    'Mock Library',
                    [
                        {
                            id: 1, email: 'honor@example.com', username: 'honor', role: 'staff'
                        },
                        {
                            id: 2, email: 'audit@example.com', username: 'audit', role: 'instructor'
                        },
                        {
                            id: 3, email: 'staff@example.com', username: 'staff', role: 'library_user'
                        }
                    ],
                    changeRoleUrl,
                    10000,
                    true
                );

                jasmine.waitUntil(function() {
                    return ($('.ui-loading').length === 0);
                }).then(done);
            });

            afterEach(function() {
                requests.restore();
                ViewHelpers.removeMockAnalytics();
            });

            it('can give a user permission to use the library', function() {
                var email = 'other@example.com';
                var reloadSpy = spyOn(ViewUtils, 'reload');
                $('.create-user-button').click();
                expect($('.wrapper-create-user')).toHaveClass('is-shown');
                $('.user-email-input').val(email);
                $('.form-create.create-user .action-primary').click();
                AjaxHelpers.expectJsonRequest(requests, 'POST', getUrl(email), {role: 'library_user'});
                AjaxHelpers.respondWithJson(requests, {result: 'ok'});
                expect(reloadSpy).toHaveBeenCalled();
            });

            it('can promote user', function() {
                var email = 'staff@example.com';
                var reloadSpy = spyOn(ViewUtils, 'reload');
                setRole('staff@example.com', 'staff');
                AjaxHelpers.expectJsonRequest(requests, 'POST', getUrl(email), {role: 'staff'});
                AjaxHelpers.respondWithJson(requests, {result: 'ok'});
                expect(reloadSpy).toHaveBeenCalled();
            });

            it('can cancel adding a user to the library', function() {
                $('.create-user-button').click();
                $('.form-create.create-user .action-secondary').click();
                expect($('.wrapper-create-user')).not.toHaveClass('is-shown');
            });

            it('displays an error when the required field is blank', function() {
                $('.create-user-button').click();
                $('.user-email-input').val('');
                var errorPromptSelector = '.wrapper-prompt.is-shown .prompt.error';
                expect($(errorPromptSelector).length).toEqual(0);
                $('.form-create.create-user .action-primary').click();
                expect($(errorPromptSelector).length).toEqual(1);
                expect($(errorPromptSelector)).toContainText('You must enter a valid email address');
                AjaxHelpers.expectNoRequests(requests);
            });

            it('displays an error when the user has already been added', function() {
                var promptSpy = ViewHelpers.createPromptSpy();
                $('.create-user-button').click();
                $('.user-email-input').val('honor@example.com');
                $('.form-create.create-user .action-primary').click();
                ViewHelpers.verifyPromptShowing(promptSpy, 'Already a library team member');
                AjaxHelpers.expectNoRequests(requests);
            });

            it("can remove a user's permission to access the library", function() {
                var promptSpy = ViewHelpers.createPromptSpy();
                var reloadSpy = spyOn(ViewUtils, 'reload');
                var email = 'honor@example.com';
                $('.user-item[data-email="' + email + '"] .action-delete .delete').click();
                ViewHelpers.verifyPromptShowing(promptSpy, 'Are you sure?');
                ViewHelpers.confirmPrompt(promptSpy);
                ViewHelpers.verifyPromptHidden(promptSpy);
                AjaxHelpers.expectJsonRequest(requests, 'DELETE', getUrl(email), {role: null});
                AjaxHelpers.respondWithJson(requests, {result: 'ok'});
                expect(reloadSpy).toHaveBeenCalled();
            });
        });

        describe('read-only access', function() {
            var mockHTML = readFixtures('mock/mock-manage-users-lib-ro.underscore');

            beforeEach(function() {
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
                ViewHelpers.installMockAnalytics();
                setFixtures(mockHTML);
                appendSetFixtures($('<script>', {id: 'team-member-tpl', type: 'text/template'}).text(team_member_fixture));
                ManageUsersFactory(
                    'Mock Library',
                    [
                        {
                            id: 1, email: 'honor@example.com', username: 'honor', role: 'staff'
                        },
                        {
                            id: 2, email: 'audit@example.com', username: 'audit', role: 'instructor'
                        },
                        {
                            id: 3, email: 'staff@example.com', username: 'staff', role: 'library_user'
                        }
                    ],
                    'dummy_change_role_url',
                    10000,
                    false
                );
            });

            afterEach(function() {
                requests.restore();
                ViewHelpers.removeMockAnalytics();
            });

            it("can't give a user permission to use the library", function() {
                expect($('.create-user-button')).not.toBeVisible();
                expect($('.wrapper-create-user')).not.toBeVisible();
            });

            it("can't promote or demote user", function() {
                expect($('.action-role')).not.toBeVisible();
            });
        });
    });
});
