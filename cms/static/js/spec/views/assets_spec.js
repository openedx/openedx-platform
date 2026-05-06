define(['jquery', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'URI', 'js/views/assets',
    'js/collections/asset', 'common/js/spec_helpers/view_helpers'],
function($, sinon, AjaxHelpers, URI, AssetsView, AssetCollection, ViewHelpers) {
    var requests, xhrFactory;
    describe('Assets', function() {
        var assetsView, mockEmptyAssetsResponse, mockAssetUploadResponse, mockFileUpload,
            assetLibraryTpl, assetTpl, uploadModalTpl;

        assetLibraryTpl = readFixtures('asset-library.underscore');
        assetTpl = readFixtures('asset.underscore');
        uploadModalTpl = readFixtures('asset-upload-modal.underscore');

        beforeEach(function() {
            // Fake XHR must be installed BEFORE new AssetsView() for two reasons:
            //
            // 1. AssetsView.initialize() calls createPagingView() which immediately calls
            //    pagingView.setPage(1), firing an AJAX request. If real XHR is active at that
            //    point, the request goes to Karma's server which returns 404. Jasmine 2.x
            //    schedules beforeEach and it() with setTimeout(0) between them, giving the
            //    browser event loop a window to process pending async work. The Karma 404
            //    response fires during that window, triggering AssetsView's error callback
            //    -> getTableBody() -> DOM creation -> .upload-button becomes visible. Tests
            //    that check this button is hidden (in loading state) then fail.
            //
            // 2. This test suite tests the loading -> loaded state transition. Several tests
            //    call setup() inside it(), which calls setPage(1) and responds to it, driving
            //    the view from "loading" to "loaded". That precondition requires the view to
            //    still be in "loading" state at the start of it(). Responding to the init
            //    request in beforeEach (to avoid the dangling request) would put the view
            //    into "loaded" state too early and break those assertions.
            //
            // With sinon installed first, the init setPage(1) is captured as requests[0] —
            // a frozen pending fake request that never gets a response unless explicitly told
            // to. No response means no callbacks, no DOM mutation, view stays in "loading"
            // state. We advance currentIndex to 1 so AjaxHelpers treats that request as
            // already consumed; test-driven setPage() calls start at index 1. The pending
            // requests[0] is abandoned when xhrFactory.restore() runs in afterEach.
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };

            setFixtures($('<script>', {id: 'asset-library-tpl', type: 'text/template'}).text(assetLibraryTpl));
            appendSetFixtures($('<script>', {id: 'asset-tpl', type: 'text/template'}).text(assetTpl));
            appendSetFixtures(uploadModalTpl);
            appendSetFixtures(sandbox({id: 'asset_table_body'}));

            spyOn($.fn, 'fileupload').and.returnValue('');

            var TestAssetsCollection = AssetCollection.extend({
                state: {
                    firstPage: 0,
                    pageSize: 2
                }
            });
            var collection = new TestAssetsCollection();
            collection.url = 'assets-url';
            assetsView = new AssetsView({
                collection: collection,
                el: $('#asset_table_body')
            });
            // render() calls afterRender(), which binds the close-button click handler
            // against the upload modal already in the fixture DOM (from appendSetFixtures
            // above). render() itself issues no XHR (pagingView.render() is a no-op), so
            // requests[0] is still the init setPage from new AssetsView() above.
            assetsView.render();
            requests.currentIndex = 1;
        });

        afterEach(function() {
            requests.restore();
        });

        var mockAsset = {
            display_name: 'dummy.jpg',
            url: 'actual_asset_url',
            portable_url: 'portable_url',
            date_added: 'date',
            thumbnail: null,
            locked: false,
            id: 'id_1',
            static_full_url: 'static_full_url',
        };

        mockEmptyAssetsResponse = {
            assets: [],
            start: 0,
            end: 0,
            page: 0,
            pageSize: 2,
            totalCount: 0
        };

        var mockExampleAssetsResponse = {
            sort: 'uploadDate',
            end: 2,
            assets: [
                {
                    display_name: 'test.jpg',
                    url: '/c4x/A/CS102/asset/test.jpg',
                    date_added: 'Nov 07, 2014 at 17:47 UTC',
                    id: '/c4x/A/CS102/asset/test.jpg',
                    portable_url: '/static/test.jpg',
                    thumbnail: '/c4x/A/CS102/thumbnail/test.jpg',
                    locked: false,
                    external_url: 'localhost:8000/c4x/A/CS102/asset/test.jpg',
                    static_full_url: '/c4x/A/CS102/asset/test.jpg',
                },
                {
                    display_name: 'test.pdf',
                    url: '/c4x/A/CS102/asset/test.pdf',
                    date_added: 'Oct 20, 2014 at 11:00 UTC',
                    id: '/c4x/A/CS102/asset/test.pdf',
                    portable_url: '/static/test.pdf',
                    thumbnail: null,
                    locked: false,
                    external_url: 'localhost:8000/c4x/A/CS102/asset/test.pdf',
                    static_full_url: '/c4x/A/CS102/asset/test.pdf',
                },
                {
                    display_name: 'test.odt',
                    url: '/c4x/A/CS102/asset/test.odt',
                    date_added: 'Oct 20, 2014 at 11:00 UTC',
                    id: '/c4x/A/CS102/asset/test.odt',
                    portable_url: '/static/test.odt',
                    thumbnail: null,
                    locked: false,
                    external_url: 'localhost:8000/c4x/A/CS102/asset/test.odt',
                    static_full_url: '/c4x/A/CS102/asset/test.odt',
                }
            ],
            pageSize: 2,
            totalCount: 2,
            start: 0,
            page: 0
        };

        var mockExampleFilteredAssetsResponse = {
            sort: 'uploadDate',
            end: 1,
            assets: [
                {
                    display_name: 'test.jpg',
                    url: '/c4x/A/CS102/asset/test.jpg',
                    date_added: 'Nov 07, 2014 at 17:47 UTC',
                    id: '/c4x/A/CS102/asset/test.jpg',
                    portable_url: '/static/test.jpg',
                    thumbnail: '/c4x/A/CS102/thumbnail/test.jpg',
                    locked: false,
                    external_url: 'localhost:8000/c4x/A/CS102/asset/test.jpg',
                    static_full_url: '/c4x/A/CS102/asset/test.jpg'
                }
            ],
            pageSize: 1,
            totalCount: 1,
            start: 0,
            page: 0
        };

        mockAssetUploadResponse = {
            asset: mockAsset,
            msg: 'Upload completed'
        };

        mockFileUpload = {
            files: [{name: 'largefile', size: 0}]
        };

        var respondWithMockAssets = function(requests) {
            var request = AjaxHelpers.currentRequest(requests);
            var url = new URI(request.url);
            var queryParameters = url.query(true); // Returns an object with each query parameter stored as a value
            var asset_type = queryParameters.asset_type;
            var response = asset_type !== '' ? mockExampleFilteredAssetsResponse : mockExampleAssetsResponse;
            AjaxHelpers.respondWithJson(requests, response);
        };

        var event = {};
        event.target = {value: 'dummy.jpg'};

        describe('AssetsView', function() {
            var setup;
            setup = function(responseData) {
                assetsView.pagingView.setPage(1);
                if (!responseData) {
                    AjaxHelpers.respondWithJson(requests, mockEmptyAssetsResponse);
                } else {
                    AjaxHelpers.respondWithJson(requests, responseData);
                }
                return requests;
            };

            beforeEach(function() {
                ViewHelpers.installMockAnalytics();
            });

            afterEach(function() {
                ViewHelpers.removeMockAnalytics();
            });

            it('shows the upload modal when clicked on "Upload your first asset" button', function() {
                expect(assetsView).toBeDefined();
                appendSetFixtures('<div class="ui-loading"/>');
                expect($('.ui-loading').is(':visible')).toBe(true);
                expect($('.upload-button').is(':visible')).toBe(false);
                setup.call(this);
                expect($('.ui-loading').is(':visible')).toBe(false);
                expect($('.upload-button').is(':visible')).toBe(true);

                expect($('.upload-modal').is(':visible')).toBe(false);
                $('a:contains("Upload your first asset")').click();
                expect($('.upload-modal').is(':visible')).toBe(true);

                $('.close-button').click();
                expect($('.upload-modal').is(':visible')).toBe(false);
            });

            it('has properly initialized constants for handling upload file errors', function() {
                expect(assetsView).toBeDefined();
                expect(assetsView.uploadChunkSizeInMBs).toBeDefined();
                expect(assetsView.maxFileSizeInMBs).toBeDefined();
                expect(assetsView.uploadChunkSizeInBytes).toBeDefined();
                expect(assetsView.maxFileSizeInBytes).toBeDefined();
                expect(assetsView.largeFileErrorMsg).toBeNull();
            });

            it('uploads file properly', function() {
                var requests = setup.call(this);
                expect(assetsView).toBeDefined();
                spyOn(assetsView, 'addAsset').and.callFake(function() {
                    assetsView.collection.add(mockAssetUploadResponse.asset);
                    assetsView.pagingView.renderPageItems();
                    assetsView.pagingView.setPage(1);
                });

                $('a:contains("Upload your first asset")').click();
                expect($('.upload-modal').is(':visible')).toBe(true);

                $('.choose-file-button').click();
                $('input[type=file]').change();
                expect($('.upload-modal h1').text()).toContain('Uploading');

                assetsView.showUploadFeedback(event, 100);
                expect($('div.progress-bar').text()).toContain('100%');

                assetsView.displayFinishedUpload(mockAssetUploadResponse);
                expect($('div.progress-bar').text()).toContain('Upload completed');
                $('.close-button').click();
                expect($('.upload-modal').is(':visible')).toBe(false);

                expect($('#asset_table_body').html()).toContain('dummy.jpg');
                expect(assetsView.collection.length).toBe(1);
            });

            it('blocks file uploads larger than the max file size', function() {
                expect(assetsView).toBeDefined();

                mockFileUpload.files[0].size = assetsView.maxFileSize * 10;

                $('.choose-file-button').click();
                $('.upload-modal .file-chooser').fileupload('add', mockFileUpload);
                expect($('.upload-modal h1').text()).not.toContain('Uploading');

                expect(assetsView.largeFileErrorMsg).toBeDefined();
                expect($('div.progress-bar').text()).not.toContain('Upload completed');
                expect($('div.progress-fill').width()).toBe(0);
            });

            it('allows file uploads equal in size to the max file size', function() {
                expect(assetsView).toBeDefined();

                mockFileUpload.files[0].size = assetsView.maxFileSize;

                $('.choose-file-button').click();
                $('.upload-modal .file-chooser').fileupload('add', mockFileUpload);

                expect(assetsView.largeFileErrorMsg).toBeNull();
            });

            it('allows file uploads smaller than the max file size', function() {
                expect(assetsView).toBeDefined();

                mockFileUpload.files[0].size = assetsView.maxFileSize / 100;

                $('.choose-file-button').click();
                $('.upload-modal .file-chooser').fileupload('add', mockFileUpload);

                expect(assetsView.largeFileErrorMsg).toBeNull();
            });

            it('returns the registered info for a filter column', function() {
                assetsView.pagingView.registerSortableColumn('test-col', 'Test Column', 'testField', 'asc');
                assetsView.pagingView.registerFilterableColumn('js-asset-type-col', 'Type', 'asset_type');
                var filterInfo = assetsView.pagingView.filterableColumnInfo('js-asset-type-col');
                expect(filterInfo.displayName).toBe('Type');
                expect(filterInfo.fieldName).toBe('asset_type');
            });

            it('throws an exception for an unregistered filter column', function() {
                expect(function() {
                    assetsView.filterableColumnInfo('no-such-column');
                }).toThrow();
            });

            it('make sure selectFilter sets collection filter if undefined', function() {
                expect(assetsView).toBeDefined();
                assetsView.collection.filterField = '';
                assetsView.pagingView.selectFilter('js-asset-type-col');
                expect(assetsView.collection.filterField).toEqual('asset_type');
            });

            it('make sure _toggleFilterColumn filters asset list', function() {
                expect(assetsView).toBeDefined();
                $.each(assetsView.pagingView.filterableColumns, function(columnID, columnData) {
                    var $typeColumn = $('#' + columnID);
                    assetsView.pagingView.setPage(1);
                    respondWithMockAssets(requests);
                    var assetsNumber = assetsView.collection.length;
                    assetsView._toggleFilterColumn('Images', 'Images');
                    respondWithMockAssets(requests);
                    var assetsNumberFiltered = assetsView.collection.length;
                    expect(assetsNumberFiltered).toBeLessThan(assetsNumber);
                    expect($typeColumn.find('.title .type-filter')).not.toEqual(assetsView.allLabel);
                });
            });

            it('opens and closes select type menu', function() {
                expect(assetsView).toBeDefined();
                setup.call(this, mockExampleAssetsResponse);
                $.each(assetsView.pagingView.filterableColumns, function(columnID, columnData) {
                    var $typeColumn = $('#' + columnID);
                    expect($typeColumn).toBeVisible();
                    var assetsNumber = $('#asset-table-body .type-col').length;
                    assetsView.openFilterColumn($typeColumn);
                    expect($typeColumn.find('.wrapper-nav-sub')).toHaveClass('is-shown');
                    expect($typeColumn.find('.title')).toHaveClass('is-selected');
                    expect($typeColumn.find('.column-filter-link')).toBeVisible();
                    $typeColumn.find('.wrapper-nav-sub').trigger('click');
                    expect($typeColumn.find('.wrapper-nav-sub').hasClass('is-shown')).toBe(false);
                });
            });

            it('check filtering works with sorting by column on', function() {
                expect(assetsView).toBeDefined();
                assetsView.pagingView.registerSortableColumn('name-col', 'Name Column', 'nameField', 'asc');
                assetsView.pagingView.registerFilterableColumn('js-asset-type-col', gettext('Type'), 'asset_type');
                assetsView.pagingView.setInitialSortColumn('name-col');
                assetsView.pagingView.setPage(1);
                respondWithMockAssets(requests);
                var sortInfo = assetsView.pagingView.sortableColumnInfo('name-col');
                expect(sortInfo.defaultSortDirection).toBe('asc');
                var $firstFilter = $($('#js-asset-type-col').find('li.nav-item a')[1]);
                $firstFilter.trigger('click');
                respondWithMockAssets(requests);
                var assetsNumberFiltered = assetsView.collection.length;
                expect(assetsNumberFiltered).toBe(1);
            });

            it('shows type select menu, selects type, and filters results', function() {
                expect(assetsView).toBeDefined();
                $.each(assetsView.pagingView.filterableColumns, function(columnID, columnData) {
                    assetsView.pagingView.setPage(1);
                    respondWithMockAssets(requests);
                    var $typeColumn = $('#' + columnID);
                    expect($typeColumn).toBeVisible();
                    var assetsNumber = assetsView.collection.length;
                    $typeColumn.trigger('click');
                    expect($typeColumn.find('.wrapper-nav-sub')).toHaveClass('is-shown');
                    expect($typeColumn.find('.title')).toHaveClass('is-selected');
                    var $allFilter = $($typeColumn.find('li.nav-item a')[0]);
                    var $firstFilter = $($typeColumn.find('li.nav-item a')[1]);
                    var $otherFilter = $($typeColumn.find('li.nav-item a[data-assetfilter="OTHER"]')[0]);
                    var select_filter_and_check = function($filterEl, result) {
                        $filterEl.trigger('click');
                        respondWithMockAssets(requests);
                        var assetsNumberFiltered = assetsView.collection.length;
                        expect(assetsNumberFiltered).toBe(result);
                    };

                    select_filter_and_check($firstFilter, 1);
                    select_filter_and_check($allFilter, assetsNumber);
                    select_filter_and_check($otherFilter, 1);
                });
            });

            it('hides the error modal if a large file, then small file is uploaded', function() {
                expect(assetsView).toBeDefined();
                mockFileUpload.files[0].size = assetsView.maxFileSize;

                $('.choose-file-button').click();
                $('.upload-modal .file-chooser').fileupload('add', mockFileUpload);

                expect(assetsView.largeFileErrorMsg).toBeDefined();

                mockFileUpload.files[0].size = assetsView.maxFileSize / 10;
                $('.choose-file-button').click();
                $('.upload-modal .file-chooser').fileupload('add', mockFileUpload);
                expect(assetsView.largeFileErrorMsg).toBeNull();
            });

            describe('Paging footer', function() {
                var firstPageAssets = {
                        sort: 'uploadDate',
                        end: 1,
                        assets: [
                            {
                                display_name: 'test.jpg',
                                url: '/c4x/A/CS102/asset/test.jpg',
                                date_added: 'Nov 07, 2014 at 17:47 UTC',
                                id: '/c4x/A/CS102/asset/test.jpg',
                                portable_url: '/static/test.jpg',
                                thumbnail: '/c4x/A/CS102/thumbnail/test.jpg',
                                locked: false,
                                external_url: 'localhost:8000/c4x/A/CS102/asset/test.jpg',
                                static_full_url: '/c4x/A/CS102/asset/test.jpg'
                            },
                            {
                                display_name: 'test.pdf',
                                url: '/c4x/A/CS102/asset/test.pdf',
                                date_added: 'Oct 20, 2014 at 11:00 UTC',
                                id: '/c4x/A/CS102/asset/test.pdf',
                                portable_url: '/static/test.pdf',
                                thumbnail: null,
                                locked: false,
                                external_url: 'localhost:8000/c4x/A/CS102/asset/test.pdf',
                                static_full_url: '/c4x/A/CS102/asset/test.pdf',
                            }
                        ],
                        pageSize: 2,
                        totalCount: 3,
                        start: 0,
                        page: 0
                    },
                    secondPageAssets = {
                        sort: 'uploadDate',
                        end: 2,
                        assets: [
                            {
                                display_name: 'test.odt',
                                url: '/c4x/A/CS102/asset/test.odt',
                                date_added: 'Oct 20, 2014 at 11:00 UTC',
                                id: '/c4x/A/CS102/asset/test.odt',
                                portable_url: '/static/test.odt',
                                thumbnail: null,
                                locked: false,
                                external_url: 'localhost:8000/c4x/A/CS102/asset/test.odt',
                                static_full_url: '/c4x/A/CS102/asset/test.odt',
                            }
                        ],
                        pageSize: 2,
                        totalCount: 3,
                        start: 2,
                        page: 1
                    };

                it('can move forward a page using the next page button', function() {
                    assetsView.pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, firstPageAssets);
                    expect(assetsView.pagingView.pagingFooter).toBeDefined();
                    expect(assetsView.pagingView.pagingFooter.$('button.next-page-link'))
                        .not.toHaveClass('is-disabled');
                    assetsView.pagingView.pagingFooter.$('button.next-page-link').click();
                    AjaxHelpers.respondWithJson(requests, secondPageAssets);
                    expect(assetsView.pagingView.pagingFooter.$('button.next-page-link'))
                        .toHaveClass('is-disabled');
                });

                it('can move back a page using the previous page button', function() {
                    assetsView.pagingView.setPage(2);
                    AjaxHelpers.respondWithJson(requests, secondPageAssets);
                    expect(assetsView.pagingView.pagingFooter).toBeDefined();
                    expect(assetsView.pagingView.pagingFooter.$('button.previous-page-link'))
                        .not.toHaveClass('is-disabled');
                    assetsView.pagingView.pagingFooter.$('button.previous-page-link').click();
                    AjaxHelpers.respondWithJson(requests, firstPageAssets);
                    expect(assetsView.pagingView.pagingFooter.$('button.previous-page-link'))
                        .toHaveClass('is-disabled');
                });

                it('can set the current page using the page number input', function() {
                    assetsView.pagingView.setPage(1);
                    AjaxHelpers.respondWithJson(requests, firstPageAssets);
                    assetsView.pagingView.pagingFooter.$('#page-number-input').val('2');
                    assetsView.pagingView.pagingFooter.$('#page-number-input').trigger('change');
                    AjaxHelpers.respondWithJson(requests, secondPageAssets);
                    expect(assetsView.collection.getPageNumber()).toBe(2);
                    expect(assetsView.pagingView.pagingFooter.$('button.previous-page-link'))
                        .not.toHaveClass('is-disabled');
                });
            });
        });
    });
});
