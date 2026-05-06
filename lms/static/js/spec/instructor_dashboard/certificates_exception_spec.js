/* global define */
define([
    'jquery',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/certificates/models/certificate_exception',
    'js/certificates/views/certificate_allowlist',
    'js/certificates/views/certificate_allowlist_editor',
    'js/certificates/collections/certificate_allowlist'
],
function($, sinon, AjaxHelpers, CertificateExceptionModel, CertificateAllowlistView, CertificateAllowlistEditorView,
    CertificateAllowlistCollection) {
    'use strict';

    describe('edx.certificates.models.certificates_exception.CertificateExceptionModel', function() {
        var certificateException = null;
        var assertValid = function(fields, isValid, expectedErrors) {
            var errors;
            certificateException.set(fields);
            errors = certificateException.validate(certificateException.attributes);

            if (isValid) {
                expect(errors).toBe(undefined);
            } else {
                expect(errors).toEqual(expectedErrors);
            }
        };

        var EXPECTED_ERRORS = {
            user_name_or_email_required: 'Student username/email field is required and can not be empty. '
                + 'Kindly fill in username/email and then press "Add to Exception List" button.'
        };

        beforeEach(function() {
            certificateException = new CertificateExceptionModel({user_name: 'test_user'}, {url: 'test/url/'});
            certificateException.set({
                notes: 'Test notes'
            });
        });

        it('accepts valid email addresses', function() {
            assertValid({user_email: 'bob@example.com'}, true);
            assertValid({user_email: 'bob+smith@example.com'}, true);
            assertValid({user_email: 'bob+smith@example.com'}, true);
            assertValid({user_email: 'bob+smith@example.com'}, true);
            assertValid({user_email: 'bob@test.example.com'}, true);
            assertValid({user_email: 'bob@test-example.com'}, true);
        });

        it('displays username or email required error', function() {
            assertValid({user_name: ''}, false, EXPECTED_ERRORS.user_name_or_email_required);
        });
    });

    describe('edx.certificates.collections.certificate_allowlist.CertificateAllowlist', function() {
        var certificateAllowlist = null,
            certificateExceptionUrl = 'test/url/',
            requests, xhrFactory;
        var certificatesExceptionsJson = [
            {
                id: 1,
                user_id: 1,
                user_name: 'test1',
                user_email: 'test1@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception'
            },
            {
                id: 2,
                user_id: 2,
                user_name: 'test2',
                user_email: 'test2@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception'
            }
        ];

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            certificateAllowlist = new CertificateAllowlistCollection(certificatesExceptionsJson, {
                parse: true,
                canBeEmpty: true,
                url: certificateExceptionUrl,
                generate_certificates_url: certificateExceptionUrl
            });
        });

        afterEach(function() {
            requests.restore();
        });

        it('has 2 models in the collection after initialization', function() {
            expect(certificateAllowlist.models.length).toEqual(2);
        });

        it("returns correct model on getModel call and 'undefined' if queried model is not present", function() {
            expect(certificateAllowlist.getModel({user_name: 'test1'})).not.toBe(undefined);
            expect(certificateAllowlist.getModel({user_name: 'test_invalid_user'})).toBe(undefined);

            expect(certificateAllowlist.getModel({user_email: 'test1@test.com'})).not.toBe(undefined);
            expect(certificateAllowlist.getModel({user_email: 'test_invalid_user@test.com'})).toBe(undefined);

            expect(certificateAllowlist.getModel({user_name: 'test1'}).attributes).toEqual(
                {
                    id: 1,
                    user_id: 1,
                    user_name: 'test1',
                    user_email: 'test1@test.com',
                    course_id: 'edX/test/course',
                    created: 'Thursday, October 29, 2015',
                    notes: 'test notes for test certificate exception',
                    certificate_generated: ''
                }
            );

            expect(certificateAllowlist.getModel({user_email: 'test2@test.com'}).attributes).toEqual(
                {
                    id: 2,
                    user_id: 2,
                    user_name: 'test2',
                    user_email: 'test2@test.com',
                    course_id: 'edX/test/course',
                    created: 'Thursday, October 29, 2015',
                    notes: 'test notes for test certificate exception',
                    certificate_generated: ''
                }
            );
        });

        it('sends empty certificate exceptions list if no model is added', function() {
            var successCallback = sinon.spy(),
                errorCallback = sinon.spy(),
                addStudents = 'all';
            var expected = {
                url: certificateExceptionUrl + addStudents,
                postData: []
            };

            certificateAllowlist.sync({success: successCallback, error: errorCallback}, addStudents);
            AjaxHelpers.expectJsonRequest(requests, 'POST', expected.url, expected.postData);
        });

        it('syncs only newly added models with the server', function() {
            var successCallback = sinon.spy(),
                errorCallback = sinon.spy(),
                addStudents = 'new',
                expected;

            certificateAllowlist.add({user_name: 'test3', notes: 'test3 notes', new: true});
            certificateAllowlist.sync({success: successCallback, error: errorCallback}, addStudents);

            expected = {
                url: certificateExceptionUrl + addStudents,
                postData: [
                    {
                        user_id: '',
                        user_name: 'test3',
                        user_email: '',
                        created: '',
                        notes: 'test3 notes',
                        certificate_generated: '',
                        new: true
                    }
                ]
            };
            AjaxHelpers.expectJsonRequest(requests, 'POST', expected.url, expected.postData);
        });
    });

    describe('edx.certificates.views.certificate_allowlist.CertificateAllowlistView', function() {
        var view = null,
            certificateExceptionUrl = 'test/url/';

        var certificatesExceptionsJson = [
            {
                id: 1,
                user_id: 1,
                user_name: 'test1',
                user_email: 'test1@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception'
            },
            {
                id: 2,
                user_id: 2,
                user_name: 'test2',
                user_email: 'test2@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception'
            }
        ];

        beforeEach(function() {
            var fixture;
            setFixtures();
            fixture = readFixtures('templates/instructor/instructor_dashboard_2/certificate-allowlist.underscore');
            setFixtures("<script type='text/template' id='certificate-allowlist-tpl'>" + fixture + '</script>'
                    + "<div class='allowlisted-students' id='allowlisted-students'></div>");

            this.certificate_allowlist = new CertificateAllowlistCollection(certificatesExceptionsJson, {
                parse: true,
                canBeEmpty: true,
                url: certificateExceptionUrl,
                generate_certificates_url: certificateExceptionUrl

            });

            view = new CertificateAllowlistView({
                collection: this.certificate_allowlist,
                active_certificate: true
            });
            view.render();
        });

        it('verifies view is initialized and rendered successfully', function() {
            expect(view).not.toBe(undefined);
            expect(view.$el.find('table tbody tr').length).toBe(2);
        });

        it('verifies that Generate Exception Certificate button is disabled', function() {
            expect(view.$el.find('table tbody tr').length).toBe(2);
            expect(view.$el.find('#generate-exception-certificates').first()).not.toHaveAttr('disabled');

            // Render the view with active_certificate set to false.
            view = new CertificateAllowlistView({
                collection: this.certificate_allowlist,
                active_certificate: false
            });
            view.render();

            // Verify that `Generate Exception Certificate` is disabled even when Collection is not empty.
            expect(view.$el.find('#generate-exception-certificates').first()).toHaveAttr('disabled', 'disabled');
            expect(view.$el.find('table tbody tr').length).toBe(2);
        });

        it('verifies view is rendered on add/update to collection', function() {
            var user = 'test1',
                notes = 'test1 notes updates',
                email = 'update_email@test.com';

            // Add another model in collection and verify it is rendered
            view.collection.add({user_name: 'test3', notes: 'test3 notes'});
            expect(view.$el.find('table tbody tr').length).toBe(3);

            // Update a model in collection and verify it is rendered
            view.collection.update([
                {user_name: user, notes: notes, user_email: email}
            ]);

            expect(view.$el.find('table tbody tr td:contains("' + user + '")').parent().html())
                .toMatch(notes);
            expect(view.$el.find('table tbody tr td:contains("' + user + '")').parent().html())
                .toMatch(email);
        });

        it('verifies collection sync is called when "Generate Exception Certificates" is clicked', function() {
            var successCallback = sinon.spy(),
                errorCallback = sinon.spy();

            sinon.stub(view, 'showSuccess').returns(successCallback);
            sinon.stub(view, 'showError').returns(errorCallback);
            sinon.stub(view.collection, 'sync');

            view.$el.find('#generate-exception-certificates').click();

            expect(view.collection.sync.called).toBe(true);
            expect(view.collection.sync.calledWith({success: successCallback, error: errorCallback}))
                .toBe(true);
        });

        it('verifies sync is called with "new/all" argument depending upon selected radio button', function() {
            var successCallback = sinon.spy(),
                errorCallback = sinon.spy();

            sinon.stub(view, 'showSuccess').returns(successCallback);
            sinon.stub(view, 'showError').returns(errorCallback);
            sinon.stub(view.collection, 'sync');

            view.$el.find('#generate-exception-certificates').click();

            // By default 'Generate a Certificate for all New additions to the Exception list ' is selected
            expect(view.collection.sync.calledWith({success: successCallback, error: errorCallback}), 'new')
                .toBe(true);

            // Select 'Generate a Certificate for all users on the Exception list ' option
            view.$el.find('input:radio[name=generate-exception-certificates-radio][value=all]').click();
            view.$el.find('#generate-exception-certificates').click();
            expect(view.collection.sync.calledWith({success: successCallback, error: errorCallback}), 'all')
                .toBe(true);
        });
    });

    describe('edx.certificates.views.certificate_allowlist_editor.CertificateAllowlistEditorView', function() {
        var view = null,
            listView = null,
            certificateExceptionUrl = 'test/url/',
            requests, xhrFactory;
        var certificatesExceptionsJson = [
            {
                url: certificateExceptionUrl,
                id: 1,
                user_id: 1,
                user_name: 'test1',
                user_email: 'test1@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception',
                new: true
            },
            {
                url: certificateExceptionUrl,
                id: 2,
                user_id: 2,
                user_name: 'test2',
                user_email: 'test2@test.com',
                course_id: 'edX/test/course',
                created: 'Thursday, October 29, 2015',
                notes: 'test notes for test certificate exception'
            }
        ];

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            var fixture, fixture2, certificateAllowlist;
            setFixtures();

            fixture = readFixtures(
                'templates/instructor/instructor_dashboard_2/certificate-allowlist-editor.underscore'
            );

            fixture2 = readFixtures(
                'templates/instructor/instructor_dashboard_2/certificate-allowlist.underscore'
            );

            setFixtures(
                "<script type='text/template' id='certificate-allowlist-editor-tpl'>" + fixture + '</script>'
                    + "<script type='text/template' id='certificate-allowlist-tpl'>" + fixture2 + '</script>'
                    + "<div id='certificate-allowlist-editor'></div>"
                    + "<div class='allowlisted-students' id='allowlisted-students'></div>"
            );

            certificateAllowlist = new CertificateAllowlistCollection(certificatesExceptionsJson, {
                parse: true,
                canBeEmpty: true,
                url: certificateExceptionUrl,
                generate_certificates_url: certificateExceptionUrl
            });

            view = new CertificateAllowlistEditorView({
                collection: certificateAllowlist,
                url: certificateExceptionUrl
            });
            view.render();

            listView = new CertificateAllowlistView({
                collection: certificateAllowlist,
                certificateAllowlistEditorView: view
            });
            listView.render();
        });

        afterEach(function() {
            requests.restore();
        });

        it('verifies view is initialized and rendered successfully', function() {
            expect(view).not.toBe(undefined);
            expect(view.$el.find('#certificate-exception').length).toBe(1);
            expect(view.$el.find('#notes').length).toBe(1);
            expect(view.$el.find('#add-exception').length).toBe(1);
        });

        it('verifies success and error messages', function() {
            var messageSelector = '.message',
                successMessage = 'test_user has been successfully added to the exception list. Click Generate'
                        + ' Exception Certificate below to send the certificate.',
                duplicateUser = 'test_user';

            var errorMessages = {
                empty_user_name_email: 'Student username/email field is required and can not be empty. '
                    + 'Kindly fill in username/email and then press "Add to Exception List" button.',
                duplicate_user: '<p>' + (duplicateUser) + ' already in exception list.</p>'
            };

            // click 'Add Exception' button with empty username/email field
            view.$el.find('#certificate-exception').val('');
            view.$el.find('#add-exception').click();

            // Verify error message for missing username/email
            expect(view.$el.find(messageSelector).html()).toMatch(errorMessages.empty_user_name_email);

            // Add a new Exception to list
            view.$el.find('#certificate-exception').val(duplicateUser);
            view.$el.find('#notes').val('test user notes');
            view.$el.find('#add-exception').click();

            AjaxHelpers.respondWithJson(
                requests,
                {
                    id: 3,
                    user_id: 3,
                    user_name: duplicateUser,
                    user_email: 'test2@test.com',
                    course_id: 'edX/test/course',
                    created: 'Thursday, October 29, 2015',
                    notes: 'test user notes'
                }
            );

            // Verify success message
            expect(view.$el.find(messageSelector).html()).toMatch(successMessage);

            // Add a duplicate Certificate Exception
            view.$el.find('#certificate-exception').val(duplicateUser);
            view.$el.find('#notes').val('test user notes');
            view.$el.find('#add-exception').click();

            // Verify success message
            expect(view.$el.find(messageSelector).html()).toEqual(errorMessages.duplicate_user);
        });

        it('verifies certificate exception can be deleted by clicking "delete" ', function() {
            var username = 'test1',
                certificateExceptionSelector = "div.allowlisted-students table tr:contains('" + username + "')",
                deleteBtnSelector = certificateExceptionSelector + ' td .delete-exception';

            $(deleteBtnSelector).click();
            AjaxHelpers.respondWithJson(requests, {});

            // Verify the certificate exception is removed from the list
            expect($(certificateExceptionSelector).length).toBe(0);
        });
    });
}
);
