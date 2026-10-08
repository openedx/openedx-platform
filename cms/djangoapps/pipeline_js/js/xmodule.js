// This file is designed to load all the XModule Javascript files in one wad
// using requirejs. It is passed through the Mako template system, which
// populates the `urls` variable with a list of paths to XModule JS files.
// These files assume that several libraries are available and bound to
// variables in the global context, so we load those libraries with requirejs
// and attach them to the global context manually.
define(
    [
        'jquery', 'underscore', 'codemirror', 'tinymce', 'scriptjs',
        'jquery.tinymce', 'jquery.qtip', 'jquery.scrollTo', 'jquery.flot',
        'jquery.cookie',
        'utility'
    ],
    function($, _, CodeMirror, tinymce, $script) {
        'use strict';

        window.$ = $;
        window._ = _;
        window.MathJax = {
            tex: {
                inlineMath: [
                    ['\\(', '\\)'],
                    ['[mathjaxinline]', '[/mathjaxinline]']
                ],
                displayMath: [
                    ['\\[', '\\]'],
                    ['[mathjax]', '[/mathjax]']
                ],
                autoload: {
                    color: [],
                    colorv2: ['color']
                },
                packages: {'[+]': ['noerrors']}
            },
            options: {
                ignoreHtmlClass: 'tex2jax_ignore',
                processHtmlClass: 'tex2jax_process',
                menuOptions: {
                    settings: {
                        collapsible: true
                    },
                },
            },
            loader: {
                load: ['input/asciimath', '[tex]/noerrors']
            }
        };

        $script(
            'https://cdn.jsdelivr.net/npm/mathjax@4.1.2/tex-mml-chtml.js',
            'mathjax',
            function() {
                window.addEventListener('resize', MJrenderer);

                let t = -1;
                // eslint-disable-next-line prefer-const
                let delay = 1000;
                let oldWidth = document.documentElement.scrollWidth;
                function MJrenderer() {
                    // don't rerender if the window is the same size as before
                    if (t >= 0) {
                        window.clearTimeout(t);
                    }
                    if (oldWidth !== document.documentElement.scrollWidth) {
                        t = window.setTimeout(function() {
                            oldWidth = document.documentElement.scrollWidth;
                            MathJax.typesetClear();
                            MathJax.typesetPromise();
                            t = -1;
                        }, delay);
                    }
                }
            }
        );
        window.CodeMirror = CodeMirror;
        window.RequireJS = {
            requirejs: {}, // This is never used by current xmodules
            require: $script, // $script([deps], callback) acts approximately like the require function
            define: define
        };
        /**
         * Loads all modules one-by-one in exact order.
         * The module should be used until we'll use RequireJS for XModules.
         * @param {Array} modules A list of urls.
         * @return {jQuery Promise}
         * */
        function requireQueue(modules) {
            var deferred = $.Deferred();
            function loadScript(queue) {
                $script.ready('mathjax', function() {
                    // Loads the next script if queue is not empty.
                    if (queue.length) {
                        $script([queue.shift()], function() {
                            loadScript(queue);
                        });
                    } else {
                        deferred.resolve();
                    }
                });
            }

            loadScript(modules.concat());
            return deferred.promise();
        }

        // if (!window.xmoduleUrls) {
        //     throw Error('window.xmoduleUrls must be defined');
        // }
        return requireQueue([]);
    }
);
