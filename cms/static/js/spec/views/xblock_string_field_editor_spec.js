import $ from 'jquery';
import sinon from 'sinon';
import AjaxHelpers from 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers';
import TemplateHelpers from 'common/js/spec_helpers/template_helpers';
import EditHelpers from 'js/spec_helpers/edit_helpers';
import XBlockInfo from 'js/models/xblock_info';
import XBlockStringFieldEditor from 'js/views/xblock_string_field_editor';

describe('XBlockStringFieldEditorView', function() {
    var initialDisplayName, updatedDisplayName, getXBlockInfo, getFieldEditorView, requests, xhrFactory;

    getXBlockInfo = function(displayName) {
        return new XBlockInfo(
            {
                display_name: displayName,
                id: 'my_xblock'
            },
            {parse: true}
        );
    };

    getFieldEditorView = function(xblockInfo) {
        if (xblockInfo === undefined) {
            xblockInfo = getXBlockInfo(initialDisplayName);
        }
        return new XBlockStringFieldEditor({
            model: xblockInfo,
            el: $('.wrapper-xblock-field')
        });
    };

    beforeEach(function() {
        xhrFactory = sinon.useFakeXMLHttpRequest();
        requests = [];
        requests.currentIndex = 0;
        requests.restore = function() { xhrFactory.restore(); };
        xhrFactory.onCreate = function(req) { requests.push(req); };
        initialDisplayName = 'Default Display Name';
        updatedDisplayName = 'Updated Display Name';
        TemplateHelpers.installTemplate('xblock-string-field-editor');
        appendSetFixtures(
            '<div class="wrapper-xblock-field incontext-editor is-editable"'
                + 'data-field="display_name" data-field-display-name="Display Name">'
                + '<h1 class="page-header-title xblock-field-value incontext-editor-value">'
                + '<span class="title-value">' + initialDisplayName + '</span>'
                + '</h1>'
                + '</div>'
        );
    });

    afterEach(function() {
        requests.restore();
    });

    describe('Editing', function() {
        var expectPostedNewDisplayName, expectEditCanceled;

        expectPostedNewDisplayName = function(requests, displayName) {
            AjaxHelpers.expectJsonRequest(requests, 'POST', '/xblock/my_xblock', {
                metadata: {
                    display_name: displayName
                }
            });
        };

        expectEditCanceled = function(test, fieldEditorView, options) {
            var displayNameInput;
            displayNameInput = EditHelpers.inlineEdit(fieldEditorView.$el, options.newTitle);
            if (options.pressEscape) {
                displayNameInput.simulate('keydown', {keyCode: $.simulate.keyCode.ESCAPE});
                displayNameInput.simulate('keyup', {keyCode: $.simulate.keyCode.ESCAPE});
            } else if (options.clickCancel) {
                fieldEditorView.$('button[name=cancel]').click();
            } else {
                displayNameInput.change();
            }
            // No requests should be made when the edit is cancelled client-side
            AjaxHelpers.expectNoRequests(requests);
            EditHelpers.verifyInlineEditChange(fieldEditorView.$el, initialDisplayName);
            expect(fieldEditorView.model.get('display_name')).toBe(initialDisplayName);
        };

        it('can inline edit the display name', function() {
            var fieldEditorView;
            fieldEditorView = getFieldEditorView().render();
            EditHelpers.inlineEdit(fieldEditorView.$el, updatedDisplayName);
            fieldEditorView.$('button[name=submit]').click();
            expectPostedNewDisplayName(requests, updatedDisplayName);
            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, { });
            // This is the response for the subsequent fetch operation.
            AjaxHelpers.respondWithJson(requests, {display_name: updatedDisplayName});
            EditHelpers.verifyInlineEditChange(fieldEditorView.$el, updatedDisplayName);
        });

        it('does not change the title when a display name update fails', function() {
            var fieldEditorView;
            fieldEditorView = getFieldEditorView().render();
            EditHelpers.inlineEdit(fieldEditorView.$el, updatedDisplayName);
            fieldEditorView.$('button[name=submit]').click();
            expectPostedNewDisplayName(requests, updatedDisplayName);
            AjaxHelpers.respondWithError(requests);
            // No fetch operation should occur.
            AjaxHelpers.expectNoRequests(requests);
            EditHelpers.verifyInlineEditChange(fieldEditorView.$el, initialDisplayName, updatedDisplayName);
        });

        it('trims whitespace from the display name', function() {
            var fieldEditorView;
            fieldEditorView = getFieldEditorView().render();
            updatedDisplayName += ' ';
            EditHelpers.inlineEdit(fieldEditorView.$el, updatedDisplayName);
            fieldEditorView.$('button[name=submit]').click();
            expectPostedNewDisplayName(requests, updatedDisplayName.trim());
            // This is the response for the change operation.
            AjaxHelpers.respondWithJson(requests, { });
            // This is the response for the subsequent fetch operation.
            AjaxHelpers.respondWithJson(requests, {display_name: updatedDisplayName.trim()});
            EditHelpers.verifyInlineEditChange(fieldEditorView.$el, updatedDisplayName.trim());
        });

        it('does not change the title when input is the empty string', function() {
            var fieldEditorView = getFieldEditorView().render();
            expectEditCanceled(this, fieldEditorView, {newTitle: ''});
        });

        it('does not change the title when input is whitespace-only', function() {
            var fieldEditorView = getFieldEditorView().render();
            expectEditCanceled(this, fieldEditorView, {newTitle: ' '});
        });

        it('can cancel an inline edit by pressing escape', function() {
            var fieldEditorView = getFieldEditorView().render();
            expectEditCanceled(this, fieldEditorView, {newTitle: updatedDisplayName, pressEscape: true});
        });

        it('can cancel an inline edit by clicking cancel', function() {
            var fieldEditorView = getFieldEditorView().render();
            expectEditCanceled(this, fieldEditorView, {newTitle: updatedDisplayName, clickCancel: true});
        });
    });

    describe('Rendering', function() {
        var expectInputMatchesModelDisplayName = function(displayName) {
            var fieldEditorView = getFieldEditorView(getXBlockInfo(displayName)).render();
            expect(fieldEditorView.$('.xblock-field-input').val()).toBe(displayName);
        };

        it('renders single quotes in input field', function() {
            expectInputMatchesModelDisplayName('Updated \'Display Name\'');
        });

        it('renders double quotes in input field', function() {
            expectInputMatchesModelDisplayName('Updated "Display Name"');
        });

        it('renders open angle bracket in input field', function() {
            expectInputMatchesModelDisplayName(updatedDisplayName + '<');
        });

        it('renders close angle bracket in input field', function() {
            expectInputMatchesModelDisplayName('>' + updatedDisplayName);
        });
    });
});
