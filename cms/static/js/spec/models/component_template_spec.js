define(['js/models/component_template'],
    function(ComponentTemplate) {
        describe('ComponentTemplates', function() {
            var mockTemplateJSON = {
                templates: [
                    {
                        category: 'openassessment',
                        boilerplate_name: null,
                        display_name: 'Peer Assessment'
                    }, {
                        category: 'word_cloud',
                        boilerplate_name: null,
                        display_name: 'Word Cloud'
                    }, { // duplicate display name to verify sort behavior
                        category: 'word_cloud',
                        boilerplate_name: 'alternate_word_cloud.yaml',
                        display_name: 'Word Cloud'
                    }],
                type: 'problem',
                support_legend: {show_legend: false}
            };

            it('orders templates correctly', function() {
                var lastTemplate = null,
                    firstComparison = true,
                    componentTemplate = new ComponentTemplate(),
                    template, templateName, i;
                componentTemplate.parse(mockTemplateJSON);
                for (i = 0; i < componentTemplate.templates.length; i++) {
                    template = componentTemplate.templates[i];
                    templateName = template.display_name;
                    if (lastTemplate) {
                        if (!firstComparison || lastTemplate.boilerplate_name) {
                            expect(lastTemplate.display_name < templateName).toBeTruthy();
                        }
                        firstComparison = false;
                    } else {
                        // If the first template is blank, make sure that it has the correct category
                        if (!template.boilerplate_name) {
                            expect(template.category).toBe('openassessment');
                        }
                        lastTemplate = template;
                    }
                }
            });
        });
    });
