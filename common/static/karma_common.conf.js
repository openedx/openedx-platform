// Karma config for common suite.
// Docs and troubleshooting tips in common/static/common/js/karma.common.conf.js

/* eslint-env node */

'use strict';

var path = require('path');

var configModule = require(path.join(__dirname, '../../common/static/common/js/karma.common.conf.js'));

var options = {

    useRequireJs: false,

    normalizePathsForCoverageFunc: function(appRoot, pattern) {
        return path.join(appRoot, '/common/static/' + pattern);
    },

    // Avoid adding files to this list. Use RequireJS.
    libraryFilesToInclude: [
        {pattern: 'js/src/ajax_prefix.js', included: true},
        {pattern: 'js/vendor/draggabilly.js', included: true},
        {pattern: 'common/js/vendor/jquery.js', included: true},
        {pattern: 'common/js/vendor/jquery-migrate.js', included: true},
        {pattern: 'js/src/jquery.immediateDescendents.js', included: true},
        {pattern: 'js/vendor/jquery.leanModal.js', included: true},
        {pattern: 'js/vendor/jquery.timeago.js', included: true},
        {pattern: 'js/vendor/jquery.truncate.js', included: true},
        {pattern: 'js/vendor/URI.min.js', included: true},
        {pattern: 'js/test/add_ajax_prefix.js', included: true},
        {pattern: 'js/test/i18n.js', included: true},

        {pattern: 'common/js/vendor/underscore.js', included: true},
        {pattern: 'common/js/vendor/underscore.string.js', included: true},
        {pattern: 'common/js/vendor/backbone.js', included: true},

        {pattern: 'edx-ui-toolkit/js/utils/global-loader.js', included: true},
        {pattern: 'edx-ui-toolkit/js/utils/string-utils.js', included: true},
        {pattern: 'edx-ui-toolkit/js/utils/html-utils.js', included: true},

        {pattern: 'js/vendor/jasmine-imagediff.js', included: true},
        {pattern: 'common/js/spec_helpers/jasmine-extensions.js', included: true},
        {pattern: 'common/js/spec_helpers/jasmine-waituntil.js', included: true},
        {pattern: 'common/js/spec_helpers/discussion_spec_helper.js', included: true},
        {pattern: 'common/js/spec/discussion/view/discussion_view_spec_helper.js', included: true}
    ],

    libraryFiles: [
    ],

    // Make sure the patterns in sourceFiles and specFiles do not match the same file.
    // Otherwise Istanbul which is used for coverage tracking will cause tests to not run.
    sourceFiles: [
        {pattern: 'common/js/xblock/core.js', included: true},
        {pattern: 'common/js/xblock/runtime.v1.js', included: true},
        {pattern: 'common/js/discussion/**/*.js', included: true},
        {pattern: 'js/src/**/*.js', included: true}
    ],

    specFiles: [
        {pattern: 'common/js/spec/xblock/*.js', included: true},
        {pattern: 'common/js/spec/discussion/**/*spec.js', included: true},
        {pattern: 'js/**/*spec.js', included: true}
    ],

    fixtureFiles: [
        {pattern: 'js/fixtures/**/*.html'},
        {pattern: 'common/templates/**/*.underscore'}
    ]
};

module.exports = function(config) {
    configModule.configure(config, options);
};
