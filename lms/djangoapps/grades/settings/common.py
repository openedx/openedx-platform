# pylint: disable=missing-function-docstring, missing-module-docstring
def plugin_settings(settings):
    # Queue to use for updating persistent grades
    settings.RECALCULATE_GRADES_ROUTING_KEY = settings.DEFAULT_PRIORITY_QUEUE

    # Queue to use for updating grades due to grading policy change
    settings.POLICY_CHANGE_GRADES_ROUTING_KEY = settings.DEFAULT_PRIORITY_QUEUE

    # Queue to use for individual learner course regrades
    settings.SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY = settings.DEFAULT_PRIORITY_QUEUE
