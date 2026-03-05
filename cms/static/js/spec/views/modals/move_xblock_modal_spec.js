define(['jquery', 'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers', 'common/js/spec_helpers/view_helpers',
    'js/views/modals/move_xblock_modal', 'js/models/xblock_info'],
function($, _, sinon, AjaxHelpers, TemplateHelpers, ViewHelpers, MoveXBlockModal, XBlockInfo) {
    'use strict';

    describe('MoveXBlockModal', function() {
        var requests, xhrFactory;
        var modal,
            showModal,
            DISPLAY_NAME = 'HTML 101',
            OUTLINE_URL = '/course/cid?format=concise',
            ANCESTORS_URL = '/xblock/USAGE_ID?fields=ancestorInfo';

        showModal = function() {
            modal = new MoveXBlockModal({
                sourceXBlockInfo: new XBlockInfo({
                    id: 'USAGE_ID',
                    display_name: DISPLAY_NAME,
                    category: 'html'
                }),
                sourceParentXBlockInfo: new XBlockInfo({
                    id: 'PARENT_ID',
                    display_name: 'VERT 101',
                    category: 'vertical'
                }),
                XBlockURLRoot: '/xblock',
                outlineURL: OUTLINE_URL,
                XBlockAncestorInfoURL: ANCESTORS_URL

            });
            modal.show();
        };

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures('<div id="page-notification"></div><div id="reader-feedback"></div>');
            TemplateHelpers.installTemplates([
                'basic-modal',
                'modal-button',
                'move-xblock-modal'
            ]);
        });

        afterEach(function() {
            requests.restore();
            modal.hide();
        });

        it('rendered as expected', function() {
            showModal();
            expect(
                modal.$el.find('.modal-header .title').contents().get(0).nodeValue.trim()
            ).toEqual('Move: ' + DISPLAY_NAME);
            expect(
                modal.$el.find('.modal-sr-title').text().trim()
            ).toEqual('Choose a location to move your component to');
            expect(modal.$el.find('.modal-actions .action-primary.action-move').text()).toEqual('Move');
        });

        it('sends request to fetch course outline', function() {
            var renderViewsSpy;
            showModal();
            expect(modal.$el.find('.ui-loading.is-hidden')).not.toExist();
            renderViewsSpy = spyOn(modal, 'renderViews');
            expect(requests.length).toEqual(2);
            AjaxHelpers.expectRequest(requests, 'GET', OUTLINE_URL);
            AjaxHelpers.respondWithJson(requests, {});
            AjaxHelpers.expectRequest(requests, 'GET', ANCESTORS_URL);
            AjaxHelpers.respondWithJson(requests, {});
            expect(renderViewsSpy).toHaveBeenCalled();
            expect(modal.$el.find('.ui-loading.is-hidden')).toExist();
        });

        it('shows error notification when fetch course outline request fails', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy('Error');
            showModal();
            AjaxHelpers.respondWithError(requests);
            ViewHelpers.verifyNotificationShowing(notificationSpy, "Studio's having trouble saving your work");
        });
    });
});
