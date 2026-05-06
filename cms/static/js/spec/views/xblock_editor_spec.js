import $ from 'jquery';
import _ from 'underscore';
import sinon from 'sinon';
import AjaxHelpers from 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers';
import EditHelpers from 'js/spec_helpers/edit_helpers';
import XBlockEditorView from 'js/views/xblock_editor';
import XBlockInfo from 'js/models/xblock_info';

describe('XBlockEditorView', function() {
    var model, editor, testDisplayName, mockSaveResponse, requests, xhrFactory;

    testDisplayName = 'Test Display Name';
    mockSaveResponse = {
        data: '<p>Some HTML</p>',
        metadata: {
            display_name: testDisplayName
        }
    };

    beforeEach(function() {
        xhrFactory = sinon.useFakeXMLHttpRequest();
        requests = [];
        requests.currentIndex = 0;
        requests.restore = function() { xhrFactory.restore(); };
        xhrFactory.onCreate = function(req) { requests.push(req); };
        EditHelpers.installEditTemplates();
        model = new XBlockInfo({
            id: 'testCourse/branch/draft/block/verticalFFF',
            display_name: 'Test Unit',
            category: 'vertical'
        });
        editor = new XBlockEditorView({
            model: model
        });
    });

    afterEach(function() {
        requests.restore();
    });

    describe('Editing an xblock', function() {
        var mockXBlockEditorHtml;

        beforeEach(function() {
            EditHelpers.installMockXBlock();
        });

        afterEach(function() {
            EditHelpers.uninstallMockXBlock();
        });

        mockXBlockEditorHtml = readFixtures('templates/mock/mock-xblock-editor.underscore');

        it('can render itself', function() {
            editor.render();
            AjaxHelpers.respondWithJson(requests, {
                html: mockXBlockEditorHtml,
                resources: []
            });

            expect(editor.$el.select('.xblock-header')).toBeTruthy();
            expect(editor.getMode()).toEqual('settings');
        });
    });

    describe('Editing an xmodule', function() {
        var mockXModuleEditorHtml;

        mockXModuleEditorHtml = readFixtures('templates/mock/mock-xmodule-editor.underscore');

        beforeEach(function() {
            EditHelpers.installMockXModule(mockSaveResponse);
        });

        afterEach(function() {
            EditHelpers.uninstallMockXModule();
        });

        it('can render itself', function() {
            editor.render();
            AjaxHelpers.respondWithJson(requests, {
                html: mockXModuleEditorHtml,
                resources: []
            });

            expect(editor.$el.select('.xblock-header')).toBeTruthy();
            expect(editor.getMode()).toEqual('editor');
        });

        it('saves any custom metadata', function() {
            var request, response;
            editor.render();
            AjaxHelpers.respondWithJson(requests, {
                html: mockXModuleEditorHtml,
                resources: []
            });
            // Give the mock xblock a save method...
            editor.xblock.save = window.MockDescriptor.save;
            editor.model.save(editor.getXBlockFieldData());
            request = AjaxHelpers.currentRequest(requests);
            response = JSON.parse(request.requestBody);
            expect(response.metadata.display_name).toBe(testDisplayName);
            expect(response.metadata.custom_field).toBe('Custom Value');
        });

        it('can render a module with only settings', function() {
            var // eslint-disable-next-line no-shadow
                mockXModuleEditorHtml;
            mockXModuleEditorHtml = readFixtures('templates/mock/mock-xmodule-settings-only-editor.underscore');

            editor.render();
            AjaxHelpers.respondWithJson(requests, {
                html: mockXModuleEditorHtml,
                resources: []
            });

            expect(editor.$el.select('.xblock-header')).toBeTruthy();
            expect(editor.getMode()).toEqual('settings');
        });
    });
});
