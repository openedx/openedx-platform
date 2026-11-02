module.exports = {
    entry: {
        AnnotatableBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/html/display.js',
            './xmodule/js/src/annotatable/display.js',
            './xmodule/js/src/javascript_loader.js',
            './xmodule/js/src/collapsible.js'
        ],
        AnnotatableBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/raw/edit/xml.js'
        ],
        ConditionalBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/conditional/display.js',
            './xmodule/js/src/javascript_loader.js',
            './xmodule/js/src/collapsible.js'
        ],
        ConditionalBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/sequence/edit.js'
        ],
        CustomTagBlockDisplay: './xmodule/js/src/xmodule.js',
        CustomTagBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/raw/edit/xml.js'
        ],
        HtmlBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/html/display.js',
            './xmodule/js/src/javascript_loader.js',
            './xmodule/js/src/collapsible.js',
            './xmodule/js/src/html/imageModal.js',
            './xmodule/js/common_static/js/vendor/draggabilly.js'
        ],
        HtmlBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/html/edit.js'
        ],
        LibraryContentBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/vertical/edit.js'
        ],
        LTIBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/lti/lti.js'
        ],
        LTIBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/raw/edit/metadata-only.js'
        ],
        PollBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/javascript_loader.js',
            './xmodule/js/src/poll/poll.js',
            './xmodule/js/src/poll/poll_main.js'
        ],
        PollBlockEditor: './xmodule/js/src/xmodule.js',
        ProblemBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/javascript_loader.js',
            './xmodule/js/src/capa/display.js',
            './xmodule/js/src/collapsible.js',
            './xmodule/js/src/capa/imageinput.js',
            './xmodule/js/src/capa/schematic.js'
        ],
        ProblemBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/problem/edit.js'
        ],
        SequenceBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/sequence/display.js'
        ],
        SequenceBlockEditor: './xmodule/js/src/xmodule.js',
        SplitTestBlockDisplay: './xmodule/js/src/xmodule.js',
        SplitTestBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/sequence/edit.js'
        ],
        VideoBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/tabs/tabs-aggregator.js'
        ],
        VideoBlockDisplay: [
            './xmodule/assets/video/public/js/10_main.js'
        ],
        WordCloudBlockDisplay: [
            './xmodule/js/src/xmodule.js',
            './xmodule/assets/word_cloud/src/js/word_cloud.js'
        ],
        WordCloudBlockEditor: [
            './xmodule/js/src/xmodule.js',
            './xmodule/js/src/raw/edit/metadata-only.js'
        ]
    }
};
