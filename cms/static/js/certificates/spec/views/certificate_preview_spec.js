// Jasmine Test Suite: Certificate Web Preview

define([
    'underscore',
    'jquery',
    'sinon',
    'js/models/course',
    'js/certificates/views/certificate_preview',
    'common/js/spec_helpers/template_helpers',
    'common/js/spec_helpers/view_helpers',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers'
],
function(_, $, sinon, Course, CertificatePreview, TemplateHelpers, ViewHelpers, AjaxHelpers) {
    'use strict';

    var requests, xhrFactory;

    var SELECTORS = {
        course_modes: '#course-modes',
        activate_certificate: '.activate-cert',
        preview_certificate: '.preview-certificate-link'
    };

    describe('Certificate Web Preview Spec:', function() {
        var selectDropDownByText = function(element, value) {
            if (value) {
                element.val(value);
                element.trigger('change');
            }
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            window.course = new Course({
                id: '5',
                name: 'Course Name',
                url_name: 'course_name',
                org: 'course_org',
                num: 'course_num',
                revision: 'course_rev'
            });
            window.CMS.User = {isGlobalStaff: true};

            TemplateHelpers.installTemplate('certificate-web-preview', true);
            appendSetFixtures('<div class="preview-certificate nav-actions"></div>');
            this.view = new CertificatePreview({
                el: $('.preview-certificate'),
                course_modes: ['test1', 'test2', 'test3'],
                certificate_web_view_url: '/users/1/courses/orgX/009/2016?preview=test1',
                certificate_activation_handler_url: '/certificates/activation/' + window.course.id,
                is_active: true
            });
            appendSetFixtures(this.view.render().el);
        });

        afterEach(function() {
            requests.restore();
            delete window.course;
            delete window.CMS.User;
        });

        describe('Certificate preview', function() {
            it('course mode event should call when user choose a new mode', function() {
                spyOn(this.view, 'courseModeChanged');
                this.view.delegateEvents();
                selectDropDownByText(this.view.$(SELECTORS.course_modes), 'test3');
                expect(this.view.courseModeChanged).toHaveBeenCalled();
            });

            it('course mode selection updating the link successfully', function() {
                selectDropDownByText(this.view.$(SELECTORS.course_modes), 'test1');
                expect(this.view.$(SELECTORS.preview_certificate).attr('href'))
                    .toEqual('/users/1/courses/orgX/009/2016?preview=test1');

                selectDropDownByText(this.view.$(SELECTORS.course_modes), 'test2');
                expect(this.view.$(SELECTORS.preview_certificate).attr('href'))
                    .toEqual('/users/1/courses/orgX/009/2016?preview=test2');

                selectDropDownByText(this.view.$(SELECTORS.course_modes), 'test3');
                expect(this.view.$(SELECTORS.preview_certificate).attr('href'))
                    .toEqual('/users/1/courses/orgX/009/2016?preview=test3');
            });

            it('toggle certificate activation event works fine', function() {
                spyOn(this.view, 'toggleCertificateActivation');
                this.view.delegateEvents();
                this.view.$(SELECTORS.activate_certificate).click();
                expect(this.view.toggleCertificateActivation).toHaveBeenCalled();
            });

            it('certificate deactivation works fine', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy();
                this.view.$(SELECTORS.activate_certificate).click();
                AjaxHelpers.expectJsonRequest(requests, 'POST', '/certificates/activation/' + window.course.id, {
                    is_active: false
                });
                ViewHelpers.verifyNotificationShowing(notificationSpy, /Deactivating/);
            });

            it('certificate activation works fine', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy();
                this.view.is_active = false;
                this.view.$(SELECTORS.activate_certificate).click();
                AjaxHelpers.expectJsonRequest(requests, 'POST', '/certificates/activation/' + window.course.id, {
                    is_active: true
                });
                ViewHelpers.verifyNotificationShowing(notificationSpy, /Activating/);
            });

            it('certificate should be deactivate when method "remove" called', function() {
                this.view.remove();
                expect(this.view.is_active).toBe(false);
            });

            it('certificate web preview should be removed when method "remove" called', function() {
                this.view.remove();
                expect(this.view.el.innerHTML).toBe('');
            });

            it('method "show" should call the render function', function() {
                spyOn(this.view, 'render');
                this.view.show();
                expect(this.view.render).toHaveBeenCalled();
            });
        });
    });
});
