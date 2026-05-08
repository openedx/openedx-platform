define([
    'jquery',
    'underscore',
    'backbone',
    'sinon',
    'edx-ui-toolkit/js/utils/spec-helpers/ajax-helpers',
    'common/js/spec_helpers/template_helpers',
    'js/verify_student/views/review_photos_step_view',
    'js/verify_student/models/verification_model'
],
function($, _, Backbone, sinon, AjaxHelpers, TemplateHelpers, ReviewPhotosStepView, VerificationModel) {
    'use strict';

    describe('edx.verify_student.ReviewPhotosStepView', function() {
        var STEP_DATA = {},
            FULL_NAME = 'Test User',
            FACE_IMAGE = 'abcd1234',
            PHOTO_ID_IMAGE = 'efgh56789',
            SERVER_ERROR_MSG = 'An error occurred!',
            requests, xhrFactory;

        var createView = function() {
            return new ReviewPhotosStepView({
                el: $('#current-step-container'),
                stepData: STEP_DATA,
                model: new VerificationModel({
                    faceImage: FACE_IMAGE,
                    identificationImage: PHOTO_ID_IMAGE
                }),
                errorModel: new (Backbone.Model.extend({}))()
            }).render();
        };

        var submitPhotos = function(requests, expectedParams, succeeds) {
            // Submit the photos
            $('#next_step_button').click();

            // Expect a request to the server
            AjaxHelpers.expectRequest(
                requests, 'POST', '/verify_student/submit-photos/',
                $.param(expectedParams)
            );

            // Simulate the server response
            if (succeeds) {
                AjaxHelpers.respondWithJson(requests, {url: '/arbitrary-url/'});
            } else {
                AjaxHelpers.respondWithTextError(requests, 400, SERVER_ERROR_MSG);
            }
        };

        var setFullName = function(fullName) {
            $('#new-name').val(fullName);
        };

        var expectSubmitEnabled = function(isEnabled) {
            var appearsDisabled = $('#next_step_button').hasClass('is-disabled'),
                isDisabled = $('#next_step_button').prop('disabled');

            expect(!appearsDisabled).toBe(isEnabled);
            expect(!isDisabled).toBe(isEnabled);
        };

        beforeEach(function() {
            window.analytics = jasmine.createSpyObj('analytics', ['track', 'page', 'trackLink']);

            setFixtures('<div id="current-step-container"></div>');
            TemplateHelpers.installTemplate('templates/verify_student/review_photos_step');
            xhrFactory = sinon.useFakeXMLHttpRequest();
            requests = [];
            requests.currentIndex = 0;
            requests.restore = function() { xhrFactory.restore(); };
            xhrFactory.onCreate = function(req) { requests.push(req); };
        });

        afterEach(function() {
            requests.restore();
        });

        it('allows the user to change her full name', function() {
            createView();
            setFullName(FULL_NAME);
            submitPhotos(
                requests,
                {
                    face_image: FACE_IMAGE,
                    photo_id_image: PHOTO_ID_IMAGE,
                    full_name: FULL_NAME
                },
                true
            );
        });

        it('submits photos for verification', function() {
            createView();
            submitPhotos(
                requests,
                {
                    face_image: FACE_IMAGE,
                    photo_id_image: PHOTO_ID_IMAGE
                },
                true
            );

            // Expect that submission is disabled to prevent
            // duplicate submission.
            expectSubmitEnabled(false);
        });

        it('displays an error if photo submission fails', function() {
            var view = createView();

            submitPhotos(
                requests,
                {
                    face_image: FACE_IMAGE,
                    photo_id_image: PHOTO_ID_IMAGE
                },
                false
            );

            // Expect the submit button is re-enabled to allow
            // the user to retry.
            expectSubmitEnabled(true);

            // Expect that an error message is displayed
            expect(view.errorModel.get('shown')).toBe(true);
            expect(view.errorModel.get('errorTitle')).toEqual('Could not submit photos');
            expect(view.errorModel.get('errorMsg')).toEqual(SERVER_ERROR_MSG);
        });
    });
}
);
