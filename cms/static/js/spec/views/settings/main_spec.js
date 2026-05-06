define([
    'jquery', 'sinon', 'js/models/settings/course_details', 'js/views/settings/main',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers', 'common/js/spec_helpers/template_helpers'
], function($, sinon, CourseDetailsModel, MainView, AjaxHelpers, TemplateHelpers) {
    'use strict';

    var requests, xhrFactory;

    var SELECTORS = {
        entrance_exam_min_score: '#entrance-exam-minimum-score-pct',
        entrance_exam_enabled_field: '#entrance-exam-enabled',
        grade_requirement_div: '.div-grade-requirements div',
        add_course_learning_info: '.add-course-learning-info',
        delete_course_learning_info: '.delete-course-learning-info',
        add_course_instructor_info: '.add-course-instructor-info',
        remove_instructor_data: '.remove-instructor-data'
    };

    describe('Settings/Main', function() {
        var urlRoot = '/course/settings/org/DemoX/Demo_Course',
            modelData = {
                start_date: '2014-10-05T00:00:00Z',
                end_date: '2014-11-05T20:00:00Z',
                enrollment_start: '2014-10-00T00:00:00Z',
                enrollment_end: '2014-11-05T00:00:00Z',
                certificates_display_behavior: 'end',
                certificate_available_date: null,
                org: '',
                course_id: '',
                run: '',
                syllabus: null,
                title: '',
                subtitle: '',
                duration: '',
                description: '',
                short_description: '',
                overview: '',
                intro_video: null,
                effort: null,
                course_image_name: '',
                course_image_asset_path: '',
                banner_image_name: '',
                banner_image_asset_path: '',
                video_thumbnail_image_name: '',
                video_thumbnail_image_asset_path: '',
                pre_requisite_courses: [],
                entrance_exam_enabled: '',
                entrance_exam_minimum_score_pct: '50',
                license: null,
                language: '',
                learning_info: [''],
                instructor_info: {
                    instructors: [{
                        name: '', title: '', organization: '', image: '', bio: ''
                    }]
                },
                self_paced: false
            },

            mockSettingsPage = readFixtures('mock/mock-settings-page.underscore'),
            learningInfoTpl = readFixtures('course-settings-learning-fields.underscore'),
            instructorInfoTpl = readFixtures('course-instructor-details.underscore');

        beforeEach(function() {
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
            TemplateHelpers.installTemplates(['course-settings-learning-fields', 'course-instructor-details'], true);
            appendSetFixtures(mockSettingsPage);
            appendSetFixtures(
                $('<script>', {id: 'basic-learning-info-tpl', type: 'text/template'}).text(learningInfoTpl)
            );
            appendSetFixtures(
                $('<script>', {id: 'basic-instructor-info-tpl', type: 'text/template'}).text(instructorInfoTpl)
            );

            this.model = new CourseDetailsModel($.extend(true, {}, modelData, {
                instructor_info: {
                    instructors: [{
                        name: '', title: '', organization: '', image: '', bio: ''
                    }]
                }
            }), {parse: true});
            this.model.urlRoot = urlRoot;
            this.view = new MainView({
                el: $('.settings-details'),
                model: this.model
            }).render();
        });

        afterEach(function() {
            requests.restore();
            // Clean up after the $.datepicker
            $('#start_date').datepicker('destroy');
            $('#due_date').datepicker('destroy');
            $('.ui-datepicker').remove();
        });

        it('Changing the time field do not affect other time fields', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    // Expect to see changes just in `start_date` field.
                    start_date: '2014-10-05T22:00:00.000Z'
                });
            this.view.$el.find('#course-start-time')
                .val('22:00')
                .trigger('input');

            this.view.saveView();
            // It sends `POST` request, because the model doesn't have `id`. In
            // this case, it is considered to be new according to Backbone documentation.
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
        });

        it('Selecting a course in pre-requisite drop down should save it as part of course details', function() {
            var pre_requisite_courses = ['test/CSS101/2012_T1'];
            var expectedJson = $.extend(true, {}, modelData, {
                    pre_requisite_courses: pre_requisite_courses
                });
            this.view.$el.find('#pre-requisite-course')
                .val(pre_requisite_courses[0])
                .trigger('change');

            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('should disallow save with an invalid minimum score percentage', function() {
            var entrance_exam_enabled_field = this.view.$(SELECTORS.entrance_exam_enabled_field),
                entrance_exam_min_score = this.view.$(SELECTORS.entrance_exam_min_score);

            // input some invalid values.
            expect(entrance_exam_min_score.val('101').trigger('input')).toHaveClass('error');
            expect(entrance_exam_min_score.val('invalidVal').trigger('input')).toHaveClass('error');
        });

        it('should provide a default value for the minimum score percentage', function() {
            var entrance_exam_min_score = this.view.$(SELECTORS.entrance_exam_min_score);

            // if input an empty value, model should be populated with the default value.
            entrance_exam_min_score.val('').trigger('input');
            expect(this.model.get('entrance_exam_minimum_score_pct'))
                .toEqual(this.model.defaults.entrance_exam_minimum_score_pct);
        });

        it('shows and hide the grade requirement section appropriately', function() {
            var entrance_exam_enabled_field = this.view.$(SELECTORS.entrance_exam_enabled_field);

            // select the entrance-exam-enabled checkbox. grade requirement section should be visible.
            entrance_exam_enabled_field
                .prop('checked', true)
                .trigger('change');

            this.view.render();
            expect(this.view.$(SELECTORS.grade_requirement_div)).toBeVisible();

            // deselect the entrance-exam-enabled checkbox. grade requirement section should be hidden.
            entrance_exam_enabled_field
                .prop('checked', false)
                .trigger('change');

            expect(this.view.$(SELECTORS.grade_requirement_div)).toBeHidden();
        });

        it('should save entrance exam course details information correctly', function() {
            var entrance_exam_minimum_score_pct = '60',
                entrance_exam_enabled = 'true',
                entrance_exam_min_score = this.view.$(SELECTORS.entrance_exam_min_score),
                entrance_exam_enabled_field = this.view.$(SELECTORS.entrance_exam_enabled_field);

            var expectedJson = $.extend(true, {}, modelData, {
                    entrance_exam_enabled: entrance_exam_enabled,
                    entrance_exam_minimum_score_pct: entrance_exam_minimum_score_pct
                });

            // select the entrance-exam-enabled checkbox.
            entrance_exam_enabled_field
                .prop('checked', true)
                .trigger('change');

            // input a valid value for entrance exam minimum score.
            entrance_exam_min_score.val(entrance_exam_minimum_score_pct).trigger('input');

            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('should save language as part of course details', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                language: 'en'
            });
            $('#course-language').val('en').trigger('change');
            expect(this.model.get('language')).toEqual('en');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
        });

        it('should not error if about_page_editable is False', function() {
            // if about_page_editable is false, there is no section.course_details
            $('.course_details').remove();
            expect(this.model.get('language')).toEqual('');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(requests, 'POST', urlRoot, modelData);
        });

        it('should save title', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    title: 'course title'
                });

            // Input some value.
            this.view.$('#course-title').val('course title');
            this.view.$('#course-title').trigger('change');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('should save subtitle', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    subtitle: 'course subtitle'
                });

            // Input some value.
            this.view.$('#course-subtitle').val('course subtitle');
            this.view.$('#course-subtitle').trigger('change');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('should save duration', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    duration: '8 weeks'
                });

            // Input some value.
            this.view.$('#course-duration').val('8 weeks');
            this.view.$('#course-duration').trigger('change');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('should save description', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    description: 'course description'
                });

            // Input some value.
            this.view.$('#course-description').val('course description');
            this.view.$('#course-description').trigger('change');
            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('can add learning information', function() {
            this.view.$(SELECTORS.add_course_learning_info).click();
            expect('click').not.toHaveBeenPreventedOn(SELECTORS.add_course_learning_info);
            expect(this.model.get('learning_info').length).toEqual(2);
            this.view.$(SELECTORS.add_course_learning_info).click();
            expect(this.model.get('learning_info').length).toEqual(3);
        });

        it('can delete learning information', function() {
            for (var i = 0; i < 2; i++) {
                this.view.$(SELECTORS.add_course_learning_info).click();
            }
            expect(this.model.get('learning_info').length).toEqual(3);
            expect(this.view.$(SELECTORS.delete_course_learning_info)).toExist();
            this.view.$(SELECTORS.delete_course_learning_info).click();
            expect(this.model.get('learning_info').length).toEqual(2);
        });

        it('can save learning information', function() {
            expect(this.model.get('learning_info').length).toEqual(1);
            var expectedJson = $.extend(true, {}, modelData, {
                    learning_info: ['testing info']
                });

            // Input some value.
            this.view.$('#course-learning-info-0').val('testing info');
            this.view.$('#course-learning-info-0').trigger('change');

            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });

        it('can add instructor information', function() {
            this.view.$(SELECTORS.add_course_instructor_info).click();
            expect(this.model.get('instructor_info').instructors.length).toEqual(2);
            this.view.$(SELECTORS.add_course_instructor_info).click();
            expect(this.model.get('instructor_info').instructors.length).toEqual(3);
        });

        it('can delete instructor information', function() {
            this.view.$(SELECTORS.add_course_instructor_info).click();
            expect(this.model.get('instructor_info').instructors.length).toEqual(2);
            expect(this.view.$(SELECTORS.remove_instructor_data)).toExist();
            this.view.$(SELECTORS.remove_instructor_data).click();
            expect(this.model.get('instructor_info').instructors.length).toEqual(1);
        });

        it('can save instructor information', function() {
            var expectedJson = $.extend(true, {}, modelData, {
                    instructor_info: {
                        instructors:
                        [{
                            name: 'test_name',
                            title: 'test_title',
                            organization: 'test_org',
                            image: 'test_image',
                            bio: 'test_bio'
                        }]
                    }
                });

            // Input some value.
            this.view.$('#course-instructor-name-0').val('test_name').trigger('change');
            this.view.$('#course-instructor-title-0').val('test_title').trigger('change');
            this.view.$('#course-instructor-organization-0').val('test_org').trigger('change');
            this.view.$('#course-instructor-bio-0').val('test_bio').trigger('change');
            this.view.$('#course-instructor-image-0').val('test_image').trigger('change');

            this.view.saveView();
            AjaxHelpers.expectJsonRequest(
                requests, 'POST', urlRoot, expectedJson
            );
            AjaxHelpers.respondWithJson(requests, expectedJson);
        });
        it('should disallow save with a certificate available date before end date', function() {
            this.model.showCertificateAvailableDate = true;
            $('#certificates-display-behavior').val('end_with_date').trigger('change');
            $('#certificate-available-date').val('01/01/2020').trigger('change');
            $('#course-end-date').val('01/01/2030').trigger('change');
            expect(this.view.$('.message-error')).toExist();
        });
        it('should allow save with a certificate available date before end date for self-paced course', function() {
            this.model.showCertificateAvailableDate = false;
            this.model.set('self_paced', true);
            $('#course-end-date').val('01/01/2030').trigger('change');
            expect(this.view.$('.message-error')).not.toExist();
            this.model.set('self_paced', false);
        });
    });
});
