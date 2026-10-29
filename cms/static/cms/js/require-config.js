/* globals _, requirejs */
/* eslint-disable quote-props, no-console, no-plusplus */

(function(require, define) {
    'use strict';

    var defineDependency, librarySetup;

    // We do not wish to bundle common libraries (that may also be used by non-RequireJS code on the page
    // into the optimized files. Therefore load these libraries through script tags and explicitly define them.
    // Note that when the optimizer executes this code, window will not be defined.
    if (window) {
        defineDependency = function(globalName, name, noShim) {
            var getGlobalValue = function() {
                    var globalNamePath = globalName.split('.'),
                        result = window,
                        i;
                    for (i = 0; i < globalNamePath.length; i++) {
                        result = result[globalNamePath[i]];
                    }
                    return result;
                },
                globalValue = getGlobalValue();
            if (globalValue) {
                if (noShim) {
                    define(name, {});
                } else {
                    define(name, [], function() { return globalValue; });
                }
            } else {
                console.error('Expected library to be included on page, but not found on window object: ' + name);
            }
        };

        librarySetup = function() {
            // This is the function to setup all the vendor libraries

            // Underscore.string no longer installs itself directly on '_'. For compatibility with existing
            // code, add it to '_' with its previous name.
            if (window._ && window.s) {
                window._.str = window.s;
            }

            window.$.ajaxSetup({
                contents: {
                    script: false
                }
            });

            // MathJax Fast Preview was introduced in 2.5. However, it
            // causes undesirable flashing/font size changes when
            // MathJax is used for interactive preview (equation editor).
            // Setting processSectionDelay to 0 (see below) fully eliminates
            // fast preview, but to reduce confusion, we are also setting
            // the option as displayed in the context menu to false.
            // When upgrading to 2.6, check if this variable name changed.
            window.MathJax = {
                menuSettings: {
                    CHTMLpreview: false,
                    collapsible: true,
                    autocollapse: false,
                    explorer: true
                }
            };
        };

        defineDependency('jQuery', 'jquery');
        defineDependency('jQuery', 'jquery-migrate');
        defineDependency('_', 'underscore');
        defineDependency('s', 'underscore.string');
        defineDependency('gettext', 'gettext');
        defineDependency('Logger', 'logger');
        defineDependency('URI', 'URI');
        defineDependency('jQuery.url', 'jquery.url');
        defineDependency('Backbone', 'backbone');

        // Add the UI Toolkit helper classes that have been installed in the 'edx' namespace
        defineDependency('edx.HtmlUtils', 'edx-ui-toolkit/js/utils/html-utils');
        defineDependency('edx.StringUtils', 'edx-ui-toolkit/js/utils/string-utils');

        // utility.js adds two functions to the window object, but does not return anything
        defineDependency('isExternal', 'utility', true);

        librarySetup();
    }

    require.config({
        // NOTE: baseUrl has been previously set in cms/static/templates/base.html
        waitSeconds: 60,
        paths: {
            'domReady': 'js/vendor/domReady',
            'codemirror': 'js/vendor/codemirror-compressed',
            'codemirror/stex': 'js/vendor/CodeMirror/stex',
            'jquery': 'common/js/vendor/jquery',
            'jquery-migrate': 'common/js/vendor/jquery-migrate',
            'jquery.ui': 'js/vendor/jquery-ui.min',
            'jquery.form': 'js/vendor/jquery.form',
            'jquery.markitup': 'js/vendor/markitup/jquery.markitup',
            'jquery.leanModal': 'js/vendor/jquery.leanModal',
            'jquery.ajaxQueue': 'js/vendor/jquery.ajaxQueue',
            'jquery.smoothScroll': 'js/vendor/jquery.smooth-scroll.min',
            'jquery.timepicker': 'js/vendor/timepicker/jquery.timepicker',
            'jquery.cookie': 'js/vendor/jquery.cookie',
            'jquery.qtip': 'js/vendor/jquery.qtip.min',
            'jquery.scrollTo': 'common/js/vendor/jquery.scrollTo',
            'jquery.flot': 'js/vendor/flot/jquery.flot.min',
            'jquery.fileupload': 'js/vendor/jQuery-File-Upload/js/jquery.fileupload',
            'jquery.fileupload-process': 'js/vendor/jQuery-File-Upload/js/jquery.fileupload-process',
            'jquery.fileupload-validate': 'js/vendor/jQuery-File-Upload/js/jquery.fileupload-validate',
            'jquery.iframe-transport': 'js/vendor/jQuery-File-Upload/js/jquery.iframe-transport',
            'jquery.inputnumber': 'js/vendor/html5-input-polyfills/number-polyfill',
            'jquery.immediateDescendents': 'js/src/jquery.immediateDescendents',
            'datepair': 'js/vendor/timepicker/datepair',
            'date': 'js/vendor/date',
            moment: 'common/js/vendor/moment-with-locales',
            'moment-timezone': 'common/js/vendor/moment-timezone-with-data',
            'text': 'js/vendor/requirejs/text',
            'underscore': 'common/js/vendor/underscore',
            'underscore.string': 'common/js/vendor/underscore.string',
            'backbone': 'common/js/vendor/backbone',
            'backbone-relational': 'js/vendor/backbone-relational.min',
            'backbone.associations': 'js/vendor/backbone-associations-min',
            'backbone.paginator': 'common/js/vendor/backbone.paginator',
            'tinymce': 'js/vendor/tinymce/js/tinymce/tinymce.full.min',
            'jquery.tinymce': 'js/vendor/tinymce/js/tinymce/jquery.tinymce.min',
            'xmodule': '/xmodule',
            'xblock/cms.runtime.v1': 'cms/js/xblock/cms.runtime.v1',
            'xblock': 'common/js/xblock',
            'utility': 'js/src/utility',
            'accessibility': 'js/src/accessibility_tools',
            'URI': 'js/vendor/URI.min',
            'ieshim': 'js/src/ie_shim',
            'tooltip_manager': 'js/src/tooltip_manager',
            'draggabilly': 'js/vendor/draggabilly',
            'hls': 'common/js/vendor/hls',
            'lang_edx': 'js/src/lang_edx',
            'jquery_extend_patch': 'js/src/jquery_extend_patch',

            // externally hosted files
            mathjax: 'https://cdn.jsdelivr.net/npm/mathjax@2.7.9/MathJax.js?config=TeX-MML-AM_SVG&delayStartupUntil=configured', // eslint-disable-line max-len
            'youtube': [
                // youtube URL does not end in '.js'. We add '?noext' to the path so
                // that require.js adds the '.js' to the query component of the URL,
                // and leaves the path component intact.
                '//www.youtube.com/player_api?noext',
                // if youtube fails to load, fallback on a local file
                // so that require doesn't fall over
                'js/src/youtube_fallback'
            ]
        },
        shim: {
            'gettext': {
                exports: 'gettext'
            },
            'date': {
                exports: 'Date'
            },
            'jquery-migrate': ['jquery'],
            'jquery.ui': {
                deps: ['jquery'],
                exports: 'jQuery.ui'
            },
            'jquery.form': {
                deps: ['jquery'],
                exports: 'jQuery.fn.ajaxForm'
            },
            'jquery.markitup': {
                deps: ['jquery'],
                exports: 'jQuery.fn.markitup'
            },
            'jquery.leanmodal': {
                deps: ['jquery'],
                exports: 'jQuery.fn.leanModal'
            },
            'jquery.ajaxQueue': {
                deps: ['jquery'],
                exports: 'jQuery.fn.ajaxQueue'
            },
            'jquery.smoothScroll': {
                deps: ['jquery'],
                exports: 'jQuery.fn.smoothScroll'
            },
            'jquery.cookie': {
                deps: ['jquery'],
                exports: 'jQuery.fn.cookie'
            },
            'jquery.qtip': {
                deps: ['jquery'],
                exports: 'jQuery.fn.qtip'
            },
            'jquery.scrollTo': {
                deps: ['jquery'],
                exports: 'jQuery.fn.scrollTo'
            },
            'jquery.flot': {
                deps: ['jquery'],
                exports: 'jQuery.fn.plot'
            },
            'jquery.fileupload': {
                deps: ['jquery.ui', 'jquery.iframe-transport'],
                exports: 'jQuery.fn.fileupload'
            },
            'jquery.fileupload-process': {
                deps: ['jquery.fileupload']
            },
            'jquery.fileupload-validate': {
                deps: ['jquery.fileupload']
            },
            'jquery.inputnumber': {
                deps: ['jquery'],
                exports: 'jQuery.fn.inputNumber'
            },
            'jquery.tinymce': {
                deps: ['jquery', 'tinymce'],
                exports: 'jQuery.fn.tinymce'
            },
            'datepair': {
                deps: ['jquery.ui', 'jquery.timepicker']
            },
            'underscore': {
                exports: '_'
            },
            'backbone': {
                deps: ['underscore', 'jquery'],
                exports: 'Backbone'
            },
            'backbone.associations': {
                deps: ['backbone'],
                exports: 'Backbone.Associations'
            },
            'backbone.paginator': {
                deps: ['backbone'],
                exports: 'Backbone.PageableCollection'
            },
            'youtube': {
                exports: 'YT'
            },
            'codemirror': {
                exports: 'CodeMirror'
            },
            'codemirror/stex': {
                deps: ['codemirror']
            },
            'tinymce': {
                exports: 'tinymce'
            },
            'lang_edx': {
                deps: ['jquery']
            },
            'mathjax': {
                exports: 'MathJax',
                init: function() {
                    window.MathJax.Hub.Config({
                        tex2jax: {
                            inlineMath: [
                                ['\\(', '\\)'],
                                ['[mathjaxinline]', '[/mathjaxinline]']
                            ],
                            displayMath: [
                                ['\\[', '\\]'],
                                ['[mathjax]', '[/mathjax]']
                            ]
                        }
                    });
                    // In order to eliminate all flashing during interactive
                    // preview, it is necessary to set processSectionDelay to 0
                    // (remove delay between input and output phases). This
                    // effectively disables fast preview, regardless of
                    // the fast preview setting as shown in the context menu.
                    window.MathJax.Hub.processSectionDelay = 0;
                    window.MathJax.Hub.Configured();
                }
            },
            'URI': {
                exports: 'URI'
            },
            'tooltip_manager': {
                deps: ['jquery', 'underscore']
            },
            'jquery.immediateDescendents': {
                deps: ['jquery']
            },
            'xblock/core': {
                exports: 'XBlock',
                deps: ['jquery', 'jquery.immediateDescendents']
            },
            'xblock/runtime.v1': {
                exports: 'XBlock',
                deps: ['xblock/core']
            },
            'cms/js/main': {
                deps: ['js/src/ajax_prefix']
            },
            'js/src/logger': {
                exports: 'Logger',
                deps: ['js/src/ajax_prefix']
            },

            // the following are all needed for annotation tools
            'video.dev': {
                exports: 'videojs'
            },
            'vjs.youtube': {
                deps: ['video.dev']
            },
            'rangeslider': {
                deps: ['video.dev']
            },
            'annotator': {
                exports: 'Annotator'
            },
            'annotator-harvardx': {
                deps: ['annotator']
            },
            'share-annotator': {
                deps: ['annotator']
            },
            'richText-annotator': {
                deps: ['annotator', 'tinymce']
            },
            'reply-annotator': {
                deps: ['annotator']
            },
            'tags-annotator': {
                deps: ['annotator']
            },
            'diacritic-annotator': {
                deps: ['annotator']
            },
            'flagging-annotator': {
                deps: ['annotator']
            },
            'grouping-annotator': {
                deps: ['annotator']
            },
            'ova': {
                exports: 'ova',
                deps: ['annotator', 'annotator-harvardx', 'video.dev', 'vjs.youtube',
                    'rangeslider', 'share-annotator', 'richText-annotator', 'reply-annotator',
                    'tags-annotator', 'flagging-annotator', 'grouping-annotator', 'diacritic-annotator',
                    'jquery-Watch', 'catch', 'handlebars', 'URI']
            },
            'osda': {
                exports: 'osda',
                deps: ['annotator', 'annotator-harvardx', 'video.dev', 'vjs.youtube',
                    'rangeslider', 'share-annotator', 'richText-annotator', 'reply-annotator',
                    'tags-annotator', 'flagging-annotator', 'grouping-annotator', 'diacritic-annotator',
                    'openseadragon', 'jquery-Watch', 'catch', 'handlebars', 'URI']
            },
            // end of annotation tool files

            // patch for jquery's extend
            'jquery_extend_patch': {
                deps: ['jquery']
            }
        },
        config: {
            text: {
                useXhr: () => true
            }
        }
    });
}).call(this, require, define);
