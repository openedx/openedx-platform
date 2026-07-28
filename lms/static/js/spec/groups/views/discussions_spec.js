define(['backbone', 'jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers',
    'js/discussions_management/views/discussions', 'js/discussions_management/models/course_discussions_detail',
    'js/discussions_management/models/course_discussions_settings'
],
function(Backbone, $, sinon, AjaxHelpers, TemplateHelpers, DiscussionsView, CourseDiscussionTopicDetailsModel,
    CourseDiscussionsSettingsModel) {
    'use strict';

    describe('Discussions View', function() {
        var createMockDiscussionsSettingsJson, createDiscussionsView, discussionsView, requests, xhrFactory, verifyMessage,
            createMockDiscussionsSettings, createMockDiscussionsJson, createMockDiscussions,
            showAndAssertDiscussionTopics;

        // Selectors
        var inlineDiscussionsFormCss = '.cohort-inline-discussions-form',
            courseWideDiscussionsFormCss = '.cohort-course-wide-discussions-form',
            courseWideDiscussionsSaveButtonCss = '.cohort-course-wide-discussions-form .action-save',
            inlineDiscussionsSaveButtonCss = '.cohort-inline-discussions-form .action-save';

        createMockDiscussionsSettingsJson = function(dividedInlineDiscussions,
            dividedCourseWideDiscussions,
            alwaysDivideInlineDiscussions,
            availableDivisionSchemes) {
            return {
                id: 0,
                divided_inline_discussions: dividedInlineDiscussions || [],
                divided_course_wide_discussions: dividedCourseWideDiscussions || [],
                always_divide_inline_discussions: alwaysDivideInlineDiscussions || false,
                available_division_schemes: availableDivisionSchemes || ['cohort']
            };
        };

        createMockDiscussionsSettings = function(dividedInlineDiscussions,
            dividedCourseWideDiscussions,
            alwaysDivideInlineDiscussions,
            availableDivisionSchemes) {
            return new CourseDiscussionsSettingsModel(
                createMockDiscussionsSettingsJson(dividedInlineDiscussions,
                    dividedCourseWideDiscussions,
                    alwaysDivideInlineDiscussions,
                    availableDivisionSchemes)
            );
        };

        createMockDiscussionsJson = function(allDivided) {
            return {
                course_wide_discussions: {
                    children: [['Topic_C_1', 'entry'], ['Topic_C_2', 'entry']],
                    entries: {
                        Topic_C_1: {
                            sort_key: null,
                            is_divided: true,
                            id: 'Topic_C_1'
                        },
                        Topic_C_2: {
                            sort_key: null,
                            is_divided: false,
                            id: 'Topic_C_2'
                        }
                    }
                },
                inline_discussions: {
                    subcategories: {
                        Topic_I_1: {
                            subcategories: {},
                            children: [['Inline_Discussion_1', 'entry'], ['Inline_Discussion_2', 'entry']],
                            entries: {
                                Inline_Discussion_1: {
                                    sort_key: null,
                                    is_divided: true,
                                    id: 'Inline_Discussion_1'
                                },
                                Inline_Discussion_2: {
                                    sort_key: null,
                                    is_divided: allDivided || false,
                                    id: 'Inline_Discussion_2'
                                }
                            }
                        }
                    },
                    children: [['Topic_I_1', 'subcategory']]
                }
            };
        };

        createMockDiscussions = function(allDivided) {
            return new CourseDiscussionTopicDetailsModel(
                createMockDiscussionsJson(allDivided)
            );
        };

        verifyMessage = function(expectedTitle, expectedMessageType, expectedAction, hasDetails) {
            expect(discussionsView.$('.message-title').text().trim()).toBe(expectedTitle);
            expect(discussionsView.$('div.message')).toHaveClass('message-' + expectedMessageType);
            if (expectedAction) {
                expect(discussionsView.$('.message-actions .action-primary').text().trim()).toBe(expectedAction);
            } else {
                expect(discussionsView.$('.message-actions .action-primary').length).toBe(0);
            } if (!hasDetails) {
                expect(discussionsView.$('.summary-items').length).toBe(0);
            }
        };

        createDiscussionsView = function(test, options) {
            var discussionSettings, dividedDiscussions, discussionOptions;
            discussionOptions = options || {};
            discussionSettings = discussionOptions.cohortSettings || createMockDiscussionsSettings();
            discussionSettings.url = '/mock_service/discussions/settings';

            dividedDiscussions = discussionOptions.dividedDiscussions || createMockDiscussions();
            dividedDiscussions.url = '/mock_service/discussion/topics';

            discussionsView = new DiscussionsView({
                el: $('.discussions-management'),
                discussionSettings: discussionSettings,
                context: {
                    courseDiscussionTopicDetailsModel: dividedDiscussions
                }
            });
            discussionsView.render();
        };

        showAndAssertDiscussionTopics = function() {
            var $courseWideDiscussionsForm,
                $inlineDiscussionsForm;

            $courseWideDiscussionsForm = discussionsView.$(courseWideDiscussionsFormCss);
            $inlineDiscussionsForm = discussionsView.$(inlineDiscussionsFormCss);

            // Discussions form should not be visible.
            expect($inlineDiscussionsForm.length).toBe(1);
            expect($courseWideDiscussionsForm.length).toBe(1);

            expect($courseWideDiscussionsForm.text())
                .toContain('Course-Wide Discussion Topics');
            expect($courseWideDiscussionsForm.text())
                .toContain('Select the course-wide discussion topics that you want to divide.');

            // Should see the inline discussions form and its content
            expect($inlineDiscussionsForm.length).toBe(1);
            expect($inlineDiscussionsForm.text())
                .toContain('Content-Specific Discussion Topics');
            expect($inlineDiscussionsForm.text())
                .toContain('Specify whether content-specific discussion topics are divided.');
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<ul class="instructor-nav">'
                    + '<li class="nav-item"><button type="button" data-section="discussion_management" '
                    + 'class="active-section">Discussions</button></li></ul><div></div>'
                    + '<div class="discussions-management"></div>');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/discussions');
            TemplateHelpers.installTemplate(
                'templates/instructor/instructor_dashboard_2/divided-discussions-course-wide'
            );
            TemplateHelpers.installTemplate(
                'templates/instructor/instructor_dashboard_2/divided-discussions-inline'
            );
            TemplateHelpers.installTemplate(
                'templates/instructor/instructor_dashboard_2/cohort-discussions-category'
            );
            TemplateHelpers.installTemplate(
                'templates/instructor/instructor_dashboard_2/cohort-discussions-subcategory'
            );
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/notification');
        });

        afterEach(function() {
            requests.restore();
        });

        describe('Discussion Topics', function() {
            var courseWideView, assertDividedTopics;

            assertDividedTopics = function(view, type) {
                expect($('.check-discussion-subcategory-' + type).length).toBe(2);
                expect($('.check-discussion-subcategory-' + type + ':checked').length).toBe(1);
            };

            it('renders the view properly', function() {
                createDiscussionsView(this);
                showAndAssertDiscussionTopics(this);
            });

            describe('Course Wide', function() {
                it('shows the "Save" button as disabled initially', function() {
                    createDiscussionsView(this);
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeTruthy();
                });

                it('has one divided and one non-divided topic', function() {
                    createDiscussionsView(this);
                    assertDividedTopics(courseWideView, 'course-wide');

                    expect($('.course-wide-discussion-topics .divided-discussion-text').length).toBe(2);
                    expect($('.course-wide-discussion-topics .divided-discussion-text.hidden').length).toBe(1);
                });

                it('enables the "Save" button after changing checkbox', function() {
                    createDiscussionsView(this);
                    // save button is disabled.
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeTruthy();

                    $($('.check-discussion-subcategory-course-wide')[0]).prop('checked', false).change();

                    // save button is enabled.
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeFalsy();
                });

                it('saves the topic successfully', function() {
                    createDiscussionsView(this);
                    $($('.check-discussion-subcategory-course-wide')[1]).prop('checked', 'checked').change();
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeFalsy();

                    // Save the updated settings
                    $('#cohort-course-wide-discussions-form .action-save').click();

                    // fake requests for discussions settings with PATCH method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/discussions/settings',
                        {divided_course_wide_discussions: ['Topic_C_1', 'Topic_C_2']}
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        {divided_course_wide_discussions: ['Topic_C_1', 'Topic_C_2']}
                    );

                    // fake request for discussion/topics with GET method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'GET', '/mock_service/discussion/topics'
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockDiscussions()
                    );

                    // verify the success message.
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeTruthy();
                    verifyMessage('Your changes have been saved.', 'confirmation');
                });

                it('shows an appropriate message when subsequent "GET" returns HTTP500', function() {
                    var expectedTitle;
                    createDiscussionsView(this);
                    $($('.check-discussion-subcategory-course-wide')[1]).prop('checked', 'checked').change();
                    expect($(courseWideDiscussionsSaveButtonCss).prop('disabled')).toBeFalsy();

                    // Save the updated settings
                    $('#cohort-course-wide-discussions-form .action-save').click();

                    // fake requests for discussion settings with PATCH method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/discussions/settings',
                        {divided_course_wide_discussions: ['Topic_C_1', 'Topic_C_2']}
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        {divided_course_wide_discussions: ['Topic_C_1', 'Topic_C_2']}
                    );

                    // fake request for discussion/topics with GET method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'GET', '/mock_service/discussion/topics'
                    );
                    AjaxHelpers.respondWithError(requests, 500);

                    expectedTitle = "We've encountered an error. Refresh your browser and then try again.";
                    expect($('.message-title').text().trim()).toBe(expectedTitle);
                });

                it('shows an appropriate error message for HTTP500', function() {
                    var expectedTitle;
                    createDiscussionsView(this);
                    $($('.check-discussion-subcategory-course-wide')[1]).prop('checked', 'checked').change();
                    $('.action-save').click();

                    AjaxHelpers.respondWithError(requests, 500);
                    expectedTitle = "We've encountered an error. Refresh your browser and then try again.";
                    expect($('.message-title').text().trim()).toBe(expectedTitle);
                });
            });

            describe('Inline', function() {
                var enableSaveButton, mockGetRequest, verifySuccess, mockPatchRequest;

                enableSaveButton = function() {
                    // enable the inline discussion topics.
                    $('.check-cohort-inline-discussions').prop('checked', 'checked').change();

                    $($('.check-discussion-subcategory-inline')[0]).prop('checked', 'checked').change();

                    expect($(inlineDiscussionsSaveButtonCss).prop('disabled')).toBeFalsy();
                };

                verifySuccess = function() {
                    // verify the success message.
                    expect($(inlineDiscussionsSaveButtonCss).prop('disabled')).toBeTruthy();
                    verifyMessage('Your changes have been saved.', 'confirmation');
                };

                mockPatchRequest = function(dividededInlineDiscussions) {
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/discussions/settings',
                        {
                            divided_inline_discussions: dividededInlineDiscussions,
                            always_divide_inline_discussions: false
                        }
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        {
                            divided_inline_discussions: dividededInlineDiscussions,
                            always_divide_inline_discussions: false
                        }
                    );
                };

                mockGetRequest = function(alldivided) {
                    // fake request for discussion/topics with GET method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'GET', '/mock_service/discussion/topics'
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockDiscussions(alldivided)
                    );
                };

                it('shows the "Save" button as disabled initially', function() {
                    createDiscussionsView(this);
                    expect($(inlineDiscussionsSaveButtonCss).prop('disabled')).toBeTruthy();
                });

                it('shows always divide radio button as selected', function() {
                    createDiscussionsView(this);
                    $('.check-all-inline-discussions').prop('checked', 'checked').change();

                    // verify always divide inline discussions is being selected.
                    expect($('.check-all-inline-discussions').prop('checked')).toBeTruthy();

                    // verify that inline topics are disabled
                    expect($('.check-discussion-subcategory-inline').prop('disabled')).toBeTruthy();
                    expect($('.check-discussion-category').prop('disabled')).toBeTruthy();

                    // verify that divide some topics are not being selected.
                    expect($('.check-cohort-inline-discussions').prop('checked')).toBeFalsy();
                });

                it('shows divide some topics radio button as selected', function() {
                    createDiscussionsView(this);
                    $('.check-cohort-inline-discussions').prop('checked', 'checked').change();

                    // verify some divide inline discussions radio is being selected.
                    expect($('.check-cohort-inline-discussions').prop('checked')).toBeTruthy();

                    // verify always divide radio is not selected.
                    expect($('.check-all-inline-discussions').prop('checked')).toBeFalsy();

                    // verify that inline topics are enabled
                    expect($('.check-discussion-subcategory-inline').prop('disabled')).toBeFalsy();
                    expect($('.check-discussion-category').prop('disabled')).toBeFalsy();
                });

                it('has divided and non-divided topics', function() {
                    createDiscussionsView(this);
                    enableSaveButton();
                    assertDividedTopics(this, 'inline');
                });

                it('enables "Save" button after changing from always inline option', function() {
                    createDiscussionsView(this);
                    enableSaveButton();
                });

                it('saves the topic', function() {
                    createDiscussionsView(this);
                    enableSaveButton();

                    // Save the updated settings
                    $('.action-save').click();

                    mockPatchRequest(['Inline_Discussion_1']);
                    mockGetRequest();

                    verifySuccess();
                });

                it('selects the parent category when all children are selected', function() {
                    createDiscussionsView(this);
                    enableSaveButton();

                    // parent category should be indeterminate.
                    expect($('.check-discussion-category:checked').length).toBe(0);
                    expect($('.check-discussion-category:indeterminate').length).toBe(1);

                    $('.check-discussion-subcategory-inline').prop('checked', 'checked').change();
                    // parent should be checked as we checked all children
                    expect($('.check-discussion-category:checked').length).toBe(1);
                });

                it('selects/deselects all children when a parent category is selected/deselected', function() {
                    createDiscussionsView(this);
                    enableSaveButton();

                    expect($('.check-discussion-category:checked').length).toBe(0);

                    $('.check-discussion-category').prop('checked', 'checked').change();

                    expect($('.check-discussion-category:checked').length).toBe(1);
                    expect($('.check-discussion-subcategory-inline:checked').length).toBe(2);

                    // un-check the parent, all children should be unchecd.
                    $('.check-discussion-category').prop('checked', false).change();
                    expect($('.check-discussion-category:checked').length).toBe(0);
                    expect($('.check-discussion-subcategory-inline:checked').length).toBe(0);
                });

                it('saves correctly when a subset of topics are selected within a category', function() {
                    createDiscussionsView(this);
                    enableSaveButton();

                    // parent category should be indeterminate.
                    expect($('.check-discussion-category:checked').length).toBe(0);
                    expect($('.check-discussion-category:indeterminate').length).toBe(1);

                    // Save the updated settings
                    $('#cohort-inline-discussions-form .action-save').click();

                    mockPatchRequest(['Inline_Discussion_1']);
                    mockGetRequest();

                    verifySuccess();
                    // parent category should be indeterminate.
                    expect($('.check-discussion-category:indeterminate').length).toBe(1);
                });

                it('saves correctly when all child topics are selected within a category', function() {
                    createDiscussionsView(this);
                    enableSaveButton();

                    // parent category should be indeterminate.
                    expect($('.check-discussion-category:checked').length).toBe(0);
                    expect($('.check-discussion-category:indeterminate').length).toBe(1);

                    $('.check-discussion-subcategory-inline').prop('checked', 'checked').change();
                    // Save the updated settings
                    $('#cohort-inline-discussions-form .action-save').click();

                    mockPatchRequest(['Inline_Discussion_1', 'Inline_Discussion_2']);
                    mockGetRequest(true);

                    verifySuccess();
                    // parent category should be checked.
                    expect($('.check-discussion-category:checked').length).toBe(1);
                });

                it('shows an appropriate message when no inline topics exist', function() {
                    var topicsJson, options, expectedTitle;

                    topicsJson = {
                        course_wide_discussions: {
                            children: [['Topic_C_1', 'entry']],
                            entries: {
                                Topic_C_1: {
                                    sort_key: null,
                                    is_divided: true,
                                    id: 'Topic_C_1'
                                }
                            }
                        },
                        inline_discussions: {
                            subcategories: {},
                            children: []
                        }
                    };
                    options = {dividedDiscussions: new CourseDiscussionTopicDetailsModel(topicsJson)};
                    createDiscussionsView(this, options);

                    expectedTitle = 'No content-specific discussion topics exist.';
                    expect($('.no-topics').text().trim()).toBe(expectedTitle);
                });

                it('shows an appropriate message when subsequent "GET" returns HTTP500', function() {
                    var expectedTitle;
                    createDiscussionsView(this);
                    enableSaveButton();

                    // Save the updated settings
                    $('#cohort-inline-discussions-form .action-save').click();

                    mockPatchRequest(['Inline_Discussion_1']);

                    // fake request for discussion/topics with GET method.
                    AjaxHelpers.expectJsonRequest(
                        requests, 'GET', '/mock_service/discussion/topics'
                    );
                    AjaxHelpers.respondWithError(requests, 500);

                    expectedTitle = "We've encountered an error. Refresh your browser and then try again.";
                    expect($('.message-title').text().trim()).toBe(expectedTitle);
                });

                it('shows an appropriate error message for HTTP500', function() {
                    var expectedTitle;
                    createDiscussionsView(this);
                    enableSaveButton();

                    $($('.check-discussion-subcategory-inline')[1]).prop('checked', 'checked').change();
                    $('#cohort-inline-discussions-form .action-save').click();

                    AjaxHelpers.respondWithError(requests, 500);
                    expectedTitle = "We've encountered an error. Refresh your browser and then try again.";
                    expect($('.message-title').text().trim()).toBe(expectedTitle);
                });
            });
        });
    });
});
