"""
i18n utility functions
"""


from django.utils.formats import dateformat, get_format
from django.utils.translation import override


def translate_date(date, language, date_format='DATE_FORMAT'):
    """
    Converts the provided date object into a string, while translating
    its value for the given language.  Both the format of the date
    as well as its values (i.e., name of the Month) are translated.

    If language is Spainish, then the entire date string is returned in
    lowercase. This is used to work around a bug in the Spanish django
    month translations.
    See EDUCATOR-2328 for more details.

    For example:
        date = datetime.datetime(2017, 12, 23)
        date_in_spanish = translate_date(date, 'es')
        assert date_in_spanish == '23 de deciembre de 2017'
    """
    with override(language):
        formatted_date = dateformat.format(
            date,
            get_format(date_format, lang=language, use_l10n=True),

        )
        if language and language.startswith('es'):
            formatted_date = formatted_date.lower()
        return formatted_date
