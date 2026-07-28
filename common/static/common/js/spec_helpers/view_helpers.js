/**
 * Provides helper methods for invoking Studio modal windows in Jasmine tests.
 */
define(['underscore', 'jquery', 'common/js/components/views/feedback_notification', 'common/js/components/views/feedback_prompt',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers'],
function(_, $, NotificationView, Prompt, AjaxHelpers) {
    'use strict';

    var installViewTemplates, createFeedbackSpy, verifyFeedbackShowing,
        verifyFeedbackHidden, createNotificationSpy, verifyNotificationShowing,
        verifyNotificationHidden, createPromptSpy, confirmPrompt, inlineEdit, verifyInlineEditChange,
        installMockAnalytics, removeMockAnalytics, verifyPromptShowing, verifyPromptHidden,
        clickDeleteItem, patchAndVerifyRequest, submitAndVerifyFormSuccess, submitAndVerifyFormError;

    // Register a single global afterEach to clean up jasmine.stealth constructor spies.
    // Jasmine 2.99.x prohibits calling afterEach() from inside it()/beforeEach() callbacks,
    // so we cannot register this inside createFeedbackSpy (which is called from it() blocks).
    // Calling afterEach() at module scope (outside any describe/it) registers a global hook
    // that runs after every spec. AMD caches this module, so the hook is registered exactly once.
    afterEach(jasmine.stealth.clearSpies);

    installViewTemplates = function() {
        appendSetFixtures('<div id="page-notification"></div>');
    };

    createFeedbackSpy = function(type, intent) {
        var feedbackSpy = jasmine.stealth.spyOnConstructor(type, intent, ['show', 'hide']);
        feedbackSpy.show.and.returnValue(feedbackSpy);
        return feedbackSpy;
    };

    verifyFeedbackShowing = function(feedbackSpy, text) {
        var options;
        expect(feedbackSpy.constructor).toHaveBeenCalled();
        expect(feedbackSpy.show).toHaveBeenCalled();
        expect(feedbackSpy.hide).not.toHaveBeenCalled();
        options = feedbackSpy.constructor.calls.mostRecent().args[0];
        expect(options.title).toMatch(text);
    };

    verifyFeedbackHidden = function(feedbackSpy) {
        expect(feedbackSpy.hide).toHaveBeenCalled();
    };

    createNotificationSpy = function(type) {
        return createFeedbackSpy(NotificationView, type || 'Mini');
    };

    verifyNotificationShowing = function() {
        verifyFeedbackShowing.apply(this, arguments);
    };

    verifyNotificationHidden = function() {
        verifyFeedbackHidden.apply(this, arguments);
    };

    createPromptSpy = function(type) {
        return createFeedbackSpy(Prompt, type || 'Warning');
    };

    confirmPrompt = function(promptSpy, pressSecondaryButton) {
        expect(promptSpy.constructor).toHaveBeenCalled();
        if (pressSecondaryButton) {
            promptSpy.constructor.calls.mostRecent().args[0].actions.secondary.click(promptSpy);
        } else {
            promptSpy.constructor.calls.mostRecent().args[0].actions.primary.click(promptSpy);
        }
    };

    verifyPromptShowing = function() {
        verifyFeedbackShowing.apply(this, arguments);
    };

    verifyPromptHidden = function() {
        verifyFeedbackHidden.apply(this, arguments);
    };

    installMockAnalytics = function() {
        window.analytics = jasmine.createSpyObj('analytics', ['track']);
        window.course_location_analytics = jasmine.createSpy();
    };

    removeMockAnalytics = function() {
        delete window.analytics;
        delete window.course_location_analytics;
    };

    inlineEdit = function(editorWrapper, newValue) {
        var inputField = editorWrapper.find('.xblock-field-input'),
            editButton = editorWrapper.find('.xblock-field-value-edit');
        editButton.click();
        expect(editorWrapper).toHaveClass('is-editing');
        inputField.val(newValue);
        return inputField;
    };

    verifyInlineEditChange = function(editorWrapper, expectedValue, failedValue) {
        var displayName = editorWrapper.find('.xblock-field-value');
        expect(displayName.text()).toBe(expectedValue);
        if (failedValue) {
            expect(editorWrapper).toHaveClass('is-editing');
        } else {
            expect(editorWrapper).not.toHaveClass('is-editing');
        }
    };

    clickDeleteItem = function(that, promptSpy, promptText) {
        that.view.$('.delete').click();
        verifyPromptShowing(promptSpy, promptText);
        confirmPrompt(promptSpy);
        verifyPromptHidden(promptSpy);
    };

    patchAndVerifyRequest = function(requests, url, notificationSpy) {
        // Backbone.emulateHTTP is enabled in our system, so setting this
        // option  will fake PUT, PATCH and DELETE requests with a HTTP POST,
        // setting the X-HTTP-Method-Override header with the true method.
        AjaxHelpers.expectJsonRequest(requests, 'POST', url);
        expect(_.last(requests).requestHeaders['X-HTTP-Method-Override']).toBe('DELETE');
        verifyNotificationShowing(notificationSpy, /Deleting/);
    };

    submitAndVerifyFormSuccess = function(view, requests, notificationSpy) {
        view.$('form').submit();
        verifyNotificationShowing(notificationSpy, /Saving/);
        AjaxHelpers.respondWithJson(requests, {});
        verifyNotificationHidden(notificationSpy);
    };

    submitAndVerifyFormError = function(view, requests, notificationSpy) {
        view.$('form').submit();
        verifyNotificationShowing(notificationSpy, /Saving/);
        AjaxHelpers.respondWithError(requests);
        verifyNotificationShowing(notificationSpy, /Saving/);
    };

    return {
        installViewTemplates: installViewTemplates,
        createNotificationSpy: createNotificationSpy,
        verifyNotificationShowing: verifyNotificationShowing,
        verifyNotificationHidden: verifyNotificationHidden,
        confirmPrompt: confirmPrompt,
        createPromptSpy: createPromptSpy,
        verifyPromptShowing: verifyPromptShowing,
        verifyPromptHidden: verifyPromptHidden,
        inlineEdit: inlineEdit,
        verifyInlineEditChange: verifyInlineEditChange,
        installMockAnalytics: installMockAnalytics,
        removeMockAnalytics: removeMockAnalytics,
        clickDeleteItem: clickDeleteItem,
        patchAndVerifyRequest: patchAndVerifyRequest,
        submitAndVerifyFormSuccess: submitAndVerifyFormSuccess,
        submitAndVerifyFormError: submitAndVerifyFormError
    };
}
);
