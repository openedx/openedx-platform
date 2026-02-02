/* eslint-env node */

// Karma config for cms-squire suite.
// Docs and troubleshooting tips in common/static/common/js/karma.common.conf.js

'use strict';

var path = require('path');

var configModule = require(path.join(__dirname, '../../common/static/common/js/karma.common.conf.js'));

var options = {

    includeCommonFiles: true,

    libraryFiles: [],

    libraryFilesToInclude: [
        {pattern: 'common/js/vendor/jquery.js', included: true},
        {pattern: 'common/js/vendor/jquery-migrate.js', included: true}
    ],

    // Make sure the patterns in sourceFiles and specFiles do not match the same file.
    // Otherwise Istanbul which is used for coverage tracking will cause tests to not run.
    sourceFiles: [
        // Split cms/js/ patterns to exclude spec/main*.js test entry points (handled by runFiles)
        {pattern: 'cms/js/!(*spec|djangojs).js'},  // Direct children of cms/js/
        {pattern: 'cms/js/!(spec)/**/!(*spec|djangojs).js'},  // Subdirs of cms/js/ except spec/
        {pattern: 'cms/js/spec/**/!(*spec|djangojs|main*).js'},  // spec/ subdir, excluding main* files
        {pattern: 'js/**/!(*spec|djangojs).js'}
    ],

    specFiles: [
        {pattern: 'js/spec/**/*spec.js'}
    ],

    fixtureFiles: [
        {pattern: 'templates/**/*.*'}
    ],

    runFiles: [
        {pattern: 'cms/js/spec/main_squire.js', included: true}
    ]
};

module.exports = function(config) {
    configModule.configure(config, options);
};
