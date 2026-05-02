define([
    'jquery', 'underscore', 'sinon', 'annotator_1.2.9', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'js/edxnotes/views/notes_visibility_factory', 'js/edxnotes/utils/notes_collector', 'js/spec/edxnotes/helpers'
], function(
    $, _, sinon, Annotator, AjaxHelpers, NotesVisibilityFactory, NotesCollector, Helpers
) {
    'use strict';

    describe('EdxNotes ToggleNotesFactory', function() {
        var requests, xhrFactory;
        var params = {
            endpoint: '/test_endpoint/',
            user: 'user12345',
            usageId: 'usageid777',
            courseId: 'courseid000',
            token: Helpers.makeToken(),
            tokenUrl: '/test_token_url'
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            loadFixtures(
                'js/fixtures/edxnotes/edxnotes_wrapper.html',
                'js/fixtures/edxnotes/toggle_notes.html'
            );
            NotesVisibilityFactory.VisibilityDecorator.factory(
                document.getElementById('edx-notes-wrapper-123'), params, true
            );
            NotesVisibilityFactory.VisibilityDecorator.factory(
                document.getElementById('edx-notes-wrapper-456'), params, true
            );
            this.toggleNotes = NotesVisibilityFactory.ToggleVisibilityView(true, '/test_url');
            this.toggleVisibilityButton = $('.action-toggle-notes');
            this.label = this.toggleVisibilityButton.find('.utility-control-label');
            this.toggleMessage = $('.action-toggle-message');
            spyOn(this.toggleNotes, 'toggleHandler').and.callThrough();
            NotesCollector.cleanup();
        });

        afterEach(function() {
            NotesVisibilityFactory.VisibilityDecorator._setVisibility(null);
            while (Annotator._instances.length > 0) {
                Annotator._instances[0].destroy();
            }
            $('.annotator-notice').remove();
            NotesCollector.cleanup();
            requests.restore();
        });

        it('can toggle notes', function() {

            expect(this.toggleVisibilityButton).not.toHaveClass('is-disabled');
            expect(this.label).toContainText('Hide notes');
            expect(this.toggleVisibilityButton).toHaveClass('is-active');
            expect(this.toggleVisibilityButton).toHaveAttr('aria-pressed', 'true');
            expect(this.toggleMessage).not.toHaveClass('is-fleeting');
            expect(this.toggleMessage).toContainText('Notes visible');

            this.toggleVisibilityButton.click();
            expect(this.label).toContainText('Show notes');
            expect(this.toggleVisibilityButton).not.toHaveClass('is-active');
            expect(this.toggleMessage).toHaveClass('is-fleeting');
            expect(this.toggleMessage).toContainText('Notes hidden');
            expect(Annotator._instances).toHaveLength(0);

            AjaxHelpers.expectJsonRequest(requests, 'PUT', '/test_url', {
                visibility: false
            });
            AjaxHelpers.respondWithJson(requests, {});

            this.toggleVisibilityButton.click();
            expect(this.label).toContainText('Hide notes');
            expect(this.toggleVisibilityButton).toHaveClass('is-active');
            expect(this.toggleMessage).toHaveClass('is-fleeting');
            expect(this.toggleMessage).toContainText('Notes visible');
            expect(Annotator._instances).toHaveLength(2);

            AjaxHelpers.expectJsonRequest(requests, 'GET',
                '/test_endpoint/search/?usage_id=usageid777&usage_id=usageid777&user=user12345&course_id=courseid000'
            );
            AjaxHelpers.respondWithJson(requests, []);

            AjaxHelpers.expectJsonRequest(requests, 'PUT', '/test_url', {
                visibility: true
            });
            AjaxHelpers.respondWithJson(requests, {});
        });

        it('can handle errors', function() {
            var $errorContainer = $('.annotator-notice');

            this.toggleVisibilityButton.click();
            AjaxHelpers.respondWithError(requests);
            expect($errorContainer).toContainText(
                'An error has occurred. Make sure that you are connected to the Internet, '
                + 'and then try refreshing the page.'
            );
            expect($errorContainer).toBeVisible();
            expect($errorContainer).toHaveClass('annotator-notice-show');
            expect($errorContainer).toHaveClass('annotator-notice-error');

            this.toggleVisibilityButton.click();

            AjaxHelpers.expectJsonRequest(requests, 'GET',
                '/test_endpoint/search/?usage_id=usageid777&usage_id=usageid777&user=user12345&course_id=courseid000'
            );
            AjaxHelpers.respondWithJson(requests, []);

            AjaxHelpers.expectJsonRequest(requests, 'PUT', '/test_url', {
                visibility: true
            });
            AjaxHelpers.respondWithJson(requests, {});

            expect($errorContainer).not.toHaveClass('annotator-notice-show');
        });

        it('toggles notes when CTRL + SHIFT + [ keydown on document', function() {
            // Character '[' has keyCode 219
            $(document).trigger($.Event('keydown', {keyCode: 219, ctrlKey: true, shiftKey: true}));
            expect(this.toggleNotes.toggleHandler).toHaveBeenCalled();
        });
    });
});
