'use strict';
'use strict';

import $ from 'jquery';
import _ from 'underscore';
import str from 'underscore.string';
import sinon from 'sinon';
import AjaxHelpers from 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers';
import TemplateHelpers from 'common/js/spec_helpers/template_helpers';
import EditHelpers from 'js/spec_helpers/edit_helpers';
import ContainerPage from 'js/views/pages/container';
import PagedContainerPage from 'js/views/pages/paged_container';
import XBlockInfo from 'js/models/xblock_info';
import ComponentTemplates from 'js/collections/component_template';
import Course from 'js/models/course';
import 'jquery.simulate';

function parameterized_suite(label, globalPageOptions) {
    describe(label + ' ContainerPage', function() {
        var getContainerPage, renderContainerPage, handleContainerPageRefresh, expectComponents,
            respondWithHtml, model, containerPage, requests, xhrFactory, initialDisplayName,
            mockContainerPage = readFixtures('templates/mock/mock-container-page.underscore'),
            mockContainerXBlockHtml = readFixtures(globalPageOptions.initial),
            mockXBlockHtml = readFixtures(globalPageOptions.addResponse),
            mockBadContainerXBlockHtml = readFixtures('templates/mock/mock-bad-javascript-container-xblock.underscore'),
            mockBadXBlockContainerXBlockHtml = readFixtures('templates/mock/mock-bad-xblock-container-xblock.underscore'),
            mockUpdatedContainerXBlockHtml = readFixtures('templates/mock/mock-updated-container-xblock.underscore'),
            mockXBlockEditorHtml = readFixtures('templates/mock/mock-xblock-editor.underscore'),
            mockXBlockVisibilityEditorHtml = readFixtures('templates/mock/mock-xblock-visibility-editor.underscore'),
            PageClass = globalPageOptions.page,
            pagedSpecificTests = globalPageOptions.pagedSpecificTests,
            hasVisibilityEditor = globalPageOptions.hasVisibilityEditor,
            hasMoveModal = globalPageOptions.hasMoveModal;

        beforeEach(function() {
            var newDisplayName = 'New Display Name';

            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            EditHelpers.installEditTemplates();
            TemplateHelpers.installTemplate('xblock-string-field-editor');
            TemplateHelpers.installTemplate('container-message');
            appendSetFixtures(mockContainerPage);

            EditHelpers.installMockXBlock({
                data: '<p>Some HTML</p>',
                metadata: {
                    display_name: newDisplayName
                }
            });

            initialDisplayName = 'Test Container';

            model = new XBlockInfo({
                id: 'locator-container',
                display_name: initialDisplayName,
                category: 'vertical'
            });
            window.course = new Course({
                id: '5',
                name: 'Course Name',
                url_name: 'course_name',
                org: 'course_org',
                num: 'course_num',
                revision: 'course_rev'
            });
        });

        afterEach(function() {
            requests.restore();
            EditHelpers.uninstallMockXBlock();
            if (containerPage !== undefined) {
                containerPage.remove();
            }
            delete window.course;
        });

        respondWithHtml = function(html) {
            AjaxHelpers.respondWithJson(
                requests,
                {html: html, resources: []}
            );
        };

        getContainerPage = function(options, componentTemplates) {
            var default_options = {
                model: model,
                templates: componentTemplates === undefined
                    ? EditHelpers.mockComponentTemplates : componentTemplates,
                el: $('#content')
            };
            return new PageClass(_.extend(options || {}, globalPageOptions, default_options));
        };

        renderContainerPage = function(test, html, options, componentTemplates) {
            containerPage = getContainerPage(options, componentTemplates);
            containerPage.render();
            respondWithHtml(html);
            AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/locator-container');
            AjaxHelpers.respondWithJson(requests, options || {});
        };

        // eslint-disable-next-line no-shadow
        handleContainerPageRefresh = function(requests) {
            var request = AjaxHelpers.currentRequest(requests);
            expect(str.startsWith(request.url,
                '/xblock/locator-container/container_preview')).toBeTruthy();
            AjaxHelpers.respondWithJson(requests, {
                html: mockUpdatedContainerXBlockHtml,
                resources: []
            });
        };

        expectComponents = function(container, locators) {
            // verify expected components (in expected order) by their locators
            var components = $(container).find('.studio-xblock-wrapper');
            expect(components.length).toBe(locators.length);
            _.each(locators, function(locator, locator_index) {
                expect($(components[locator_index]).data('locator')).toBe(locator);
            });
        };

        describe('Initial display', function() {
            it('can render itself', function() {
                renderContainerPage(this, mockContainerXBlockHtml);
                expect(containerPage.$('.xblock-header').length).toBe(9);
                expect(containerPage.$('.wrapper-xblock .level-nesting')).not.toHaveClass('is-hidden');
            });

            it('shows a loading indicator', function() {
                containerPage = getContainerPage();
                containerPage.render();
                expect(containerPage.$('.ui-loading')).not.toHaveClass('is-hidden');
                respondWithHtml(mockContainerXBlockHtml);
                expect(containerPage.$('.ui-loading')).toHaveClass('is-hidden');
            });

            it('can show an xblock with broken JavaScript', function() {
                renderContainerPage(this, mockBadContainerXBlockHtml);
                expect(containerPage.$('.wrapper-xblock .level-nesting')).not.toHaveClass('is-hidden');
                expect(containerPage.$('.ui-loading')).toHaveClass('is-hidden');
            });

            it('can show an xblock with an invalid XBlock', function() {
                renderContainerPage(this, mockBadXBlockContainerXBlockHtml);
                expect(containerPage.$('.wrapper-xblock .level-nesting')).not.toHaveClass('is-hidden');
                expect(containerPage.$('.ui-loading')).toHaveClass('is-hidden');
            });

            it('inline edits the display name when performing a new action', function() {
                renderContainerPage(this, mockContainerXBlockHtml, {
                    action: 'new'
                });
                expect(containerPage.$('.xblock-header').length).toBe(9);
                expect(containerPage.$('.wrapper-xblock .level-nesting')).not.toHaveClass('is-hidden');
                expect(containerPage.$('.xblock-field-input')).not.toHaveClass('is-hidden');
            });
        });

        describe('Editing the container', function() {
            var updatedDisplayName = 'Updated Test Container',
                getDisplayNameWrapper;

            afterEach(function() {
                EditHelpers.cancelModalIfShowing();
            });

            getDisplayNameWrapper = function() {
                return containerPage.$('.wrapper-xblock-field');
            };

            it('can edit itself', function() {
                var editButtons, displayNameElement, request;
                renderContainerPage(this, mockContainerXBlockHtml);
                displayNameElement = containerPage.$('.page-header-title');

                // Click the root edit button
                editButtons = containerPage.$('.nav-actions .edit-button');
                editButtons.first().click();

                // Expect a request to be made to show the studio view for the container
                request = AjaxHelpers.currentRequest(requests);
                expect(str.startsWith(request.url, '/xblock/locator-container/studio_view')).toBeTruthy();
                AjaxHelpers.respondWithJson(requests, {
                    html: mockContainerXBlockHtml,
                    resources: []
                });
                expect(EditHelpers.isShowingModal()).toBeTruthy();

                // Expect the correct title to be shown
                expect(EditHelpers.getModalTitle()).toBe('Editing: Test Container');

                // Press the save button and respond with a success message to the save
                EditHelpers.pressModalButton('.action-save');
                AjaxHelpers.respondWithJson(requests, { });
                expect(EditHelpers.isShowingModal()).toBeFalsy();

                // Expect the last request be to refresh the container page
                handleContainerPageRefresh(requests);

                // Respond to the subsequent xblock info fetch request.
                AjaxHelpers.respondWithJson(requests, {display_name: updatedDisplayName});

                // Expect the title to have been updated
                expect(displayNameElement.text().trim()).toBe(updatedDisplayName);
            });

            it('can inline edit the display name', function() {
                var displayNameInput, displayNameWrapper;
                renderContainerPage(this, mockContainerXBlockHtml);
                displayNameWrapper = getDisplayNameWrapper();
                displayNameInput = EditHelpers.inlineEdit(displayNameWrapper, updatedDisplayName);
                displayNameInput.change();
                // This is the response for the change operation.
                AjaxHelpers.respondWithJson(requests, { });
                // This is the response for the subsequent fetch operation.
                AjaxHelpers.respondWithJson(requests, {display_name: updatedDisplayName});
                EditHelpers.verifyInlineEditChange(displayNameWrapper, updatedDisplayName);
                expect(containerPage.model.get('display_name')).toBe(updatedDisplayName);
            });
        });

        describe('Editing an xblock', function() {
            afterEach(function() {
                EditHelpers.cancelModalIfShowing();
            });

            it('can show an edit modal for a child xblock', function() {
                var editButtons, request;
                renderContainerPage(this, mockContainerXBlockHtml);
                editButtons = containerPage.$('.wrapper-xblock .edit-button');
                // The container should have rendered six mock xblocks
                expect(editButtons.length).toBe(6);
                editButtons[0].click();
                // Make sure that the correct xblock is requested to be edited
                request = AjaxHelpers.currentRequest(requests);
                expect(str.startsWith(request.url, '/xblock/locator-component-A1/studio_view')).toBeTruthy();
                AjaxHelpers.respondWithJson(requests, {
                    html: mockXBlockEditorHtml,
                    resources: []
                });
                expect(EditHelpers.isShowingModal()).toBeTruthy();
            });

            it('can show an edit modal for a child xblock with broken JavaScript', function() {
                var editButtons;
                renderContainerPage(this, mockBadContainerXBlockHtml);
                editButtons = containerPage.$('.wrapper-xblock .edit-button');
                editButtons[0].click();
                AjaxHelpers.respondWithJson(requests, {
                    html: mockXBlockEditorHtml,
                    resources: []
                });
                expect(EditHelpers.isShowingModal()).toBeTruthy();
            });

            it('can show a visibility modal for a child xblock if supported for the page', function() {
                var accessButtons, request;
                renderContainerPage(this, mockContainerXBlockHtml);
                accessButtons = containerPage.$('.wrapper-xblock .access-button');
                if (hasVisibilityEditor) {
                    expect(accessButtons.length).toBe(6);
                    accessButtons[0].click();
                    request = AjaxHelpers.currentRequest(requests);
                    expect(str.startsWith(request.url, '/xblock/locator-component-A1/visibility_view'))
                        .toBeTruthy();
                    AjaxHelpers.respondWithJson(requests, {
                        html: mockXBlockVisibilityEditorHtml,
                        resources: []
                    });
                    expect(EditHelpers.isShowingModal()).toBeTruthy();
                } else {
                    expect(accessButtons.length).toBe(0);
                }
            });

            it('can show a move modal for a child xblock', function() {
                var moveButtons;
                renderContainerPage(this, mockContainerXBlockHtml);
                moveButtons = containerPage.$('.wrapper-xblock .move-button');
                if (hasMoveModal) {
                    expect(moveButtons.length).toBe(6);
                    moveButtons[0].click();
                    expect(EditHelpers.isShowingModal()).toBeTruthy();
                } else {
                    expect(moveButtons.length).toBe(0);
                }
            });
        });

        describe('Editing an xmodule', function() {
            var mockXModuleEditor = readFixtures('templates/mock/mock-xmodule-editor.underscore'),
                newDisplayName = 'New Display Name';

            beforeEach(function() {
                EditHelpers.installMockXModule({
                    data: '<p>Some HTML</p>',
                    metadata: {
                        display_name: newDisplayName
                    }
                });
            });

            afterEach(function() {
                EditHelpers.uninstallMockXModule();
                EditHelpers.cancelModalIfShowing();
            });

            it('can save changes to settings', function() {
                var editButtons, $modal, mockUpdatedXBlockHtml;
                mockUpdatedXBlockHtml = readFixtures('mock/mock-updated-xblock.underscore');
                renderContainerPage(this, mockContainerXBlockHtml);
                editButtons = containerPage.$('.wrapper-xblock .edit-button');
                // The container should have rendered six mock xblocks
                expect(editButtons.length).toBe(6);
                editButtons[0].click();
                AjaxHelpers.respondWithJson(requests, {
                    html: mockXModuleEditor,
                    resources: []
                });

                $modal = $('.edit-xblock-modal');
                expect($modal.length).toBe(1);
                // Click on the settings tab
                $modal.find('.settings-button').click();
                // Change the display name's text
                $modal.find('.setting-input').text('Mock Update');
                // Press the save button
                $modal.find('.action-save').click();
                // Respond to the save
                AjaxHelpers.respondWithJson(requests, {
                    id: model.id
                });

                // Respond to the request to refresh
                respondWithHtml(mockUpdatedXBlockHtml);

                // Verify that the xblock was updated
                expect(containerPage.$('.mock-updated-content').text()).toBe('Mock Update');
            });
        });

        describe('xblock operations', function() {
            var getGroupElement,
                NUM_COMPONENTS_PER_GROUP = 3,
                GROUP_TO_TEST = 'A',
                allComponentsInGroup = _.map(
                    _.range(NUM_COMPONENTS_PER_GROUP),
                    function(index) {
                        return 'locator-component-' + GROUP_TO_TEST + (index + 1);
                    }
                );

            getGroupElement = function() {
                return containerPage.$("[data-locator='locator-group-" + GROUP_TO_TEST + "']");
            };

            describe('Deleting an xblock', function() {
                var clickDelete, deleteComponent, deleteComponentWithSuccess,
                    promptSpy;

                beforeEach(function() {
                    promptSpy = EditHelpers.createPromptSpy();
                });

                clickDelete = function(componentIndex, clickNo) {
                    // find all delete buttons for the given group
                    var deleteButtons = getGroupElement().find('.delete-button');
                    expect(deleteButtons.length).toBe(NUM_COMPONENTS_PER_GROUP);

                    // click the requested delete button
                    deleteButtons[componentIndex].click();

                    // click the 'yes' or 'no' button in the prompt
                    EditHelpers.confirmPrompt(promptSpy, clickNo);
                };

                deleteComponent = function(componentIndex) {
                    clickDelete(componentIndex);

                    // first request to delete the component
                    AjaxHelpers.expectJsonRequest(requests, 'DELETE',
                        '/xblock/locator-component-' + GROUP_TO_TEST + (componentIndex + 1),
                        null);
                    AjaxHelpers.respondWithNoContent(requests);

                    // then handle the request to refresh the preview
                    if (globalPageOptions.requiresPageRefresh) {
                        handleContainerPageRefresh(requests);
                    }

                    // final request to refresh the xblock info
                    AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/locator-container');
                    AjaxHelpers.respondWithJson(requests, {});
                };

                deleteComponentWithSuccess = function(componentIndex) {
                    deleteComponent(componentIndex);

                    // verify the new list of components within the group (unless reloading)
                    if (!globalPageOptions.requiresPageRefresh) {
                        expectComponents(
                            getGroupElement(),
                            _.without(allComponentsInGroup, allComponentsInGroup[componentIndex])
                        );
                    }
                };

                it('can delete the first xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    deleteComponentWithSuccess(0);
                });

                it('can delete a middle xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    deleteComponentWithSuccess(1);
                });

                it('can delete the last xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    deleteComponentWithSuccess(NUM_COMPONENTS_PER_GROUP - 1);
                });

                it('can delete an xblock with broken JavaScript', function() {
                    renderContainerPage(this, mockBadContainerXBlockHtml);
                    containerPage.$('.delete-button').first().click();
                    EditHelpers.confirmPrompt(promptSpy);

                    // expect the second to last request to be a delete of the xblock
                    AjaxHelpers.expectJsonRequest(requests, 'DELETE', '/xblock/locator-broken-javascript');
                    AjaxHelpers.respondWithNoContent(requests);

                    // handle the refresh request for pages that require a full refresh on delete
                    if (globalPageOptions.requiresPageRefresh) {
                        handleContainerPageRefresh(requests);
                    }

                    // expect the last request to be a fetch of the xblock info for the parent container
                    AjaxHelpers.expectJsonRequest(requests, 'GET', '/xblock/locator-container');
                });

                it('does not delete when clicking No in prompt', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);

                    // click delete on the first component but press no
                    clickDelete(0, true);

                    // all components should still exist
                    expectComponents(getGroupElement(), allComponentsInGroup);

                    // no requests should have been sent to the server
                    AjaxHelpers.expectNoRequests(requests);
                });

                it('shows a notification during the delete operation', function() {
                    var notificationSpy = EditHelpers.createNotificationSpy();
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickDelete(0);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Deleting/);
                    AjaxHelpers.respondWithJson(requests, {});
                    EditHelpers.verifyNotificationHidden(notificationSpy);
                });

                it('does not delete an xblock upon failure', function() {
                    var notificationSpy = EditHelpers.createNotificationSpy();
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickDelete(0);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Deleting/);
                    AjaxHelpers.respondWithError(requests);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Deleting/);
                    expectComponents(getGroupElement(), allComponentsInGroup);
                });
            });

            describe('Duplicating an xblock', function() {
                var clickDuplicate, duplicateComponentWithSuccess,
                    refreshXBlockSpies;

                clickDuplicate = function(componentIndex) {
                    // find all duplicate buttons for the given group
                    var duplicateButtons = getGroupElement().find('.duplicate-button');
                    expect(duplicateButtons.length).toBe(NUM_COMPONENTS_PER_GROUP);

                    // click the requested duplicate button
                    duplicateButtons[componentIndex].click();
                };

                duplicateComponentWithSuccess = function(componentIndex) {
                    refreshXBlockSpies = spyOn(containerPage, 'refreshXBlock');

                    clickDuplicate(componentIndex);

                    // verify content of request
                    AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                        duplicate_source_locator: 'locator-component-' + GROUP_TO_TEST + (componentIndex + 1),
                        parent_locator: 'locator-group-' + GROUP_TO_TEST
                    });

                    // send the response
                    AjaxHelpers.respondWithJson(requests, {
                        locator: 'locator-duplicated-component'
                    });

                    // expect parent container to be refreshed
                    expect(refreshXBlockSpies).toHaveBeenCalled();
                };

                it('can duplicate the first xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    duplicateComponentWithSuccess(0);
                });

                it('can duplicate a middle xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    duplicateComponentWithSuccess(1);
                });

                it('can duplicate the last xblock', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    duplicateComponentWithSuccess(NUM_COMPONENTS_PER_GROUP - 1);
                });

                it('can duplicate an xblock with broken JavaScript', function() {
                    renderContainerPage(this, mockBadContainerXBlockHtml);
                    containerPage.$('.duplicate-button').first().click();
                    AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/', {
                        duplicate_source_locator: 'locator-broken-javascript',
                        parent_locator: 'locator-container'
                    });
                });

                it('shows a notification when duplicating', function() {
                    var notificationSpy = EditHelpers.createNotificationSpy();
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickDuplicate(0);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
                    AjaxHelpers.respondWithJson(requests, {locator: 'new_item'});
                    EditHelpers.verifyNotificationHidden(notificationSpy);
                });

                it('does not duplicate an xblock upon failure', function() {
                    var notificationSpy = EditHelpers.createNotificationSpy();
                    renderContainerPage(this, mockContainerXBlockHtml);
                    refreshXBlockSpies = spyOn(containerPage, 'refreshXBlock');
                    clickDuplicate(0);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
                    AjaxHelpers.respondWithError(requests);
                    expectComponents(getGroupElement(), allComponentsInGroup);
                    expect(refreshXBlockSpies).not.toHaveBeenCalled();
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Duplicating/);
                });
            });

            describe('Previews', function() {
                var getButtonIcon, getButtonText;

                // eslint-disable-next-line no-shadow
                getButtonIcon = function(containerPage) {
                    return containerPage.$('.action-toggle-preview .fa');
                };

                // eslint-disable-next-line no-shadow
                getButtonText = function(containerPage) {
                    return containerPage.$('.action-toggle-preview .preview-text').text().trim();
                };

                if (pagedSpecificTests) {
                    it('has no text on the preview button to start with', function() {
                        containerPage = getContainerPage();
                        expect(getButtonIcon(containerPage)).toHaveClass('fa-refresh');
                        expect(getButtonIcon(containerPage).parent()).toHaveClass('is-hidden');
                        expect(getButtonText(containerPage)).toBe('');
                    });

                    var updatePreviewButtonTest = function(show_previews, expected_text) {
                        it('can set preview button to "' + expected_text + '"', function() {
                            containerPage = getContainerPage();
                            containerPage.updatePreviewButton(show_previews);
                            expect(getButtonText(containerPage)).toBe(expected_text);
                        });
                    };

                    updatePreviewButtonTest(true, 'Hide Previews');
                    updatePreviewButtonTest(false, 'Show Previews');

                    it('triggers underlying view togglePreviews when preview button clicked', function() {
                        containerPage = getContainerPage();
                        containerPage.render();
                        spyOn(containerPage.xblockView, 'togglePreviews');

                        containerPage.$('.toggle-preview-button').click();
                        expect(containerPage.xblockView.togglePreviews).toHaveBeenCalled();
                    });
                }
            });

            describe('createNewComponent ', function() {
                var clickNewComponent;

                clickNewComponent = function(index) {
                    containerPage.$('.new-component .new-component-type button.single-template')[index].click();
                };

                it('Attaches a handler to new component button', function() {
                    containerPage = getContainerPage();
                    containerPage.render();
                    // Stub jQuery.scrollTo module.
                    $.scrollTo = jasmine.createSpy('jQuery.scrollTo');
                    containerPage.$('.new-component-button').click();
                    expect($.scrollTo).toHaveBeenCalled();
                });

                it('sends the correct JSON to the server', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickNewComponent(0);
                    EditHelpers.verifyXBlockRequest(requests, {
                        category: 'discussion',
                        type: 'discussion',
                        parent_locator: 'locator-group-A'
                    });
                });

                it('also works for older-style add component links', function() {
                    // Some third party xblocks (problem-builder in particular) expect add
                    // event handlers on custom <a> add buttons which is what the platform
                    // used to use instead of <button>s.
                    // This can be removed once there is a proper API that XBlocks can use
                    // to add children or allow authors to add children.
                    renderContainerPage(this, mockContainerXBlockHtml);
                    $('.add-xblock-component-button').each(function() {
                        var $htmlAsLink = $($(this).prop('outerHTML').replace(/(<\/?)button/g, '$1a'));
                        $(this).replaceWith($htmlAsLink);
                    });
                    $('.add-xblock-component-button').first().click();
                    EditHelpers.verifyXBlockRequest(requests, {
                        category: 'discussion',
                        type: 'discussion',
                        parent_locator: 'locator-group-A'
                    });
                });

                it('shows a notification while creating', function() {
                    var notificationSpy = EditHelpers.createNotificationSpy();
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickNewComponent(0);
                    EditHelpers.verifyNotificationShowing(notificationSpy, /Adding/);
                    AjaxHelpers.respondWithJson(requests, { });
                    EditHelpers.verifyNotificationHidden(notificationSpy);
                });

                it('does not insert component upon failure', function() {
                    renderContainerPage(this, mockContainerXBlockHtml);
                    clickNewComponent(0);
                    AjaxHelpers.respondWithError(requests);
                    // No new requests should be made to refresh the view
                    AjaxHelpers.expectNoRequests(requests);
                    expectComponents(getGroupElement(), allComponentsInGroup);
                });

                describe('Template Picker', function() {
                    var showTemplatePicker, verifyCreateHtmlComponent;

                    showTemplatePicker = function() {
                        containerPage.$('.new-component .new-component-type button.multiple-templates')[0].click();
                    };

                    verifyCreateHtmlComponent = function(test, templateIndex, expectedRequest) {
                        var xblockCount;
                        renderContainerPage(test, mockContainerXBlockHtml);
                        showTemplatePicker();
                        xblockCount = containerPage.$('.studio-xblock-wrapper').length;
                        containerPage.$('.new-component-html button')[templateIndex].click();
                        EditHelpers.verifyXBlockRequest(requests, expectedRequest);
                        AjaxHelpers.respondWithJson(requests, {locator: 'new_item'});
                        respondWithHtml(mockXBlockHtml);
                        expect(containerPage.$('.studio-xblock-wrapper').length).toBe(xblockCount + 1);
                    };

                    it('can add an HTML component without a template', function() {
                        verifyCreateHtmlComponent(this, 0, {
                            category: 'html',
                            parent_locator: 'locator-group-A'
                        });
                    });

                    it('can add an HTML component with a template', function() {
                        verifyCreateHtmlComponent(this, 1, {
                            category: 'html',
                            boilerplate: 'announcement.yaml',
                            parent_locator: 'locator-group-A'
                        });
                    });

                    it('does not show the support legend if show_legend is false', function() {
                        // By default, show_legend is false in the mock component Templates.
                        renderContainerPage(this, mockContainerXBlockHtml);
                        showTemplatePicker();
                        expect(containerPage.$('.support-documentation').length).toBe(0);
                    });

                    it('does show the support legend if show_legend is true', function() {
                        var templates = new ComponentTemplates([
                                {
                                    templates: [
                                        {
                                            category: 'html',
                                            boilerplate_name: null,
                                            display_name: 'Text'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'announcement.yaml',
                                            display_name: 'Announcement'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'raw.yaml',
                                            display_name: 'Raw HTML'
                                        }],
                                    type: 'html',
                                    support_legend: {
                                        show_legend: true,
                                        documentation_label: 'Documentation Label:',
                                        allow_unsupported_xblocks: false
                                    }
                                }],
                            {
                                parse: true
                            }),
                            supportDocumentation;
                        renderContainerPage(this, mockContainerXBlockHtml, {}, templates);
                        showTemplatePicker();
                        supportDocumentation = containerPage.$('.support-documentation');
                        // On this page, groups are being shown, each of which has a new component menu.
                        expect(supportDocumentation.length).toBeGreaterThan(0);

                        // check that the documentation label is displayed
                        expect($(supportDocumentation[0]).find('.support-documentation-link').text().trim())
                            .toBe('Documentation Label:');

                        // show_unsupported_xblocks is false, so only 2 support levels should be shown
                        expect($(supportDocumentation[0]).find('.support-documentation-level').length).toBe(2);
                    });

                    it('does show unsupported level if enabled', function() {
                        var templates = new ComponentTemplates([
                                {
                                    templates: [
                                        {
                                            category: 'html',
                                            boilerplate_name: null,
                                            display_name: 'Text'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'announcement.yaml',
                                            display_name: 'Announcement'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'raw.yaml',
                                            display_name: 'Raw HTML'
                                        }],
                                    type: 'html',
                                    support_legend: {
                                        show_legend: true,
                                        documentation_label: 'Documentation Label:',
                                        allow_unsupported_xblocks: true
                                    }
                                }],
                            {
                                parse: true
                            }),
                            supportDocumentation;
                        renderContainerPage(this, mockContainerXBlockHtml, {}, templates);
                        showTemplatePicker();
                        supportDocumentation = containerPage.$('.support-documentation');

                        // show_unsupported_xblocks is true, so 3 support levels should be shown
                        expect($(supportDocumentation[0]).find('.support-documentation-level').length).toBe(3);

                        // verify only one has the unsupported item
                        expect($(supportDocumentation[0]).find('.fa-circle-o').length).toBe(1);
                    });

                    it('does render support level indicators if present in JSON', function() {
                        var templates = new ComponentTemplates([
                                {
                                    templates: [
                                        {
                                            category: 'html',
                                            boilerplate_name: null,
                                            display_name: 'Text',
                                            support_level: 'fs'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'announcement.yaml',
                                            display_name: 'Announcement',
                                            support_level: 'ps'
                                        }, {
                                            category: 'html',
                                            boilerplate_name: 'raw.yaml',
                                            display_name: 'Raw HTML',
                                            support_level: 'us'
                                        }],
                                    type: 'html',
                                    support_legend: {
                                        show_legend: true,
                                        documentation_label: 'Documentation Label:',
                                        allow_unsupported_xblocks: true
                                    }
                                }],
                            {
                                parse: true
                            }),
                            supportLevelIndicators, getScreenReaderText;
                        renderContainerPage(this, mockContainerXBlockHtml, {}, templates);
                        showTemplatePicker();

                        supportLevelIndicators = $(containerPage.$('.new-component-template')[0])
                            .find('.support-level');
                        expect(supportLevelIndicators.length).toBe(3);

                        getScreenReaderText = function(index) {
                            return $($(supportLevelIndicators[index]).siblings()[0]).text().trim();
                        };
                        // Verify one level of each type was rendered.
                        expect(getScreenReaderText(0)).toBe('Fully Supported');
                        expect(getScreenReaderText(1)).toBe('Provisionally Supported');
                        expect(getScreenReaderText(2)).toBe('Not Supported');
                    });
                });
            });
        });
    });
}

// Create a suite for a non-paged container that includes 'edit visibility' buttons
parameterized_suite('Non paged',
    {
        page: ContainerPage,
        requiresPageRefresh: false,
        initial: 'templates/mock/mock-container-xblock.underscore',
        addResponse: 'templates/mock/mock-xblock.underscore',
        hasVisibilityEditor: true,
        pagedSpecificTests: false,
        hasMoveModal: true
    }
);

// Create a suite for a paged container that does not include 'edit visibility' buttons
parameterized_suite('Paged',
    {
        page: PagedContainerPage,
        page_size: 42,
        requiresPageRefresh: true,
        initial: 'templates/mock/mock-container-paged-xblock.underscore',
        addResponse: 'templates/mock/mock-xblock-paged.underscore',
        hasVisibilityEditor: false,
        pagedSpecificTests: true,
        hasMoveModal: false
    }
);
