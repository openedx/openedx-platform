define([
    'sinon',
    'js/api_admin/views/catalog_preview',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers'
], function(
    sinon, CatalogPreviewView, AjaxHelpers
) {
    'use strict';

    describe('Catalog preview view', function() {
        var view,
            requests,
            xhrFactory,
            previewUrl = 'http://example.com/api-admin/catalogs/preview/',
            catalogApiUrl = 'http://api.example.com/catalog/v1/courses/';

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            setFixtures(
                '<div class="catalog-body">'
                + '<textarea id="id_query"></textarea>'
                + '<div class="preview-results"></div>'
                + '</div>'
            );
            view = new CatalogPreviewView({
                el: '.catalog-body',
                previewUrl: previewUrl,
                catalogApiUrl: catalogApiUrl
            });
            view.render();
        });

        afterEach(function() {
            requests.restore();
        });

        it('can render itself', function() {
            expect(view.$('button.preview-query').length).toBe(1);
        });

        it('can retrieve a list of catalogs and display them', function() {
            view.$('#id_query').val('*');
            view.$('.preview-query').click();
            AjaxHelpers.expectRequest(requests, 'GET', previewUrl + '?q=*');
            AjaxHelpers.respondWithJson(requests, {
                results: [{key: 'TestX', title: 'Test Course'}],
                count: 1,
                next: null,
                prev: null
            });
            expect(view.$('.preview-results').text()).toContain('Test Course');
            expect(view.$('.preview-results-list li a').attr('href')).toEqual(catalogApiUrl + 'TestX');
        });

        it('displays an error when courses cannot be retrieved', function() {
            view.$('#id_query').val('*');
            view.$('.preview-query').click();
            AjaxHelpers.respondWithError(requests, 500);
            expect(view.$('.preview-results').text()).toContain(
                'There was an error retrieving preview results for this catalog.'
            );
        });
    });
});
