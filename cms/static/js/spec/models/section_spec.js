/*
 * decaffeinate suggestions:
 * DS102: Remove unnecessary code created because of implicit returns
 * Full docs: https://github.com/decaffeinate/decaffeinate/blob/master/docs/suggestions.md
 */
define(["js/models/section", "sinon", "edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers", "js/utils/module"], (Section, sinon, AjaxHelpers, ModuleUtils) =>
    describe("Section", function() {
        describe("basic", function() {
            beforeEach(function() {
                this.model = new Section({
                    id: 42,
                    name: "Life, the Universe, and Everything"
                });
            });

            it("should take an id argument", function() {
                expect(this.model.get("id")).toEqual(42);
            });

            it("should take a name argument", function() {
                expect(this.model.get("name")).toEqual("Life, the Universe, and Everything");
            });

            it("should have a URL set", function() {
                expect(this.model.url()).toEqual(ModuleUtils.getUpdateUrl(42));
            });

            it("should serialize to JSON correctly", function() {
                expect(this.model.toJSON()).toEqual({
                metadata:
                    {
                    display_name: "Life, the Universe, and Everything"
                    }
                });
            });
        });

        describe("XHR", function() {
            var server;
            beforeEach(function() {
                server = sinon.fakeServer.create();
                spyOn(Section.prototype, 'showNotification');
                spyOn(Section.prototype, 'hideNotification');
                this.model = new Section({
                    id: 42,
                    name: "Life, the Universe, and Everything"
                });
            });

            afterEach(function() {
                server.restore();
            });

            it("show/hide a notification when it saves to the server", function() {
                server.respondWith([200, {"Content-Type": "application/json"}, "{}"]);
                this.model.save();
                expect(Section.prototype.showNotification).toHaveBeenCalled();
                server.respond();
                expect(Section.prototype.hideNotification).toHaveBeenCalled();
            });

            it("don't hide notification when saving fails", function() {
                // this is handled by the global AJAX error handler
                server.respondWith([500, {"Content-Type": "application/json"}, "{}"]);
                this.model.save();
                server.respond();
                expect(Section.prototype.hideNotification).not.toHaveBeenCalled();
            });
        });
    })
);
