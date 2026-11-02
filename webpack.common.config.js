/* eslint-env node */

'use strict';

var path = require('path');
var webpack = require('webpack');
var BundleTracker = require('webpack-bundle-tracker');
var Merge = require('webpack-merge');

var files = require('./webpack-config/file-lists.js');
var builtinBlocksJS = require('./webpack.builtinblocks.config.js');

var filesWithRequireJSBlocks = [
    path.resolve(__dirname, 'common/static/common/js/components/utils/view_utils.js'),
    /xmodule\/js\/src/
];

var defineHeader = /\(function ?\(((define|require|requirejs|\$)(, )?)+\) ?\{/;
var defineCallFooter = /\}\)\.call\(this, ((define|require)( \|\| RequireJS\.(define|require))?(, )?)+?\);/;
var defineDirectFooter = /\}\(((window\.)?(RequireJS\.)?(requirejs|define|require|jQuery)(, )?)+\)\);/;
var defineFancyFooter = /\}\).call\(\s*this(\s|.)*define(\s|.)*\);/;
var defineFooter = new RegExp('(' + defineCallFooter.source + ')|('
                             + defineDirectFooter.source + ')|('
                             + defineFancyFooter.source + ')', 'm');

var staticRootLms = process.env.STATIC_ROOT_LMS || './test_root/staticfiles';
var staticRootCms = process.env.STATIC_ROOT_CMS || (staticRootLms + '/studio');

class DieHardPlugin {
  /* A small plugin which ensures that if Webpack fails, it causes the surrounding process to fail
   * as well. This helps us prevent JavaScript CI from "false passing" upon build failures--that is,
   * we want to avoid having another situation where the Webpack build breaks under Karma (our
   * test runner) but Karma just lets it slide and moves on to the next test suite.
   *
   * One would imagine that this would be Webpack's default behavior (and maybe it is?) but,
   * regardless, karma-webpack does not seem to consider Webpack build failures to be fatal errors
   * without this plugin. We don't fully understand it, but this is good enough given that we plan
   * to remove all JS in this repo soon (https://github.com/openedx/edx-platform/issues/31620).
   *
   * Inpsired by: https://github.com/codymikol/karma-webpack/issues/49#issuecomment-842682050
   */
  apply(compiler) {
    compiler.hooks.failed.tap('DieHardPlugin', (error) => {
      console.error(error);
      process.exit(1);
    });
  }
}

var workerConfig = function() {
    try {
        return {
            webworker: {
                target: 'webworker',
                context: __dirname,
                // eslint-disable-next-line global-require
                entry: require('../workers.json'),
                output: {
                    publicPath: "", // https://stackoverflow.com/a/65272040
                    filename: '[name].js',
                    path: path.resolve(__dirname, 'common/static/bundles')
                },
                plugins: [
                    new BundleTracker({
                        path: staticRootLms,
                        filename: 'webpack-worker-stats.json'
                    }),
                    new webpack.DefinePlugin({
                        'process.env.JS_ENV_EXTRA_CONFIG': JSON.parse(process.env.JS_ENV_EXTRA_CONFIG),
                        'CAPTIONS_CONTENT_TO_REPLACE': JSON.stringify(process.env.CAPTIONS_CONTENT_TO_REPLACE || ''),
                        'CAPTIONS_CONTENT_REPLACEMENT': JSON.stringify(process.env.CAPTIONS_CONTENT_REPLACEMENT || '')
                    })
                ],
                module: {
                    rules: [
                        {
                            test: /\.(js|jsx)$/,
                            include: [
                                /node_modules\//
                            ],
                            use: 'babel-loader'
                        }
                    ]
                },
                resolve: {
                    extensions: ['.js']
                }
            }
        };
    } catch (err) {
        return {};
    }
};

module.exports = Merge.merge({
    web: {
        context: __dirname,

        entry: {
            // Studio
            Import: './cms/static/js/features/import/factories/import.js',
            CourseOrLibraryListing: './cms/static/js/features_jsx/studio/CourseOrLibraryListing.jsx',
            'js/factories/textbooks': './cms/static/js/factories/textbooks.js',
            'js/factories/container': './cms/static/js/factories/container.js',
            'js/factories/context_course': './cms/static/js/factories/context_course.js',
            'js/factories/library': './cms/static/js/factories/library.js',
            'js/factories/xblock_validation': './cms/static/js/factories/xblock_validation.js',
            'js/factories/edit_tabs': './cms/static/js/factories/edit_tabs.js',
            'js/sock': './cms/static/js/sock.js',
            'js/factories/tag_count': './cms/static/js/factories/tag_count.js',

            // LMS
            SingleSupportForm: './lms/static/support/jsx/single_support_form.jsx',
            AlertStatusBar: './lms/static/js/accessible_components/StatusBarAlert.jsx',
            EntitlementSupportPage: './lms/djangoapps/support/static/support/jsx/entitlements/index.jsx',
            LinkProgramEnrollmentsSupportPage: './lms/djangoapps/support/static/support/jsx/'
                                               + 'program_enrollments/index.jsx',
            ProgramEnrollmentsInspectorPage: './lms/djangoapps/support/static/support/jsx/'
                                               + 'program_enrollments/inspector.jsx',
            PasswordResetConfirmation: './lms/static/js/student_account/components/PasswordResetConfirmation.jsx',
            StudentAccountDeletion: './lms/static/js/student_account/components/StudentAccountDeletion.jsx',
            StudentAccountDeletionInitializer: './lms/static/js/student_account/StudentAccountDeletionInitializer.js',
            ProblemBrowser: './lms/djangoapps/instructor/static/instructor/ProblemBrowser/index.jsx',
            EnterpriseLearnerPortalModal: './lms/static/js/learner_dashboard/EnterpriseLearnerPortalModal.jsx',

            // Learner Dashboard
            EntitlementFactory: './lms/static/js/learner_dashboard/course_entitlement_factory.js',
            EntitlementUnenrollmentFactory: './lms/static/js/learner_dashboard/entitlement_unenrollment_factory.js',
            ProgramDetailsFactory: './lms/static/js/learner_dashboard/program_details_factory.js',
            ProgramListFactory: './lms/static/js/learner_dashboard/program_list_factory.js',
            UnenrollmentFactory: './lms/static/js/learner_dashboard/unenrollment_factory.js',
            CompletionOnViewService: './lms/static/completion/js/CompletionOnViewService.js',

            // Features
            Currency: './openedx/features/course_experience/static/course_experience/js/currency.js',

            CookiePolicyBanner: './common/static/js/src/CookiePolicyBanner.jsx',

            // Common
            ReactRenderer: './common/static/js/src/ReactRenderer.jsx',
            XModuleShim: './xmodule/js/src/xmodule.js',
            VerticalStudentView: './xmodule/assets/vertical/public/js/vertical_student_view.js',
            commons: 'babel-polyfill'
        },

        output: {
            publicPath: "", // https://stackoverflow.com/a/65272040
            path: path.resolve(__dirname, 'common/static/bundles'),
            library: {
                type: 'window'
            }
        },

        plugins: [
            new webpack.ProgressPlugin(), // report progress during compilation
            new webpack.NoEmitOnErrorsPlugin(),
            new BundleTracker({
                path: staticRootCms,
                filename: 'webpack-stats.json'
            }),
            new BundleTracker({
                path: staticRootLms,
                filename: 'webpack-stats.json'
            }),
            new webpack.ProvidePlugin({
                _: 'underscore',
                $: 'jquery',
                jQuery: 'jquery',
                'window.jQuery': 'jquery',
                Popper: 'popper.js', // used by bootstrap
                CodeMirror: 'codemirror',
                'edx.HtmlUtils': 'edx-ui-toolkit/js/utils/html-utils',
                AjaxPrefix: 'ajax_prefix',
                // This is used by some XModules/XBlocks, which don't have
                // any other way to declare that dependency.
                $script: 'scriptjs'
            }),
            new DieHardPlugin(),
        ],

        module: {
            noParse: [
                // See sinon/webpack interaction weirdness:
                // https://github.com/webpack/webpack/issues/304#issuecomment-272150177
                // (I've tried every other suggestion solution on that page, this
                // was the only one that worked.)
                /\/sinon\.js|codemirror-compressed\.js|hls\.js|tinymce.js/
            ],
            rules: [
                {
                    test: files.namespacedRequire.concat(files.textBangUnderscore, filesWithRequireJSBlocks),
                    loader: 'string-replace-loader',
                    options: {
                        multiple: [
                            { search: defineHeader, replace: '' },
                            { search: defineFooter, replace: '' },
                            {
                                search: /(\/\* RequireJS) \*\//g,
                                replace(match, p1, offset, string) {
                                    return p1;
                                }
                            },
                            {
                                search: /\/\* Webpack/g,
                                replace(match, p1, offset, string) {
                                    return match + ' */';
                                }
                            },
                            {
                                search: /text!(.*?\.underscore)/g,
                                replace(match, p1, offset, string) {
                                    return p1;
                                }
                            },
                            { search: /RequireJS.require/g, replace: 'require' }
                        ]
                    }
                },
                {
                    test: /\.(js|jsx)$/,
                    exclude: [
                        /node_modules/,
                        files.namespacedRequire,
                        files.textBangUnderscore,
                        filesWithRequireJSBlocks
                    ],
                    use: 'babel-loader'
                },
                {
                    test: /\.(js|jsx)$/,
                    include: [
                        /paragon/
                    ],
                    use: 'babel-loader'
                },
                {
                    test: path.resolve(__dirname, 'common/static/js/src/ajax_prefix.js'),
                    use: [
                        'babel-loader',
                        {
                            loader: 'exports-loader',
                            options: {
                                'this.AjaxPrefix': true
                            }
                        }
                    ]
                },
                {
                    test: /\.underscore$/,
                    use: 'raw-loader'
                },
                {
                    test: /\.(woff2?|ttf|eot)(\?v=\d+\.\d+\.\d+)?$/,
                    loader: 'file-loader'
                },
                {
                    test: /\.svg$/,
                    loader: 'svg-inline-loader'
                },
                {
                    test: /xblock\/core/,
                    use: [
                        {
                            loader: 'exports-loader',
                            options: 'window.XBlock'
                        },
                        {
                            loader: 'imports-loader',
                            options: 'jquery,jquery.immediateDescendents,this=>window'
                        }
                    ]
                },
                {
                    test: /xblock\/runtime.v1/,
                    use: [
                        {
                            loader: 'exports-loader',
                            options: 'window.XBlock'
                        },
                        {
                            loader: 'imports-loader',
                            options: 'XBlock=xblock/core,this=>window'
                        }
                    ]
                },
                /** *****************************************************************************************************
                /* BUILT-IN XBLOCK ASSETS WITH GLOBAL DEFINITIONS:
                 *
                 * The monstrous list of globally-namespace modules below is the result of a JS build refactoring.
                 * Originally, all of these modules were copied to common/static/xmodule/js/[module|descriptors]/, and
                 * this file simply contained the lines:
                 *
                 *   {
                 *       test: /descriptors\/js/,
                 *       loader: 'imports-loader?this=>window'
                 *   },
                 *   {
                 *       test: /modules\/js/,
                 *       loader: 'imports-loader?this=>window'
                 *   },
                 *
                 * We removed that asset copying because it added complexity to the build, but as a result, in order to
                 * preserve exact parity with the preexisting global namespace, we had to enumerate all formely-copied
                 * modules here. It is very likely that many of these modules do not need to be in this list. Future
                 * refactorings are welcome to try to prune the list down to the minimal set of modules. As far as
                 * we know, the only modules that absolutely need to be added to the global namespace are those
                 * which define module types, for example "Problem" (in xmodule/js/src/capa/display.js).
                 */
                {
                    test: /xmodule\/assets\/word_cloud\/src\/js\/word_cloud.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/common_static\/js\/vendor\/draggabilly.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/annotatable\/display.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/capa\/display.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/capa\/imageinput.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/capa\/schematic.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/collapsible.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/conditional\/display.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/html\/display.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/html\/edit.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/html\/imageModal.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/javascript_loader.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/lti\/lti.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/poll\/poll.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/poll\/poll_main.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/problem\/edit.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/raw\/edit\/metadata-only.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/raw\/edit\/xml.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/sequence\/display.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/sequence\/edit.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/tabs\/tabs-aggregator.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/vertical\/edit.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                /*
                 * END BUILT-IN XBLOCK ASSETS WITH GLOBAL DEFINITIONS
                 ***************************************************************************************************** */
                {
                    test: /codemirror/,
                    use: [
                        {
                            loader: 'exports-loader',
                            options: 'window.CodeMirror'
                        }
                    ]
                },
                {
                    test: /tinymce/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /xmodule\/js\/src\/xmodule/,
                    use: [
                        {
                            loader: 'exports-loader',
                            options: 'window.XModule'
                        },
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ],
                },
                {
                    test: /mock-ajax/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'exports=>false'
                        }
                    ]
                },
                {
                    test: /d3.min/,
                    use: [
                        'babel-loader',
                        {
                            loader: 'exports-loader',
                            options: {
                                d3: true
                            }
                        }
                    ]
                },
                {
                    test: /logger/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                // spec files that use import
                {
                    test: /lms\/static\/completion\/js\/spec\/ViewedEvent_spec.js/,
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                {
                    test: /\.js$/,
                    include: [
                        "/lms/static/js/learner_dashboard/spec/"
                    ],
                    use: [
                        {
                            loader: 'imports-loader',
                            options: 'this=>window'
                        }
                    ]
                },
                // end spec files that use import
            ]
        },

        resolve: {
            extensions: ['.js', '.jsx', '.json'],
            alias: {
                AjaxPrefix: 'ajax_prefix',
                accessibility: 'accessibility_tools',
                codemirror: 'codemirror-compressed',
                datepair: 'timepicker/datepair',
                'edx-ui-toolkit': 'edx-ui-toolkit/src/', // @TODO: some paths in toolkit are not valid relative paths
                ieshim: 'ie_shim',
                jquery: 'jquery/src/jquery', // Use the non-diqst form of jQuery for better debugging + optimization
                'jquery.flot': 'flot/jquery.flot.min',
                'jquery.ui': 'jquery-ui.min',
                'jquery.tinymce': 'jquery.tinymce.min',
                'jquery.inputnumber': 'html5-input-polyfills/number-polyfill',
                'jquery.qtip': 'jquery.qtip.min',
                'jquery.smoothScroll': 'jquery.smooth-scroll.min',
                'jquery.timepicker': 'timepicker/jquery.timepicker',
                'backbone.associations': 'backbone-associations/backbone-associations-min',
                squire: 'Squire',
                tinymce: 'tinymce',

                // See sinon/webpack interaction weirdness:
                // https://github.com/webpack/webpack/issues/304#issuecomment-272150177
                // (I've tried every other suggestion solution on that page, this
                // was the only one that worked.)
                // eslint-disable-next-line no-path-concat
                sinon: __dirname + '/node_modules/sinon/pkg/sinon.js',
                hls: 'hls.js/dist/hls.js'
            },
            modules: [
                'cms/djangoapps/pipeline_js/js',
                'cms/static',
                'cms/static/cms/js',
                'cms/templates/js',
                'lms/static',
                path.resolve(__dirname),
                'xmodule/js/src',
                'xmodule/assets/word_cloud/src/js',
                'common/static',
                'common/static/coffee/src',
                'common/static/common/js',
                'common/static/common/js/vendor/',
                'common/static/common/js/components',
                'common/static/js/src',
                'common/static/js/vendor/',
                'common/static/js/vendor/jQuery-File-Upload/js/',
                'common/static/js/vendor/tinymce/js/tinymce',
                'node_modules',
                'common/static/xmodule'
            ],

            // We used to have node: { fs: 'empty' } in this file,
            // that is no longer supported. Adding this based on the recommendation in
            // https://stackoverflow.com/questions/64361940/webpack-error-configuration-node-has-an-unknown-property-fs
            //
            // With this uncommented tests fail
            // Tests failed in the following suites:
            // * lms javascript
            // * xmodule-webpack javascript
            // Error: define cannot be used indirect
            //
            // fallback: {
            //     fs: false
            // }
        },

        resolveLoader: {
            alias: {
                text: 'raw-loader' // Compatibility with RequireJSText's text! loader, uses raw-loader under the hood
            }
        },

        externals: {
            $: 'jQuery',
            backbone: 'Backbone',
            canvas: 'canvas',
            fs: 'fs',
            gettext: 'gettext',
            jquery: 'jQuery',
            logger: 'Logger',
            path: 'path',
            underscore: '_',
            URI: 'URI',
            XBlockToXModuleShim: 'XBlockToXModuleShim',
            XModule: 'XModule'
        },

        watchOptions: {
            poll: true
        },

    }
}, {web: builtinBlocksJS}, workerConfig());
