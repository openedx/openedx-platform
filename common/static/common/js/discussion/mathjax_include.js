if (typeof MathJax === 'undefined') {
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
    var vendorScript = document.createElement('script');
    vendorScript.src = 'https://cdn.jsdelivr.net/npm/mathjax@4.1.2/tex-mml-chtml.js';
    document.body.appendChild(vendorScript);
}
