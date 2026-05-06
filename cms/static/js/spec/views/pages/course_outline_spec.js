import $ from 'jquery';
import sinon from 'sinon';
import AjaxHelpers from 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers';
import ViewUtils from 'common/js/components/utils/view_utils';
import CourseOutlinePage from 'js/views/pages/course_outline';
import XBlockOutlineInfo from 'js/models/xblock_outline_info';
import DateUtils from 'js/utils/date_utils';
import EditHelpers from 'js/spec_helpers/edit_helpers';
import TemplateHelpers from 'common/js/spec_helpers/template_helpers';
import Course from 'js/models/course';

describe('CourseOutlinePage', function() {
    var createCourseOutlinePage, displayNameInput, model, outlinePage, requests, xhrFactory, getItemsOfType, getItemHeaders,
        verifyItemsExpanded, expandItemsAndVerifyState, collapseItemsAndVerifyState, selectBasicSettings,
        selectVisibilitySettings, selectDiscussionSettings, selectAdvancedSettings, createMockCourseJSON, createMockSectionJSON,
        createMockSubsectionJSON, verifyTypePublishable, mockCourseJSON, mockEmptyCourseJSON, setSelfPaced, setSelfPacedCustomPLS,
        mockSingleSectionCourseJSON, createMockVerticalJSON, createMockIndexJSON, mockCourseEntranceExamJSON,
        selectOnboardingExam, createMockCourseJSONWithReviewRules, mockCourseJSONWithReviewRules,
        mockOutlinePage = readFixtures('templates/mock/mock-course-outline-page.underscore'),
        mockRerunNotification = readFixtures('templates/mock/mock-course-rerun-notification.underscore');

    createMockCourseJSON = function(options, children) {
        return $.extend(true, {}, {
            id: 'mock-course',
            display_name: 'Mock Course',
            category: 'course',
            enable_proctored_exams: true,
            enable_timed_exams: true,
            studio_url: '/course/slashes:MockCourse',
            is_container: true,
            has_changes: false,
            published: true,
            edited_on: 'Jul 02, 2014 at 20:56 UTC',
            edited_by: 'MockUser',
            has_explicit_staff_lock: false,
            child_info: {
                category: 'chapter',
                display_name: 'Section',
                children: []
            },
            unit_level_discussions: false,
            user_partitions: [],
            user_partition_info: {},
            highlights_enabled: true,
            highlights_enabled_for_messaging: false,
            hide_from_toc: false,
            enable_hide_from_toc_ui: true
        }, options, {child_info: {children: children}});
    };

    createMockCourseJSONWithReviewRules = function(options, children) {
        return $.extend(true, {}, {
            id: 'mock-course',
            display_name: 'Mock Course',
            category: 'course',
            enable_proctored_exams: true,
            enable_timed_exams: true,
            studio_url: '/course/slashes:MockCourse',
            is_container: true,
            has_changes: false,
            published: true,
            edited_on: 'Jul 02, 2014 at 20:56 UTC',
            edited_by: 'MockUser',
            has_explicit_staff_lock: false,
            child_info: {
                category: 'chapter',
                display_name: 'Section',
                children: []
            },
            user_partitions: [],
            show_review_rules: true,
            user_partition_info: {},
            highlights_enabled: true,
            highlights_enabled_for_messaging: false,
            hide_from_toc: false,
            enable_hide_from_toc_ui: true
        }, options, {child_info: {children: children}});
    };

    createMockSectionJSON = function(options, children) {
        return $.extend(true, {}, {
            id: 'mock-section',
            display_name: 'Mock Section',
            category: 'chapter',
            studio_url: '/course/slashes:MockCourse',
            is_container: true,
            has_changes: false,
            published: true,
            edited_on: 'Jul 02, 2014 at 20:56 UTC',
            edited_by: 'MockUser',
            has_explicit_staff_lock: false,
            child_info: {
                category: 'sequential',
                display_name: 'Subsection',
                children: []
            },
            user_partitions: [],
            group_access: {},
            user_partition_info: {},
            highlights: [],
            highlights_enabled: true,
            hide_from_toc: false,
            enable_hide_from_toc_ui: true
        }, options, {child_info: {children: children}});
    };

    createMockSubsectionJSON = function(options, children) {
        return $.extend(true, {}, {
            id: 'mock-subsection',
            display_name: 'Mock Subsection',
            category: 'sequential',
            studio_url: '/course/slashes:MockCourse',
            is_container: true,
            has_changes: false,
            published: true,
            edited_on: 'Jul 02, 2014 at 20:56 UTC',
            edited_by: 'MockUser',
            course_graders: ['Lab', 'Howework'],
            has_explicit_staff_lock: false,
            is_prereq: false,
            prereqs: [],
            prereq: '',
            prereq_min_score: '',
            prereq_min_completion: '',
            show_correctness: 'always',
            child_info: {
                category: 'vertical',
                display_name: 'Unit',
                children: []
            },
            user_partitions: [],
            group_access: {},
            user_partition_info: {},
            hide_from_toc: false,
            enable_hide_from_toc_ui: true,
        }, options, {child_info: {children: children}});
    };

    createMockVerticalJSON = function(options) {
        return $.extend(true, {}, {
            id: 'mock-unit',
            display_name: 'Mock Unit',
            category: 'vertical',
            studio_url: '/container/mock-unit',
            is_container: true,
            has_changes: false,
            published: true,
            visibility_state: 'unscheduled',
            edited_on: 'Jul 02, 2014 at 20:56 UTC',
            edited_by: 'MockUser',
            user_partitions: [],
            group_access: {},
            user_partition_info: {},
            hide_from_toc: false,
            enable_hide_from_toc_ui: true
        }, options);
    };

    createMockIndexJSON = function(option) {
        if (option) {
            return JSON.stringify({
                developer_message: 'Course has been successfully reindexed.',
                user_message: 'Course has been successfully reindexed.'
            });
        } else {
            return JSON.stringify({
                developer_message: 'Could not reindex course.',
                user_message: 'Could not reindex course.'
            });
        }
    };

    getItemsOfType = function(type) {
        return outlinePage.$('.outline-' + type);
    };

    getItemHeaders = function(type) {
        return getItemsOfType(type).find('> .' + type + '-header');
    };

    verifyItemsExpanded = function(type, isExpanded) {
        var element = getItemsOfType(type);
        if (isExpanded) {
            expect(element).not.toHaveClass('is-collapsed');
        } else {
            expect(element).toHaveClass('is-collapsed');
        }
    };

    expandItemsAndVerifyState = function(type) {
        getItemHeaders(type).find('.ui-toggle-expansion').click();
        verifyItemsExpanded(type, true);
    };

    collapseItemsAndVerifyState = function(type) {
        getItemHeaders(type).find('.ui-toggle-expansion').click();
        verifyItemsExpanded(type, false);
    };

    selectBasicSettings = function() {
        $(".modal-section .settings-tab-button[data-tab='basic']").click();
    };

    selectVisibilitySettings = function() {
        $(".modal-section .settings-tab-button[data-tab='visibility']").click();
    };

    selectAdvancedSettings = function() {
        $(".modal-section .settings-tab-button[data-tab='advanced']").click();
    };

    function selectDiscussionSettings() {
        $(".modal-section .settings-tab-button[data-tab='discussion']").click();
    }

    setSelfPaced = function() {
        /* global course */
        course.set('self_paced', true);
    };

    setSelfPacedCustomPLS = function() {
        setSelfPaced();
        course.set('is_custom_relative_dates_active', true);
    };

    createCourseOutlinePage = function(test, courseJSON, createOnly) {
        model = new XBlockOutlineInfo(courseJSON, {parse: true});
        outlinePage = new CourseOutlinePage({
            model: model,
            el: $('#content')
        });
        if (!createOnly) {
            outlinePage.render();
        }
        return outlinePage;
    };

    verifyTypePublishable = function(type, getMockCourseJSON) {
        var createCourseOutlinePageAndShowUnit, verifyPublishButton;

        createCourseOutlinePageAndShowUnit = function(test, courseJSON, createOnly) {
            outlinePage = createCourseOutlinePage.apply(this, arguments);
            if (type === 'unit') {
                expandItemsAndVerifyState('subsection');
            }
        };

        verifyPublishButton = function(test, courseJSON, createOnly) {
            createCourseOutlinePageAndShowUnit.apply(this, arguments);
            expect(getItemHeaders(type).find('.publish-button')).toExist();
        };

        it('can be published', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = getMockCourseJSON({
                has_changes: true
            });
            createCourseOutlinePageAndShowUnit(this, mockCourseJSON);
            getItemHeaders(type).find('.publish-button').click();
            $('.wrapper-modal-window .action-publish').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-' + type, {
                publish: 'make_public'
            });
            expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');
            AjaxHelpers.respondWithJson(requests, {});
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
        });

        it('should show publish button if it is not published and not changed', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = getMockCourseJSON({
                has_changes: false,
                published: false
            });
            verifyPublishButton(this, mockCourseJSON);
        });

        it('should show publish button if it is published and changed', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = getMockCourseJSON({
                has_changes: true,
                published: true
            });
            verifyPublishButton(this, mockCourseJSON);
        });

        it('should show publish button if it is not published, but changed', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = getMockCourseJSON({
                has_changes: true,
                published: false
            });
            verifyPublishButton(this, mockCourseJSON);
        });

        it('should hide publish button if it is not changed, but published', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = getMockCourseJSON({
                has_changes: false,
                published: true
            });
            createCourseOutlinePageAndShowUnit(this, mockCourseJSON);
            expect(getItemHeaders(type).find('.publish-button')).not.toExist();
        });
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

        EditHelpers.installMockAnalytics();
        EditHelpers.installViewTemplates();
        TemplateHelpers.installTemplates([
            'course-outline', 'xblock-string-field-editor', 'modal-button',
            'basic-modal', 'course-outline-modal', 'release-date-editor',
            'due-date-editor', 'self-paced-due-date-editor', 'grading-editor', 'publish-editor',
            'staff-lock-editor', 'unit-access-editor', 'discussion-editor', 'content-visibility-editor',
            'settings-modal-tabs', 'timed-examination-preference-editor', 'access-editor',
            'show-correctness-editor', 'highlights-editor', 'highlights-enable-editor',
            'course-highlights-enable', 'course-video-sharing-enable', 'summary-configuration-editor',
            'subsection-share-link-modal-tabs', 'full-page-share-link-editor', 'embed-link-share-link-editor',
        ]);
        appendSetFixtures(mockOutlinePage);
        mockCourseJSON = createMockCourseJSON({}, [
            createMockSectionJSON({}, [
                createMockSubsectionJSON({}, [
                    createMockVerticalJSON()
                ])
            ])
        ]);
        mockCourseJSONWithReviewRules = createMockCourseJSONWithReviewRules({}, [
            createMockSectionJSON({}, [
                createMockSubsectionJSON({}, [
                    createMockVerticalJSON()
                ])
            ])
        ]);
        mockEmptyCourseJSON = createMockCourseJSON();
        mockSingleSectionCourseJSON = createMockCourseJSON({}, [
            createMockSectionJSON()
        ]);
        mockCourseEntranceExamJSON = createMockCourseJSON({}, [
            createMockSectionJSON({}, [
                createMockSubsectionJSON({is_header_visible: false}, [
                    createMockVerticalJSON()
                ])
            ])
        ]);

        // Create a mock Course object as the JS now expects it.
        window.course = new Course({
            id: '333',
            name: 'Course Name',
            url_name: 'course_name',
            org: 'course_org',
            num: 'course_num',
            revision: 'course_rev'
        });
    });

    afterEach(function() {
        requests.restore();
        EditHelpers.cancelModalIfShowing();
        EditHelpers.removeMockAnalytics();
        // Clean up after the $.datepicker
        $('#start_date').datepicker('destroy');
        $('#due_date').datepicker('destroy');
        $('.ui-datepicker').remove();
        delete window.course;
    });

    describe('Initial display', function() {
        it('can render itself', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            expect(outlinePage.$('.list-sections')).toExist();
            expect(outlinePage.$('.list-subsections')).toExist();
            expect(outlinePage.$('.list-units')).toExist();
        });

        it('shows a loading indicator', function() {
            createCourseOutlinePage(this, mockCourseJSON, true);
            expect(outlinePage.$('.ui-loading')).not.toHaveClass('is-hidden');
            outlinePage.render();
            expect(outlinePage.$('.ui-loading')).toHaveClass('is-hidden');
        });

        it('shows subsections initially collapsed', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            verifyItemsExpanded('subsection', false);
            expect(getItemsOfType('unit')).not.toExist();
        });

        it('unit initially exist for entrance exam', function() {
            createCourseOutlinePage(this, mockCourseEntranceExamJSON);
            expect(getItemsOfType('unit')).toExist();
        });
    });

    describe('Rerun notification', function() {
        it('can be dismissed', function() {
            appendSetFixtures(mockRerunNotification);
            createCourseOutlinePage(this, mockEmptyCourseJSON);
            expect($('.wrapper-alert-announcement')).not.toHaveClass('is-hidden');
            $('.dismiss-button').click();
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', 'dummy_dismiss_url');
            AjaxHelpers.respondWithNoContent(requests);
            expect($('.wrapper-alert-announcement')).toHaveClass('is-hidden');
        });
    });

    describe('Button bar', function() {
        it('can add a section', function() {
            createCourseOutlinePage(this, mockEmptyCourseJSON);
            outlinePage.$('.nav-actions .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'chapter',
                display_name: 'Section',
                parent_locator: 'mock-course'
            });
            AjaxHelpers.respondWithJson(requests, {
                locator: 'mock-section',
                courseKey: 'slashes:MockCourse'
            });
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-course');
            AjaxHelpers.respondWithJson(requests, mockSingleSectionCourseJSON);
            expect(outlinePage.$('.no-content')).not.toExist();
            expect(outlinePage.$('.list-sections li.outline-section').data('locator')).toEqual('mock-section');
        });

        it('can add a second section', function() {
            var sectionElements;
            createCourseOutlinePage(this, mockSingleSectionCourseJSON);
            outlinePage.$('.nav-actions .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'chapter',
                display_name: 'Section',
                parent_locator: 'mock-course'
            });
            AjaxHelpers.respondWithJson(requests, {
                locator: 'mock-section-2',
                courseKey: 'slashes:MockCourse'
            });
            // Expect the UI to just fetch the new section and repaint it
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section-2');
            AjaxHelpers.respondWithJson(requests,
                createMockSectionJSON({id: 'mock-section-2', display_name: 'Mock Section 2'}));
            sectionElements = getItemsOfType('section');
            expect(sectionElements.length).toBe(2);
            expect($(sectionElements[0]).data('locator')).toEqual('mock-section');
            expect($(sectionElements[1]).data('locator')).toEqual('mock-section-2');
        });

        it('can expand and collapse all sections', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            verifyItemsExpanded('section', true);
            outlinePage.$('.nav-actions .button-toggle-expand-collapse .collapse-all').click();
            verifyItemsExpanded('section', false);
            outlinePage.$('.nav-actions .button-toggle-expand-collapse .expand-all').click();
            verifyItemsExpanded('section', true);
        });

        it('can start reindex of a course', function() {
            createCourseOutlinePage(this, mockSingleSectionCourseJSON);
            var reindexSpy = spyOn(outlinePage, 'startReIndex').and.callThrough();
            var successSpy = spyOn(outlinePage, 'onIndexSuccess').and.callThrough();
            var reindexButton = outlinePage.$('.button.button-reindex');
            var test_url = '/course/5/search_reindex';
            reindexButton.attr('href', test_url);
            reindexButton.trigger('click');
            AjaxHelpers.expectJsonRequest(requests, 'GET', test_url);
            AjaxHelpers.respondWithJson(requests, createMockIndexJSON(true));
            expect(reindexSpy).toHaveBeenCalled();
            expect(successSpy).toHaveBeenCalled();
        });

        it('shows an error message when reindexing fails', function() {
            createCourseOutlinePage(this, mockSingleSectionCourseJSON);
            var reindexSpy = spyOn(outlinePage, 'startReIndex').and.callThrough();
            var errorSpy = spyOn(outlinePage, 'onIndexError').and.callThrough();
            var reindexButton = outlinePage.$('.button.button-reindex');
            var test_url = '/course/5/search_reindex';
            reindexButton.attr('href', test_url);
            reindexButton.trigger('click');
            AjaxHelpers.expectJsonRequest(requests, 'GET', test_url);
            AjaxHelpers.respondWithError(requests, 500, createMockIndexJSON(false));
            expect(reindexSpy).toHaveBeenCalled();
            expect(errorSpy).toHaveBeenCalled();
        });
    });

    describe('Duplicate an xblock', function() {
        var duplicateXBlockWithSuccess;

        duplicateXBlockWithSuccess = function(xblockLocator, parentLocator, xblockType, xblockIndex) {
            getItemHeaders(xblockType).find('.duplicate-button')[xblockIndex].click();

            // verify content of request
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                duplicate_source_locator: xblockLocator,
                parent_locator: parentLocator
            });

            // send the response
            AjaxHelpers.respondWithJson(requests, {
                locator: 'locator-duplicated-xblock'
            });
        };

        it('section can be duplicated', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            expect(outlinePage.$('.list-sections li.outline-section').length).toEqual(1);
            expect(getItemsOfType('section').length, 1);
            duplicateXBlockWithSuccess('mock-section', 'mock-course', 'section', 0);
            expect(getItemHeaders('section').length, 2);
        });

        it('subsection can be duplicated', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            expect(getItemsOfType('subsection').length, 1);
            duplicateXBlockWithSuccess('mock-subsection', 'mock-section', 'subsection', 0);
            expect(getItemHeaders('subsection').length, 2);
        });

        it('unit can be duplicated', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            expandItemsAndVerifyState('subsection');
            expect(getItemsOfType('unit').length, 1);
            duplicateXBlockWithSuccess('mock-unit', 'mock-subsection', 'unit', 0);
            expect(getItemHeaders('unit').length, 2);
        });

        it('shows a notification when duplicating', function() {
            var notificationSpy = EditHelpers.createNotificationSpy();
            createCourseOutlinePage(this, mockCourseJSON);
            getItemHeaders('section').find('.duplicate-button').first()
                .click();
            EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
            AjaxHelpers.respondWithJson(requests, {locator: 'locator-duplicated-xblock'});
            EditHelpers.verifyNotificationHidden(notificationSpy);
        });

        it('does not duplicate an xblock upon failure', function() {
            var notificationSpy = EditHelpers.createNotificationSpy();
            createCourseOutlinePage(this, mockCourseJSON);
            expect(getItemHeaders('section').length, 1);
            getItemHeaders('section').find('.duplicate-button').first()
                .click();
            EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
            AjaxHelpers.respondWithError(requests);
            expect(getItemHeaders('section').length, 2);
            EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
        });
    });

    describe('Empty course', function() {
        it('shows an empty course message initially', function() {
            createCourseOutlinePage(this, mockEmptyCourseJSON);
            expect(outlinePage.$('.no-content')).not.toHaveClass('is-hidden');
            expect(outlinePage.$('.no-content .button-new')).toExist();
        });

        it('can add a section', function() {
            createCourseOutlinePage(this, mockEmptyCourseJSON);
            $('.no-content .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'chapter',
                display_name: 'Section',
                parent_locator: 'mock-course'
            });
            AjaxHelpers.respondWithJson(requests, {
                locator: 'mock-section',
                courseKey: 'slashes:MockCourse'
            });
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-course');
            AjaxHelpers.respondWithJson(requests, mockSingleSectionCourseJSON);
            expect(outlinePage.$('.no-content')).not.toExist();
            expect(outlinePage.$('.list-sections li.outline-section').data('locator')).toEqual('mock-section');
        });

        it('remains empty if an add fails', function() {
            var requestCount;
            createCourseOutlinePage(this, mockEmptyCourseJSON);
            $('.no-content .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'chapter',
                display_name: 'Section',
                parent_locator: 'mock-course'
            });
            AjaxHelpers.respondWithError(requests);
            AjaxHelpers.expectNoRequests(requests);
            expect(outlinePage.$('.no-content')).not.toHaveClass('is-hidden');
            expect(outlinePage.$('.no-content .button-new')).toExist();
        });
    });

    describe('Content Highlights', function() {
        let createCourse, createCourseWithHighlights, clickSaveOnModal, clickCancelOnModal;

        beforeEach(function() {
            setSelfPaced();
        });

        // eslint-disable-next-line prefer-const
        createCourse = function(sectionOptions, courseOptions) {
            createCourseOutlinePage(this,
                createMockCourseJSON(courseOptions, [
                    createMockSectionJSON(sectionOptions)
                ])
            );
        };

        // eslint-disable-next-line prefer-const
        createCourseWithHighlights = function(highlights) {
            createCourse({highlights: highlights});
        };

        // eslint-disable-next-line prefer-const
        clickSaveOnModal = function() {
            $('.wrapper-modal-window .action-save').click();
        };

        // eslint-disable-next-line prefer-const
        clickCancelOnModal = function() {
            $('.wrapper-modal-window .action-cancel').click();
        };

        describe('Course Highlights Setting', function() {
            var highlightsSetting, expectHighlightsEnabledToBe, expectServerHandshake, openHighlightsSettings;

            highlightsSetting = function() {
                return $('.course-highlights-setting');
            };

            expectHighlightsEnabledToBe = function(expectedEnabled) {
                if (expectedEnabled) {
                    expect('.status-highlights-enabled-value.button').not.toExist();
                    expect('.status-highlights-enabled-value.text').toExist();
                } else {
                    expect('.status-highlights-enabled-value.button').toExist();
                    expect('.status-highlights-enabled-value.text').not.toExist();
                }
            };

            expectServerHandshake = function() {
                // POST to update course
                AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-course', {
                    publish: 'republish',
                    metadata: {
                        highlights_enabled_for_messaging: true
                    }
                });
                AjaxHelpers.respondWithJson(requests, {});

                // GET updated course
                AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-course');
                AjaxHelpers.respondWithJson(
                    requests, createMockCourseJSON({highlights_enabled_for_messaging: true})
                );
            };

            openHighlightsSettings = function() {
                $('button.status-highlights-enabled-value').click();
            };

            it('displays settings when enabled', function() {
                createCourseWithHighlights([]);
                expect(highlightsSetting()).toExist();
            });

            it('displays settings as not enabled for messaging', function() {
                createCourse();
                expectHighlightsEnabledToBe(false);
            });

            it('displays settings as enabled for messaging', function() {
                createCourse({}, {highlights_enabled_for_messaging: true});
                expectHighlightsEnabledToBe(true);
            });

            it('changes settings when enabled for messaging', function() {
                createCourse();
                openHighlightsSettings();
                clickSaveOnModal();
                expectServerHandshake();
                expectHighlightsEnabledToBe(true);
            });

            it('does not change settings when enabling is cancelled', function() {
                createCourse();
                openHighlightsSettings();
                clickCancelOnModal();
                expectHighlightsEnabledToBe(false);
            });
        });

        describe('Section Highlights', function() {
            var mockHighlightValues, highlightsLink, highlightInputs, openHighlights, saveHighlights,
                cancelHighlights, setHighlights, expectHighlightLinkNumberToBe, expectHighlightsToBe,
                expectServerHandshakeWithHighlights, expectHighlightsToUpdate,
                maxNumHighlights = 5;

            mockHighlightValues = function(numberOfHighlights) {
                var highlights = [],
                    i;
                for (i = 0; i < numberOfHighlights; i++) {
                    highlights.push('Highlight' + (i + 1));
                }
                return highlights;
            };

            highlightsLink = function() {
                return outlinePage.$('.section-status >> .highlights-button');
            };

            highlightInputs = function() {
                return $('.highlight-input-text');
            };

            openHighlights = function() {
                highlightsLink().click();
            };

            saveHighlights = function() {
                clickSaveOnModal();
            };

            cancelHighlights = function() {
                clickCancelOnModal();
            };

            setHighlights = function(highlights) {
                var i;
                for (i = 0; i < highlights.length; i++) {
                    $(highlightInputs()[i]).val(highlights[i]);
                }
                for (i = highlights.length; i < maxNumHighlights; i++) {
                    $(highlightInputs()[i]).val('');
                }
            };

            expectHighlightLinkNumberToBe = function(expectedNumber) {
                var link = highlightsLink();
                expect(link).toContainText('Section Highlights');
                expect(link.find('.number-highlights')).toHaveHtml(expectedNumber);
            };

            expectHighlightsToBe = function(expectedHighlights) {
                var highlights = highlightInputs(),
                    i;

                expect(highlights).toHaveLength(maxNumHighlights);

                for (i = 0; i < expectedHighlights.length; i++) {
                    expect(highlights[i]).toHaveValue(expectedHighlights[i]);
                }
                for (i = expectedHighlights.length; i < maxNumHighlights; i++) {
                    expect(highlights[i]).toHaveValue('');
                    expect(highlights[i]).toHaveAttr(
                        'placeholder',
                        'A highlight to look forward to this week.'
                    );
                }
            };

            expectServerHandshakeWithHighlights = function(highlights) {
                // POST to update section
                AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-section', {
                    publish: 'republish',
                    metadata: {
                        highlights: highlights
                    }
                });
                AjaxHelpers.respondWithJson(requests, {});

                // GET updated section
                AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
                AjaxHelpers.respondWithJson(requests, createMockSectionJSON({highlights: highlights}));
            };

            expectHighlightsToUpdate = function(originalHighlights, updatedHighlights) {
                createCourseWithHighlights(originalHighlights);

                openHighlights();
                setHighlights(updatedHighlights);
                saveHighlights();

                expectServerHandshakeWithHighlights(updatedHighlights);
                expectHighlightLinkNumberToBe(updatedHighlights.length);

                openHighlights();
                expectHighlightsToBe(updatedHighlights);
            };

            it('displays link when no highlights exist', function() {
                createCourseWithHighlights([]);
                expectHighlightLinkNumberToBe(0);
            });

            it('displays link when highlights exist', function() {
                var highlights = mockHighlightValues(2);
                createCourseWithHighlights(highlights);
                expectHighlightLinkNumberToBe(2);
            });

            it('can view when no highlights exist', function() {
                createCourseWithHighlights([]);
                openHighlights();
                expectHighlightsToBe([]);
            });

            it('can view existing highlights', function() {
                var highlights = mockHighlightValues(2);
                createCourseWithHighlights(highlights);
                openHighlights();
                expectHighlightsToBe(highlights);
            });

            it('does not save highlights when cancelled', function() {
                var originalHighlights = mockHighlightValues(2),
                    editedHighlights = originalHighlights;
                editedHighlights[1] = 'A New Value';

                createCourseWithHighlights(originalHighlights);
                openHighlights();
                setHighlights(editedHighlights);

                cancelHighlights();
                AjaxHelpers.expectNoRequests(requests);

                openHighlights();
                expectHighlightsToBe(originalHighlights);
            });

            it('can add highlights', function() {
                expectHighlightsToUpdate(
                    mockHighlightValues(0),
                    mockHighlightValues(1)
                );
            });

            it('can remove highlights', function() {
                expectHighlightsToUpdate(
                    mockHighlightValues(5),
                    mockHighlightValues(3)
                );
            });

            it('can edit highlights', function() {
                var originalHighlights = mockHighlightValues(3),
                    editedHighlights = originalHighlights;
                editedHighlights[2] = 'A New Value';
                expectHighlightsToUpdate(originalHighlights, editedHighlights);
            });
        });
    });

    describe('Video sharing', function() {
        beforeEach(function() {
            setSelfPaced();
        });

        const createCourse = function(courseOptions) {
            createCourseOutlinePage(this,
                createMockCourseJSON(courseOptions)
            );
        };

        const selectedOption = function() {
            return $('select#video-sharing-configuration-options>option[selected="true"]');
        };

        it('displays video sharing link when enabled', function() {
            createCourse({
                video_sharing_enabled: true,
                video_sharing_options: 'all-on',
                video_sharing_doc_url: 'http://rick.roll'
            });
            expect($('.course-video-sharing')).toExist();
            expect(selectedOption().val()).toEqual('all-on');
        });

        it('if option invalid, none is selected', function() {
            createCourse({
                video_sharing_enabled: true,
                video_sharing_options: 'invalid-option',
                video_sharing_doc_url: 'http://rick.roll'
            });
            expect($('.course-video-sharing')).toExist();
            expect(selectedOption()).not.toExist();
        });

        it('will not display video sharing link when disabled', function() {
            createCourse({
                video_sharing_enabled: false,
                video_sharing_options: 'all-on',
                video_sharing_doc_url: 'http://rick.roll'
            });
            expect($('div.course-video-sharing')).not.toExist();
            expect(selectedOption()).not.toExist();
        });

        it('tracks changes to video sharing option', function() {
            createCourse({
                video_sharing_enabled: true,
                video_sharing_options: 'all-on',
                video_sharing_doc_url: 'http://rick.roll'
            });

            selectedOption().val('per-video').trigger('change');

            expect(window.analytics.track).toHaveBeenCalledWith(
                'edx.social.video_sharing_options.changed',
                {
                    course_id: 'mock-course',
                    video_sharing_options: 'per-video'
                }
            );
        });
    });

    describe('Section', function() {
        var getDisplayNameWrapper;

        getDisplayNameWrapper = function() {
            return getItemHeaders('section').find('.wrapper-xblock-field');
        };

        it('can be deleted', function() {
            var promptSpy = EditHelpers.createPromptSpy();
            createCourseOutlinePage(this, createMockCourseJSON({}, [
                createMockSectionJSON(),
                createMockSectionJSON({id: 'mock-section-2', display_name: 'Mock Section 2'})
            ]));
            getItemHeaders('section').find('.delete-button').first().click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-section');
            AjaxHelpers.respondWithJson(requests, {});
            AjaxHelpers.expectNoRequests(requests); // No fetch should be performed
            expect(outlinePage.$('[data-locator="mock-section"]')).not.toExist();
            expect(outlinePage.$('[data-locator="mock-section-2"]')).toExist();
        });

        it('can be deleted if it is the only section', function() {
            var promptSpy = EditHelpers.createPromptSpy();
            createCourseOutlinePage(this, mockSingleSectionCourseJSON);
            getItemHeaders('section').find('.delete-button').click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-section');
            AjaxHelpers.respondWithJson(requests, {});
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-course');
            AjaxHelpers.respondWithJson(requests, mockEmptyCourseJSON);
            expect(outlinePage.$('.no-content')).not.toHaveClass('is-hidden');
            expect(outlinePage.$('.no-content .button-new')).toExist();
        });

        it('remains visible if its deletion fails', function() {
            var promptSpy = EditHelpers.createPromptSpy(),
                requestCount;
            createCourseOutlinePage(this, mockSingleSectionCourseJSON);
            getItemHeaders('section').find('.delete-button').click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-section');
            AjaxHelpers.respondWithError(requests);
            AjaxHelpers.expectNoRequests(requests);
            expect(outlinePage.$('.list-sections li.outline-section').data('locator')).toEqual('mock-section');
        });

        it('can add a subsection', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            getItemsOfType('section').find('> .outline-content > .add-subsection .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'sequential',
                display_name: 'Subsection',
                parent_locator: 'mock-section'
            });
            AjaxHelpers.respondWithJson(requests, {
                locator: 'new-mock-subsection',
                courseKey: 'slashes:MockCourse'
            });
            // Note: verification of the server response and the UI's handling of it
            // is handled in the acceptance tests.
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
        });

        it('can be renamed inline', function() {
            var updatedDisplayName = 'Updated Section Name',
                displayNameWrapper,
                sectionModel;
            createCourseOutlinePage(this, mockCourseJSON);
            displayNameWrapper = getDisplayNameWrapper();
            displayNameInput = EditHelpers.inlineEdit(displayNameWrapper, updatedDisplayName);
            displayNameInput.change();
            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, { });
            // This is the response for the subsequent fetch operation.
            AjaxHelpers.respondWithJson(requests, {display_name: updatedDisplayName});
            EditHelpers.verifyInlineEditChange(displayNameWrapper, updatedDisplayName);
            sectionModel = outlinePage.model.get('child_info').children[0];
            expect(sectionModel.get('display_name')).toBe(updatedDisplayName);
        });

        it('can be expanded and collapsed', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            collapseItemsAndVerifyState('section');
            expandItemsAndVerifyState('section');
            collapseItemsAndVerifyState('section');
        });

        it('can be edited', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.section-header-actions .configure-button').click();
            $('#start_date').val('1/2/2015');
            // Section release date can't be cleared.
            expect($('.wrapper-modal-window .action-clear')).not.toExist();

            // Section does not contain due_date or grading type selector
            expect($('due_date')).not.toExist();
            expect($('grading_format')).not.toExist();

            // Staff lock controls are always visible on the visibility tab
            selectVisibilitySettings();
            expect($('#staff_lock')).toExist();
            selectBasicSettings();
            $('.wrapper-modal-window .action-save').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-section', {
                metadata: {
                    start: '2015-01-02T00:00:00.000Z'
                }
            });
            expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');

            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, {});
            var mockResponseSectionJSON = createMockSectionJSON({
                release_date: 'Jan 02, 2015 at 00:00 UTC'
            }, [
                createMockSubsectionJSON({}, [
                    createMockVerticalJSON({
                        has_changes: true,
                        published: false
                    })
                ])
            ]);
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
            AjaxHelpers.respondWithJson(requests, mockResponseSectionJSON);
            AjaxHelpers.expectNoRequests(requests);
            expect($('.outline-section .status-release-value')).toContainText('Jan 02, 2015 at 00:00 UTC');
        });

        verifyTypePublishable('section', function(options) {
            return createMockCourseJSON({}, [
                createMockSectionJSON(options, [
                    createMockSubsectionJSON({}, [
                        createMockVerticalJSON()
                    ])
                ])
            ]);
        });

        it('can display a publish modal with a list of unpublished subsections and units', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = createMockCourseJSON({}, [
                    createMockSectionJSON({has_changes: true}, [
                        createMockSubsectionJSON({has_changes: true}, [
                            createMockVerticalJSON(),
                            createMockVerticalJSON({has_changes: true, display_name: 'Unit 100'}),
                            createMockVerticalJSON({published: false, display_name: 'Unit 50'})
                        ]),
                        createMockSubsectionJSON({has_changes: true}, [
                            createMockVerticalJSON({has_changes: true, display_name: 'Unit 1'})
                        ]),
                        createMockSubsectionJSON({}, [createMockVerticalJSON])
                    ]),
                    createMockSectionJSON({has_changes: true}, [
                        createMockSubsectionJSON({has_changes: true}, [
                            createMockVerticalJSON({has_changes: true})
                        ])
                    ])
                ]),
                modalWindow;

            createCourseOutlinePage(this, mockCourseJSON, false);
            getItemHeaders('section').first().find('.publish-button').click();
            var $modalWindow = $('.wrapper-modal-window');
            expect($modalWindow.find('.outline-unit').length).toBe(3);
            expect(_.compact(_.map($modalWindow.find('.outline-unit').text().split('\n'), $.trim))).toEqual(
                ['Unit 100', 'Unit 50', 'Unit 1']
            );
            expect($modalWindow.find('.outline-subsection').length).toBe(2);
        });
    });

    describe('Subsection', function() {
        var getDisplayNameWrapper, setEditModalValues, setEditModalValuesForCustomPacing, setContentVisibility, mockServerValuesJson,
            mockCustomPacingServerValuesJson, selectDisableSpecialExams, selectTimedExam, selectProctoredExam, selectPracticeExam,
            selectPrerequisite, selectLastPrerequisiteSubsection, selectRelativeWeeksSubsection, checkOptionFieldVisibility,
            defaultModalSettings, modalSettingsWithExamReviewRules, getMockNoPrereqOrExamsCourseJSON, expectShowCorrectness;

        getDisplayNameWrapper = function() {
            return getItemHeaders('subsection').find('.wrapper-xblock-field');
        };

        setEditModalValues = function(start_date, due_date, grading_type) {
            $('#start_date').val(start_date);
            $('#due_date').val(due_date);
            $('#grading_type').val(grading_type);
        };

        setContentVisibility = function(visibility) {
            $('input[name=content-visibility][value=' + visibility + ']').prop('checked', true);
        };

        selectDisableSpecialExams = function() {
            $('input.no_special_exam').prop('checked', true).trigger('change');
        };

        selectTimedExam = function(time_limit) {
            $('input.timed_exam').prop('checked', true).trigger('change');
            $('.field-time-limit input').val(time_limit);
            $('.field-time-limit input').trigger('focusout');
            setContentVisibility('hide_after_due');
        };

        selectProctoredExam = function(time_limit) {
            $('input.proctored_exam').prop('checked', true).trigger('change');
            $('.field-time-limit input').val(time_limit);
            $('.field-time-limit input').trigger('focusout');
        };

        selectPracticeExam = function(time_limit) {
            $('input.practice_exam').prop('checked', true).trigger('change');
            $('.field-time-limit input').val(time_limit);
            $('.field-time-limit input').trigger('focusout');
        };

        selectOnboardingExam = function(time_limit) {
            $('input.onboarding_exam').prop('checked', true).trigger('change');
            $('.field-time-limit input').val(time_limit);
            $('.field-time-limit input').trigger('focusout');
        };

        selectPrerequisite = function() {
            $('#is_prereq').prop('checked', true).trigger('change');
        };

        selectLastPrerequisiteSubsection = function(minScore, minCompletion) {
            $('#prereq option:last').prop('selected', true).trigger('change');
            $('#prereq_min_score').val(minScore).trigger('keyup');
            $('#prereq_min_completion').val(minCompletion).trigger('keyup');
        };

        // Helper to validate oft-checked additional option fields' visibility
        checkOptionFieldVisibility = function(time_limit, review_rules) {
            expect($('.field-time-limit').is(':visible')).toBe(time_limit);
            expect($('.field-exam-review-rules').is(':visible')).toBe(review_rules);
        };

        expectShowCorrectness = function(showCorrectness) {
            expect($('input[name=show-correctness][value=' + showCorrectness + ']').is(':checked')).toBe(true);
        };

        getMockNoPrereqOrExamsCourseJSON = function() {
            var mockVerticalJSON = createMockVerticalJSON({}, []);
            var mockSubsectionJSON = createMockSubsectionJSON({}, [mockVerticalJSON]);
            delete mockSubsectionJSON.is_prereq;
            delete mockSubsectionJSON.prereqs;
            delete mockSubsectionJSON.prereq;
            delete mockSubsectionJSON.prereq_min_score;
            delete mockSubsectionJSON.prereq_min_completion;
            return createMockCourseJSON({
                enable_proctored_exams: false,
                enable_timed_exams: false
            }, [
                createMockSectionJSON({}, [mockSubsectionJSON])
            ]);
        };

        defaultModalSettings = {
            graderType: 'notgraded',
            isPrereq: false,
            metadata: {
                due: null,
                is_practice_exam: false,
                is_time_limited: false,
                is_proctored_enabled: false,
                default_time_limit_minutes: null,
                is_onboarding_exam: false
            }
        };

        modalSettingsWithExamReviewRules = {
            graderType: 'notgraded',
            isPrereq: false,
            metadata: {
                due: null,
                is_practice_exam: false,
                is_time_limited: false,
                is_proctored_enabled: false,
                default_time_limit_minutes: null,
                is_onboarding_exam: false
            }
        };

        // Contains hard-coded dates because dates are presented in different formats.
        mockServerValuesJson = createMockSectionJSON({
            release_date: 'Jan 01, 2970 at 05:00 UTC'
        }, [
            createMockSubsectionJSON({
                graded: true,
                due_date: 'Jul 10, 2014 at 00:00 UTC',
                release_date: 'Jul 09, 2014 at 00:00 UTC',
                start: '2014-07-09T00:00:00Z',
                format: 'Lab',
                due: '2014-07-10T00:00:00Z',
                has_explicit_staff_lock: true,
                staff_only_message: true,
                is_prereq: false,
                show_correctness: 'never',
                is_time_limited: true,
                is_practice_exam: false,
                is_proctored_exam: false,
                default_time_limit_minutes: 150,
                hide_after_due: true,
                hide_from_toc: false,
                enable_hide_from_toc_ui: true,
            }, [
                createMockVerticalJSON({
                    has_changes: true,
                    published: false
                })
            ])
        ]);

        it('can be deleted', function() {
            var promptSpy = EditHelpers.createPromptSpy();
            createCourseOutlinePage(this, mockCourseJSON);
            getItemHeaders('subsection').find('.delete-button').click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-subsection');
            AjaxHelpers.respondWithJson(requests, {});
            // Note: verification of the server response and the UI's handling of it
            // is handled in the acceptance tests.
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
        });

        it('can add a unit', function() {
            var redirectSpy;
            createCourseOutlinePage(this, mockCourseJSON);
            redirectSpy = spyOn(ViewUtils, 'redirect');
            getItemsOfType('subsection').find('> .outline-content > .add-unit .button-new').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                category: 'vertical',
                display_name: 'Unit',
                parent_locator: 'mock-subsection'
            });
            AjaxHelpers.respondWithJson(requests, {
                locator: 'new-mock-unit',
                courseKey: 'slashes:MockCourse'
            });
            expect(redirectSpy).toHaveBeenCalledWith('/container/new-mock-unit?action=new');
        });

        it('can be renamed inline', function() {
            var updatedDisplayName = 'Updated Subsection Name',
                displayNameWrapper,
                subsectionModel;
            createCourseOutlinePage(this, mockCourseJSON);
            displayNameWrapper = getDisplayNameWrapper();
            displayNameInput = EditHelpers.inlineEdit(displayNameWrapper, updatedDisplayName);
            displayNameInput.change();
            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, { });
            // This is the response for the subsequent fetch operation for the section.
            AjaxHelpers.respondWithJson(requests,
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        display_name: updatedDisplayName
                    })
                ])
            );
            // Find the display name again in the refreshed DOM and verify it
            displayNameWrapper = getItemHeaders('subsection').find('.wrapper-xblock-field');
            EditHelpers.verifyInlineEditChange(displayNameWrapper, updatedDisplayName);
            subsectionModel = outlinePage.model.get('child_info').children[0].get('child_info').children[0];
            expect(subsectionModel.get('display_name')).toBe(updatedDisplayName);
        });

        it('can be expanded and collapsed', function() {
            createCourseOutlinePage(this, mockCourseJSON);
            verifyItemsExpanded('subsection', false);
            expandItemsAndVerifyState('subsection');
            collapseItemsAndVerifyState('subsection');
            expandItemsAndVerifyState('subsection');
        });

        it('subsection can show basic settings', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectBasicSettings();
            expect($('.modal-section .settings-tab-button[data-tab="basic"]')).toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="visibility"]')).not.toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="advanced"]')).not.toHaveClass('active');
        });

        it('subsection can show visibility settings', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectVisibilitySettings();
            expect($('.modal-section .settings-tab-button[data-tab="basic"]')).not.toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="visibility"]')).toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="advanced"]')).not.toHaveClass('active');
        });

        it('subsection can show advanced settings', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('.modal-section .settings-tab-button[data-tab="basic"]')).not.toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="visibility"]')).not.toHaveClass('active');
            expect($('.modal-section .settings-tab-button[data-tab="advanced"]')).toHaveClass('active');
        });

        it('subsection does not show advanced settings tab if no special exams or prerequisites', function() {
            var mockNoPrereqCourseJSON = getMockNoPrereqOrExamsCourseJSON();
            createCourseOutlinePage(this, mockNoPrereqCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.modal-section .settings-tab-button[data-tab="basic"]')).toExist();
            expect($('.modal-section .settings-tab-button[data-tab="visibility"]')).toExist();
            expect($('.modal-section .settings-tab-button[data-tab="advanced"]')).not.toExist();
        });

        it('unit does not show settings tab headers if there is only one tab to show', function() {
            var mockNoPrereqCourseJSON = getMockNoPrereqOrExamsCourseJSON();
            createCourseOutlinePage(this, mockNoPrereqCourseJSON, false);
            outlinePage.$('.outline-unit .configure-button').click();
            expect($('.settings-tabs-header').length).toBe(0);
        });

        it('can show correct editors for self_paced course', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({}, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseJSON, false);
            setSelfPaced();
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.edit-settings-release').length).toBe(0);
            expect($('.grading-due-date').length).toBe(0);
            expect($('.edit-settings-grading').length).toBe(1);
            expect($('.edit-content-visibility').length).toBe(1);
            expect($('.edit-show-correctness').length).toBe(1);
        });

        it('can select valid time', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();

            var default_time = '00:30';
            var valid_times = ['00:30', '23:00', '24:00', '99:00'];
            var invalid_times = ['00:00', '100:00', '01:60'];
            var time_limit, i;

            for (i = 0; i < valid_times.length; i++) {
                time_limit = valid_times[i];
                selectTimedExam(time_limit);
                expect($('.field-time-limit input').val()).toEqual(time_limit);
            }
            for (i = 0; i < invalid_times.length; i++) {
                time_limit = invalid_times[i];
                selectTimedExam(time_limit);
                expect($('.field-time-limit input').val()).not.toEqual(time_limit);
                expect($('.field-time-limit input').val()).toEqual(default_time);
            }
        });

        it('can be saved', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            $('.wrapper-modal-window .action-save').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection', defaultModalSettings);
            expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');
        });

        it('can be edited', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectAdvancedSettings();
            selectTimedExam('02:30');
            $('.wrapper-modal-window .action-save').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection', {
                graderType: 'Lab',
                publish: 'republish',
                isPrereq: false,
                metadata: {
                    visible_to_staff_only: null,
                    start: '2014-07-09T00:00:00.000Z',
                    due: '2014-07-10T00:00:00.000Z',
                    is_time_limited: true,
                    is_practice_exam: false,
                    is_proctored_enabled: false,
                    default_time_limit_minutes: 150,
                    hide_after_due: true,
                    is_onboarding_exam: false,
                    hide_from_toc: null,
                }
            });
            expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');
            AjaxHelpers.respondWithJson(requests, {});

            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
            AjaxHelpers.respondWithJson(requests, mockServerValuesJson);
            AjaxHelpers.expectNoRequests(requests);

            expect($('.outline-subsection .status-release-value')).toContainText(
                'Jul 09, 2014 at 00:00 UTC'
            );
            expect($('.outline-subsection .status-grading-date')).toContainText(
                'Due: Jul 10, 2014 at 00:00 UTC'
            );
            expect($('.outline-subsection .status-grading-value')).toContainText(
                'Lab'
            );
            expect($('.outline-subsection .status-message-copy')).toContainText(
                'Contains staff only content'
            );

            expect($('.outline-item .outline-subsection .status-grading-value')).toContainText('Lab');
            outlinePage.$('.outline-item .outline-subsection .configure-button').click();
            expect($('#start_date').val()).toBe('7/9/2014');
            expect($('#due_date').val()).toBe('7/10/2014');
            expect($('#grading_type').val()).toBe('Lab');
            expect($('input[name=content-visibility][value=staff_only]').is(':checked')).toBe(true);
            expect($('input.timed_exam').is(':checked')).toBe(true);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.practice_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('02:30');
            expectShowCorrectness('never');
        });

        it('review rules exists', function() {
            createCourseOutlinePage(this, mockCourseJSONWithReviewRules, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            $('.wrapper-modal-window .action-save').click();
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection', modalSettingsWithExamReviewRules);
            expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');
        });

        it('can hide time limit and hide after due fields when the None radio box is selected', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectVisibilitySettings();
            setContentVisibility('staff_only');
            selectAdvancedSettings();
            selectDisableSpecialExams();

            // all additional options should be hidden
            expect($('.exam-options').is(':hidden')).toBe(true);
        });

        it('can select the practice exam', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectVisibilitySettings();
            setContentVisibility('staff_only');
            selectAdvancedSettings();
            selectPracticeExam('00:30');

            // time limit should be visible, review rules should be hidden
            checkOptionFieldVisibility(true, false);

            $('.wrapper-modal-window .action-save').click();
        });

        it('can select the onboarding exam when a course supports onboarding', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: true,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150,
                        supports_onboarding: true,
                        show_review_rules: true
                    }, [
                    ])
                ])
            ]);

            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectVisibilitySettings();
            setContentVisibility('staff_only');
            selectAdvancedSettings();
            selectOnboardingExam('00:30');

            // time limit should be visible, review rules should be hidden
            checkOptionFieldVisibility(true, false);

            $('.wrapper-modal-window .action-save').click();
        });

        it('does not show practice exam option when course supports onboarding', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: true,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150,
                        supports_onboarding: true,
                    }, [
                    ])
                ])
            ]);

            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.practice_exam')).not.toExist();
            expect($('input.onboarding_exam')).toExist();
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('does not show onboarding exam option when course does not support onboarding', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: true,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150,
                        supports_onboarding: false,
                    }, [
                    ])
                ])
            ]);

            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.practice_exam')).toExist();
            expect($('input.onboarding_exam')).not.toExist();
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('can select the timed exam', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectAdvancedSettings();
            selectTimedExam('00:30');

            // time limit should be visible, review rules should be hidden
            checkOptionFieldVisibility(true, false);

            $('.wrapper-modal-window .action-save').click();
        });

        it('can select the Proctored exam option', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: true,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150,
                        supports_onboarding: false,
                        show_review_rules: true,
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectVisibilitySettings();
            setContentVisibility('staff_only');
            selectAdvancedSettings();
            selectProctoredExam('00:30');

            // time limit and review rules should be visible
            checkOptionFieldVisibility(true, true);

            $('.wrapper-modal-window .action-save').click();
        });

        it('entering invalid time format uses default value of 30 minutes.', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            selectVisibilitySettings();
            setContentVisibility('staff_only');
            selectAdvancedSettings();
            selectProctoredExam('abcd');

            // time limit field should be visible and have the correct value
            expect($('.field-time-limit').is(':visible')).toBe(true);
            expect($('.field-time-limit input').val()).toEqual('00:30');
        });

        it('can show a saved non-special exam correctly', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: false,
                        is_practice_exam: false,
                        is_proctored_exam: false,
                        default_time_limit_minutes: 150,
                        hide_after_due: false
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(false);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(true);
            expect($('input.practice_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('can show a saved timed exam correctly when hide_after_due is true', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: false,
                        is_proctored_exam: false,
                        default_time_limit_minutes: 10,
                        hide_after_due: true
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(true);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.practice_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('00:10');
        });

        it('can show a saved timed exam correctly when hide_after_due is true', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: false,
                        is_proctored_exam: false,
                        default_time_limit_minutes: 10,
                        hide_after_due: false
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(true);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.practice_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('00:10');
            expect($('.field-hide-after-due input').is(':checked')).toBe(false);
        });

        it('can show a saved practice exam correctly', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: true,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(false);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.practice_exam').is(':checked')).toBe(true);
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('can show a saved proctored exam correctly', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: false,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(false);
            expect($('input.proctored_exam').is(':checked')).toBe(true);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.practice_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('can show a saved onboarding exam correctly', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: true,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: false,
                        is_proctored_exam: true,
                        default_time_limit_minutes: 150,
                        supports_onboarding: true,
                        is_onboarding_exam: true
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(false);
            expect($('input.proctored_exam').is(':checked')).toBe(false);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('input.onboarding_exam').is(':checked')).toBe(true);
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('does not show proctored settings if proctored exams not enabled', function() {
            var mockCourseWithSpecialExamJSON = createMockCourseJSON({}, [
                createMockSectionJSON({
                    has_changes: true,
                    enable_proctored_exams: false,
                    enable_timed_exams: true

                }, [
                    createMockSubsectionJSON({
                        has_changes: true,
                        is_time_limited: true,
                        is_practice_exam: false,
                        is_proctored_exam: false,
                        default_time_limit_minutes: 150,
                        hide_after_due: true
                    }, [
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithSpecialExamJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            expect($('input.timed_exam').is(':checked')).toBe(true);
            expect($('input.no_special_exam').is(':checked')).toBe(false);
            expect($('.field-time-limit input').val()).toBe('02:30');
        });

        it('can select prerequisite', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectPrerequisite();
            expect($('#is_prereq').is(':checked')).toBe(true);
            $('.wrapper-modal-window .action-save').click();
        });

        it('can be deleted when it is a prerequisite', function() {
            var promptSpy = EditHelpers.createPromptSpy();
            var mockCourseWithPrequisiteJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        is_prereq: true
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPrequisiteJSON, false);
            getItemHeaders('subsection').find('.delete-button').click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-subsection');
            AjaxHelpers.respondWithJson(requests, {});
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
        });

        it('can show a saved prerequisite correctly', function() {
            var mockCourseWithPrequisiteJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        is_prereq: true
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPrequisiteJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('#is_prereq').is(':checked')).toBe(true);
        });

        it('does not display prerequisite subsections if none are available', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.gating-prereq').length).toBe(0);
        });

        it('can display available prerequisite subsections', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}]
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.gating-prereq').length).toBe(1);
        });

        it('can select prerequisite subsection', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}]
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectLastPrerequisiteSubsection('80', '0');
            expect($('#prereq_min_score_input').css('display')).not.toBe('none');
            expect($('#prereq option:selected').val()).toBe('usage_key');
            expect($('#prereq_min_score').val()).toBe('80');
            expect($('#prereq_min_completion_input').css('display')).not.toBe('none');
            expect($('#prereq_min_completion').val()).toBe('0');
            $('.wrapper-modal-window .action-save').click();
        });

        it('can display gating correctly', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        visibility_state: 'gated',
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}],
                        prereq: 'usage_key',
                        prereq_min_score: '80'
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            expect($('.outline-subsection .status-message-copy')).toContainText(
                'Prerequisite: Prereq Subsection 1'
            );
        });

        it('can show a saved prerequisite subsection correctly', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}],
                        prereq: 'usage_key',
                        prereq_min_score: '80',
                        prereq_min_completion: '50'
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.gating-prereq').length).toBe(1);
            expect($('#prereq option:selected').val()).toBe('usage_key');
            expect($('#prereq_min_score_input').css('display')).not.toBe('none');
            expect($('#prereq_min_score').val()).toBe('80');
            expect($('#prereq_min_completion_input').css('display')).not.toBe('none');
            expect($('#prereq_min_completion').val()).toBe('50');
        });

        it('can show a saved prerequisite subsection with empty min score correctly', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}],
                        prereq: 'usage_key',
                        prereq_min_score: '',
                        prereq_min_completion: '50'
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('.gating-prereq').length).toBe(1);
            expect($('#prereq option:selected').val()).toBe('usage_key');
            expect($('#prereq_min_score_input').css('display')).not.toBe('none');
            expect($('#prereq_min_score').val()).toBe('100');
            expect($('#prereq_min_completion_input').css('display')).not.toBe('none');
            expect($('#prereq_min_completion').val()).toBe('50');
        });

        it('can display validation error on non-integer or empty minimum score', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}]
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectLastPrerequisiteSubsection('', '50');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('50', '');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('', '');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('abc', '50');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('50', 'abc');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('5.5', '50');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('50', '5.5');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
        });

        it('can display validation error on out of bounds minimum score', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}]
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectLastPrerequisiteSubsection('-5', '50');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('50', '-5');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('105', '50');
            expect($('#prereq_min_score_error').css('display')).not.toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
            selectLastPrerequisiteSubsection('50', '105');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).not.toBe('none');
            expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
            expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);
        });

        it('does not display validation error on valid minimum score', function() {
            var mockCourseWithPreqsJSON = createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({
                        prereqs: [{block_usage_key: 'usage_key', block_display_name: 'Prereq Subsection 1'}]
                    }, [])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseWithPreqsJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            selectAdvancedSettings();
            selectLastPrerequisiteSubsection('80', '50');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            selectLastPrerequisiteSubsection('0', '0');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
            selectLastPrerequisiteSubsection('100', '100');
            expect($('#prereq_min_score_error').css('display')).toBe('none');
            expect($('#prereq_min_completion_error').css('display')).toBe('none');
        });

        it('release date, due date, grading type, and staff lock can be cleared.', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-item .outline-subsection .configure-button').click();
            setEditModalValues('7/9/2014', '7/10/2014', 'Lab');
            setContentVisibility('staff_only');
            $('.wrapper-modal-window .action-save').click();

            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, {});
            // This is the response for the subsequent fetch operation.
            AjaxHelpers.respondWithJson(requests, mockServerValuesJson);

            expect($('.outline-subsection .status-release-value')).toContainText(
                'Jul 09, 2014 at 00:00 UTC'
            );
            expect($('.outline-subsection .status-grading-date')).toContainText(
                'Due: Jul 10, 2014 at 00:00 UTC'
            );
            expect($('.outline-subsection .status-grading-value')).toContainText(
                'Lab'
            );
            expect($('.outline-subsection .status-message-copy')).toContainText(
                'Contains staff only content'
            );

            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('#start_date').val()).toBe('7/9/2014');
            expect($('#due_date').val()).toBe('7/10/2014');
            expect($('#grading_type').val()).toBe('Lab');
            expect($('input[name=content-visibility][value=staff_only]').is(':checked')).toBe(true);

            $('.wrapper-modal-window .scheduled-date-input .action-clear').click();
            $('.wrapper-modal-window .due-date-input .action-clear').click();
            expect($('#start_date').val()).toBe('');
            expect($('#due_date').val()).toBe('');

            $('#grading_type').val('notgraded');
            setContentVisibility('visible');

            $('.wrapper-modal-window .action-save').click();

            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, {});
            // This is the response for the subsequent fetch operation.
            AjaxHelpers.respondWithJson(requests,
                createMockSectionJSON({}, [createMockSubsectionJSON()])
            );
            expect($('.outline-subsection .status-release-value')).not.toContainText(
                'Jul 09, 2014 at 00:00 UTC'
            );
            expect($('.outline-subsection .status-grading-date')).not.toExist();
            expect($('.outline-subsection .status-grading-value')).not.toExist();
            expect($('.outline-subsection .status-message-copy')).not.toContainText(
                'Contains staff only content'
            );
        });

        describe('Show correctness setting set as expected.', function() {
            var setShowCorrectness;

            setShowCorrectness = function(showCorrectness) {
                $('input[name=show-correctness][value=' + showCorrectness + ']').click();
            };

            describe('Show correctness set by subsection metadata.', function() {
                $.each(['always', 'never', 'past_due'], function(index, showCorrectness) {
                    it('show_correctness="' + showCorrectness + '"', function() {
                        var mockCourseJSONCorrectness = createMockCourseJSON({}, [
                            createMockSectionJSON({}, [
                                createMockSubsectionJSON({show_correctness: showCorrectness}, [])
                            ])
                        ]);
                        createCourseOutlinePage(this, mockCourseJSONCorrectness, false);
                        outlinePage.$('.outline-subsection .configure-button').click();
                        selectVisibilitySettings();
                        expectShowCorrectness(showCorrectness);
                    });
                });
            });

            describe('Show correctness editor works as expected.', function() {
                beforeEach(function() {
                    createCourseOutlinePage(this, mockCourseJSON, false);
                    outlinePage.$('.outline-subsection .configure-button').click();
                    selectVisibilitySettings();
                });

                it('show_correctness="always" (default, unchanged metadata)', function() {
                    setShowCorrectness('always');
                    $('.wrapper-modal-window .action-save').click();
                    AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection',
                        defaultModalSettings);
                });

                $.each(['never', 'past_due'], function(index, showCorrectness) {
                    it('show_correctness="' + showCorrectness + '" updates settings, republishes', function() {
                        var expectedSettings = $.extend(true, {}, defaultModalSettings, {publish: 'republish'});
                        expectedSettings.metadata.show_correctness = showCorrectness;

                        setShowCorrectness(showCorrectness);
                        $('.wrapper-modal-window .action-save').click();
                        AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection',
                            expectedSettings);
                    });
                });
            });
        });

        verifyTypePublishable('subsection', function(options) {
            return createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON(options, [
                        createMockVerticalJSON()
                    ])
                ])
            ]);
        });

        it('can display a publish modal with a list of unpublished units', function() {
            // eslint-disable-next-line no-shadow
            var mockCourseJSON = createMockCourseJSON({}, [
                    createMockSectionJSON({has_changes: true}, [
                        createMockSubsectionJSON({has_changes: true}, [
                            createMockVerticalJSON(),
                            createMockVerticalJSON({has_changes: true, display_name: 'Unit 100'}),
                            createMockVerticalJSON({published: false, display_name: 'Unit 50'})
                        ]),
                        createMockSubsectionJSON({has_changes: true}, [
                            createMockVerticalJSON({has_changes: true})
                        ]),
                        createMockSubsectionJSON({}, [createMockVerticalJSON])
                    ])
                ]),
                $modalWindow;

            createCourseOutlinePage(this, mockCourseJSON, false);
            getItemHeaders('subsection').first().find('.publish-button').click();
            $modalWindow = $('.wrapper-modal-window');
            expect($modalWindow.find('.outline-unit').length).toBe(2);
            expect(_.compact(_.map($modalWindow.find('.outline-unit').text().split('\n'), $.trim))).toEqual(
                ['Unit 100', 'Unit 50']
            );
            expect($modalWindow.find('.outline-subsection')).not.toExist();
        });

        describe('Self Paced with Custom Personalized Learner Schedules (PLS)', function() {
            beforeEach(function() {
                // eslint-disable-next-line no-shadow
                var mockCourseJSON = createMockCourseJSON({}, [
                    createMockSectionJSON({}, [
                        createMockSubsectionJSON({}, [])
                    ])
                ]);
                createCourseOutlinePage(this, mockCourseJSON, false);
                setSelfPacedCustomPLS();
                course.set('start', '2014-07-09T00:00:00Z');
            });

            setEditModalValuesForCustomPacing = function(grading_type, due_in) {
                $('#grading_type').val(grading_type);
                $('#due_in').val(due_in);
            };

            selectRelativeWeeksSubsection = function(weeks) {
                $('#due_in').val(weeks).trigger('keyup');
            };

            mockCustomPacingServerValuesJson = createMockSectionJSON({
                release_date: 'Jan 01, 2970 at 05:00 UTC'
            }, [
                createMockSubsectionJSON({
                    graded: true,
                    relative_weeks_due: 3,
                    format: 'Lab',
                    has_explicit_staff_lock: true,
                    staff_only_message: true,
                    is_prereq: false,
                    show_correctness: 'never',
                    is_time_limited: false,
                    is_practice_exam: false,
                    is_proctored_exam: false,
                    default_time_limit_minutes: null,
                    hide_from_toc: false,
                    enable_hide_from_toc_ui: true,
                }, [
                    createMockVerticalJSON({
                        has_changes: true,
                        published: false
                    })
                ])
            ]);

            it('can show correct editors for self_paced course with custom pacing', function() {
                outlinePage.$('.outline-subsection .configure-button').click();
                expect($('.edit-settings-release').length).toBe(0);
                // Due date input exists for custom pacing self paced courses
                expect($('.grading-due-date').length).toBe(1);
                expect($('.edit-settings-grading').length).toBe(1);
                expect($('.edit-content-visibility').length).toBe(1);
                expect($('.edit-show-correctness').length).toBe(1);
            });

            it('can be edited when custom pacing for self paced course is active', function() {
                outlinePage.$('.outline-subsection .configure-button').click();
                setEditModalValuesForCustomPacing('Lab', '3');
                $('.wrapper-modal-window .action-save').click();

                AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-subsection', {
                    graderType: 'Lab',
                    isPrereq: false,
                    metadata: {
                        relative_weeks_due: 3,
                        is_time_limited: false,
                        is_practice_exam: false,
                        is_proctored_enabled: false,
                        default_time_limit_minutes: null,
                        is_onboarding_exam: false,
                    }
                });
                expect(requests[0].requestHeaders['X-HTTP-Method-Override']).toBe('PATCH');
                AjaxHelpers.respondWithJson(requests, {});

                AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
                AjaxHelpers.respondWithJson(requests, mockCustomPacingServerValuesJson);
                AjaxHelpers.expectNoRequests(requests);

                expect($('.outline-subsection .status-custom-grading-date').text().trim()).toEqual(
                    'Custom due date: 3 weeks from enrollment'
                );

                expect($('.outline-subsection .status-grading-value')).toContainText(
                    'Lab'
                );
                expect($('.outline-subsection .status-message-copy')).toContainText(
                    'Contains staff only content'
                );

                expect($('.outline-item .outline-subsection .status-grading-value')).toContainText('Lab');
                outlinePage.$('.outline-item .outline-subsection .configure-button').click();

                expect($('#relative_date_input').css('display')).not.toBe('none');
                expect($('#relative_weeks_due_projected.message').text().trim()).toEqual('If a learner starts on Jul 09, 2014, this subsection will be due on Jul 30, 2014.');
                expect($('#due_in').val()).toBe('3');
                expect($('#grading_type').val()).toBe('Lab');
                expect($('input[name=content-visibility][value=staff_only]').is(':checked')).toBe(true);
                expect($('input.timed_exam').is(':checked')).toBe(false);
                expect($('input.proctored_exam').is(':checked')).toBe(false);
                expect($('input.no_special_exam').is(':checked')).toBe(true);
                expect($('input.practice_exam').is(':checked')).toBe(false);
                expectShowCorrectness('never');
            });

            it('does not show relative date input when assignment is not graded', function() {
                outlinePage.$('.outline-subsection .configure-button').click();
                $('#grading_type').val('Lab').trigger('change');
                $('#due_in').val('').trigger('change');
                expect($('#relative_date_input').css('display')).not.toBe('none');

                $('#grading_type').val('notgraded').trigger('change');
                $('#due_in').val('').trigger('change');
                expect($('#relative_date_input').css('display')).toBe('none');
            });

            it('shows validation error on relative date', function() {
                outlinePage.$('.outline-subsection .configure-button').click();

                // when due number of weeks is less than 1
                selectRelativeWeeksSubsection('-1');
                expect($('#relative_weeks_due_warning_min').css('display')).not.toBe('none');
                expect($('#relative_weeks_due_warning_min')).toContainText('The minimum number of weeks this subsection can be due in is 1 week from the learner enrollment date.');
                expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(true);
                expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(true);

                // when no validation error should show up
                selectRelativeWeeksSubsection('19');
                expect($('#relative_weeks_due_warning_min').css('display')).toBe('none');
                expect($('.wrapper-modal-window .action-save').prop('disabled')).toBe(false);
                expect($('.wrapper-modal-window .action-save').hasClass('is-disabled')).toBe(false);
            });

            it('outline with assignment type and date are cleared when relative date input is cleared.', function() {
                outlinePage.$('.outline-item .outline-subsection .configure-button').click();
                setEditModalValuesForCustomPacing('Lab', '3');
                setContentVisibility('staff_only');
                $('.wrapper-modal-window .action-save').click();

                // This is the response for the change operation.
                AjaxHelpers.respondWithJson(requests, {});
                // This is the response for the subsequent fetch operation.
                AjaxHelpers.respondWithJson(requests, mockCustomPacingServerValuesJson);

                expect($('.outline-subsection .status-custom-grading-date').text().trim()).toEqual(
                    'Custom due date: 3 weeks from enrollment'
                );

                expect($('.outline-subsection .status-grading-value')).toContainText(
                    'Lab'
                );
                expect($('.outline-subsection .status-message-copy')).toContainText(
                    'Contains staff only content'
                );

                outlinePage.$('.outline-subsection .configure-button').click();
                expect($('#relative_weeks_due_projected.message').text().trim()).toEqual('If a learner starts on Jul 09, 2014, this subsection will be due on Jul 30, 2014.');
                expect($('#due_in').val()).toBe('3');
                expect($('#grading_type').val()).toBe('Lab');
                expect($('input[name=content-visibility][value=staff_only]').is(':checked')).toBe(true);

                $('#due_in').val('');

                $('#grading_type').val('notgraded');
                setContentVisibility('visible');

                $('.wrapper-modal-window .action-save').click();

                // This is the response for the change operation.
                AjaxHelpers.respondWithJson(requests, {});
                // This is the response for the subsequent fetch operation.
                AjaxHelpers.respondWithJson(requests,
                    createMockSectionJSON({}, [createMockSubsectionJSON()])
                );

                expect($('.outline-subsection .status-custom-grading-date')).not.toExist();
                expect($('.outline-subsection .status-grading-value')).not.toExist();
                expect($('.outline-subsection .status-message-copy')).not.toContainText(
                    'Contains staff only content'
                );
            });
        });
    });

    // Note: most tests for units can be found in Bok Choy
    describe('Unit', function() {
        var getUnitStatus = function(options, courseOptions) {
            courseOptions = courseOptions || {};
            mockCourseJSON = createMockCourseJSON(courseOptions, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({}, [
                        createMockVerticalJSON(options)
                    ])
                ])
            ]);
            createCourseOutlinePage(this, mockCourseJSON);
            expandItemsAndVerifyState('subsection');
            return getItemsOfType('unit').find('.unit-status .status-message');
        };

        it('can be deleted', function() {
            var promptSpy = EditHelpers.createPromptSpy();
            createCourseOutlinePage(this, mockCourseJSON);
            expandItemsAndVerifyState('subsection');
            getItemHeaders('unit').find('.delete-button').click();
            EditHelpers.confirmPrompt(promptSpy);
            AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/mock-unit');
            AjaxHelpers.respondWithJson(requests, {});
            // Note: verification of the server response and the UI's handling of it
            // is handled in the acceptance tests.
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/outline/mock-section');
        });

        it('has a link to the unit page', function() {
            var unitAnchor;
            createCourseOutlinePage(this, mockCourseJSON);
            expandItemsAndVerifyState('subsection');
            unitAnchor = getItemsOfType('unit').find('.unit-title a');
            expect(unitAnchor.attr('href')).toBe('/container/mock-unit');
        });

        it('shows partition group information', function() {
            var messages = getUnitStatus({has_partition_group_components: true});
            expect(messages.length).toBe(1);
            expect(messages).toContainText(
                'Access to some content in this unit is restricted to specific groups of learners'
            );
        });

        it('shows partition group information with group_access set', function() {
            var partitions = [
                {
                    scheme: 'cohort',
                    id: 1,
                    groups: [
                        {
                            deleted: false,
                            selected: true,
                            id: 2,
                            name: 'Group 2'
                        },
                        {
                            deleted: false,
                            selected: true,
                            id: 3,
                            name: 'Group 3'
                        }
                    ],
                    name: 'Content Group Configuration'
                }
            ];
            var messages = getUnitStatus({
                has_partition_group_components: true,
                user_partitions: partitions,
                group_access: {1: [2, 3]},
                user_partition_info: {
                    selected_partition_index: 1,
                    selected_groups_label: '1, 2',
                    selectable_partitions: partitions
                }
            });
            expect(messages.length).toBe(1);
            expect(messages).toContainText(
                'Access to this unit is restricted to'
            );
        });

        it('does not show partition group information if visible to all', function() {
            var messages = getUnitStatus({});
            expect(messages.length).toBe(0);
        });

        it('does not show partition group information if staff locked', function() {
            var messages = getUnitStatus(
                {has_partition_group_components: true, staff_only_message: true}
            );
            expect(messages.length).toBe(1);
            expect(messages).toContainText('Contains staff only content');
        });

        describe('discussion settings', function() {
            it('hides discussion settings if unit level discussions are disabled', function() {
                getUnitStatus({}, {unit_level_discussions: false});
                outlinePage.$('.outline-unit .configure-button').click();
                expect($('.modal-section .edit-discussion')).not.toExist();
            });
        });

        describe('summary configuration', function() {
            it('hides summary configuration settings if summary_configuration_enabled is not a boolean', function() {
                getUnitStatus({summary_configuration_enabled: null});
                outlinePage.$('.outline-unit .configure-button').click();
                expect($('.modal-section .summary-configuration')).not.toExist();
            });

            it('shows summary configuration settings if summary_configuration_enabled is true', function() {
                getUnitStatus({summary_configuration_enabled: true});
                outlinePage.$('.outline-unit .configure-button').click();
                expect($('.modal-section .summary-configuration')).toExist();
            });

            it('shows summary configuration settings if summary_configuration_enabled is false', function() {
                getUnitStatus({summary_configuration_enabled: false});
                outlinePage.$('.outline-unit .configure-button').click();
                expect($('.modal-section .summary-configuration')).toExist();
            });

            it('can be updated', function() {
                getUnitStatus({summary_configuration_enabled: false});
                outlinePage.$('.outline-unit .configure-button').click();
                expect($('#summary_configuration_enabled').is(':checked')).toBe(false);
                $('#summary_configuration_enabled').prop('checked', true).trigger('change');
                $('.wrapper-modal-window .action-save').click();
                AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/mock-unit', {
                    summary_configuration_enabled: true,
                    publish: 'republish',
                    metadata: {
                        visible_to_staff_only: null,
                        hide_from_toc: null
                    }
                });
            });
        });

        verifyTypePublishable('unit', function(options) {
            return createMockCourseJSON({}, [
                createMockSectionJSON({}, [
                    createMockSubsectionJSON({}, [
                        createMockVerticalJSON(options)
                    ])
                ])
            ]);
        });
    });

    describe('Date and Time picker', function() {
        // Two datetime formats can came from server: '%Y-%m-%dT%H:%M:%SZ' and %Y-%m-%dT%H:%M:%S+TZ:TZ'
        it('can parse dates in both formats that can come from server', function() {
            createCourseOutlinePage(this, mockCourseJSON, false);
            outlinePage.$('.outline-subsection .configure-button').click();
            expect($('#start_date').val()).toBe('');
            expect($('#start_time').val()).toBe('');
            DateUtils.setDate($('#start_date'), ('#start_time'), '2015-08-10T05:10:00Z');
            expect($('#start_date').val()).toBe('8/10/2015');
            expect($('#start_time').val()).toBe('05:10');
            DateUtils.setDate($('#start_date'), ('#start_time'), '2014-07-09T00:00:00+00:00');
            expect($('#start_date').val()).toBe('7/9/2014');
            expect($('#start_time').val()).toBe('00:00');
        });
    });
});
