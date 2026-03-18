// Karma config for lms suite.
// Docs and troubleshooting tips in common/static/common/js/karma.common.conf.js

/* eslint-env node */

'use strict';

var path = require('path');

var configModule = require(path.join(__dirname, '../../common/static/common/js/karma.common.conf.js'));

var options = {

    includeCommonFiles: true,

    // Avoid adding files to this list. Use RequireJS.
    libraryFilesToInclude: [
        {pattern: '../../common/static/common/js/vendor/jquery.js', included: true},
        {pattern: '../../common/static/common/js/vendor/jquery-migrate.js', included: true},
        {pattern: '../../common/static/js/vendor/jquery.event.drag-2.2.js', included: true},
        {pattern: '../../common/static/js/vendor/slick.core.js', included: true},
        {pattern: '../../common/static/js/vendor/slick.grid.js', included: true}
    ],

    libraryFiles: [
        {pattern: 'js/RequireJS-namespace-undefine.js'}
    ],

    // Make sure the patterns in sourceFiles and specFiles do not match the same file.
    // Otherwise Istanbul which is used for coverage tracking will cause tests to not run.
    sourceFiles: [
        {pattern: 'course_bookmarks/**/!(*spec).js'},
        {pattern: 'course_search/**/!(*spec).js'},
        {pattern: 'discussion/js/**/!(*spec).js'},
        {pattern: 'js/**/!(*spec|djangojs).js'},
        {pattern: 'learner_profile/**/!(*spec).js'},
        // Split lms/js/ to exclude spec/main*.js test entry points (handled by runFiles)
        {pattern: 'lms/js/!(*spec).js'},  // Direct children of lms/js/
        {pattern: 'lms/js/!(spec)/**/!(*spec).js'},  // Subdirs of lms/js/ except spec/
        {pattern: 'lms/js/spec/**/!(*spec|main*).js'},  // spec/ subdir, excluding main* files
        {pattern: 'support/js/**/!(*spec).js'},
        {pattern: 'teams/js/**/!(*spec).js'},
        {pattern: 'completion/js/**/!(*spec).js'}
    ],

    specFiles: [
        // Define the Webpack-built spec files first
        {pattern: 'course_experience/js/**/*_spec.js', webpack: true},
        {pattern: 'js/learner_dashboard/**/*_spec.js', webpack: true},
        {pattern: 'js/student_account/components/**/*_spec.js', webpack: true},
        {pattern: 'completion/js/**/*_spec.js', webpack: true},

        // Add all remaining spec files to be used without Webpack
        {pattern: '../**/*spec.js'}
    ],

    fixtureFiles: [
        {pattern: '../**/fixtures/**/*.html'},
        {pattern: '../**/templates/**/*.html'},
        {pattern: '../**/*.underscore'},
        {pattern: '../**/*.svg'}
    ],

    runFiles: [
        {pattern: 'lms/js/spec/main.js', included: true}
    ],

    preprocessors: {}
};

options.specFiles
    .filter(function(file) { return file.webpack; })
    .forEach(function(file) {
        options.preprocessors[file.pattern] = ['webpack', 'sourcemap'];
    });

module.exports = function(config) {
    configModule.configure(config, options);
};
