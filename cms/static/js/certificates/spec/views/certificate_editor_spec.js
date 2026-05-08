// Jasmine Test Suite: Certifiate Editor View

define([
    'underscore',
    'sinon',
    'js/models/course',
    'js/certificates/models/certificate',
    'js/certificates/models/signatory',
    'js/certificates/collections/certificates',
    'js/certificates/views/certificate_editor',
    'common/js/components/views/feedback_notification',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers',
    'common/js/spec_helpers/view_helpers',
    'js/spec_helpers/validation_helpers',
    'js/certificates/spec/custom_matchers'
],
function(_, sinon, Course, CertificateModel, SignatoryModel, CertificatesCollection, CertificateEditorView,
    Notification, AjaxHelpers, TemplateHelpers, ViewHelpers, ValidationHelpers, CustomMatchers) {
    'use strict';

    var MAX_SIGNATORIES_LIMIT = 10;
    var requests, xhrFactory;
    var SELECTORS = {
        detailsView: '.certificate-details',
        editView: '.certificate-edit',
        itemView: '.certificates-list-item',
        name: '.certificate-name',
        description: '.certificate-description',
        errorMessage: '.certificate-edit-error',
        inputCertificateName: '.collection-name-input',
        inputCertificateDescription: '.certificate-description-input',
        inputSignatoryName: '.signatory-name-input',
        inputSignatoryTitle: '.signatory-title-input',
        inputSignatoryOrganization: '.signatory-organization-input',
        inputSignatorySignature: '.signatory-signature-input',
        warningMessage: '.certificate-validation-text',
        warningIcon: '.wrapper-certificate-validation > i',
        note: '.wrapper-delete-button',
        addSignatoryButton: '.action-add-signatory',
        signatoryDeleteButton: '.signatory-panel-delete',
        uploadSignatureButton: '.action-upload-signature',
        uploadDialog: 'form.upload-dialog',
        uploadDialogButton: '.action-upload',
        uploadDialogFileInput: 'form.upload-dialog input[type=file]',
        saveCertificateButton: 'button.action-primary'
    };

    var clickDeleteItem = function(that, promptText, element, url) {
        var promptSpy = ViewHelpers.createPromptSpy(),
            notificationSpy = ViewHelpers.createNotificationSpy();
        that.view.$(element).click();

        ViewHelpers.verifyPromptShowing(promptSpy, promptText);
        ViewHelpers.confirmPrompt(promptSpy);
        ViewHelpers.verifyPromptHidden(promptSpy);
        if (!_.isUndefined(url) && !_.isEmpty(url)) {
            AjaxHelpers.expectJsonRequest(requests, 'POST', url);
            expect(_.last(requests).requestHeaders['X-HTTP-Method-Override']).toBe('DELETE');
            ViewHelpers.verifyNotificationShowing(notificationSpy, /Deleting/);
            AjaxHelpers.respondWithNoContent(requests);
            ViewHelpers.verifyNotificationHidden(notificationSpy);
        }
    };

    var showConfirmPromptAndClickCancel = function(view, element, promptText) {
        var promptSpy = ViewHelpers.createPromptSpy();
        view.$(element).click();
        ViewHelpers.verifyPromptShowing(promptSpy, promptText);
        ViewHelpers.confirmPrompt(promptSpy, true);
        ViewHelpers.verifyPromptHidden(promptSpy);
    };

    var uploadFile = function(file_path, requests) {
        $(SELECTORS.uploadDialogFileInput).change();
        $(SELECTORS.uploadDialogButton).click();
        AjaxHelpers.respondWithJson(requests, {asset: {url: file_path}});
    };

    describe('Certificate editor view', function() {
        var setValuesToInputs = function(view, values) {
            _.each(values, function(value, selector) {
                if (SELECTORS[selector]) {
                    view.$(SELECTORS[selector]).val(value);
                    view.$(SELECTORS[selector]).trigger('change');
                }
            });
        };
        var basicModalTpl = readFixtures('basic-modal.underscore'),
            modalButtonTpl = readFixtures('modal-button.underscore'),
            uploadDialogTpl = readFixtures('upload-dialog.underscore');

        beforeEach(function() {
            TemplateHelpers.installTemplates(['certificate-editor', 'signatory-editor'], true);

            window.course = new Course({
                id: '5',
                name: 'Course Name',
                url_name: 'course_name',
                org: 'course_org',
                num: 'course_num',
                revision: 'course_rev'
            });
            window.CMS.User = {isGlobalStaff: true};

            this.newModelOptions = {add: true};
            this.model = new CertificateModel({
                name: 'Test Name',
                description: 'Test Description',
                is_active: true

            }, this.newModelOptions);

            this.collection = new CertificatesCollection([this.model], {
                certificateUrl: '/certificates/' + window.course.id
            });
            this.model.set('id', 0);
            this.view = new CertificateEditorView({
                model: this.model,
                max_signatories_limit: MAX_SIGNATORIES_LIMIT
            });
            appendSetFixtures(this.view.render().el);
            CustomMatchers();
        });

        afterEach(function() {
            delete window.course;
            delete window.CMS.User;
        });

        describe('Basic', function() {
            beforeEach(function() {
                xhrFactory = sinon.useFakeXMLHttpRequest();
                requests = [];
                requests.currentIndex = 0;
                requests.restore = function() { xhrFactory.restore(); };
                xhrFactory.onCreate = function(req) { requests.push(req); };
                appendSetFixtures(
                    $('<script>', {id: 'basic-modal-tpl', type: 'text/template'}).text(basicModalTpl)
                );
                appendSetFixtures(
                    $('<script>', {id: 'modal-button-tpl', type: 'text/template'}).text(modalButtonTpl)
                );
                appendSetFixtures(
                    $('<script>', {id: 'upload-dialog-tpl', type: 'text/template'}).text(uploadDialogTpl)
                );
            });

            afterEach(function() {
                requests.restore();
                $('.wrapper-modal-window-assetupload').remove();
            });

            it('can render properly', function() {
                expect(this.view.$("[name='certificate-name']").val()).toBe('Test Name');
                expect(this.view.$("[name='certificate-description']").val()).toBe('Test Description');
                expect(this.view.$('.action-delete')).toExist();
            });

            it('should not have delete button if user is not global staff and certificate is active', function() {
                window.CMS.User = {isGlobalStaff: false};
                appendSetFixtures(this.view.render().el);
                expect(this.view.$('.action-delete')).not.toExist();
            });

            it('should save properly', function() {
                var notificationSpy = ViewHelpers.createNotificationSpy();
                this.view.$('.action-add').click();

                setValuesToInputs(this.view, {
                    inputCertificateName: 'New Test Name',
                    inputCertificateDescription: 'New Test Description'
                });

                ViewHelpers.submitAndVerifyFormSuccess(this.view, requests, notificationSpy);
                expect(this.model).toBeCorrectValuesInModel({
                    name: 'New Test Name',
                    description: 'New Test Description'
                });
            });

            it('does not hide saving message if failure', function() {
                var notificationSpy = ViewHelpers.createNotificationSpy();
                this.view.$(SELECTORS.inputCertificateName).val('New Test Name');
                this.view.$(SELECTORS.inputCertificateDescription).val('New Test Description');
                ViewHelpers.submitAndVerifyFormError(this.view, requests, notificationSpy);
            });

            it('does not save on cancel', function() {
                // When we cancel the action, the model values should be reverted to original state
                // And the model should still be present in the collection
                expect(this.view.$('.action-add'));
                this.view.$('.action-add').click();
                this.view.$(SELECTORS.inputCertificateName).val('New Certificate');
                this.view.$(SELECTORS.inputCertificateDescription).val('New Certificate Description');

                this.view.$('.action-cancel').click();
                expect(this.model).toBeCorrectValuesInModel({
                    name: 'Test Name',
                    description: 'Test Description'
                });
                expect(this.collection.length).toBe(1);
            });

            it('user can only add signatories up to limit', function() {
                for (var i = 1; i < MAX_SIGNATORIES_LIMIT; i++) {
                    this.view.$(SELECTORS.addSignatoryButton).click();
                }
                expect(this.view.$(SELECTORS.addSignatoryButton)).toHaveClass('disableClick');
            });

            it('user can add signatories if not reached the upper limit', function() {
                spyOnEvent(SELECTORS.addSignatoryButton, 'click');
                this.view.$(SELECTORS.addSignatoryButton).click();
                expect('click').not.toHaveBeenPreventedOn(SELECTORS.addSignatoryButton);
                expect(this.view.$(SELECTORS.addSignatoryButton)).not.toHaveClass('disableClick');
            });

            it('user can add signatories when signatory reached the upper limit But after deleting a signatory',
                function() {
                    for (var i = 1; i < MAX_SIGNATORIES_LIMIT; i++) {
                        this.view.$(SELECTORS.addSignatoryButton).click();
                    }
                    expect(this.view.$(SELECTORS.addSignatoryButton)).toHaveClass('disableClick');

                    // now delete anyone of the signatory, Add signatory should be enabled.
                    var signatory = this.model.get('signatories').at(0);
                    var text = 'Delete "' + signatory.get('name') + '" from the list of signatories?';
                    clickDeleteItem(this, text, SELECTORS.signatoryDeleteButton + ':first');
                    expect(this.view.$(SELECTORS.addSignatoryButton)).not.toHaveClass('disableClick');
                }
            );

            it('signatories should save when fields have too many characters per line', function() {
                this.view.$(SELECTORS.addSignatoryButton).click();
                setValuesToInputs(this.view, {
                    inputCertificateName: 'New Certificate Name that has too many characters without any limit'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryName: 'New Signatory Name'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryTitle: 'This is a certificate signatory title that has waaaaaaay more than 106 characters, in order to cause an exception.'
                });

                this.view.$(SELECTORS.saveCertificateButton).click();
                expect(this.view.$('.certificate-edit-error')).not.toHaveClass('is-shown');
            });

            it('signatories should save when title span on more than 2 lines', function() {
                this.view.$(SELECTORS.addSignatoryButton).click();
                setValuesToInputs(this.view, {
                    inputCertificateName: 'New Certificate Name'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryName: 'New Signatory Name longer than 40 characters'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryTitle: 'Signatory Title \non three \nlines'
                });

                setValuesToInputs(this.view, {
                    inputSignatorySignature: '/c4x/edX/DemoX/asset/Signature-450.png'
                });

                this.view.$(SELECTORS.saveCertificateButton).click();
                expect(this.view.$('.certificate-edit-error')).not.toHaveClass('is-shown');
            });

            it('user can delete those signatories already saved', function() {
                this.view.$(SELECTORS.addSignatoryButton).click();
                var total_signatories = this.model.get('signatories').length;
                var signatory = this.model.get('signatories').at(0);
                var signatory_url = '/certificates/signatory';
                signatory.url = signatory_url;
                spyOn(signatory, 'isNew').and.returnValue(false);
                var text = 'Delete "' + signatory.get('name') + '" from the list of signatories?';
                clickDeleteItem(this, text, SELECTORS.signatoryDeleteButton + ':first', signatory_url);
                expect(this.model.get('signatories').length).toEqual(total_signatories - 1);
            });

            it('can cancel deletion of signatories', function() {
                this.view.$(SELECTORS.addSignatoryButton).click();
                var signatory = this.model.get('signatories').at(0);
                spyOn(signatory, 'isNew').and.returnValue(false);
                // add one more signatory
                this.view.$(SELECTORS.addSignatoryButton).click();
                var total_signatories = this.model.get('signatories').length;
                var signatory_url = '/certificates/signatory';
                signatory.url = signatory_url;
                var text = 'Delete "' + signatory.get('name') + '" from the list of signatories?';
                showConfirmPromptAndClickCancel(this.view, SELECTORS.signatoryDeleteButton + ':first', text);
                expect(this.model.get('signatories').length).toEqual(total_signatories);
            });

            it('signatories should save properly', function() {
                var notificationSpy = ViewHelpers.createNotificationSpy();
                this.view.$('.action-add').click();

                setValuesToInputs(this.view, {
                    inputCertificateName: 'New Test Name'
                });

                setValuesToInputs(this.view, {
                    inputCertificateDescription: 'New Test Description'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryName: 'New Signatory Name'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryTitle: 'New Signatory Title'
                });

                setValuesToInputs(this.view, {
                    inputSignatoryOrganization: 'New Signatory Organization'
                });

                this.view.$(SELECTORS.uploadSignatureButton).click();
                var sinature_image_path = '/c4x/edX/DemoX/asset/Signature-450.png';
                uploadFile(sinature_image_path, requests);

                ViewHelpers.submitAndVerifyFormSuccess(this.view, requests, notificationSpy);
                expect(this.model).toBeCorrectValuesInModel({
                    name: 'New Test Name',
                    description: 'New Test Description'
                });

                // get the first signatory from the signatories collection.
                var signatory = this.model.get('signatories').at(0);
                expect(signatory).toBeInstanceOf(SignatoryModel);
                expect(signatory.get('name')).toEqual('New Signatory Name');
                expect(signatory.get('title')).toEqual('New Signatory Title');
                expect(signatory.get('organization')).toEqual('New Signatory Organization');
                expect(signatory.get('signature_image_path')).toEqual(sinature_image_path);
            });
        });
    });
});
