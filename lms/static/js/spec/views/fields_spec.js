define(['backbone', 'jquery', 'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers', 'js/views/fields', 'js/spec/views/fields_helpers',
    'string_utils'],
function(Backbone, $, _, sinon, AjaxHelpers, TemplateHelpers, FieldViews, FieldViewsSpecHelpers) {
    'use strict';

    var USERNAME = 'Legolas',
        BIO = "My Name is Theon Greyjoy. I'm member of House Greyjoy";

    describe('edx.FieldViews', function() {
        var requests,
            xhrFactory,
            timerCallback,
            dropdownSelectClass = '.u-field-value > select',
            dropdownButtonClass = '.u-field-value > button',
            textareaLinkClass = '.u-field-value .clickable';

        var fieldViewClasses = [
            FieldViews.ReadonlyFieldView,
            FieldViews.TextFieldView,
            FieldViews.DropdownFieldView,
            FieldViews.LinkFieldView,
            FieldViews.TextareaFieldView
        ];

        beforeEach(function() {
            timerCallback = jasmine.createSpy('timerCallback');
            jasmine.clock().install();
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            jasmine.clock().uninstall();
            requests.restore();
        });

        it('updates messages correctly for all fields', function() {
            for (var i = 0; i < fieldViewClasses.length; i++) {
                var fieldViewClass = fieldViewClasses[i];
                var fieldData = FieldViewsSpecHelpers.createFieldData(fieldViewClass, {
                    title: 'Username',
                    valueAttribute: 'username',
                    helpMessage: 'The username that you use to sign in to edX.'
                });

                var view = new fieldViewClass(fieldData).render();
                FieldViewsSpecHelpers.verifyMessageUpdates(view, fieldData, timerCallback);
            }
        });

        it('resets to help message some time after success message is set', function() {
            for (var i = 0; i < fieldViewClasses.length; i++) {
                var fieldViewClass = fieldViewClasses[i];
                var fieldData = FieldViewsSpecHelpers.createFieldData(fieldViewClass, {
                    title: 'Username',
                    valueAttribute: 'username',
                    helpMessage: 'The username that you use to sign in to edX.'
                });

                var view = new fieldViewClass(fieldData).render();
                FieldViewsSpecHelpers.verifySuccessMessageReset(view, fieldData, timerCallback);
            }
        });

        it('sends a PATCH request when saveAttributes is called', function() {

            var fieldViewClass = FieldViews.EditableFieldView;
            var fieldData = FieldViewsSpecHelpers.createFieldData(fieldViewClass, {
                title: 'Preferred Language',
                valueAttribute: 'language',
                helpMessage: 'Your preferred language.',
                persistChanges: true
            });

            var view = new fieldViewClass(fieldData);
            view.saveAttributes(
                {language: 'ur'},
                {headers: {Priority: 'Urgent'}}
            );

            var request = requests[0];
            expect(request.method).toBe('PATCH');
            expect(request.requestHeaders['Content-Type']).toBe('application/merge-patch+json;charset=utf-8');
            expect(request.requestHeaders.Priority).toBe('Urgent');
            expect(request.requestBody).toBe('{"language":"ur"}');
        });

        it('correctly renders and updates ReadonlyFieldView', function() {
            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.ReadonlyFieldView, {
                title: 'Username',
                valueAttribute: 'username',
                helpMessage: 'The username that you use to sign in to edX.'
            });
            var view = new FieldViews.ReadonlyFieldView(fieldData).render();

            FieldViewsSpecHelpers.expectTitleAndMessageToContain(view, fieldData.title, fieldData.helpMessage);
            expect(view.fieldValue()).toBe(USERNAME);

            view.model.set({username: 'bookworm'});
            expect(view.fieldValue()).toBe('bookworm');
        });

        it('correctly renders, updates and persists changes to TextFieldView when editable == always', function() {

            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.TextFieldView, {
                title: 'Full Name',
                valueAttribute: 'name',
                helpMessage: 'How are you?',
                persistChanges: true
            });
            var view = new FieldViews.TextFieldView(fieldData).render();

            FieldViewsSpecHelpers.verifyTextField(view, {
                title: fieldData.title,
                valueAttribute: fieldData.valueAttribute,
                helpMessage: fieldData.helpMessage,
                validValue: 'My Name',
                invalidValue1: 'Your Name',
                invalidValue2: 'Her Name',
                validationError: 'Think again!',
                defaultValue: ''
            }, requests);
        });

        it('correctly renders and updates DropdownFieldView when editable == never', function() {

            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.DropdownFieldView, {
                title: 'Full Name',
                valueAttribute: 'name',
                helpMessage: 'edX full name',
                editable: 'never',
                persistChanges: true

            });
            var view = new FieldViews.DropdownFieldView(fieldData).render();
            var readOnlyDisplayClass = '.u-field-value-readonly';

            FieldViewsSpecHelpers.expectDropdownSrTitleToContain(view, fieldData.title);
            FieldViewsSpecHelpers.expectMessageContains(view, fieldData.helpMessage);
            expect(view.el).toHaveClass('mode-hidden');
            // Note that "name" will be retrieved from the model, but the options specified are
            // the languages options. Therefore initially the placeholder message will be shown because
            // the model value does not match any of the possible options.
            expect(view.fieldValue()).toBeNull();
            expect(view.$(readOnlyDisplayClass).text()).toBe(fieldData.placeholderValue);
            // Make sure that the select and the button are not in the HTML.
            expect(view.$(dropdownSelectClass).length).toBe(0);
            expect(view.$(dropdownButtonClass).length).toBe(0);

            view.model.set({name: fieldData.options[1][0]});
            expect(view.el).toHaveClass('mode-display');
            expect(view.fieldValue()).toBe(fieldData.options[1][0]);
            expect(view.$(readOnlyDisplayClass).text()).toBe(fieldData.options[1][1]);

            view.$el.click();
            expect(view.el).toHaveClass('mode-display');
            // Make sure that the select and the button still are not in the HTML.
            expect(view.$(dropdownSelectClass).length).toBe(0);
            expect(view.$(dropdownButtonClass).length).toBe(0);
        });

        it('correctly renders, updates and persists changes to DropdownFieldView when editable == always', function() {

            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.DropdownFieldView, {
                title: 'Full Name',
                valueAttribute: 'name',
                helpMessage: 'edX full name',
                persistChanges: true
            });
            var view = new FieldViews.DropdownFieldView(fieldData).render();

            FieldViewsSpecHelpers.verifyDropDownField(view, {
                title: fieldData.title,
                valueAttribute: fieldData.valueAttribute,
                helpMessage: fieldData.helpMessage,
                validValue: FieldViewsSpecHelpers.SELECT_OPTIONS[0][0],
                invalidValue1: FieldViewsSpecHelpers.SELECT_OPTIONS[1][0],
                invalidValue2: FieldViewsSpecHelpers.SELECT_OPTIONS[2][0],
                validationError: 'Nope, this will not do!',
                defaultValue: null
            }, requests);
        });

        it('correctly renders, updates and persists changes to DropdownFieldView when editable == toggle', function() {

            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.DropdownFieldView, {
                title: 'Full Name',
                valueAttribute: 'name',
                helpMessage: 'edX full name',
                editable: 'toggle',
                persistChanges: true
            });
            var view = new FieldViews.DropdownFieldView(fieldData).render();

            FieldViewsSpecHelpers.verifyDropDownField(view, {
                title: fieldData.title,
                valueAttribute: fieldData.valueAttribute,
                helpMessage: fieldData.helpMessage,
                editable: 'toggle',
                validValue: FieldViewsSpecHelpers.SELECT_OPTIONS[0][0],
                invalidValue1: FieldViewsSpecHelpers.SELECT_OPTIONS[1][0],
                invalidValue2: FieldViewsSpecHelpers.SELECT_OPTIONS[2][0],
                validationError: 'Nope, this will not do!',
                defaultValue: null
            }, requests);
        });

        it('only shows empty option in DropdownFieldView if required is false or model value is not set', function() {

            var editableOptions = ['toggle', 'always'];
            _.each(editableOptions, function(editable) {
                var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.DropdownFieldView, {
                    title: 'Drop Down Field',
                    valueAttribute: 'drop-down',
                    helpMessage: 'edX drop down',
                    editable: editable,
                    required: true,
                    persistChanges: true
                });
                var view = new FieldViews.DropdownFieldView(fieldData).render();

                expect(view.modelValueIsSet()).toBe(false);
                expect(view.displayValue()).toBe('');

                if (editable === 'toggle') {
                    expect(view.$(dropdownButtonClass).length).toBe(1);
                    view.showEditMode(true);
                }
                expect(view.$(dropdownSelectClass).length).toBe(1);
                view.$(dropdownSelectClass).val(FieldViewsSpecHelpers.SELECT_OPTIONS[0]).change();
                view.$(dropdownSelectClass).focusout();
                expect(view.fieldValue()).toBe(FieldViewsSpecHelpers.SELECT_OPTIONS[0][0]);

                AjaxHelpers.respondWithNoContent(requests);
                if (editable === 'toggle') { view.showEditMode(true); }
                // When server returns success, there should no longer be an empty option.
                expect($(view.$('.u-field-value option')[0]).val()).toBe('si');
            });
        });

        it('correctly renders and updates TextAreaFieldView when editable == never', function() {
            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.TextareaFieldView, {
                title: 'About me',
                valueAttribute: 'bio',
                helpMessage: 'Wicked is good',
                placeholderValue: 'Tell other edX learners a little about yourself: where you live, '
                        + 'what your interests are, why you’re taking courses on edX, or what you hope to learn.',
                editable: 'never',
                persistChanges: true,
                messagePosition: 'header'
            });

            // set bio to empty to see the placeholder.
            fieldData.model.set({bio: ''});
            var view = new FieldViews.TextareaFieldView(fieldData).render();
            FieldViewsSpecHelpers.expectTitleAndMessageToContain(view, fieldData.title, fieldData.helpMessage);
            expect(view.el).toHaveClass('mode-hidden');
            expect(view.fieldValue()).toBe(fieldData.placeholderValue);
            expect(view.$(textareaLinkClass).length).toBe(0);

            var bio = 'Too much to tell!';
            view.model.set({bio: bio});
            expect(view.el).toHaveClass('mode-display');
            expect(view.fieldValue()).toBe(bio);
            view.$el.click();
            expect(view.el).toHaveClass('mode-display');
            expect(view.$(textareaLinkClass).length).toBe(0);
        });

        it('correctly renders, updates and persists changes to TextAreaFieldView when editable == toggle', function() {

            var valueInputSelector = '.u-field-value > textarea';
            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.TextareaFieldView, {
                title: 'About me',
                valueAttribute: 'bio',
                helpMessage: 'Wicked is good',
                placeholderValue: 'Tell other edX learners a little about yourself: where you live, '
                        + 'what your interests are, why you’re taking courses on edX, or what you hope to learn.',
                editable: 'toggle',
                persistChanges: true,
                messagePosition: 'header'
            });
            fieldData.model.set({bio: ''});

            var view = new FieldViews.TextareaFieldView(fieldData).render();

            FieldViewsSpecHelpers.expectTitleToContain(view, fieldData.title);
            FieldViewsSpecHelpers.expectMessageContains(view, view.indicators.canEdit);
            expect(view.el).toHaveClass('mode-placeholder');
            expect(view.fieldValue()).toBe(fieldData.placeholderValue);
            expect(view.$(textareaLinkClass).length).toBe(1);

            view.$('.wrapper-u-field').click();
            expect(view.el).toHaveClass('mode-edit');
            view.$(valueInputSelector).val(BIO).focusout();
            expect(view.fieldValue()).toBe(BIO);
            expect(view.$(textareaLinkClass).length).toBe(0);
            AjaxHelpers.expectJsonRequest(
                requests, 'PATCH', view.model.url, {bio: BIO}
            );
            AjaxHelpers.respondWithNoContent(requests);
            expect(view.el).toHaveClass('mode-display');
            expect(view.$(textareaLinkClass).length).toBe(1);

            view.$('.wrapper-u-field').click();
            view.$(valueInputSelector).val('').focusout();
            AjaxHelpers.respondWithNoContent(requests);
            expect(view.el).toHaveClass('mode-placeholder');
            expect(view.fieldValue()).toBe(fieldData.placeholderValue);
        });

        it('correctly renders LinkFieldView', function() {
            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.LinkFieldView, {
                title: 'Title',
                linkTitle: 'Link title',
                helpMessage: 'Click the link.',
                valueAttribute: 'password-reset'
            });
            var view = new FieldViews.LinkFieldView(fieldData).render();

            FieldViewsSpecHelpers.expectTitleAndMessageToContain(view, fieldData.title, fieldData.helpMessage);
            expect(view.$('.u-field-value > a .u-field-link-title-' + view.options.valueAttribute).text().trim()).toBe(fieldData.linkTitle);
        });

        it("can't persist changes if persistChanges is off", function() {
            var fieldClasses = [
                FieldViews.TextFieldView,
                FieldViews.DropdownFieldView,
                FieldViews.TextareaFieldView
            ];
            for (var i = 0; i < fieldClasses.length; i++) {
                FieldViewsSpecHelpers.verifyPersistence(fieldClasses[i], requests);
            }
        });

        it('correctly renders DateFieldView', function() {
            var fieldData = FieldViewsSpecHelpers.createFieldData(FieldViews.DateFieldView, {
                    title: 'Title',
                    helpMessage: '',
                    dateFormat: 'MMM YYYY',
                    valueAttribute: 'date_joined',
                    userLanguage: 'en-US',
                    userTimezone: 'America/New_York'
                }),
                joinDate = new Date(1990, 0, 15),
                view;
            fieldData.model.set({date_joined: joinDate.toDateString()});
            view = new FieldViews.DateFieldView(fieldData).render();
            expect(view.$('.u-field-value').text().trim()).toBe('Jan 1990');
        });
    });
});
