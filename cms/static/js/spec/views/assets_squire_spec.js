define(["jquery", "sinon", "edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers", "squire"],
function($, sinon, AjaxHelpers, Squire) {

    const assetLibraryTpl = readFixtures('asset-library.underscore');
    const assetTpl = readFixtures('asset.underscore');

    describe("Asset view", function() {
        var requests, xhrFactory;

        beforeEach(function(done) {
            setFixtures($("<script>", {id: "asset-tpl", type: "text/template"}).text(assetTpl));
            appendSetFixtures(sandbox({id: "page-prompt"}));

            this.promptSpies = jasmine.createSpyObj('Prompt.Warning', ["constructor", "show", "hide"]);
            this.promptSpies.constructor.and.returnValue(this.promptSpies);
            this.promptSpies.show.and.returnValue(this.promptSpies);

            this.confirmationSpies = jasmine.createSpyObj('Notification.Confirmation', ["constructor", "show"]);
            this.confirmationSpies.constructor.and.returnValue(this.confirmationSpies);
            this.confirmationSpies.show.and.returnValue(this.confirmationSpies);

            this.savingSpies = jasmine.createSpyObj('Notification.Mini', ["constructor", "show", "hide"]);
            this.savingSpies.constructor.and.returnValue(this.savingSpies);
            this.savingSpies.show.and.returnValue(this.savingSpies);

            this.injector = new Squire();
            this.injector.mock("common/js/components/views/feedback_prompt", {
                "Warning": this.promptSpies.constructor
            });
            this.injector.mock("common/js/components/views/feedback_notification", {
                "Confirmation": this.confirmationSpies.constructor,
                "Mini": this.savingSpies.constructor
            });

            this.injector.require(["js/models/asset", "js/collections/asset", "js/views/asset"],
                (AssetModel, AssetCollection, AssetView) => {
                    this.model = new AssetModel({
                        display_name: "test asset",
                        url: 'actual_asset_url',
                        portable_url: 'portable_url',
                        date_added: 'date',
                        thumbnail: null,
                        id: 'id',
                        static_full_url: 'static_full_url',
                    });
                    spyOn(this.model, "destroy").and.callThrough();
                    spyOn(this.model, "save").and.callThrough();

                    this.collection = new AssetCollection([this.model]);
                    this.collection.url = "assets-url";
                    this.createAssetView = () => {
                        const view = new AssetView({model: this.model});
                        return {view};
                    };
                    return done();
            });
        });

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
            this.injector.clean();
            this.injector.remove();
        });

        describe("Basic", function() {
            it("should render properly", function() {
                this.view = this.createAssetView().view;
                this.view.render();
                expect(this.view.$el).toContainText("test asset");
            });

            it("should pop a delete confirmation when the delete button is clicked", function() {
                this.view = this.createAssetView().view;
                this.view.render().$(".remove-asset-button").click();
                expect(this.promptSpies.constructor).toHaveBeenCalled();
                const ctorOptions = this.promptSpies.constructor.calls.mostRecent().args[0];
                expect(ctorOptions.title).toMatch('Delete File Confirmation');
                // hasn't actually been removed
                expect(this.model.destroy).not.toHaveBeenCalled();
                expect(this.collection).toContain(this.model);
            });
        });

        describe("AJAX", function() {
            it("should destroy itself on confirmation", function() {
                this.view = this.createAssetView().view;

                this.view.render().$(".remove-asset-button").click();
                const ctorOptions = this.promptSpies.constructor.calls.mostRecent().args[0];
                // run the primary function to indicate confirmation
                ctorOptions.actions.primary.click(this.promptSpies);
                // AJAX request has been sent, but not yet returned
                expect(this.model.destroy).toHaveBeenCalled();
                expect(requests.length).toEqual(1);
                expect(this.confirmationSpies.constructor).not.toHaveBeenCalled();
                expect(this.collection.contains(this.model)).toBeTruthy();
                // return a success response
                requests[0].respond(204);
                expect(this.confirmationSpies.constructor).toHaveBeenCalled();
                expect(this.confirmationSpies.show).toHaveBeenCalled();
                const savingOptions = this.confirmationSpies.constructor.calls.mostRecent().args[0];
                expect(savingOptions.title).toMatch("Your file has been deleted.");
                expect(this.collection.contains(this.model)).toBeFalsy();
            });

            it("should not destroy itself if server errors", function() {
                this.view = this.createAssetView().view;

                this.view.render().$(".remove-asset-button").click();
                const ctorOptions = this.promptSpies.constructor.calls.mostRecent().args[0];
                // run the primary function to indicate confirmation
                ctorOptions.actions.primary.click(this.promptSpies);
                // AJAX request has been sent, but not yet returned
                expect(this.model.destroy).toHaveBeenCalled();
                // return an error response
                requests[0].respond(404);
                expect(this.confirmationSpies.constructor).not.toHaveBeenCalled();
                expect(this.collection.contains(this.model)).toBeTruthy();
            });

            it("should lock the asset on confirmation", function() {
                this.view = this.createAssetView().view;

                this.view.render().$(".lock-checkbox").click();
                // AJAX request has been sent, but not yet returned
                expect(this.model.save).toHaveBeenCalled();
                expect(requests.length).toEqual(1);
                expect(this.savingSpies.constructor).toHaveBeenCalled();
                expect(this.savingSpies.show).toHaveBeenCalled();
                const savingOptions = this.savingSpies.constructor.calls.mostRecent().args[0];
                expect(savingOptions.title).toMatch("Saving");
                expect(this.model.get("locked")).toBeFalsy();
                // return a success response
                requests[0].respond(204);
                expect(this.savingSpies.hide).toHaveBeenCalled();
                expect(this.model.get("locked")).toBeTruthy();
            });

            it("should not lock the asset if server errors", function() {
                this.view = this.createAssetView().view;

                this.view.render().$(".lock-checkbox").click();
                // return an error response
                requests[0].respond(404);
                // Don't call hide because that closes the notification showing the server error.
                expect(this.savingSpies.hide).not.toHaveBeenCalled();
                expect(this.model.get("locked")).toBeFalsy();
            });
        });
    });

    describe("Assets view", function() {
        var requests, xhrFactory;

        beforeEach(function(done) {
            setFixtures($("<script>", {id: "asset-library-tpl", type: "text/template"}).text(assetLibraryTpl));
            appendSetFixtures($("<script>", {id: "asset-tpl", type: "text/template"}).text(assetTpl));
            window.analytics = jasmine.createSpyObj('analytics', ['track']);
            window.course_location_analytics = jasmine.createSpy();
            appendSetFixtures(sandbox({id: "asset_table_body"}));

            this.promptSpies = jasmine.createSpyObj('Prompt.Warning', ["constructor", "show", "hide"]);
            this.promptSpies.constructor.and.returnValue(this.promptSpies);
            this.promptSpies.show.and.returnValue(this.promptSpies);

            this.injector = new Squire();
            this.injector.mock("common/js/components/views/feedback_prompt", {
                "Warning": this.promptSpies.constructor
            });

            this.mockAsset1 = {
                display_name: "test asset 1",
                url: 'actual_asset_url_1',
                portable_url: 'portable_url_1',
                date_added: 'date_1',
                thumbnail: null,
                id: 'id_1',
                static_full_url: 'static_full_url_1',
            };
            this.mockAsset2 = {
                display_name: "test asset 2",
                url: 'actual_asset_url_2',
                portable_url: 'portable_url_2',
                date_added: 'date_2',
                thumbnail: null,
                id: 'id_2',
                static_full_url: 'static_full_url_2',
            };
            this.mockAssetsResponse = {
                assets: [ this.mockAsset1, this.mockAsset2 ],
                start: 0,
                end: 1,
                page: 0,
                pageSize: 5,
                totalCount: 2
            };

            this.injector.require(["js/models/asset", "js/collections/asset", "js/views/assets"],
                (AssetModel, AssetCollection, AssetsView) => {
                    this.AssetModel = AssetModel;
                    this.collection = new AssetCollection();
                    this.collection.url = "assets-url";
                    this.createAssetsView = () => {
                        const view = new AssetsView({
                            collection: this.collection,
                            el: $('#asset_table_body')
                        });
                        view.render();
                        return {view};
                    };
                    return done();
            });

            return $.ajax();
        });

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
            delete window.analytics;
            delete window.course_location_analytics;

            this.injector.clean();
            this.injector.remove();
        });

        const addMockAsset = function(requests) {
            const model = new this.AssetModel({
                display_name: "new asset",
                url: 'new_actual_asset_url',
                portable_url: 'portable_url',
                date_added: 'date',
                thumbnail: null,
                id: 'idx',
                static_full_url: 'static_full_url',
            });
            this.view.addAsset(model);
            return AjaxHelpers.respondWithJson(requests,
                {
                    assets: [
                        this.mockAsset1, this.mockAsset2,
                        {
                            display_name: "new asset",
                            url: 'new_actual_asset_url',
                            portable_url: 'portable_url',
                            date_added: 'date',
                            thumbnail: null,
                            id: 'idx',
                            static_full_url: 'static_full_url',
                        }
                    ],
                    start: 0,
                    end: 2,
                    page: 0,
                    pageSize: 5,
                    totalCount: 3
                });
        };


        describe("Basic", function() {
            // Separate setup method to work-around mis-parenting of beforeEach methods
            const setup = function(requests) {
                this.view.pagingView.setPage(1);
                return AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
            };

            $.fn.fileupload = () => '';

            const clickEvent = html_selector => $(html_selector).click();

            it("should show upload modal on clicking upload asset button", function() {
                this.view = this.createAssetsView().view;
                spyOn(this.view, "showUploadModal");
                setup.call(this, requests);
                expect(this.view.showUploadModal).not.toHaveBeenCalled();
                this.view.showUploadModal(clickEvent(".upload-button"));
                expect(this.view.showUploadModal).toHaveBeenCalled();
            });

            it("should show file selection menu on choose file button", function() {
                this.view = this.createAssetsView().view;
                spyOn(this.view, "showFileSelectionMenu");
                setup.call(this, requests);
                expect(this.view.showFileSelectionMenu).not.toHaveBeenCalled();
                this.view.showFileSelectionMenu(clickEvent(".choose-file-button"));
                expect(this.view.showFileSelectionMenu).toHaveBeenCalled();
            });

            it("should hide upload modal on clicking close button", function() {
                this.view = this.createAssetsView().view;
                spyOn(this.view, "hideModal");
                setup.call(this, requests);
                expect(this.view.hideModal).not.toHaveBeenCalled();
                this.view.hideModal(clickEvent(".close-button"));
                expect(this.view.hideModal).toHaveBeenCalled();
            });

            it("should show a status indicator while loading", function() {
                this.view = this.createAssetsView().view;
                appendSetFixtures('<div class="ui-loading"/>');
                expect($('.ui-loading').is(':visible')).toBe(true);
                setup.call(this, requests);
                expect($('.ui-loading').is(':visible')).toBe(false);
            });

            it("should hide the status indicator if an error occurs while loading", function() {
                this.view = this.createAssetsView().view;
                appendSetFixtures('<div class="ui-loading"/>');
                expect($('.ui-loading').is(':visible')).toBe(true);
                this.view.pagingView.setPage(1);
                AjaxHelpers.respondWithError(requests);
                expect($('.ui-loading').is(':visible')).toBe(false);
            });

            it("should render both assets", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                expect(this.view.$el).toContainText("test asset 1");
                expect(this.view.$el).toContainText("test asset 2");
            });

            it("should remove the deleted asset from the view", function() {
                this.view = this.createAssetsView().view;
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                setup.call(this, requests);
                // Delete the 2nd asset with success from server.
                this.view.$(".remove-asset-button")[1].click();
                this.promptSpies.constructor.calls.mostRecent().args[0].actions.primary.click(this.promptSpies);
                AjaxHelpers.respondWithNoContent(requests);
                expect(this.view.$el).toContainText("test asset 1");
                expect(this.view.$el).not.toContainText("test asset 2");
            });

            it("does not remove asset if deletion failed", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                // Delete the 2nd asset, but mimic a failure from the server.
                this.view.$(".remove-asset-button")[1].click();
                this.promptSpies.constructor.calls.mostRecent().args[0].actions.primary.click(this.promptSpies);
                AjaxHelpers.respondWithError(requests);
                expect(this.view.$el).toContainText("test asset 1");
                expect(this.view.$el).toContainText("test asset 2");
            });

            it("adds an asset if asset does not already exist", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                addMockAsset.call(this, requests);
                expect(this.view.$el).toContainText("new asset");
                expect(this.collection.models.length).toBe(3);
            });

            it("does not add an asset if asset already exists", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                spyOn(this.collection, "add").and.callThrough();
                const model = this.collection.models[1];
                this.view.addAsset(model);
                expect(this.collection.add).not.toHaveBeenCalled();
            });
        });

        describe("Sorting", function() {
            // Separate setup method to work-around mis-parenting of beforeEach methods
            const setup = function(requests) {
                this.view.pagingView.setPage(1);
                return AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
            };

            it("should have the correct default sort order", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                expect(this.view.pagingView.sortDisplayName()).toBe("Date Added");
                expect(this.view.collection.sortDirection).toBe("desc");
            });

            it("should toggle the sort order when clicking on the currently sorted column", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                expect(this.view.pagingView.sortDisplayName()).toBe("Date Added");
                expect(this.view.collection.sortDirection).toBe("desc");
                this.view.$("#js-asset-date-col").click();
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                expect(this.view.pagingView.sortDisplayName()).toBe("Date Added");
                expect(this.view.collection.sortDirection).toBe("asc");
                this.view.$("#js-asset-date-col").click();
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                expect(this.view.pagingView.sortDisplayName()).toBe("Date Added");
                expect(this.view.collection.sortDirection).toBe("desc");
            });

            it("should switch the sort order when clicking on a different column", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                this.view.$("#js-asset-name-col").click();
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                expect(this.view.pagingView.sortDisplayName()).toBe("Name");
                expect(this.view.collection.sortDirection).toBe("asc");
                this.view.$("#js-asset-name-col").click();
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                expect(this.view.pagingView.sortDisplayName()).toBe("Name");
                expect(this.view.collection.sortDirection).toBe("desc");
            });

            it("should switch sort to most recent date added when a new asset is added", function() {
                this.view = this.createAssetsView().view;
                setup.call(this, requests);
                this.view.$("#js-asset-name-col").click();
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                addMockAsset.call(this, requests);
                AjaxHelpers.respondWithJson(requests, this.mockAssetsResponse);
                expect(this.view.pagingView.sortDisplayName()).toBe("Date Added");
                expect(this.view.collection.sortDirection).toBe("desc");
            });
        });
    });
});
