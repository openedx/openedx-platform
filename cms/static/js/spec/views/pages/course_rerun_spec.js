define(['jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'common/js/spec_helpers/view_helpers',
    'js/views/course_rerun', 'js/views/utils/create_course_utils', 'common/js/components/utils/view_utils',
    'jquery.simulate'],
function($, sinon, AjaxHelpers, ViewHelpers, CourseRerunUtils, CreateCourseUtilsFactory, ViewUtils) {
    var requests, xhrFactory;
    describe('Create course rerun page', function() {
        var selectors = {
                org: '.rerun-course-org',
                number: '.rerun-course-number',
                run: '.rerun-course-run',
                name: '.rerun-course-name',
                tipError: 'span.tip-error',
                save: '.rerun-course-save',
                cancel: '.rerun-course-cancel',
                errorWrapper: '.wrapper-error',
                errorMessage: '#course_rerun_error',
                error: '.error',
                allowUnicode: '.allow-unicode-course-id'
            },
            classes = {
                shown: 'is-shown',
                showing: 'is-showing',
                hiding: 'is-hidden',
                hidden: 'is-hidden',
                error: 'error',
                disabled: 'is-disabled',
                processing: 'is-processing'
            },
            mockCreateCourseRerunHTML = readFixtures('mock/mock-create-course-rerun.underscore');

        var CreateCourseUtils = new CreateCourseUtilsFactory(selectors, classes);

        var fillInFields = function(org, number, run, name) {
            $(selectors.org).val(org);
            $(selectors.number).val(number);
            $(selectors.run).val(run);
            $(selectors.name).val(name);
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            ViewHelpers.installMockAnalytics();
            window.source_course_key = 'test_course_key';
            appendSetFixtures(mockCreateCourseRerunHTML);
            CourseRerunUtils.onReady();
        });

        afterEach(function() {
            requests.restore();
            ViewHelpers.removeMockAnalytics();
            delete window.source_course_key;
        });

        describe('Field validation', function() {
            it('returns a message for an empty string', function() {
                var message = ViewUtils.validateRequiredField('');
                expect(message).not.toBe('');
            });

            it('does not return a message for a non empty string', function() {
                var message = ViewUtils.validateRequiredField('edX');
                expect(message).toBe('');
            });
        });

        describe('Error messages', function() {
            var setErrorMessage = function(selector, message) {
                var element = $(selector).parent();
                CreateCourseUtils.setFieldInErr(element, message);
                return element;
            };

            var type = function(input, value) {
                input.val(value);
                input.simulate('keyup', {keyCode: $.simulate.keyCode.SPACE});
            };

            it('shows an error message', function() {
                var element = setErrorMessage(selectors.org, 'error message');
                expect(element).toHaveClass(classes.error);
                expect(element.children(selectors.tipError)).not.toHaveClass(classes.hidden);
                expect(element.children(selectors.tipError)).toContainText('error message');
            });

            it('hides an error message', function() {
                var element = setErrorMessage(selectors.org, '');
                expect(element).not.toHaveClass(classes.error);
                expect(element.children(selectors.tipError)).toHaveClass(classes.hidden);
            });

            it('disables the save button', function() {
                setErrorMessage(selectors.org, 'error message');
                expect($(selectors.save)).toHaveClass(classes.disabled);
            });

            it('enables the save button when all errors are removed', function() {
                setErrorMessage(selectors.org, 'error message 1');
                setErrorMessage(selectors.number, 'error message 2');
                expect($(selectors.save)).toHaveClass(classes.disabled);
                setErrorMessage(selectors.org, '');
                setErrorMessage(selectors.number, '');
                expect($(selectors.save)).not.toHaveClass(classes.disabled);
            });

            it('does not enable the save button when errors remain', function() {
                setErrorMessage(selectors.org, 'error message 1');
                setErrorMessage(selectors.number, 'error message 2');
                expect($(selectors.save)).toHaveClass(classes.disabled);
                setErrorMessage(selectors.org, '');
                expect($(selectors.save)).toHaveClass(classes.disabled);
            });

            it('shows an error message when non URL characters are entered', function() {
                var $input = $(selectors.org);
                expect($input.parent()).not.toHaveClass(classes.error);
                type($input, '%');
                expect($input.parent()).toHaveClass(classes.error);
            });

            it('does not show an error message when tabbing into a field', function() {
                var $input = $(selectors.number);
                $input.val('');
                expect($input.parent()).not.toHaveClass(classes.error);
                $input.simulate('keyup', {keyCode: $.simulate.keyCode.TAB});
                expect($input.parent()).not.toHaveClass(classes.error);
            });

            it('shows an error message when a required field is empty', function() {
                var $input = $(selectors.org);
                $input.val('');
                expect($input.parent()).not.toHaveClass(classes.error);
                $input.simulate('keyup', {keyCode: $.simulate.keyCode.ENTER});
                expect($input.parent()).toHaveClass(classes.error);
            });

            it('shows an error message when spaces are entered and unicode is allowed', function() {
                var $input = $(selectors.org);
                $(selectors.allowUnicode).val('True');
                expect($input.parent()).not.toHaveClass(classes.error);
                type($input, ' ');
                expect($input.parent()).toHaveClass(classes.error);
            });

            it('shows an error message when total length exceeds 65 characters', function() {
                expect($(selectors.errorWrapper)).not.toHaveClass(classes.shown);
                type($(selectors.org), 'ThisIsAVeryLongNameThatWillExceedTheSixtyFiveCharacterLimit');
                type($(selectors.number), 'ThisIsAVeryLongNameThatWillExceedTheSixtyFiveCharacterLimit');
                type($(selectors.run), 'ThisIsAVeryLongNameThatWillExceedTheSixtyFiveCharacterLimit');
                expect($(selectors.errorWrapper)).toHaveClass(classes.shown);
            });

            describe('Name field', function() {
                it('does not show an error message when non URL characters are entered', function() {
                    var $input = $(selectors.name);
                    expect($input.parent()).not.toHaveClass(classes.error);
                    type($input, '%');
                    expect($input.parent()).not.toHaveClass(classes.error);
                });
            });
        });

        it('saves course reruns', function() {
            var redirectSpy = spyOn(ViewUtils, 'redirect');
            fillInFields('DemoX', 'DM101', '2014', 'Demo course');
            $(selectors.save).click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/course/', {
                source_course_key: 'test_course_key',
                org: 'DemoX',
                number: 'DM101',
                run: '2014',
                display_name: 'Demo course'
            });
            expect($(selectors.save)).toHaveClass(classes.disabled);
            expect($(selectors.save)).toHaveClass(classes.processing);
            expect($(selectors.cancel)).toHaveClass(classes.hidden);
            AjaxHelpers.respondWithJson(requests, {
                url: 'dummy_test_url'
            });
            expect(redirectSpy).toHaveBeenCalledWith('dummy_test_url');
        });

        it('displays an error when saving fails', function() {
            fillInFields('DemoX', 'DM101', '2014', 'Demo course');
            $(selectors.save).click();
            AjaxHelpers.respondWithJson(requests, {
                ErrMsg: 'error message'
            });
            expect($(selectors.errorWrapper)).not.toHaveClass(classes.hidden);
            expect($(selectors.errorWrapper)).toContainText('error message');
            expect($(selectors.save)).not.toHaveClass(classes.processing);
            expect($(selectors.cancel)).not.toHaveClass(classes.hidden);
        });

        it('does not save if there are validation errors', function() {
            fillInFields('DemoX', 'DM101', '', 'Demo course');
            $(selectors.save).click();
            AjaxHelpers.expectNoRequests(requests);
        });

        it('can be canceled', function() {
            var redirectSpy = spyOn(ViewUtils, 'redirect');
            $(selectors.cancel).click();
            expect(redirectSpy).toHaveBeenCalledWith('/course/');
        });
    });
});
