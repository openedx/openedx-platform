define(["underscore", "sinon", "js/models/uploads", "js/views/uploads",
        "edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers", "js/spec_helpers/modal_helpers"],
    (_, sinon, FileUpload, UploadDialog, AjaxHelpers, modal_helpers) =>

        describe("UploadDialog", function() {
            const tpl = readFixtures("upload-dialog.underscore"),
                uploadData = {
                    edx_video_id: '123-456-789-0',
                    language_code: 'en',
                    new_language_code: 'ur'
                };

            beforeEach(function() {
                let dialogResponse;
                modal_helpers.installModalTemplates();
                appendSetFixtures($("<script>", {id: "upload-dialog-tpl", type: "text/template"}).text(tpl));
                CMS.URL.UPLOAD_ASSET = "/upload";
                this.model = new FileUpload({
                    mimeTypes: ['application/pdf']
                });
                this.dialogResponse = (dialogResponse = []);
                this.mockFiles = [];});

            afterEach(function() {
                delete CMS.URL.UPLOAD_ASSET;
                modal_helpers.cancelModalIfShowing();
            });

            const createTestView = function(test) {
                const view = new UploadDialog({
                    model: test.model,
                    url:  CMS.URL.UPLOAD_ASSET,
                    onSuccess: response => {
                        return test.dialogResponse.push(response.response);
                    },
                    uploadData: uploadData
                });
                spyOn(view, 'remove').and.callThrough();

                // create mock file input, so that we aren't subject to browser restrictions
                const mockFileInput = jasmine.createSpy('mockFileInput');
                mockFileInput.files = test.mockFiles;
                const jqMockFileInput = jasmine.createSpyObj('jqMockFileInput', ['get', 'replaceWith']);
                jqMockFileInput.get.and.returnValue(mockFileInput);
                const originalView$ = view.$;
                spyOn($.fn, 'ajaxSubmit').and.callThrough();
                spyOn(view, "$").and.callFake(function(selector) {
                    if (selector === "input[type=file]") {
                        return jqMockFileInput;
                    } else {
                        return originalView$.apply(this, arguments);
                    }
                });
                this.lastView = view;
                return view;
            };

            describe("Basic", function() {
                it("should render without a file selected", function() {
                    const view = createTestView(this);
                    view.render();
                    expect(view.$el).toContainElement("input[type=file]");
                    expect(view.$(".action-upload")).toHaveClass("disabled");
                });

                it("should render with a PDF selected", function() {
                    const view = createTestView(this);
                    const file = {name: "fake.pdf", "type": "application/pdf"};
                    this.mockFiles.push(file);
                    this.model.set("selectedFile", file);
                    view.render();
                    expect(view.$el).toContainElement("input[type=file]");
                    expect(view.$el).not.toContainElement("#upload_error");
                    expect(view.$(".action-upload")).not.toHaveClass("disabled");
                });

                it("should render an error with an invalid file type selected", function() {
                    const view = createTestView(this);
                    const file = {name: "fake.png", "type": "image/png"};
                    this.mockFiles.push(file);
                    this.model.set("selectedFile", file);
                    view.render();
                    expect(view.$el).toContainElement("input[type=file]");
                    expect(view.$el).toContainElement("#upload_error");
                    expect(view.$(".action-upload")).toHaveClass("disabled");
                });

                it("should render an error with an invalid file type after a correct file type selected", function() {
                    const view = createTestView(this);
                    const correctFile = {name: "fake.pdf", "type": "application/pdf"};
                    const inCorrectFile = {name: "fake.png", "type": "image/png"};
                    const event = {};
                    view.render();

                    event.target = {"files": [correctFile]};
                    view.selectFile(event);
                    expect(view.$el).toContainElement("input[type=file]");
                    expect(view.$el).not.toContainElement("#upload_error");
                    expect(view.$(".action-upload")).not.toHaveClass("disabled");

                    const realMethod = this.model.set;
                    spyOn(this.model, "set").and.callFake(function(data) {
                        if (data.selectedFile !== undefined) {
                            this.attributes.selectedFile = data.selectedFile;
                            return this.changed = {};
                        } else {
                            return realMethod.apply(this, arguments);
                        }
                    });

                    event.target = {"files": [inCorrectFile]};
                    view.selectFile(event);
                    expect(view.$el).toContainElement("input[type=file]");
                    expect(view.$el).toContainElement("#upload_error");
                    expect(view.$(".action-upload")).toHaveClass("disabled");
                });
            });

            describe("Uploads", function() {
                var requests, xhrFactory;
                beforeEach(function() {
                    xhrFactory = sinon.useFakeXMLHttpRequest();
                    requests = [];
                    requests.currentIndex = 0;
                    requests.restore = function() { xhrFactory.restore(); };
                    xhrFactory.onCreate = function(req) { requests.push(req); };
                    this.clock = sinon.useFakeTimers();
                });

                afterEach(function() {
                    requests.restore();
                    modal_helpers.cancelModalIfShowing();
                    this.clock.restore();
                });

                it("can upload correctly", function() {
                    const view = createTestView(this);
                    view.render();
                    view.upload();
                    expect(this.model.get("uploading")).toBeTruthy();
                    AjaxHelpers.expectRequest(requests, "POST", "/upload");
                    expect($.fn.ajaxSubmit.calls.mostRecent().args[0].data).toEqual(
                        _.extend({}, uploadData, {notifyOnError: false})
                    );
                    AjaxHelpers.respondWithJson(requests, { response: "dummy_response"});
                    expect(this.model.get("uploading")).toBeFalsy();
                    expect(this.model.get("finished")).toBeTruthy();
                    expect(this.dialogResponse.pop()).toEqual("dummy_response");
                });

                it("can handle upload errors", function() {
                    const view = createTestView(this);
                    view.render();
                    view.upload();
                    AjaxHelpers.respondWithError(requests);
                    expect(this.model.get("title")).toMatch(/error/);
                    expect(view.remove).not.toHaveBeenCalled();
                });

                it("removes itself after two seconds on successful upload", function() {
                    const view = createTestView(this);
                    view.render();
                    view.upload();
                    AjaxHelpers.respondWithJson(requests, { response: "dummy_response"});
                    expect(modal_helpers.isShowingModal(view)).toBeTruthy();
                    this.clock.tick(2001);
                    expect(modal_helpers.isShowingModal(view)).toBeFalsy();
                });
            });
        })
);
