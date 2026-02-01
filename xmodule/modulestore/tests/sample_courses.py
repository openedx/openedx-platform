"""
The data type and use of it for declaratively creating test courses.
"""


import datetime
from collections import namedtuple

# used to create course subtrees in ModuleStoreTestCase.create_test_course
# adds to self properties w/ the given block_id which hold the UsageKey for easy retrieval.
# fields is a dictionary of keys and values. sub_tree is a collection of BlockInfo
BlockInfo = namedtuple('BlockInfo', 'block_id, category, fields, sub_tree')

default_block_info_tree = [  # pylint: disable=invalid-name
    BlockInfo(
        'chapter_x', 'chapter', {}, [
            BlockInfo(
                'sequential_x1', 'sequential', {}, [
                    BlockInfo(
                        'vertical_x1a', 'vertical', {}, [
                            BlockInfo('problem_x1a_1', 'problem', {}, []),
                            BlockInfo('problem_x1a_2', 'problem', {}, []),
                            BlockInfo('problem_x1a_3', 'problem', {}, []),
                            BlockInfo('html_x1a_1', 'html', {}, []),
                        ]
                    )
                ]
            )
        ]
    ),
    BlockInfo(
        'chapter_y', 'chapter', {}, [
            BlockInfo(
                'sequential_y1', 'sequential', {}, [
                    BlockInfo(
                        'vertical_y1a', 'vertical', {}, [
                            BlockInfo('problem_y1a_1', 'problem', {}, []),
                            BlockInfo('problem_y1a_2', 'problem', {}, []),
                            BlockInfo('problem_y1a_3', 'problem', {}, []),
                        ]
                    )
                ]
            )
        ]
    )
]
# equivalent to toy course in xml
TOY_BLOCK_INFO_TREE = [
    BlockInfo(
        'Overview', "chapter", {"display_name": "Overview"}, [
            BlockInfo(
                "Toy_Videos", "sequential", {
                    "xml_attributes": {"filename": ["", None]}, "display_name": "Toy Videos", "format": "Lecture Sequence"  # lint-amnesty, pylint: disable=line-too-long
                }, [
                    BlockInfo(
                        "secret:toylab", "html", {
                            "data": "<b>Lab 2A: Superposition Experiment</b>\n\n\n<p>Isn't the toy course great?</p>\n"
                                    "\n<p>Let's add some markup that uses non-ascii characters.\n'For example,"
                                    " we should be able to write words like encyclop&aelig;dia, or foreign words like "
                                    "fran&ccedil;ais.\nLooking beyond latin-1, we should handle math symbols: "
                                    " &pi;r&sup2 &le; &#8734.\nAnd it shouldn't matter if we use entities or numeric"
                                    " codes &mdash; &Omega; &ne; &pi; &equiv; &#937; &#8800; &#960;.\n</p>\n\n",
                            "xml_attributes": {"filename": ["html/secret/toylab.xml", "html/secret/toylab.xml"]},
                            "display_name": "Toy lab"
                        }, []
                    ),
                    BlockInfo(
                        "toyjumpto", "html", {
                            "data": "<a href=\"/jump_to_id/vertical_test\">This is a link to another page and some Chinese 四節比分和七年前</a> <p>Some more Chinese 四節比分和七年前</p>\n",  # lint-amnesty, pylint: disable=line-too-long
                            "xml_attributes": {"filename": ["html/toyjumpto.xml", "html/toyjumpto.xml"]}
                        }, []),
                    BlockInfo(
                        "toyhtml", "html", {
                            "data": "<a href='/static/handouts/sample_handout.txt'>Sample</a>",
                            "xml_attributes": {"filename": ["html/toyhtml.xml", "html/toyhtml.xml"]}
                        }, []),
                    BlockInfo(
                        "nonportable", "html", {
                            "data": "<a href=\"/static/foo.jpg\">link</a>\n",
                            "xml_attributes": {"filename": ["html/nonportable.xml", "html/nonportable.xml"]}
                        }, []),
                    BlockInfo(
                        "nonportable_link", "html", {
                            "data": "<a href=\"/jump_to_id/nonportable_link\">link</a>\n\n",
                            "xml_attributes": {"filename": ["html/nonportable_link.xml", "html/nonportable_link.xml"]}
                        }, []),
                    BlockInfo(
                        "badlink", "html", {
                            "data": "<img src=\"/static//file.jpg\" />\n",
                            "xml_attributes": {"filename": ["html/badlink.xml", "html/badlink.xml"]}
                        }, []),
                    BlockInfo(
                        "with_styling", "html", {
                            "data": "<p style=\"font:italic bold 72px/30px Georgia, serif; color: red; \">Red text here</p>",  # lint-amnesty, pylint: disable=line-too-long
                            "xml_attributes": {"filename": ["html/with_styling.xml", "html/with_styling.xml"]}
                        }, []),
                    BlockInfo(
                        "just_img", "html", {
                            "data": "<img src=\"/static/foo_bar.jpg\" />",
                            "xml_attributes": {"filename": ["html/just_img.xml", "html/just_img.xml"]}
                        }, []),
                    BlockInfo(
                        "Video_Resources", "video", {
                            "youtube_id_1_0": "1bK-WdDi6Qw", "display_name": "Video Resources"
                        }, []),
                ]),
            BlockInfo(
                "Welcome", "video", {"data": "", "youtube_id_1_0": "p2Q6BrNhdh8", "display_name": "Welcome"}, []
            ),
            BlockInfo(
                "video_123456789012", "video", {"data": "", "youtube_id_1_0": "p2Q6BrNhdh8", "display_name": "Test Video"}, []  # lint-amnesty, pylint: disable=line-too-long
            ),
            BlockInfo(
                "video_4f66f493ac8f", "video", {"youtube_id_1_0": "p2Q6BrNhdh8"}, []
            )
        ]
    ),
    BlockInfo(
        "secret:magic", "chapter", {
            "xml_attributes": {"filename": ["chapter/secret/magic.xml", "chapter/secret/magic.xml"]}
        }, [
            BlockInfo(
                "toyvideo", "video", {"youtube_id_1_0": "OEoXaMPEzfMA", "display_name": "toyvideo"}, []
            )
        ]
    ),
    BlockInfo(
        "poll_test", "chapter", {}, [
            BlockInfo(
                "T1_changemind_poll_foo", "poll_question", {
                    "question": "<p>Have you changed your mind? ’</p>",
                    "answers": [{"text": "Yes", "id": "yes"}, {"text": "No", "id": "no"}],
                    "xml_attributes": {"reset": "false", "filename": ["", None]},
                    "display_name": "Change your answer"
                }, [])]
    ),
    BlockInfo(
        "vertical_container", "chapter", {
            "xml_attributes": {"filename": ["chapter/vertical_container.xml", "chapter/vertical_container.xml"]}
        }, [
            BlockInfo("vertical_sequential", "sequential", {}, [
                BlockInfo("vertical_test", "vertical", {
                    "xml_attributes": {"filename": ["vertical/vertical_test.xml", "vertical_test"]}
                }, [
                    BlockInfo(
                        "sample_video", "video", {
                            "youtube_id_1_0": "OEoXaMPEzfM",
                            "display_name": "default",
                        }, []),
                    BlockInfo(
                        "separate_file_video", "video", {
                            "youtube_id_1_0": "OEoXaMPEzfM",
                            "display_name": "default",
                        }, []),
                    BlockInfo(
                        "video_with_end_time", "video", {
                            "display_name": "default",
                            "youtube_id_1_0": "OEoXaMPEzfM",
                            "end_time": datetime.timedelta(seconds=10),
                        }, []),
                    BlockInfo(
                        "T1_changemind_poll_foo_2", "poll_question", {
                            "question": "<p>Have you changed your mind?</p>",
                            "answers": [{"text": "Yes", "id": "yes"}, {"text": "No", "id": "no"}],
                            "xml_attributes": {"reset": "false", "filename": ["", None]},
                            "display_name": "Change your answer"
                        }, []),
                ]),
                BlockInfo("unicode", "html", {
                    "data": "…", "xml_attributes": {"filename": ["", None]}
                }, [])
            ]),
        ]
    ),
    BlockInfo(
        "handout_container", "chapter", {
            "xml_attributes": {"filename": ["chapter/handout_container.xml", "chapter/handout_container.xml"]}
        }, [
            BlockInfo(
                "html_7e5578f25f79", "html", {
                    "data": "<a href=\"/static/handouts/sample_handout.txt\"> handouts</a>",
                    "xml_attributes": {"filename": ["", None]}
                }, []
            ),
        ]
    )
]
