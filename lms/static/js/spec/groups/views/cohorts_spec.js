/* globals _ */

define(['backbone', 'jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers',
    'js/groups/views/cohorts', 'js/groups/collections/cohort', 'js/groups/models/content_group',
    'js/groups/models/course_cohort_settings', 'js/utils/animation', 'js/vendor/jquery.qubit',
    'js/groups/views/course_cohort_settings_notification'
],
function(Backbone, $, sinon, AjaxHelpers, TemplateHelpers, CohortsView, CohortCollection, ContentGroupModel,
    CourseCohortSettingsModel, AnimationUtil, Qubit, CourseCohortSettingsNotificationView) {
    'use strict';

    describe('Cohorts View', function() {
        var catLoversInitialCount = 123,
            dogLoversInitialCount = 456,
            unknownUserMessage,
            notAllowedUserMessage,
            invalidEmailMessage, createMockCohort, createMockCohorts, createMockContentGroups,
            createMockCohortSettingsJson,
            createCohortsView, cohortsView, requests, xhrFactory, respondToRefresh, verifyMessage, verifyNoMessage,
            verifyDetailedMessage, verifyHeader,
            expectCohortAddRequest, getAddModal, selectContentGroup, clearContentGroup,
            saveFormAndExpectErrors, createMockCohortSettings, MOCK_COHORTED_USER_PARTITION_ID,
            MOCK_UPLOAD_COHORTS_CSV_URL, MOCK_STUDIO_ADVANCED_SETTINGS_URL, MOCK_STUDIO_GROUP_CONFIGURATIONS_URL,
            MOCK_MANUAL_ASSIGNMENT, MOCK_RANDOM_ASSIGNMENT;

        MOCK_MANUAL_ASSIGNMENT = 'manual';
        MOCK_RANDOM_ASSIGNMENT = 'random';
        MOCK_COHORTED_USER_PARTITION_ID = 0;
        MOCK_UPLOAD_COHORTS_CSV_URL = 'http://upload-csv-file-url/';
        MOCK_STUDIO_ADVANCED_SETTINGS_URL = 'http://studio/settings/advanced';
        MOCK_STUDIO_GROUP_CONFIGURATIONS_URL = 'http://studio/group_configurations';

        createMockCohort = function(name, id, userCount, groupId, userPartitionId, assignmentType) {
            return {
                id: id !== undefined ? id : 1,
                name: name,
                assignment_type: assignmentType || MOCK_MANUAL_ASSIGNMENT,
                user_count: userCount !== undefined ? userCount : 0,
                group_id: groupId,
                user_partition_id: userPartitionId
            };
        };

        createMockCohorts = function(catCount, dogCount) {
            return {
                cohorts: [
                    createMockCohort('Cat Lovers', 1, catCount || catLoversInitialCount),
                    createMockCohort('Dog Lovers', 2, dogCount || dogLoversInitialCount)
                ]
            };
        };

        createMockContentGroups = function() {
            return [
                new ContentGroupModel({
                    id: 0, name: 'Dog Content', user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                }),
                new ContentGroupModel({
                    id: 1, name: 'Cat Content', user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                })
            ];
        };

        createMockCohortSettingsJson = function(isCohorted) {
            return {
                id: 0,
                is_cohorted: isCohorted || false
            };
        };

        createMockCohortSettings = function(isCohorted) {
            return new CourseCohortSettingsModel(
                createMockCohortSettingsJson(isCohorted)
            );
        };

        createCohortsView = function(test, options) {
            var cohortsJson, cohorts, contentGroups, cohortSettings;
            options = options || {};
            cohortsJson = options.cohorts ? {cohorts: options.cohorts} : createMockCohorts();
            cohorts = new CohortCollection(cohortsJson, {parse: true});
            contentGroups = options.contentGroups || createMockContentGroups();
            cohortSettings = options.cohortSettings || createMockCohortSettings(true);
            cohortSettings.url = '/mock_service/cohorts/settings';
            cohorts.url = '/mock_service/cohorts';

            cohortsView = new CohortsView({
                model: cohorts,
                contentGroups: contentGroups,
                cohortSettings: cohortSettings,
                context: {
                    uploadCohortsCsvUrl: MOCK_UPLOAD_COHORTS_CSV_URL,
                    studioAdvancedSettingsUrl: MOCK_STUDIO_ADVANCED_SETTINGS_URL,
                    studioGroupConfigurationsUrl: MOCK_STUDIO_GROUP_CONFIGURATIONS_URL,
                }
            });

            cohortsView.render();
            if (options && options.selectCohort) {
                cohortsView.$('.cohort-select').val(options.selectCohort.toString()).change();
            }
        };

        respondToRefresh = function(catCount, dogCount) {
            AjaxHelpers.respondWithJson(requests, createMockCohorts(catCount, dogCount));
        };

        expectCohortAddRequest = function(name, groupId, userPartitionId, assignmentType) {
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', '/mock_service/cohorts',
                {
                    name: name,
                    user_count: 0,
                    assignment_type: assignmentType,
                    group_id: groupId,
                    user_partition_id: userPartitionId
                }
            );
        };

        getAddModal = function() {
            return cohortsView.$('.cohort-management-add-form');
        };

        selectContentGroup = function(groupId, userPartitionId) {
            var ids = groupId + ':' + userPartitionId;
            cohortsView.$('.radio-yes').prop('checked', true).change();
            cohortsView.$('.input-cohort-group-association').val(ids).change();
            expect(cohortsView.$('.input-cohort-group-association').prop('disabled')).toBeFalsy();
        };

        clearContentGroup = function() {
            cohortsView.$('.radio-no').prop('checked', true).change();
            expect(cohortsView.$('.input-cohort-group-association').prop('disabled')).toBeTruthy();
            expect(cohortsView.$('.input-cohort-group-association').val()).toBe(null);
        };

        verifyMessage = function(expectedTitle, expectedMessageType, expectedAction, hasDetails) {
            expect(cohortsView.$('.message-title').text().trim()).toBe(expectedTitle);
            expect(cohortsView.$('div.message')).toHaveClass('message-' + expectedMessageType);
            if (expectedAction) {
                expect(cohortsView.$('.message-actions .action-primary').text().trim()).toBe(expectedAction);
            } else {
                expect(cohortsView.$('.message-actions .action-primary').length).toBe(0);
            }
            if (!hasDetails) {
                expect(cohortsView.$('.summary-items').length).toBe(0);
            }
        };

        verifyNoMessage = function() {
            expect(cohortsView.$('.message').length).toBe(0);
        };

        verifyDetailedMessage = function(expectedTitle, expectedMessageType, expectedDetails, expectedAction) {
            var numDetails = cohortsView.$('.summary-items').children().length;
            verifyMessage(expectedTitle, expectedMessageType, expectedAction, true);
            expect(numDetails).toBe(expectedDetails.length);
            cohortsView.$('.summary-item').each(function(index) {
                expect($(this).text().trim()).toBe(expectedDetails[index]);
            });
        };

        verifyHeader = function(expectedCohortId, expectedTitle, expectedCount, assignmentType) {
            var header = cohortsView.$('.cohort-management-group-header');
            expect(cohortsView.$('.cohort-select').val()).toBe(expectedCohortId.toString());
            expect(cohortsView.$('.cohort-select option:selected').text()).toBe(
                interpolate_text(
                    '{title} ({count})', {title: expectedTitle, count: expectedCount}
                )
            );
            expect(header.find('.title-value').text()).toBe(expectedTitle);
            expect(header.find('.group-count').text()).toBe(
                interpolate_ntext(
                    '(contains {count} student)',
                    '(contains {count} students)',
                    expectedCount,
                    {count: expectedCount}
                )
            );
            assignmentType = assignmentType || MOCK_MANUAL_ASSIGNMENT;
            var manualMessage = 'Learners are added to this cohort only when you provide their email addresses '
                    + 'or usernames on this page.';
            var randomMessage = 'Learners are added to this cohort automatically.';
            var message = (assignmentType === MOCK_MANUAL_ASSIGNMENT) ? manualMessage : randomMessage;
            expect(header.find('.cohort-management-group-setup .setup-value').text().trim().split('\n')[0]).toBe(message);
        };

        saveFormAndExpectErrors = function(action, errors) {
            var form, expectedTitle;
            if (action === 'add') {
                expectedTitle = 'The cohort cannot be added';
                form = getAddModal();
            } else {
                expectedTitle = 'The cohort cannot be saved';
                form = cohortsView.$('.cohort-management-settings-form');
            }
            form.find('.action-save').click();
            AjaxHelpers.expectNoRequests(requests);
            verifyDetailedMessage(expectedTitle, 'error', errors);
        };

        unknownUserMessage = function(name) {
            return 'Unknown username: ' + name;
        };

        invalidEmailMessage = function(name) {
            return 'Invalid email address: ' + name;
        };

        notAllowedUserMessage = function(email) {
            return 'Cohort assignment not allowed: ' + email;
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<ul class="instructor-nav">'
                    + '<li class="nav-item"><button type="button" data-section="cohort_management" '
                    + 'class="active-section">Cohort Management</button></li></ul><div></div>'
                    + '<div class="cohort-management"><div class="cohort-state-message"></div></div>');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohorts');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohort-form');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohort-selector');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohort-editor');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohort-group-header');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/notification');
            TemplateHelpers.installTemplate('templates/instructor/instructor_dashboard_2/cohort-state');
            TemplateHelpers.installTemplate('templates/file-upload');
        });

        afterEach(function() {
            requests.restore();
        });

        it('shows an error if no cohorts are defined', function() {
            createCohortsView(this, {cohorts: []});
            verifyMessage(
                'You currently have no cohorts configured',
                'warning',
                'Add Cohort'
            );

            // If no cohorts have been created, can't upload a CSV file.
            expect(cohortsView.$('.wrapper-cohort-supplemental')).toHaveClass('hidden');
        });

        it('syncs data when membership tab is clicked', function() {
            createCohortsView(this, {selectCohort: 1});
            verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
            $(cohortsView.getSectionCss('cohort_management')).click();
            AjaxHelpers.expectRequest(requests, 'GET', '/mock_service/cohorts');
            respondToRefresh(1001, 2);
            verifyHeader(1, 'Cat Lovers', 1001);
        });

        it('can upload a CSV of cohort assignments if a cohort exists', function() {
            var uploadCsvToggle, fileUploadForm,
                fileUploadFormCss = '#file-upload-form';

            createCohortsView(this);

            // Should see the control to toggle CSV file upload.
            expect(cohortsView.$('.wrapper-cohort-supplemental')).not.toHaveClass('hidden');
            // But upload form should not be visible until toggle is clicked.
            expect(cohortsView.$(fileUploadFormCss).length).toBe(0);
            uploadCsvToggle = cohortsView.$('.toggle-cohort-management-secondary');
            expect(uploadCsvToggle.text())
                .toContain('Assign learners to cohorts by uploading a CSV file');
            uploadCsvToggle.click();
            // After toggle is clicked, it should be hidden.
            expect(uploadCsvToggle).toHaveClass('hidden');

            fileUploadForm = cohortsView.$(fileUploadFormCss);
            expect(fileUploadForm.length).toBe(1);
            cohortsView.$(fileUploadForm).fileupload('add', {files: [{name: 'upload_file.txt'}]});
            cohortsView.$('.submit-file-button').click();

            // No file will actually be uploaded because "uploaded_file.txt" doesn't actually exist.
            AjaxHelpers.expectRequest(requests, 'POST', MOCK_UPLOAD_COHORTS_CSV_URL, new FormData());
            AjaxHelpers.respondWithJson(requests, {});
            expect(cohortsView.$('.file-upload-form-result .message-confirmation .message-title').text().trim())
                .toBe("Your file 'upload_file.txt' has been uploaded. Allow a few minutes for processing.");
        });

        describe('Cohort Selector', function() {
            it('has no initial selection', function() {
                createCohortsView(this);
                expect(cohortsView.$('.cohort-select').val()).toBe('');
                expect(cohortsView.$('.cohort-management-group-header .title-value').text()).toBe('');
            });

            it('can select a cohort', function() {
                createCohortsView(this, {selectCohort: 1});
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
            });

            it('can switch cohort', function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.cohort-select').val('2').change();
                verifyHeader(2, 'Dog Lovers', dogLoversInitialCount);
            });
        });

        describe('Course Cohort Settings', function() {
            it('can enable and disable cohorting', function() {
                createCohortsView(this, {cohortSettings: createMockCohortSettings(false)});

                expect(cohortsView.$('.cohorts-state').prop('checked')).toBeFalsy();

                cohortsView.$('.cohorts-state').prop('checked', true).change();
                AjaxHelpers.expectJsonRequest(
                    requests, 'PATCH', '/mock_service/cohorts/settings',
                    {is_cohorted: true}
                );
                AjaxHelpers.respondWithJson(
                    requests,
                    {is_cohorted: true}
                );
                expect(cohortsView.$('.cohorts-state').prop('checked')).toBeTruthy();

                cohortsView.$('.cohorts-state').prop('checked', false).change();
                AjaxHelpers.expectJsonRequest(
                    requests, 'PATCH', '/mock_service/cohorts/settings',
                    {is_cohorted: false}
                );
                AjaxHelpers.respondWithJson(
                    requests,
                    {is_cohorted: false}
                );
                expect(cohortsView.$('.cohorts-state').prop('checked')).toBeFalsy();
            });

            it('shows an appropriate cohort status message', function() {
                var createCourseCohortSettingsNotificationView = function(is_cohorted) {
                    var notificationView = new CourseCohortSettingsNotificationView({
                        el: $('.cohort-state-message'),
                        cohortEnabled: is_cohorted
                    });
                    notificationView.render();
                    return notificationView;
                };

                var notificationView = createCourseCohortSettingsNotificationView(true);
                expect(notificationView.$('.action-toggle-message').text().trim()).toBe('Cohorts Enabled');

                notificationView = createCourseCohortSettingsNotificationView(false);
                expect(notificationView.$('.action-toggle-message').text().trim()).toBe('Cohorts Disabled');
            });

            it('shows an appropriate error message for HTTP500', function() {
                createCohortsView(this, {cohortSettings: createMockCohortSettings(false)});
                expect(cohortsView.$('.cohorts-state').prop('checked')).toBeFalsy();
                cohortsView.$('.cohorts-state').prop('checked', true).change();
                AjaxHelpers.respondWithError(requests, 500);
                var expectedTitle = "We've encountered an error. Refresh your browser and then try again.";
                expect(cohortsView.$('.message-title').text().trim()).toBe(expectedTitle);
            });
        });

        describe('Cohort Group Header', function() {
            it('renders header correctly', function() {
                var cohortName = 'Transformers',
                    newCohortName = 'X Men';
                var expectedRequest = function(assignment_type) {
                    return {
                        name: newCohortName,
                        assignment_type: assignment_type,
                        group_id: null,
                        user_partition_id: null
                    };
                };
                createCohortsView(this, {
                    cohorts: [
                        {
                            id: 1,
                            name: cohortName,
                            assignment_type: MOCK_RANDOM_ASSIGNMENT,
                            group_id: 111,
                            user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                        }
                    ],
                    selectCohort: 1
                });

                // Select settings tab
                cohortsView.$('.tab-settings a').click();

                verifyHeader(1, cohortName, 0, MOCK_RANDOM_ASSIGNMENT);

                // Update existing cohort values
                cohortsView.$('.cohort-name').val(newCohortName);
                cohortsView.$('.type-manual').prop('checked', true).change();
                clearContentGroup();

                // Save the updated settings
                cohortsView.$('.action-save').click();
                AjaxHelpers.expectJsonRequest(
                    requests, 'PATCH', '/mock_service/cohorts/1',
                    expectedRequest(MOCK_MANUAL_ASSIGNMENT)
                );
                AjaxHelpers.respondWithJson(
                    requests,
                    createMockCohort(newCohortName, 1, 0, null, null)
                );

                verifyHeader(1, newCohortName, 0, MOCK_MANUAL_ASSIGNMENT);
            });
        });

        describe('Cohort Editor Tab Panel', function() {
            it('initially selects the Manage Students tab', function() {
                createCohortsView(this, {selectCohort: 1});
                expect(cohortsView.$('.tab-manage_students')).toHaveClass('is-selected');
                expect(cohortsView.$('.tab-settings')).not.toHaveClass('is-selected');
                expect(cohortsView.$('.tab-content-manage_students')).not.toHaveClass('is-hidden');
                expect(cohortsView.$('.tab-content-settings')).toHaveClass('is-hidden');
            });

            it('can select the Settings tab', function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.tab-settings button').click();
                expect(cohortsView.$('.tab-manage_students')).not.toHaveClass('is-selected');
                expect(cohortsView.$('.tab-settings')).toHaveClass('is-selected');
                expect(cohortsView.$('.tab-content-manage_students')).toHaveClass('is-hidden');
                expect(cohortsView.$('.tab-content-settings')).not.toHaveClass('is-hidden');
            });
        });

        describe('Add Cohorts Form', function() {
            var defaultCohortName = 'New Cohort';
            var assignmentType = 'random';

            it('can add a cohort', function() {
                var contentGroupId = 0;
                createCohortsView(this, {cohorts: []});
                cohortsView.$('.action-create').click();
                expect(cohortsView.$('.cohort-management-settings-form').length).toBe(1);
                expect(cohortsView.$('.cohort-management-nav')).toHaveClass('is-disabled');
                expect(cohortsView.$('.cohort-management-group')).toHaveClass('hidden');
                cohortsView.$('.cohort-name').val(defaultCohortName);
                cohortsView.$('.type-random').prop('checked', true).change();
                selectContentGroup(contentGroupId, MOCK_COHORTED_USER_PARTITION_ID);
                cohortsView.$('.action-save').click();
                expectCohortAddRequest(defaultCohortName, contentGroupId, MOCK_COHORTED_USER_PARTITION_ID, assignmentType);
                AjaxHelpers.respondWithJson(
                    requests,
                    {
                        id: 1,
                        name: defaultCohortName,
                        assignment_type: assignmentType,
                        group_id: contentGroupId,
                        user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                    }
                );
                AjaxHelpers.respondWithJson(
                    requests,
                    {cohorts: createMockCohort(defaultCohortName, 1, 0, null, null, assignmentType)}
                );
                verifyMessage(
                    'The ' + defaultCohortName + ' cohort has been created.'
                            + ' You can manually add students to this cohort below.',
                    'confirmation'
                );
                verifyHeader(1, defaultCohortName, 0, MOCK_RANDOM_ASSIGNMENT);
                expect(cohortsView.$('.cohort-management-nav')).not.toHaveClass('is-disabled');
                expect(cohortsView.$('.cohort-management-group')).not.toHaveClass('hidden');
                expect(getAddModal().find('.cohort-management-settings-form').length).toBe(0);
            });

            it('has default assignment type set to manual', function() {
                var cohortName = 'iCohort';
                createCohortsView(this, {cohorts: []});
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val(cohortName);
                cohortsView.$('.action-save').click();
                expectCohortAddRequest(cohortName, null, null, MOCK_MANUAL_ASSIGNMENT);
                AjaxHelpers.respondWithJson(
                    requests,
                    {
                        id: 1,
                        name: cohortName,
                        assignment_type: MOCK_MANUAL_ASSIGNMENT,
                        group_id: null,
                        user_partition_id: null
                    }
                );
                AjaxHelpers.respondWithJson(
                    requests,
                    {cohorts: createMockCohort(cohortName, 1, 0, null, null, MOCK_MANUAL_ASSIGNMENT)}
                );
                verifyHeader(1, cohortName, 0, MOCK_MANUAL_ASSIGNMENT);
            });

            it('trims off whitespace before adding a cohort', function() {
                createCohortsView(this);
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('  New Cohort   ');
                cohortsView.$('.action-save').click();
                expectCohortAddRequest('New Cohort', null, null, MOCK_MANUAL_ASSIGNMENT);
            });

            it('does not allow a blank cohort name to be submitted', function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('  ');
                saveFormAndExpectErrors('add', ['You must specify a name for the cohort']);
            });

            it('shows a message saving when choosing to have content groups but not selecting one', function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('New Cohort');
                cohortsView.$('.radio-yes').prop('checked', true).change();
                saveFormAndExpectErrors('add', ['You did not select a content group']);
            });

            it('shows two message when both fields have problems', function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('');
                cohortsView.$('.radio-yes').prop('checked', true).change();
                saveFormAndExpectErrors('add', [
                    'You must specify a name for the cohort',
                    'You did not select a content group'
                ]);
            });

            it('shows a message when adding a cohort returns a server error', function() {
                var addModal;
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.action-create').click();
                addModal = getAddModal();
                expect(addModal.find('.cohort-management-settings-form').length).toBe(1);
                addModal.find('.cohort-name').val(defaultCohortName);
                addModal.find('.action-save').click();
                AjaxHelpers.respondWithError(requests, 400, {
                    error: 'You cannot add two cohorts with the same name'
                });
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
                verifyMessage(
                    'You cannot add two cohorts with the same name',
                    'error'
                );
            });

            it("is removed when 'Cancel' is clicked", function() {
                createCohortsView(this, {selectCohort: 1});
                cohortsView.$('.action-create').click();
                expect(getAddModal().find('.cohort-management-settings-form').length).toBe(1);
                expect(cohortsView.$('.cohort-management-nav')).toHaveClass('is-disabled');
                cohortsView.$('.action-cancel').click();
                expect(getAddModal().find('.cohort-management-settings-form').length).toBe(0);
                expect(cohortsView.$('.cohort-management-nav')).not.toHaveClass('is-disabled');
            });

            it('shows an error if canceled when no cohorts are defined', function() {
                createCohortsView(this, {cohorts: []});
                cohortsView.$('.action-create').click();
                expect(getAddModal().find('.cohort-management-settings-form').length).toBe(1);
                expect(cohortsView.$('.cohort-management-nav')).toHaveClass('is-disabled');
                cohortsView.$('.action-cancel').click();
                verifyMessage(
                    'You currently have no cohorts configured',
                    'warning',
                    'Add Cohort'
                );
            });

            it('hides any error message when switching to show a cohort', function() {
                createCohortsView(this, {selectCohort: 1});

                // First try to save a blank name to create a message
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('');
                saveFormAndExpectErrors('add', ['You must specify a name for the cohort']);

                // Now switch to a different cohort
                cohortsView.$('.cohort-select').val('2').change();
                verifyHeader(2, 'Dog Lovers', dogLoversInitialCount);
                verifyNoMessage();
            });

            it('hides any error message when canceling the form', function() {
                createCohortsView(this, {selectCohort: 1});

                // First try to save a blank name to create a message
                cohortsView.$('.action-create').click();
                cohortsView.$('.cohort-name').val('');
                saveFormAndExpectErrors('add', ['You must specify a name for the cohort']);

                // Now cancel the form
                cohortsView.$('.action-cancel').click();
                verifyNoMessage();
            });
        });

        describe('Add Students Button', function() {
            var getStudentInput, addStudents, respondToAdd;

            getStudentInput = function() {
                return cohortsView.$('.cohort-management-group-add-students');
            };

            addStudents = function(students) {
                getStudentInput().val(students);
                cohortsView.$('.cohort-management-group-add-form').submit();
            };

            respondToAdd = function(result) {
                AjaxHelpers.respondWithJson(
                    requests,
                    _.extend({
                        unknown: [],
                        added: [],
                        present: [],
                        changed: [],
                        not_allowed: [],
                        success: true,
                        preassigned: [],
                        invalid: []
                    }, result)
                );
            };

            it('shows an error when adding with no students specified', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('    ');
                AjaxHelpers.expectNoRequests(requests);
                verifyMessage('Enter a username or email.', 'error');
                expect(getStudentInput().val()).toBe('');
            });

            it('can add a single student', function() {
                var catLoversUpdatedCount = catLoversInitialCount + 1;
                createCohortsView(this, {selectCohort: 1});
                addStudents('student@sample.com');
                respondToAdd({added: ['student@sample.com']});
                respondToRefresh(catLoversUpdatedCount, dogLoversInitialCount);
                verifyHeader(1, 'Cat Lovers', catLoversUpdatedCount);
                verifyMessage('1 learner has been added to this cohort.', 'confirmation');
                expect(getStudentInput().val()).toBe('');
            });

            it('preassigns an email address if it is not associated with a user', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('unknown@sample.com');
                AjaxHelpers.expectRequest(
                    requests, 'POST', '/mock_service/cohorts/1/add', 'users=unknown%40sample.com'
                );
                respondToAdd({preassigned: ['unknown@sample.com']});
                respondToRefresh(catLoversInitialCount, dogLoversInitialCount);
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
                verifyDetailedMessage('1 learner was pre-assigned for this cohort. '
                        + 'This learner will automatically be added to the cohort when they enroll in the course.',
                'warning',
                ['unknown@sample.com']);
                expect(getStudentInput().val()).toBe('');
            });

            it('shows an error when adding an invalid email address', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('unknown@');
                AjaxHelpers.expectRequest(
                    requests, 'POST', '/mock_service/cohorts/1/add', 'users=unknown%40'
                );
                respondToAdd({invalid: ['unknown@']});
                respondToRefresh(catLoversInitialCount, dogLoversInitialCount);
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
                verifyDetailedMessage('There was an error when trying to add learners:', 'error',
                    [invalidEmailMessage('unknown@')]
                );
            });

            it('shows an error when adding an unknown user', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('unknown');
                AjaxHelpers.expectRequest(
                    requests, 'POST', '/mock_service/cohorts/1/add', 'users=unknown'
                );
                respondToAdd({unknown: ['unknown']});
                respondToRefresh(catLoversInitialCount, dogLoversInitialCount);
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
                verifyDetailedMessage('There was an error when trying to add learners:', 'error',
                    [unknownUserMessage('unknown')]
                );
            });

            it('shows an error when user assignment not allowed', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('not_allowed');
                AjaxHelpers.expectRequest(
                    requests, 'POST', '/mock_service/cohorts/1/add', 'users=not_allowed'
                );
                respondToAdd({not_allowed: ['not_allowed']});
                respondToRefresh(catLoversInitialCount, dogLoversInitialCount);
                verifyHeader(1, 'Cat Lovers', catLoversInitialCount);
                verifyDetailedMessage('There was an error when trying to add learners:', 'error',
                    [notAllowedUserMessage('not_allowed')]
                );
            });

            it('shows a "view all" button when more than 5 students do not exist', function() {
                var sixUsers = 'unknown1, unknown2, unknown3, unknown4, unknown5, unknown6';
                createCohortsView(this, {selectCohort: 1});

                addStudents(sixUsers);
                AjaxHelpers.expectRequest(
                    requests, 'POST', '/mock_service/cohorts/1/add',
                    'users=' + sixUsers.replace(/@/g, '%40').replace(/, /g, '%2C+')
                );
                respondToAdd({
                    unknown: [
                        'unknown1',
                        'unknown2',
                        'unknown3',
                        'unknown4',
                        'unknown5',
                        'unknown6']
                });
                respondToRefresh(catLoversInitialCount + 6, dogLoversInitialCount);
                verifyDetailedMessage('6 learners could not be added to this cohort:', 'error',
                    [
                        unknownUserMessage('unknown1'), unknownUserMessage('unknown2'),
                        unknownUserMessage('unknown3'), unknownUserMessage('unknown4'),
                        unknownUserMessage('unknown5')
                    ],
                    'View all errors'
                );
                expect(getStudentInput().val()).toBe(sixUsers);
                // Click "View all"
                cohortsView.$('.action-expand').click();
                verifyDetailedMessage('6 learners could not be added to this cohort:', 'error',
                    [
                        unknownUserMessage('unknown1'), unknownUserMessage('unknown2'),
                        unknownUserMessage('unknown3'), unknownUserMessage('unknown4'),
                        unknownUserMessage('unknown5'), unknownUserMessage('unknown6')
                    ]
                );
            });

            it('shows students moved from one cohort to another', function() {
                var sixUsers = 'moved1@sample.com, moved2@sample.com, moved3@sample.com, alreadypresent@sample.com';
                createCohortsView(this, {selectCohort: 1});

                addStudents(sixUsers);
                AjaxHelpers.expectRequest(requests, 'POST', '/mock_service/cohorts/1/add',
                    'users=' + sixUsers.replace(/@/g, '%40').replace(/, /g, '%2C+')
                );
                respondToAdd({
                    changed: [
                        {
                            email: 'moved1@sample.com', name: 'moved1', previous_cohort: 'cohort 2', username: 'moved1'
                        },
                        {
                            email: 'moved2@sample.com', name: 'moved2', previous_cohort: 'cohort 2', username: 'moved2'
                        },
                        {
                            email: 'moved3@sample.com', name: 'moved3', previous_cohort: 'cohort 3', username: 'moved3'
                        }
                    ],
                    present: ['alreadypresent@sample.com']
                });
                respondToRefresh();

                verifyDetailedMessage('3 learners have been added to this cohort.', 'confirmation',
                    [
                        '2 learners were moved from cohort 2',
                        '1 learner was moved from cohort 3',
                        '1 learner was already in the cohort'
                    ]
                );
                expect(getStudentInput().val()).toBe('');
            });

            it('shows a message when the add fails', function() {
                createCohortsView(this, {selectCohort: 1});
                addStudents('student@sample.com');
                AjaxHelpers.respondWithError(requests);
                verifyMessage('Error adding learners.', 'error');
                expect(getStudentInput().val()).toBe('student@sample.com');
            });

            it('clears an error message on subsequent add', function() {
                createCohortsView(this, {selectCohort: 1});

                // First verify that an error is shown
                addStudents('student@sample.com');
                AjaxHelpers.respondWithError(requests);
                verifyMessage('Error adding learners.', 'error');

                // Now verify that the error is removed on a subsequent add
                addStudents('student@sample.com');
                respondToAdd({added: ['student@sample.com']});
                respondToRefresh(catLoversInitialCount + 1, dogLoversInitialCount);
                verifyMessage('1 learner has been added to this cohort.', 'confirmation');
            });
        });

        describe('Cohort Settings', function() {
            describe('Content Group Setting', function() {
                var createCohortsViewWithDeletedContentGroup;

                createCohortsViewWithDeletedContentGroup = function(test) {
                    createCohortsView(test, {
                        cohorts: [
                            {
                                id: 1,
                                name: 'Cat Lovers',
                                assignment_type: MOCK_RANDOM_ASSIGNMENT,
                                group_id: 999,
                                user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                            }
                        ],
                        selectCohort: 1
                    });
                };

                it('shows a select element with an option for each content group', function() {
                    var options;
                    createCohortsView(this, {selectCohort: 1});
                    cohortsView.$('.tab-settings a').click();
                    expect(cohortsView.$('.input-cohort-group-association').prop('disabled')).toBeTruthy();
                    options = cohortsView.$('.input-cohort-group-association option');
                    expect(options.length).toBe(3);
                    expect($(options[0]).text().trim()).toBe('Not selected');
                    expect($(options[1]).text().trim()).toBe('Cat Content');
                    expect($(options[2]).text().trim()).toBe('Dog Content');
                });

                it('can select a single content group', function() {
                    createCohortsView(this, {selectCohort: 1});
                    cohortsView.$('.tab-settings a').click();

                    // Select the content group with id 1 and verify the radio button was switched to 'Yes'
                    selectContentGroup(0, MOCK_COHORTED_USER_PARTITION_ID);
                    expect(cohortsView.$('.radio-yes').prop('checked')).toBeTruthy();

                    // Click the save button and verify that the correct request is sent
                    cohortsView.$('.action-save').click();
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/cohorts/1',
                        {
                            name: 'Cat Lovers',
                            assignment_type: MOCK_MANUAL_ASSIGNMENT,
                            group_id: 0,
                            user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                        }
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockCohort('Cat Lovers', 1, catLoversInitialCount, 0, 0)
                    );
                    verifyMessage('Saved cohort', 'confirmation');
                });

                it('can clear selected content group', function() {
                    createCohortsView(this, {
                        cohorts: [
                            {
                                id: 1, name: 'Cat Lovers', group_id: 0, assignment_type: MOCK_MANUAL_ASSIGNMENT
                            }
                        ],
                        selectCohort: 1
                    });
                    cohortsView.$('.tab-settings a').click();
                    expect(cohortsView.$('.radio-yes').prop('checked')).toBeTruthy();
                    clearContentGroup();

                    // Click the save button and verify that the correct request is sent
                    cohortsView.$('.action-save').click();
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/cohorts/1',
                        {
                            name: 'Cat Lovers',
                            assignment_type: MOCK_MANUAL_ASSIGNMENT,
                            group_id: null,
                            user_partition_id: null
                        }
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockCohort('Cat Lovers', 1, catLoversInitialCount, 0, 0)
                    );
                    verifyMessage('Saved cohort', 'confirmation');
                });

                it('shows a message saving when choosing to have content groups but not selecting one', function() {
                    createCohortsView(this, {selectCohort: 1});
                    cohortsView.$('.tab-settings a').click();
                    cohortsView.$('.cohort-name').val('New Cohort');
                    cohortsView.$('.radio-yes').prop('checked', true).change();
                    saveFormAndExpectErrors('update', ['You did not select a content group']);
                });

                it('shows a message when the selected content group does not exist', function() {
                    createCohortsViewWithDeletedContentGroup(this);
                    cohortsView.$('.tab-settings a').click();
                    expect(cohortsView.$('option.option-unavailable').text().trim()).toBe('Deleted Content Group');
                    expect(cohortsView.$('.cohort-management-details-association-course .copy-error').text().trim()).toBe(
                        'Warning: The previously selected content group was deleted. Select another content group.'
                    );
                });

                it('can clear a selected content group which had been deleted', function() {
                    createCohortsViewWithDeletedContentGroup(this);
                    cohortsView.$('.tab-settings a').click();
                    expect(cohortsView.$('.radio-yes').prop('checked')).toBeTruthy();
                    clearContentGroup();

                    // Click the save button and verify that the correct request is sent
                    cohortsView.$('.action-save').click();
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockCohort('Cat Lovers', 1, catLoversInitialCount, 0, 0, MOCK_RANDOM_ASSIGNMENT)
                    );
                    verifyMessage('Saved cohort', 'confirmation');

                    // Verify that the deleted content group and associated message have been removed
                    expect(cohortsView.$('option.option-unavailable').text().trim()).toBe('');
                    expect(cohortsView.$('.cohort-management-details-association-course .copy-error').text().trim()).toBe('');
                });

                it('shows an error when saving with a deleted content group', function() {
                    createCohortsViewWithDeletedContentGroup(this);
                    cohortsView.$('.tab-settings a').click();
                    saveFormAndExpectErrors('save', ['The selected content group does not exist']);
                });

                it('shows an error when the save fails', function() {
                    createCohortsView(this, {selectCohort: 1});
                    cohortsView.$('.tab-settings a').click();
                    cohortsView.$('.action-save').click();
                    AjaxHelpers.respondWithError(requests);
                    verifyMessage(
                        'We\'ve encountered an error. Refresh your browser and then try again.',
                        'error'
                    );
                });

                it('shows an error message when no content groups are specified', function() {
                    var message;
                    createCohortsView(this, {selectCohort: 1, contentGroups: []});
                    cohortsView.$('.tab-settings a').click();
                    expect(cohortsView.$('.radio-yes').prop('disabled')).toBeTruthy();
                    message = cohortsView.$('.msg-inline').text().trim();
                    expect(message).toContain('Warning: No content groups exist.');
                    expect(message).toContain('Create a content group');
                    expect(
                        cohortsView.$('.msg-inline a').attr('href'),
                        MOCK_STUDIO_GROUP_CONFIGURATIONS_URL
                    );
                });

                it('can update existing cohort settings', function() {
                    var cohortName = 'Transformers',
                        newCohortName = 'X Men';
                    var expectedRequest = function(assignment_type) {
                        return {
                            name: newCohortName,
                            assignment_type: assignment_type,
                            group_id: null,
                            user_partition_id: null
                        };
                    };
                    createCohortsView(this, {
                        cohorts: [
                            {
                                id: 1,
                                name: cohortName,
                                assignment_type: MOCK_RANDOM_ASSIGNMENT,
                                group_id: 111,
                                user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                            }
                        ],
                        selectCohort: 1
                    });

                    // Select settings tab
                    cohortsView.$('.tab-settings a').click();

                    // Verify the existing cohort values
                    expect(cohortsView.$('.cohort-name').val()).toBe(cohortName);
                    expect(cohortsView.$('input[name="cohort-assignment-type"]:checked').val()).toBe(MOCK_RANDOM_ASSIGNMENT);
                    expect(cohortsView.$('.radio-yes').prop('checked')).toBeTruthy();

                    // Update existing cohort values
                    cohortsView.$('.cohort-name').val(newCohortName);
                    cohortsView.$('.type-manual').prop('checked', true).change();
                    clearContentGroup();

                    // Save the updated settings
                    cohortsView.$('.action-save').click();
                    AjaxHelpers.expectJsonRequest(
                        requests, 'PATCH', '/mock_service/cohorts/1',
                        expectedRequest(MOCK_MANUAL_ASSIGNMENT)
                    );
                    AjaxHelpers.respondWithJson(
                        requests,
                        createMockCohort(newCohortName, 1, 0, null, null)
                    );

                    // Verify the new/updated cohort values
                    expect(cohortsView.$('.cohort-name').val()).toBe(newCohortName);
                    expect(cohortsView.$('input[name="cohort-assignment-type"]:checked').val()).toBe(MOCK_MANUAL_ASSIGNMENT);
                    expect(cohortsView.$('.radio-no').prop('checked')).toBeTruthy();

                    verifyMessage('Saved cohort', 'confirmation');

                    // Now try to update existing cohort name with an empty name
                    // We can't save a cohort with empty name, so we should see an error message
                    cohortsView.$('.cohort-name').val('');

                    saveFormAndExpectErrors('update', ['You must specify a name for the cohort']);
                });

                it('assignment settings are disabled for default cohort', function() {
                    createCohortsView(this, {
                        cohorts: [
                            {
                                id: 1,
                                name: 'Cohort.me',
                                assignment_type: MOCK_RANDOM_ASSIGNMENT,
                                group_id: 111,
                                user_partition_id: MOCK_COHORTED_USER_PARTITION_ID
                            }
                        ],
                        selectCohort: 1
                    });

                    // We have a single random cohort so we should not be allowed to change it assignment type
                    expect(cohortsView.$('.cohort-management-assignment-type-settings')).toHaveClass('is-disabled');
                    expect(cohortsView.$('.copy-error').text()).toContain('There must be one cohort to which students can automatically be assigned.');
                });

                it('cancel settings works', function() {
                    createCohortsView(this, {selectCohort: 1, contentGroups: []});
                    cohortsView.$('.tab-settings a').click();
                    cohortsView.$('.cohort-name').val('One Two Three');
                    cohortsView.$('.action-cancel').click();
                    expect(cohortsView.$('.tab-manage_students')).toHaveClass('is-selected');
                    expect(cohortsView.$('.tab-settings')).not.toHaveClass('is-selected');
                });
            });
        });
    });
});
