define(
    [
        'sinon',
        'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
        'common/js/components/utils/view_utils',
        'lms/js/preview/preview_factory'
    ],
    function(sinon, AjaxHelpers, ViewUtils, PreviewFactory) {
        'use strict';

        describe('Preview Factory', function() {
            var showPreview,
                previewActionSelect,
                usernameInput,
                requests, xhrFactory;

            showPreview = function(options) {
                PreviewFactory(options);
            };

            previewActionSelect = function() {
                return $('.action-preview-select');
            };

            usernameInput = function() {
                return $('.action-preview-username');
            };

            beforeEach(function() {
                loadFixtures('lms/fixtures/preview/course_preview.html');
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
            });

            afterEach(function() {
                requests.restore();
            });

            it('can render preview for a staff user', function() {
                showPreview({
                    courseId: 'test_course'
                });
                expect(previewActionSelect().val()).toBe('staff');
            });

            it('can disable course access for a student', function() {
                var select;
                showPreview({
                    courseId: 'test_course',
                    disableStudentAccess: true
                });
                select = previewActionSelect();
                expect(select.attr('disabled')).toBe('disabled');
                expect(select.attr('title')).toBe('Course is not yet visible to students.');
            });

            it('can switch to view as a student', function() {
                var reloadSpy = spyOn(ViewUtils, 'reload');
                showPreview({
                    courseId: 'test_course'
                });
                previewActionSelect().find('option[value="student"]').prop('selected', 'selected').change();
                AjaxHelpers.expectJsonRequest(
                    requests, 'POST', '/courses/test_course/masquerade',
                    {
                        role: 'student',
                        user_name: null
                    }
                );
                AjaxHelpers.respondWithJson(requests, {
                    success: true
                });
                expect(reloadSpy).toHaveBeenCalled();
            });

            it('can switch to view as a content group', function() {
                var reloadSpy = spyOn(ViewUtils, 'reload');
                showPreview({
                    courseId: 'test_course'
                });
                previewActionSelect().find('option[value="group-b"]').prop('selected', 'selected').change();
                AjaxHelpers.expectJsonRequest(
                    requests, 'POST', '/courses/test_course/masquerade',
                    {
                        role: 'student',
                        user_name: null,
                        user_partition_id: 'test_partition_b_id',
                        group_id: 'group-b'
                    }
                );
                AjaxHelpers.respondWithJson(requests, {
                    success: true
                });
                expect(reloadSpy).toHaveBeenCalled();
            });

            it('can switch to masquerade as a specific student', function() {
                var reloadSpy = spyOn(ViewUtils, 'reload');
                showPreview({
                    courseId: 'test_course'
                });
                previewActionSelect().find('option[value="specific student"]').prop('selected', 'selected').change();
                usernameInput().val('test_user').change();
                AjaxHelpers.expectJsonRequest(
                    requests, 'POST', '/courses/test_course/masquerade',
                    {
                        role: 'student',
                        user_name: 'test_user'
                    }
                );
                AjaxHelpers.respondWithJson(requests, {
                    success: true
                });
                expect(reloadSpy).toHaveBeenCalled();
            });

            it('shows the correct information when masquerading as a specific student', function() {
                showPreview({
                    specificStudentSelected: true,
                    masqueradeUsername: 'test_user'
                });
                expect(usernameInput().val()).toBe('test_user');
            });
        });
    }
);
