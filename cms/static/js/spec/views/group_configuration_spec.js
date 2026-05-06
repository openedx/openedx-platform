define([
    'underscore', 'sinon', 'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'common/js/spec_helpers/template_helpers',
    'common/js/spec_helpers/view_helpers', 'js/models/course', 'js/models/group_configuration', 'js/models/group',
    'js/collections/group_configuration', 'js/collections/group', 'js/views/group_configuration_details',
    'js/views/group_configurations_list', 'js/views/group_configuration_editor', 'js/views/group_configuration_item',
    'js/views/experiment_group_edit', 'js/views/partition_group_list', 'js/views/partition_group_details',
    'js/views/content_group_editor', 'js/views/partition_group_item'
], function(
    _, sinon, AjaxHelpers, TemplateHelpers, ViewHelpers, Course, GroupConfigurationModel, GroupModel,
    GroupConfigurationCollection, GroupCollection, GroupConfigurationDetailsView, GroupConfigurationsListView,
    GroupConfigurationEditorView, GroupConfigurationItemView, ExperimentGroupEditView, GroupList,
    PartitionGroupDetailsView, ContentGroupEditorView, PartitionGroupItemView
) {
    'use strict';

    // Wrap all specs in a single describe so that the beforeEach/afterEach below are
    // scoped to this file only. Without this wrapper those hooks sit at the top level of
    // the AMD factory, which Jasmine treats as global hooks — they run before/after every
    // spec in the entire test suite, not just the ones in this file. That causes two problems:
    //
    //   1. sinon conflict: other spec files also call sinon.useFakeXMLHttpRequest() in their
    //      own beforeEach. In sinon v19, restoring one factory clears the .restore() method
    //      on all other active factories. When the global afterEach then calls
    //      xhrFactory.restore(), it throws "TypeError: xhrFactory.restore is not a function".
    //
    //   2. State leak: the global afterEach deletes window.course, corrupting tests in other
    //      spec files that set up window.course in their own beforeEach.
    describe('Group Configurations', function() {

    // Pre-populate the jasmine-fixtures cache for every template used in the inner
    // beforeEach blocks below. The outer beforeEach installs sinon.useFakeXMLHttpRequest(),
    // which intercepts the synchronous XHR that readFixtures() uses to load .underscore
    // files from the Karma server. Calling readFixtures() here — at describe-body level,
    // before any beforeEach runs — loads the files over real XHR and caches the results.
    // Subsequent readFixtures() calls inside beforeEach then return from cache without
    // making a new (intercepted) request.
    readFixtures(
        'group-configuration-details.underscore',
        'group-configuration-editor.underscore',
        'group-edit.underscore',
        'list.underscore',
        'content-group-editor.underscore',
        'partition-group-details.underscore'
    );

    var requests, xhrFactory;
    var SELECTORS = {
        detailsView: '.group-configuration-details',
        editView: '.group-configuration-edit',
        itemView: '.group-configurations-list-item',
        group: '.group',
        groupFields: '.groups-fields',
        name: '.group-configuration-name',
        description: '.group-configuration-description',
        groupsCount: '.group-configuration-groups-count',
        groupsAllocation: '.group-allocation',
        errorMessage: '.group-configuration-edit-error',
        inputGroupName: '.group-name',
        inputName: '.collection-name-input',
        inputDescription: '.group-configuration-description-input',
        usageCount: '.group-configuration-usage-count',
        usage: '.group-configuration-usage',
        usageText: '.group-configuration-usage-text',
        usageTextAnchor: '.group-configuration-usage-text > a',
        usageUnit: '.group-configuration-usage-unit',
        usageUnitAnchor: '.group-configuration-usage-unit a',
        usageUnitMessage: '.group-configuration-validation-message',
        usageUnitWarningIcon: '.group-configuration-usage-unit .fa-warning',
        usageUnitErrorIcon: '.group-configuration-usage-unit .fa-exclamation-circle',
        warningMessage: '.group-configuration-validation-text',
        warningIcon: '.wrapper-group-configuration-validation > .fa-warning',
        note: '.wrapper-delete-button'
    };

    var assertTheDetailsView = function(view, text) {
        expect(view.$el).toContainText(text);
        expect(view.$el).toContainText('ID: 0');
        expect(view.$('.delete')).toExist();
    };
    var assertShowEmptyUsages = function(view, usageText) {
        expect(view.$(SELECTORS.usageCount)).not.toExist();
        expect(view.$(SELECTORS.usageText)).toContainText(usageText);
        expect(view.$(SELECTORS.usageTextAnchor)).toExist();
        expect(view.$(SELECTORS.usageUnit)).not.toExist();
    };
    var assertHideEmptyUsages = function(view) {
        expect(view.$(SELECTORS.usageText)).not.toExist();
        expect(view.$(SELECTORS.usageUnit)).not.toExist();
        expect(view.$(SELECTORS.usageCount)).toContainText('Not in Use');
    };
    var assertShowNonEmptyUsages = function(view, usageText, toolTipText) {
        var usageUnitAnchors = view.$(SELECTORS.usageUnitAnchor);

        expect(view.$(SELECTORS.note)).toHaveAttr(
            'data-tooltip', toolTipText
        );
        expect(view.$('.delete')).toHaveClass('is-disabled');
        expect(view.$(SELECTORS.usageCount)).not.toExist();
        expect(view.$(SELECTORS.usageText)).toContainText(usageText);
        expect(view.$(SELECTORS.usageUnit).length).toBe(2);
        expect(usageUnitAnchors.length).toBe(2);
        expect(usageUnitAnchors.eq(0)).toContainText('label1');
        expect(usageUnitAnchors.eq(0).attr('href')).toBe('url1');
        expect(usageUnitAnchors.eq(1)).toContainText('label2');
        expect(usageUnitAnchors.eq(1).attr('href')).toBe('url2');
    };
    var assertHideNonEmptyUsages = function(view) {
        expect(view.$('.delete')).toHaveClass('is-disabled');
        expect(view.$(SELECTORS.usageText)).not.toExist();
        expect(view.$(SELECTORS.usageUnit)).not.toExist();
        expect(view.$(SELECTORS.usageCount)).toContainText('Used in 2 locations');
    };
    var setUsageInfo = function(model) {
        model.set('usage', [
            {label: 'label1', url: 'url1'},
            {label: 'label2', url: 'url2'}
        ]);
    };
    var assertHideValidationContent = function(view) {
        expect(view.$(SELECTORS.usageUnitMessage)).not.toExist();
        expect(view.$(SELECTORS.usageUnitWarningIcon)).not.toExist();
        expect(view.$(SELECTORS.usageUnitErrorIcon)).not.toExist();
    };
    var assertControllerView = function(view, detailsView, editView) {
        // Details view by default
        expect(view.$(detailsView)).toExist();
        view.$('.action-edit .edit').click();
        expect(view.$(editView)).toExist();
        expect(view.$(detailsView)).not.toExist();
        view.$('.action-cancel').click();
        expect(view.$(detailsView)).toExist();
        expect(view.$(editView)).not.toExist();
    };
    var assertAndDeleteItemError = function(that, url, promptText) {
        var promptSpy = ViewHelpers.createPromptSpy(),
            notificationSpy = ViewHelpers.createNotificationSpy();

        ViewHelpers.clickDeleteItem(that, promptSpy, promptText);

        ViewHelpers.patchAndVerifyRequest(requests, url, notificationSpy);

        AjaxHelpers.respondWithNoContent(requests);
        ViewHelpers.verifyNotificationHidden(notificationSpy);
        expect($(SELECTORS.itemView)).not.toExist();
    };
    var assertAndDeleteItemWithError = function(that, url, listItemView, promptText) {
        var promptSpy = ViewHelpers.createPromptSpy(),
            notificationSpy = ViewHelpers.createNotificationSpy();

        ViewHelpers.clickDeleteItem(that, promptSpy, promptText);
        ViewHelpers.patchAndVerifyRequest(requests, url, notificationSpy);

        AjaxHelpers.respondWithError(requests);
        ViewHelpers.verifyNotificationShowing(notificationSpy, /Deleting/);
        expect($(listItemView)).toExist();
    };
    var assertCannotDeleteUsed = function(that, toolTipText, warningText) {
        setUsageInfo(that.model);
        that.view.render();
        expect(that.view.$(SELECTORS.note)).toHaveAttr(
            'data-tooltip', toolTipText
        );
        expect(that.view.$(SELECTORS.warningMessage)).toContainText(warningText);
        expect(that.view.$(SELECTORS.warningIcon)).toExist();
        expect(that.view.$('.delete')).toHaveClass('is-disabled');
    };
    var assertUnusedOptions = function(that) {
        that.model.set('usage', []);
        that.view.render();
        expect(that.view.$(SELECTORS.warningMessage)).not.toExist();
        expect(that.view.$(SELECTORS.warningIcon)).not.toExist();
    };

    beforeEach(function() {
        xhrFactory = sinon.useFakeXMLHttpRequest();
        requests = [];
        requests.currentIndex = 0;
        requests.restore = function() { xhrFactory.restore(); };
        xhrFactory.onCreate = function(req) { requests.push(req); };
        window.course = new Course({
            id: '5',
            name: 'Course Name',
            url_name: 'course_name',
            org: 'course_org',
            num: 'course_num',
            revision: 'course_rev'
        });

        jasmine.addMatchers({
            toContainText: function() {
                return {
                    compare: function(actual, text) {
                        var trimmedText = $.trim(actual.text()),
                            passed;

                        if (text && $.isFunction(text.test)) {
                            passed = text.test(trimmedText);
                        } else {
                            passed = trimmedText.indexOf(text) !== -1;
                        }

                        return {
                            pass: passed
                        };
                    }
                };
            },
            toBeCorrectValuesInInputs: function() {
                return {
                    compare: function(actual, values) {
                        var expected = {
                            name: actual.$(SELECTORS.inputName).val(),
                            description: actual
                                .$(SELECTORS.inputDescription).val()
                        };

                        var passed = _.isEqual(values, expected);

                        return {
                            pass: passed
                        };
                    }
                };
            },
            toBeCorrectValuesInModel: function() {
                return {
                    compare: function(actual, values) {
                        var passed = _.every(values, function(value, key) {
                            return actual.get(key) === value;
                        });

                        return {
                            pass: passed
                        };
                    }
                };
            },
            toHaveDefaultNames: function() {
                return {
                    compare: function(actual, values) {
                        var actualValues = $.map(actual, function(item) {
                            return $(item).val();
                        });

                        var passed = _.isEqual(actualValues, values);

                        return {
                            pass: passed
                        };
                    }
                };
            }
        });
    });

    afterEach(function() {
        requests.restore();
        delete window.course;
    });

    describe('Experiment group configurations details view', function() {
        beforeEach(function() {
            TemplateHelpers.installTemplate('group-configuration-details', true);

            this.model = new GroupConfigurationModel({
                name: 'Configuration',
                description: 'Configuration Description',
                id: 0
            });

            this.collection = new GroupConfigurationCollection([this.model]);
            this.collection.outlineUrl = '/outline';
            this.view = new GroupConfigurationDetailsView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should render properly', function() {
            assertTheDetailsView(this.view, 'Configuration');
        });

        it('should show groups appropriately', function() {
            this.model.get('groups').add([{}]);
            this.model.set('showGroups', false);
            this.view.$('.show-groups').click();

            expect(this.model.get('showGroups')).toBeTruthy();
            expect(this.view.$(SELECTORS.group).length).toBe(3);
            expect(this.view.$(SELECTORS.groupsCount)).not.toExist();
            expect(this.view.$(SELECTORS.description))
                .toContainText('Configuration Description');
            expect(this.view.$(SELECTORS.groupsAllocation))
                .toContainText('33%');
        });

        it('should hide groups appropriately', function() {
            this.model.get('groups').add([{}]);
            this.model.set('showGroups', true);
            this.view.$('.hide-groups').click();

            expect(this.model.get('showGroups')).toBeFalsy();
            expect(this.view.$(SELECTORS.group)).not.toExist();
            expect(this.view.$(SELECTORS.groupsCount))
                .toContainText('Contains 3 groups');
            expect(this.view.$(SELECTORS.description)).not.toExist();
            expect(this.view.$(SELECTORS.groupsAllocation)).not.toExist();
        });

        it('should show empty usage appropriately', function() {
            this.model.set('showGroups', false);
            this.view.$('.show-groups').click();
            assertShowEmptyUsages(
                this.view,
                'This Group Configuration is not in use. '
                + 'Start by adding a content experiment to any Unit via the'
            );
        });

        it('should hide empty usage appropriately', function() {
            this.model.set('showGroups', true);
            this.view.$('.hide-groups').click();
            assertHideEmptyUsages(this.view);
        });

        it('should show non-empty usage appropriately', function() {
            setUsageInfo(this.model);
            this.model.set('showGroups', false);
            this.view.$('.show-groups').click();

            assertShowNonEmptyUsages(
                this.view,
                'This Group Configuration is used in:',
                'Cannot delete when in use by an experiment'
            );
        });

        it('should hide non-empty usage appropriately', function() {
            setUsageInfo(this.model);
            this.model.set('showGroups', true);
            this.view.$('.hide-groups').click();

            expect(this.view.$(SELECTORS.note)).toHaveAttr(
                'data-tooltip', 'Cannot delete when in use by an experiment'
            );
            assertHideNonEmptyUsages(this.view);
        });

        it('should show validation warning icon and message appropriately', function() {
            this.model.set('usage', [
                {
                    label: 'label1',
                    url: 'url1',
                    validation: {
                        text: 'Warning message',
                        type: 'warning'
                    }
                }
            ]);
            this.model.set('showGroups', false);
            this.view.$('.show-groups').click();

            expect(this.view.$(SELECTORS.usageUnitMessage)).toContainText('Warning message');
            expect(this.view.$(SELECTORS.usageUnitWarningIcon)).toExist();
        });

        it('should show validation error icon and message appropriately', function() {
            this.model.set('usage', [
                {
                    label: 'label1',
                    url: 'url1',
                    validation: {
                        text: 'Error message',
                        type: 'error'
                    }
                }
            ]);
            this.model.set('showGroups', false);
            this.view.$('.show-groups').click();

            expect(this.view.$(SELECTORS.usageUnitMessage)).toContainText('Error message');
            expect(this.view.$(SELECTORS.usageUnitErrorIcon)).toExist();
        });

        it('should hide validation icons and messages appropriately', function() {
            setUsageInfo(this.model);
            this.model.set('showGroups', true);
            this.view.$('.hide-groups').click();

            assertHideValidationContent(this.view);
        });
    });

    describe('Experiment group configurations editor view', function() {
        var setValuesToInputs = function(view, values) {
            _.each(values, function(value, selector) {
                if (SELECTORS[selector]) {
                    view.$(SELECTORS[selector]).val(value);
                }
            });
        };

        beforeEach(function() {
            ViewHelpers.installViewTemplates();
            TemplateHelpers.installTemplates([
                'group-configuration-editor', 'group-edit'
            ]);

            this.model = new GroupConfigurationModel({
                name: 'Configuration',
                description: 'Configuration Description',
                id: 0,
                editing: true
            });
            this.collection = new GroupConfigurationCollection([this.model]);
            this.collection.url = '/group_configurations';
            this.view = new GroupConfigurationEditorView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should render properly', function() {
            expect(this.view).toBeCorrectValuesInInputs({
                name: 'Configuration',
                description: 'Configuration Description'
            });
            expect(this.view.$('.delete')).toExist();
        });

        it('should allow you to create new groups', function() {
            var numGroups = this.model.get('groups').length;
            this.view.$('.action-add-group').click();
            expect(this.model.get('groups').length).toEqual(numGroups + 1);
        });

        it('should save properly', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy(),
                groups;

            this.view.$('.action-add-group').click();
            setValuesToInputs(this.view, {
                inputName: 'New Configuration',
                inputDescription: 'New Description'
            });

            ViewHelpers.submitAndVerifyFormSuccess(this.view, requests, notificationSpy);

            expect(this.model).toBeCorrectValuesInModel({
                name: 'New Configuration',
                description: 'New Description'
            });

            groups = this.model.get('groups');
            expect(groups.length).toBe(3);
            expect(groups.at(2).get('name')).toBe('Group C');
            expect(this.view.$el).not.toBeInDOM();
        });

        it('does not hide saving message if failure', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy();

            setValuesToInputs(this.view, {inputName: 'New Configuration'});
            ViewHelpers.submitAndVerifyFormError(this.view, requests, notificationSpy);
        });

        it('does not save on cancel', function() {
            this.view.$('.action-add-group').click();
            setValuesToInputs(this.view, {
                inputName: 'New Configuration',
                inputDescription: 'New Description'
            });

            expect(this.model.get('groups').length).toBe(3);

            this.view.$('.action-cancel').click();
            expect(this.model).toBeCorrectValuesInModel({
                name: 'Configuration',
                description: 'Configuration Description'
            });
            // Model is still exist in the collection
            expect(this.collection.indexOf(this.model)).toBeGreaterThan(-1);
            expect(this.collection.length).toBe(1);
            expect(this.model.get('groups').length).toBe(2);
        });

        it('should be removed on cancel if it is a new item', function() {
            spyOn(this.model, 'isNew').and.returnValue(true);
            setValuesToInputs(this.view, {
                inputName: 'New Configuration',
                inputDescription: 'New Description'
            });
            this.view.$('.action-cancel').click();
            // Model is removed from the collection
            expect(this.collection.length).toBe(0);
        });

        it('should be possible to correct validation errors', function() {
            // Set incorrect value
            setValuesToInputs(this.view, {inputName: ''});
            // Try to save
            this.view.$('form').submit();
            // See error message
            expect(this.view.$(SELECTORS.errorMessage)).toContainText(
                'Group Configuration name is required'
            );
            // No request
            AjaxHelpers.expectNoRequests(requests);
            // Set correct value
            setValuesToInputs(this.view, {inputName: 'New Configuration'});
            // Try to save
            this.view.$('form').submit();
            AjaxHelpers.respondWithJson(requests, {});
            // Model is updated
            expect(this.model).toBeCorrectValuesInModel({
                name: 'New Configuration'
            });
            // Error message disappear
            expect(this.view.$(SELECTORS.errorMessage)).not.toBeInDOM();
            AjaxHelpers.expectNoRequests(requests);
        });

        describe('removes all newly created groups on cancel', function() {
            it('if the model has a non-empty groups', function() {
                var groups = this.model.get('groups');

                this.view.render();
                groups.add([{name: 'non-empty'}]);
                expect(groups.length).toEqual(3);
                this.view.$('.action-cancel').click();
                // Restore to default state (2 groups by default).
                expect(groups.length).toEqual(2);
            });

            it('if the model has no non-empty groups', function() {
                var groups = this.model.get('groups');

                this.view.render();
                groups.add([{}, {}, {}]);
                expect(groups.length).toEqual(5);
                this.view.$('.action-cancel').click();
                // Restore to default state (2 groups by default).
                expect(groups.length).toEqual(2);
            });
        });

        it('groups have correct default names', function() {
            var group1 = new GroupModel({name: 'Group A'}),
                group2 = new GroupModel({name: 'Group B'}),
                collection = this.model.get('groups');

            collection.reset([group1, group2]); // Group A, Group B
            this.view.$('.action-add-group').click(); // Add Group C
            this.view.$('.action-add-group').click(); // Add Group D
            this.view.$('.action-add-group').click(); // Add Group E

            expect(this.view.$(SELECTORS.inputGroupName)).toHaveDefaultNames([
                'Group A', 'Group B', 'Group C', 'Group D', 'Group E'
            ]);

            // Remove Group B
            this.view.$('.group-1 .action-close').click();

            expect(this.view.$(SELECTORS.inputGroupName)).toHaveDefaultNames([
                'Group A', 'Group C', 'Group D', 'Group E'
            ]);

            this.view.$('.action-add-group').click(); // Add Group F
            this.view.$('.action-add-group').click(); // Add Group G

            expect(this.view.$(SELECTORS.inputGroupName)).toHaveDefaultNames([
                'Group A', 'Group C', 'Group D', 'Group E', 'Group F', 'Group G'
            ]);
        });

        it('cannot be deleted if it is in use', function() {
            assertCannotDeleteUsed(
                this,
                'Cannot delete when in use by an experiment',
                'This configuration is currently used in content '
                + 'experiments. If you make changes to the groups, you may '
                + 'need to edit those experiments.'
            );
        });

        it('does not contain warning message if it is not in use', function() {
            assertUnusedOptions(this);
        });
    });

    describe('Experiment group configurations list view', function() {
        var emptyMessage = 'You have not created any group configurations yet.';

        beforeEach(function() {
            TemplateHelpers.installTemplates(
                ['group-configuration-editor', 'group-edit', 'list']
            );

            this.model = new GroupConfigurationModel({id: 0});
            this.collection = new GroupConfigurationCollection();
            this.view = new GroupConfigurationsListView({
                collection: this.collection
            });
            appendSetFixtures(this.view.render().el);
        });

        describe('empty template', function() {
            it('should be rendered if no group configurations', function() {
                expect(this.view.$el).toContainText(emptyMessage);
                expect(this.view.$('.new-button')).toExist();
                expect(this.view.$(SELECTORS.itemView)).not.toExist();
            });

            it('should disappear if group configuration is added', function() {
                expect(this.view.$el).toContainText(emptyMessage);
                expect(this.view.$(SELECTORS.itemView)).not.toExist();
                this.collection.add(this.model);
                expect(this.view.$el).not.toContainText(emptyMessage);
                expect(this.view.$(SELECTORS.itemView)).toExist();
            });

            it('should appear if configurations were removed', function() {
                this.collection.add(this.model);
                expect(this.view.$(SELECTORS.itemView)).toExist();
                this.collection.remove(this.model);
                expect(this.view.$el).toContainText(emptyMessage);
                expect(this.view.$(SELECTORS.itemView)).not.toExist();
            });

            it('can create a new group configuration', function() {
                this.view.$('.new-button').click();
                expect($('.group-configuration-edit').length).toBeGreaterThan(0);
            });
        });
    });

    describe('Experiment group configurations controller view', function() {
        beforeEach(function() {
            TemplateHelpers.installTemplates([
                'group-configuration-editor', 'group-configuration-details'
            ], true);
            this.model = new GroupConfigurationModel({id: 0});
            this.collection = new GroupConfigurationCollection([this.model]);
            this.collection.url = '/group_configurations';
            this.view = new GroupConfigurationItemView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should render properly', function() {
            assertControllerView(this.view, SELECTORS.detailsView, SELECTORS.editView);
        });

        it('should destroy itself on confirmation of deleting', function() {
            assertAndDeleteItemError(this, '/group_configurations/0', 'Delete this group configuration?');
        });

        it('does not hide deleting message if failure', function() {
            assertAndDeleteItemWithError(
                this,
                '/group_configurations/0',
                SELECTORS.itemView,
                'Delete this group configuration?'
            );
        });
    });

    describe('Experiment group configurations group editor view', function() {
        beforeEach(function() {
            TemplateHelpers.installTemplate('group-edit', true);

            this.model = new GroupModel({
                name: 'Group A'
            });

            this.collection = new GroupCollection([this.model]);

            this.view = new ExperimentGroupEditView({
                model: this.model
            });
        });

        describe('Basic', function() {
            it('can render properly', function() {
                this.view.render();
                expect(this.view.$('.group-name').val()).toBe('Group A');
                expect(this.view.$('.group-allocation')).toContainText('100%');
            });

            it('can delete itself', function() {
                this.view.render().$('.action-close').click();
                expect(this.collection.length).toEqual(0);
            });
        });
    });

    describe('Content groups list view', function() {
        var newGroupCss = '.new-button',
            addGroupCss = '.action-add',
            inputCss = '.collection-name-input',
            saveButtonCss = '.action-primary',
            cancelButtonCss = '.action-cancel',
            validationErrorCss = '.content-group-edit-error',
            scopedGroupSelector, createGroups, renderView, saveOrCancel, editNewGroup, editExistingGroup,
            verifyEditingGroup, respondToSave, expectGroupsVisible, correctValidationError;

        scopedGroupSelector = function(groupIndex, additionalSelectors) {
            var groupSelector = '.partition-groups-list-item-' + groupIndex;
            if (additionalSelectors) {
                return groupSelector + ' ' + additionalSelectors;
            } else {
                return groupSelector;
            }
        };

        createGroups = function(groupNamesWithId) {
            var groups = new GroupCollection(_.map(groupNamesWithId, function(groupName, id) {
                    return {id: id, name: groupName};
                })),
                groupConfiguration = new GroupConfigurationModel({
                    id: 0,
                    name: 'Content Group Configuration',
                    groups: groups
                }, {canBeEmpty: true});
            groupConfiguration.urlRoot = '/mock_url';
            groupConfiguration.outlineUrl = '/mock_url';
            return groups;
        };

        renderView = function(groupNamesWithId) {
            var view = new GroupList({collection: createGroups(groupNamesWithId || {})}).render();
            appendSetFixtures(view.el);
            return view;
        };

        saveOrCancel = function(view, options, groupIndex) {
            if (options.save) {
                view.$(scopedGroupSelector(groupIndex, saveButtonCss)).click();
            } else if (options.cancel) {
                view.$(scopedGroupSelector(groupIndex, cancelButtonCss)).click();
            }
        };

        editNewGroup = function(view, options) {
            var newGroupIndex;
            if (view.collection.length === 0) {
                view.$(newGroupCss).click();
            } else {
                view.$(addGroupCss).click();
            }
            newGroupIndex = view.collection.length - 1;
            view.$(inputCss).val(options.newName);
            verifyEditingGroup(view, true, newGroupIndex);
            saveOrCancel(view, options, newGroupIndex);
        };

        editExistingGroup = function(view, options) {
            var groupIndex = options.groupIndex || 0;
            view.$(scopedGroupSelector(groupIndex, '.edit')).click();
            view.$(scopedGroupSelector(groupIndex, inputCss)).val(options.newName);
            saveOrCancel(view, options, groupIndex);
        };

        verifyEditingGroup = function(view, expectEditing, index) {
            // Should prevent the user from opening more than one edit
            // form at a time by removing the add button(s) when
            // editing a group.
            index = index || 0;
            if (expectEditing) {
                expect(view.$(scopedGroupSelector(index, '.content-group-edit'))).toExist();
                expect(view.$(newGroupCss)).not.toExist();
                expect(view.$(addGroupCss)).toHaveClass('is-hidden');
            } else {
                expect(view.$('.content-group-edit')).not.toExist();
                if (view.collection.length === 0) {
                    expect(view.$(newGroupCss)).toExist();
                    expect(view.$(addGroupCss)).not.toExist();
                } else {
                    expect(view.$(newGroupCss)).not.toExist();
                    expect(view.$(addGroupCss)).not.toHaveClass('is-hidden');
                }
            }
        };

        respondToSave = function(requests, view) {
            var request = AjaxHelpers.currentRequest(requests);
            expect(request.method).toBe('POST');
            expect(request.url).toBe('/mock_url/0');
            AjaxHelpers.respondWithJson(requests, {
                name: 'Content Group Configuration',
                groups: view.collection.map(function(groupModel, index) {
                    return _.extend(groupModel.toJSON(), {id: index});
                })
            });
        };

        correctValidationError = function(view, requests, newGroupName) {
            expect(view.$(validationErrorCss)).toExist();
            verifyEditingGroup(view, true);
            view.$(inputCss).val(newGroupName);
            view.$(saveButtonCss).click();
            respondToSave(requests, view);
            expect(view.$(validationErrorCss)).not.toExist();
        };

        expectGroupsVisible = function(view, groupNames) {
            _.each(groupNames, function(groupName) {
                expect(view.$('.partition-groups-list-item')).toContainText(groupName);
            });
        };

        beforeEach(function() {
            TemplateHelpers.installTemplates(
                ['content-group-editor', 'partition-group-details', 'list']
            );
        });

        it('shows a message when no groups are present', function() {
            expect(renderView().$('.no-content'))
                .toContainText('You have not created any content groups yet.');
        });

        it('can render groups', function() {
            var groupNames = ['Group 1', 'Group 2', 'Group 3'];
            renderView(groupNames).$('.partition-group-details').each(function(index) {
                expect($(this)).toContainText(groupNames[index]);
            });
        });

        it('can create an initial group and save', function() {
            var newGroupName = 'New Group Name',
                view = renderView();
            editNewGroup(view, {newName: newGroupName, save: true});
            respondToSave(requests, view);
            verifyEditingGroup(view, false);
            expectGroupsVisible(view, [newGroupName]);
        });

        it('can add another group and save', function() {
            var oldGroupName = 'Old Group Name',
                newGroupName = 'New Group Name',
                view = renderView({1: oldGroupName});
            editNewGroup(view, {newName: newGroupName, save: true});
            respondToSave(requests, view);
            verifyEditingGroup(view, false, 1);
            expectGroupsVisible(view, [oldGroupName, newGroupName]);
        });

        it('can cancel adding a group', function() {
            var newGroupName = 'New Group Name',
                view = renderView();
            editNewGroup(view, {newName: newGroupName, cancel: true});
            AjaxHelpers.expectNoRequests(requests);
            verifyEditingGroup(view, false);
            expect(view.$()).not.toContainText(newGroupName);
        });

        it('can cancel editing a group', function() {
            var originalGroupName = 'Original Group Name',
                view = renderView([originalGroupName]);
            editExistingGroup(view, {newName: 'New Group Name', cancel: true});
            verifyEditingGroup(view, false);
            AjaxHelpers.expectNoRequests(requests);
            expect(view.collection.at(0).get('name')).toBe(originalGroupName);
        });

        it('can show and correct a validation error', function() {
            var newGroupName = 'New Group Name',
                view = renderView();
            editNewGroup(view, {newName: '', save: true});
            AjaxHelpers.expectNoRequests(requests);
            correctValidationError(view, requests, newGroupName);
        });

        it('can not invalidate an existing content group', function() {
            var oldGroupName = 'Old Group Name',
                view = renderView([oldGroupName]);
            editExistingGroup(view, {newName: '', save: true});
            AjaxHelpers.expectNoRequests(requests);
            correctValidationError(view, requests, oldGroupName);
        });

        it('trims whitespace', function() {
            var newGroupName = 'New Group Name',
                view = renderView();
            editNewGroup(view, {newName: '  ' + newGroupName + '  ', save: true});
            respondToSave(requests, view);
            expect(view.collection.at(0).get('name')).toBe(newGroupName);
        });

        it('only edits one form at a time', function() {
            var view = renderView();
            view.collection.add({name: 'Editing Group', editing: true});
            verifyEditingGroup(view, true);
        });
    });

    describe('Content groups details view', function() {
        beforeEach(function() {
            TemplateHelpers.installTemplate('partition-group-details', true);
            this.model = new GroupModel({name: 'Content Group', id: 0, courseOutlineUrl: 'CourseOutlineUrl'});

            var saveableModel = new GroupConfigurationModel({
                name: 'Content Group Configuration',
                id: 0,
                scheme: 'cohort',
                groups: new GroupCollection([this.model])
            }, {canBeEmpty: true});

            saveableModel.urlRoot = '/mock_url';

            this.collection = new GroupConfigurationCollection([saveableModel]);
            this.collection.outlineUrl = '/outline';

            this.view = new PartitionGroupDetailsView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should render properly', function() {
            assertTheDetailsView(this.view, 'Content Group');
        });

        it('should show empty usage appropriately', function() {
            this.view.$('.show-groups').click();
            assertShowEmptyUsages(this.view, 'use this group to control access to a component');
        });

        it('should hide empty usage appropriately', function() {
            this.view.$('.hide-groups').click();
            assertHideEmptyUsages(this.view);
        });

        it('should show non-empty usage appropriately', function() {
            setUsageInfo(this.model);
            this.view.$('.show-groups').click();

            assertShowNonEmptyUsages(
                this.view,
                'This group controls access to:',
                'Cannot delete when in use by a unit'
            );
        });

        it('should hide non-empty usage appropriately', function() {
            setUsageInfo(this.model);
            this.view.$('.hide-groups').click();

            expect(this.view.$('li.action-delete')).toHaveAttr(
                'data-tooltip', 'Cannot delete when in use by a unit'
            );
            assertHideNonEmptyUsages(this.view);
        });

        it('should hide validation icons and messages appropriately', function() {
            setUsageInfo(this.model);
            this.view.$('.hide-groups').click();
            assertHideValidationContent(this.view);
        });
    });

    describe('Content groups editor view', function() {
        beforeEach(function() {
            ViewHelpers.installViewTemplates();
            TemplateHelpers.installTemplates(['content-group-editor']);

            this.model = new GroupModel({name: 'Content Group', id: 0});

            this.saveableModel = new GroupConfigurationModel({
                name: 'Content Group Configuration',
                id: 0,
                scheme: 'cohort',
                groups: new GroupCollection([this.model]),
                editing: true
            });

            this.collection = new GroupConfigurationCollection([this.saveableModel]);
            this.collection.outlineUrl = '/outline';
            this.collection.url = '/group_configurations';

            this.view = new ContentGroupEditorView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should save properly', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy();

            this.view.$('.action-add').click();
            this.view.$(SELECTORS.inputName).val('New Content Group');

            ViewHelpers.submitAndVerifyFormSuccess(this.view, requests, notificationSpy);

            expect(this.model).toBeCorrectValuesInModel({
                name: 'New Content Group'
            });
            expect(this.view.$el).not.toBeInDOM();
        });

        it('does not hide saving message if failure', function() {
            var notificationSpy = ViewHelpers.createNotificationSpy();
            this.view.$(SELECTORS.inputName).val('New Content Group');

            ViewHelpers.submitAndVerifyFormError(this.view, requests, notificationSpy);
        });

        it('does not save on cancel', function() {
            expect(this.view.$('.action-add'));
            this.view.$('.action-add').click();
            this.view.$(SELECTORS.inputName).val('New Content Group');

            this.view.$('.action-cancel').click();
            expect(this.model).toBeCorrectValuesInModel({
                name: 'Content Group'
            });
            // Model is still exist in the collection
            expect(this.collection.indexOf(this.saveableModel)).toBeGreaterThan(-1);
            expect(this.collection.length).toBe(1);
        });

        it('cannot be deleted if it is in use', function() {
            assertCannotDeleteUsed(
                this,
                'Cannot delete when in use by a unit',
                'This content group is used in one or more units.'
            );
        });

        it('does not contain warning message if it is not in use', function() {
            assertUnusedOptions(this);
        });
    });

    describe('Content group controller view', function() {
        beforeEach(function() {
            TemplateHelpers.installTemplates([
                'content-group-editor', 'partition-group-details'
            ], true);

            this.model = new GroupModel({name: 'Content Group', id: 0, courseOutlineUrl: 'CourseOutlineUrl'});

            this.saveableModel = new GroupConfigurationModel({
                name: 'Content Group Configuration',
                id: 0,
                scheme: 'cohort',
                groups: new GroupCollection([this.model])
            });
            this.saveableModel.urlRoot = '/group_configurations';
            this.collection = new GroupConfigurationCollection([this.saveableModel]);
            this.collection.url = '/group_configurations';
            this.view = new PartitionGroupItemView({
                model: this.model
            });
            appendSetFixtures(this.view.render().el);
        });

        it('should render properly', function() {
            assertControllerView(this.view, '.partition-group-details', '.content-group-edit');
        });

        it('should destroy itself on confirmation of deleting', function() {
            assertAndDeleteItemError(this, '/group_configurations/0/0', 'Delete this content group');
        });

        it('does not hide deleting message if failure', function() {
            assertAndDeleteItemWithError(
                this,
                '/group_configurations/0/0',
                '.partition-groups-list-item',
                'Delete this content group'
            );
        });
    });

    }); // end describe('Group Configurations')
});
