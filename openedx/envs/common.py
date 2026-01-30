"""
Common Django settings for Open edX services.

This module defines configuration shared between the LMS and CMS (Studio)
environments. It centralizes common settings in one place and reduces duplication.

Service-specific settings should import from this module and override as needed.

WARNING: Mutable values defined in this file may be unintentionally modified
downstream, if settings are shared across services. Some settings modules
(cms/envs/common.py, cms/envs/test.py) import settings across services
(the CMS imports settings from the LMS). In such cases, if an LMS settings
module modifies a mutable value defined here, the final value of the corresponding
CMS setting may also be affected. To avoid this risk, create a deep copy of the
value in the module that modifies it.

Note: More settings will be added to this file as the effort to simplify
settings moves forward. See docs/decisions/0022-settings-simplification.rst for
more details on the effort to simplify settings across Open edX services.

To create section headers in this file, use the following function:

```python
def center_with_hashes(text: str, width: int = 76):
    print(f"{f' {text} ':#^{width}}")
```
"""
import os
import importlib.util
from path import Path as path

from django.utils.translation import gettext_lazy as _

from openedx.core.lib.derived import Derived
from openedx.core.release import doc_version
from openedx.core.djangoapps.theming.helpers_dirs import (
    get_themes_unchecked,
    get_theme_base_dirs_from_settings
)

# We have legacy components that reference these constants via the settings module.
# New code should import them directly from `openedx.core.constants` instead.
from openedx.core.constants import (  # pylint: disable=unused-import
    ASSET_KEY_PATTERN,
    COURSE_KEY_REGEX,
    COURSE_KEY_PATTERN,
    COURSE_ID_PATTERN,
    USAGE_KEY_PATTERN,
    USAGE_ID_PATTERN,
)

from xmodule.modulestore.edit_info import EditInfoMixin
from xmodule.modulestore.inheritance import InheritanceMixin
from xmodule.x_module import XModuleMixin
from lms.djangoapps.lms_xblock.mixin import LmsBlockMixin

################ Shared Functions for Derived Configuration ################


def make_mako_template_dirs(settings):
    """
    Derives the final list of Mako template directories based on the provided settings.

    Args:
        settings: A Django settings module object.

    Returns:
        list: A list of Mako template directories, potentially updated with additional
        theme directories.
    """
    if settings.ENABLE_COMPREHENSIVE_THEMING:
        themes_dirs = get_theme_base_dirs_from_settings(settings.COMPREHENSIVE_THEME_DIRS)
        for theme in get_themes_unchecked(themes_dirs, settings.PROJECT_ROOT):
            if theme.themes_base_dir not in settings.MAKO_TEMPLATE_DIRS_BASE:
                settings.MAKO_TEMPLATE_DIRS_BASE.insert(0, theme.themes_base_dir)
    return settings.MAKO_TEMPLATE_DIRS_BASE


def _make_locale_paths(settings):
    """
    Constructs a list of paths to locale directories used for translation.

    Localization (l10n) strings (e.g. django.po) are found in these directories.

    Args:
        settings: A Django settings module object.

    Returns:
        list: A list of paths, `str` or `path.Path`, to locale directories.
    """
    locale_paths = list(settings.PREPEND_LOCALE_PATHS)
    locale_paths += [settings.REPO_ROOT + '/conf/locale']  # edx-platform/conf/locale/

    if settings.ENABLE_COMPREHENSIVE_THEMING:
        # Add locale paths to settings for comprehensive theming.
        for locale_path in settings.COMPREHENSIVE_THEME_LOCALE_PATHS:
            locale_paths += (path(locale_path), )
    return locale_paths

################################## Paths ###################################

REPO_ROOT = path(__file__).abspath().dirname().dirname().dirname()
COMMON_ROOT = REPO_ROOT / "common"
OPENEDX_ROOT = REPO_ROOT / "openedx"
ENV_ROOT = REPO_ROOT.dirname()  # virtualenv dir /edx-platform is in
COURSES_ROOT = ENV_ROOT / "data"
XMODULE_ROOT = REPO_ROOT / "xmodule"
DATA_DIR = COURSES_ROOT

############################# Django Built-Ins #############################

DEBUG = False

USE_TZ = True
TIME_ZONE = 'UTC'

# User-uploaded content
MEDIA_ROOT = '/edx/var/edxapp/media/'
MEDIA_URL = '/media/'

# Dummy secret key for dev/test
SECRET_KEY = 'dev key'

# IMPORTANT: With this enabled, the server must always be behind a proxy that strips the header HTTP_X_FORWARDED_PROTO
# from client requests. Otherwise, a user can fool our server into thinking it was an https connection. See
# https://docs.djangoproject.com/en/dev/ref/settings/#secure-proxy-ssl-header for other warnings.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_NAME = 'sessionid'
SESSION_COOKIE_SECURE = False
SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
SESSION_SAVE_EVERY_REQUEST = False
SESSION_SERIALIZER = 'openedx.core.lib.session_serializers.PickleSerializer'

CSRF_COOKIE_AGE = 60 * 60 * 24 * 7 * 52
CSRF_TRUSTED_ORIGINS = []
# It is highly recommended that you override this in any environment accessed by end users
CSRF_COOKIE_SECURE = False

ALLOWED_HOSTS = ['*']

# Clickjacking protection can be disbaled by setting this to 'ALLOW'
X_FRAME_OPTIONS = 'DENY'

ROOT_URLCONF = Derived(lambda settings: f'{settings.SERVICE_VARIANT}.urls')

ADMINS = []
MANAGERS = ADMINS

DEFAULT_FROM_EMAIL = 'registration@example.com'
SERVER_EMAIL = 'devops@example.com'

# Set request limits for maximum size of a request body and maximum number of GET/POST parameters. (>=Django 1.10)
# Limits are currently disabled - but can be used for finer-grained denial-of-service protection.
DATA_UPLOAD_MAX_MEMORY_SIZE = None
DATA_UPLOAD_MAX_NUMBER_FIELDS = None

# See https://github.com/openedx/edx-django-sites-extensions for more info.
# Default site to use if site matching request headers does not exist.
SITE_ID = 1

STATICFILES_DIRS = [
    COMMON_ROOT / "static",
    Derived(lambda settings: settings.PROJECT_ROOT / "static"),
    # Temporarily adding the following static path as we are migrating the built-in blocks' Sass to vanilla CSS.
    # Once all of the built-in blocks are extracted from edx-platform, we can remove this static path.
    # Relevant ticket: https://github.com/openedx/edx-platform/issues/35300
    XMODULE_ROOT / "static",
]

# List of finder classes that know how to find static files in various locations.
# Note: the pipeline finder is included to be able to discover optimized files
STATICFILES_FINDERS = [
    'openedx.core.djangoapps.theming.finders.ThemeFilesFinder',
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    'openedx.core.lib.xblock_pipeline.finder.XBlockPipelineFinder',
    'pipeline.finders.PipelineFinder',
]

AUTHENTICATION_BACKENDS = [
    'rules.permissions.ObjectPermissionBackend',
    'django.contrib.auth.backends.AllowAllUsersModelBackend',
    'bridgekeeper.backends.RulePermissionBackend',
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "common.djangoapps.util.password_policy_validators.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8
        }
    },
    {
        "NAME": "common.djangoapps.util.password_policy_validators.MaximumLengthValidator",
        "OPTIONS": {
            "max_length": 75
        }
    },
]

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage'
    },
    'staticfiles': {
        'BACKEND': 'openedx.core.storage.ProductionStorage'
    }
}

# Messages
MESSAGE_STORAGE = 'django.contrib.messages.storage.session.SessionStorage'

USE_I18N = True

# these languages display right to left
LANGUAGES_BIDI = ("he", "ar", "fa", "ur", "fa-ir", "rtl")

LANGUAGE_COOKIE_NAME = "openedx-language-preference"

LOCALE_PATHS = Derived(_make_locale_paths)

LANGUAGE_CODE = 'en'  # http://www.i18nguy.com/unicode/language-identifiers.html

# Sourced from http://www.localeplanet.com/icu/ and wikipedia
LANGUAGES = [
    ('en', 'English'),
    ('rtl', 'Right-to-Left Test Language'),
    ('eo', 'Dummy Language (Esperanto)'),  # Dummy languaged used for testing

    ('am', 'አማርኛ'),  # Amharic
    ('ar', 'العربية'),  # Arabic
    ('az', 'azərbaycanca'),  # Azerbaijani
    ('bg-bg', 'български (България)'),  # Bulgarian (Bulgaria)
    ('bn-bd', 'বাংলা (বাংলাদেশ)'),  # Bengali (Bangladesh)
    ('bn-in', 'বাংলা (ভারত)'),  # Bengali (India)
    ('bs', 'bosanski'),  # Bosnian
    ('ca', 'Català'),  # Catalan
    ('ca@valencia', 'Català (València)'),  # Catalan (Valencia)
    ('cs', 'Čeština'),  # Czech
    ('cy', 'Cymraeg'),  # Welsh
    ('da', 'dansk'),  # Danish
    ('de-de', 'Deutsch (Deutschland)'),  # German (Germany)
    ('el', 'Ελληνικά'),  # Greek
    ('en-uk', 'English (United Kingdom)'),  # English (United Kingdom)
    ('en@lolcat', 'LOLCAT English'),  # LOLCAT English
    ('en@pirate', 'Pirate English'),  # Pirate English
    ('es-419', 'Español (Latinoamérica)'),  # Spanish (Latin America)
    ('es-ar', 'Español (Argentina)'),  # Spanish (Argentina)
    ('es-ec', 'Español (Ecuador)'),  # Spanish (Ecuador)
    ('es-es', 'Español (España)'),  # Spanish (Spain)
    ('es-mx', 'Español (México)'),  # Spanish (Mexico)
    ('es-pe', 'Español (Perú)'),  # Spanish (Peru)
    ('et-ee', 'Eesti (Eesti)'),  # Estonian (Estonia)
    ('eu-es', 'euskara (Espainia)'),  # Basque (Spain)
    ('fa', 'فارسی'),  # Persian
    ('fa-ir', 'فارسی (ایران)'),  # Persian (Iran)
    ('fi-fi', 'Suomi (Suomi)'),  # Finnish (Finland)
    ('fil', 'Filipino'),  # Filipino
    ('fr', 'Français'),  # French
    ('gl', 'Galego'),  # Galician
    ('gu', 'ગુજરાતી'),  # Gujarati
    ('he', 'עברית'),  # Hebrew
    ('hi', 'हिन्दी'),  # Hindi
    ('hr', 'hrvatski'),  # Croatian
    ('hu', 'magyar'),  # Hungarian
    ('hy-am', 'Հայերեն (Հայաստան)'),  # Armenian (Armenia)
    ('id', 'Bahasa Indonesia'),  # Indonesian
    ('it-it', 'Italiano (Italia)'),  # Italian (Italy)
    ('ja-jp', '日本語 (日本)'),  # Japanese (Japan)
    ('kk-kz', 'қазақ тілі (Қазақстан)'),  # Kazakh (Kazakhstan)
    ('km-kh', 'ភាសាខ្មែរ (កម្ពុជា)'),  # Khmer (Cambodia)
    ('kn', 'ಕನ್ನಡ'),  # Kannada
    ('ko-kr', '한국어 (대한민국)'),  # Korean (Korea)
    ('lt-lt', 'Lietuvių (Lietuva)'),  # Lithuanian (Lithuania)
    ('ml', 'മലയാളം'),  # Malayalam
    ('mn', 'Монгол хэл'),  # Mongolian
    ('mr', 'मराठी'),  # Marathi
    ('ms', 'Bahasa Melayu'),  # Malay
    ('nb', 'Norsk bokmål'),  # Norwegian Bokmål
    ('ne', 'नेपाली'),  # Nepali
    ('nl-nl', 'Nederlands (Nederland)'),  # Dutch (Netherlands)
    ('or', 'ଓଡ଼ିଆ'),  # Oriya
    ('pl', 'Polski'),  # Polish
    ('pt-br', 'Português (Brasil)'),  # Portuguese (Brazil)
    ('pt-pt', 'Português (Portugal)'),  # Portuguese (Portugal)
    ('ro', 'română'),  # Romanian
    ('ru', 'Русский'),  # Russian
    ('si', 'සිංහල'),  # Sinhala
    ('sk', 'Slovenčina'),  # Slovak
    ('sl', 'Slovenščina'),  # Slovenian
    ('sq', 'shqip'),  # Albanian
    ('sr', 'Српски'),  # Serbian
    ('sv', 'svenska'),  # Swedish
    ('sw', 'Kiswahili'),  # Swahili
    ('ta', 'தமிழ்'),  # Tamil
    ('te', 'తెలుగు'),  # Telugu
    ('th', 'ไทย'),  # Thai
    ('tr-tr', 'Türkçe (Türkiye)'),  # Turkish (Turkey)
    ('uk', 'Українська'),  # Ukranian
    ('ur', 'اردو'),  # Urdu
    ('vi', 'Tiếng Việt'),  # Vietnamese
    ('uz', 'Ўзбек'),  # Uzbek
    ('zh-cn', '中文 (简体)'),  # Chinese (China)
    ('zh-hk', '中文 (香港)'),  # Chinese (Hong Kong)
    ('zh-tw', '中文 (台灣)'),  # Chinese (Taiwan)
]

CACHES = {
    'course_structure_cache': {
        'KEY_PREFIX': 'course_structure',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': '604800',  # 1 week
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'celery': {
        'KEY_PREFIX': 'celery',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': '7200',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'mongo_metadata_inheritance': {
        'KEY_PREFIX': 'mongo_metadata_inheritance',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'TIMEOUT': 300,
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'staticfiles': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'staticfiles_general',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'default': {
        'VERSION': '1',
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'default',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'configuration': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'configuration',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
    'general': {
        'KEY_FUNCTION': 'common.djangoapps.util.memcache.safe_key',
        'LOCATION': ['localhost:11211'],
        'KEY_PREFIX': 'general',
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'OPTIONS': {
            'no_delay': True,
            'ignore_exc': True,
            'use_pooling': True,
            'connect_timeout': 0.5
        }
    },
}

DATABASES = {
    # edxapp's edxapp-migrate scripts and the edxapp_migrate play
    # will ensure that any DB not named read_replica will be migrated
    # for both the lms and cms.
    'default': {
        'ATOMIC_REQUESTS': True,
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    },
    'read_replica': {
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    },
    'student_module_history': {
        'CONN_MAX_AGE': 0,
        'ENGINE': 'django.db.backends.mysql',
        'HOST': '127.0.0.1',
        'NAME': 'edxapp_csmh',
        'OPTIONS': {},
        'PASSWORD': 'password',
        'PORT': '3306',
        'USER': 'edxapp001'
    }
}

DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

DATABASE_ROUTERS = [
    'openedx.core.lib.django_courseware_routers.StudentModuleHistoryExtendedRouter',
]

TEMPLATES = [
    {
        'NAME': 'django',
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': [
            Derived(lambda settings: settings.PROJECT_ROOT / "templates"),
            COMMON_ROOT / 'templates',
            XMODULE_ROOT / 'capa' / 'templates',
            COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
            COMMON_ROOT / 'static',  # required to statically include common Underscore templates
        ],
        # Options specific to this backend.
        'OPTIONS': {
            'loaders': [
                # We have to use mako-aware template loaders to be able to include
                # mako templates inside django templates (such as main_django.html).
                'openedx.core.djangoapps.theming.template_loaders.ThemeTemplateLoader',
                'common.djangoapps.edxmako.makoloader.MakoFilesystemLoader',
                'common.djangoapps.edxmako.makoloader.MakoAppDirectoriesLoader',
            ],
            'context_processors': Derived(lambda settings: settings.CONTEXT_PROCESSORS),
            # Change 'debug' in your environment settings files - not here.
            'debug': False
        }
    },
    {
        'NAME': 'mako',
        'BACKEND': 'common.djangoapps.edxmako.backend.Mako',
        # Don't look for template source files inside installed applications.
        'APP_DIRS': False,
        # Instead, look for template source files in these dirs.
        'DIRS': Derived(make_mako_template_dirs),
        # Options specific to this backend.
        'OPTIONS': {
            'context_processors': Derived(lambda settings: settings.CONTEXT_PROCESSORS),
            # Change 'debug' in your environment settings files - not here.
            'debug': False,
        }
    },
]

################################ Templates #################################


def make_mako_module_dir(settings):
    """
    Returns the directory where Mako templates are stored.

    Args:
        settings: A Django settings module object.

    Returns:
        list: A list of Mako template directories, potentially updated with additional
        theme directories.
    """
    import tempfile
    return os.path.join(tempfile.gettempdir(), f'mako_{settings.SERVICE_VARIANT}')

MAKO_MODULE_DIR = Derived(make_mako_module_dir)
MAKO_TEMPLATE_DIRS_BASE = [
    Derived(lambda settings: settings.PROJECT_ROOT / 'templates'),
    COMMON_ROOT / 'templates',
    COMMON_ROOT / 'djangoapps' / 'pipeline_mako' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'cors_csrf' / 'templates',
    OPENEDX_ROOT / 'core' / 'djangoapps' / 'dark_lang' / 'templates',
    OPENEDX_ROOT / 'core' / 'lib' / 'license' / 'templates',
]

# Since the CMS uses the LMS's list of mako template directories for the "preview"
# template engine, we define the list here
lms_mako_template_dirs_base = list(MAKO_TEMPLATE_DIRS_BASE)
lms_mako_template_dirs_base.insert(2, XMODULE_ROOT / 'capa' / 'templates')
lms_mako_template_dirs_base.append(OPENEDX_ROOT / 'features' / 'course_experience' / 'templates')

CONTEXT_PROCESSORS = [
    'django.template.context_processors.request',
    'django.template.context_processors.static',
    'django.contrib.messages.context_processors.messages',
    'django.template.context_processors.i18n',
    'django.contrib.auth.context_processors.auth',  # this is required for admin
    'django.template.context_processors.csrf',
    # Online contextual help
    'help_tokens.context_processor',
    'openedx.core.djangoapps.site_configuration.context_processors.configuration_context',
]

DEFAULT_TEMPLATE_ENGINE = TEMPLATES[0]

############################## Site Settings ###############################

HTTPS = 'on'
SITE_NAME = "localhost"
FAVICON_PATH = 'images/favicon.ico'

BUGS_EMAIL = 'bugs@example.com'
CONTACT_EMAIL = 'info@example.com'
DEFAULT_FEEDBACK_EMAIL = 'feedback@example.com'
PRESS_EMAIL = 'press@example.com'
TECH_SUPPORT_EMAIL = 'technical@example.com'
UNIVERSITY_EMAIL = 'university@example.com'

################################# Language #################################

# Source:
# http://loc.gov/standards/iso639-2/ISO-639-2_utf-8.txt according to http://en.wikipedia.org/wiki/ISO_639-1
# Note that this is used as the set of choices to the `code` field of the `LanguageProficiency` model.
ALL_LANGUAGES = [
    ["aa", "Afar"],
    ["ab", "Abkhazian"],
    ["af", "Afrikaans"],
    ["ak", "Akan"],
    ["sq", "Albanian"],
    ["am", "Amharic"],
    ["ar", "Arabic"],
    ["an", "Aragonese"],
    ["hy", "Armenian"],
    ["as", "Assamese"],
    ["av", "Avaric"],
    ["ae", "Avestan"],
    ["ay", "Aymara"],
    ["az", "Azerbaijani"],
    ["ba", "Bashkir"],
    ["bm", "Bambara"],
    ["eu", "Basque"],
    ["be", "Belarusian"],
    ["bn", "Bengali"],
    ["bh", "Bihari languages"],
    ["bi", "Bislama"],
    ["bs", "Bosnian"],
    ["br", "Breton"],
    ["bg", "Bulgarian"],
    ["my", "Burmese"],
    ["ca", "Catalan"],
    ["ch", "Chamorro"],
    ["ce", "Chechen"],
    ["zh", "Chinese"],
    ["zh_HANS", "Simplified Chinese"],
    ["zh_HANT", "Traditional Chinese"],
    ["cu", "Church Slavic"],
    ["cv", "Chuvash"],
    ["kw", "Cornish"],
    ["co", "Corsican"],
    ["cr", "Cree"],
    ["cs", "Czech"],
    ["da", "Danish"],
    ["dv", "Divehi"],
    ["nl", "Dutch"],
    ["dz", "Dzongkha"],
    ["en", "English"],
    ["eo", "Esperanto"],
    ["et", "Estonian"],
    ["ee", "Ewe"],
    ["fo", "Faroese"],
    ["fj", "Fijian"],
    ["fi", "Finnish"],
    ["fr", "French"],
    ["fy", "Western Frisian"],
    ["ff", "Fulah"],
    ["ka", "Georgian"],
    ["de", "German"],
    ["gd", "Gaelic"],
    ["ga", "Irish"],
    ["gl", "Galician"],
    ["gv", "Manx"],
    ["el", "Greek"],
    ["gn", "Guarani"],
    ["gu", "Gujarati"],
    ["ht", "Haitian"],
    ["ha", "Hausa"],
    ["he", "Hebrew"],
    ["hz", "Herero"],
    ["hi", "Hindi"],
    ["ho", "Hiri Motu"],
    ["hr", "Croatian"],
    ["hu", "Hungarian"],
    ["ig", "Igbo"],
    ["is", "Icelandic"],
    ["io", "Ido"],
    ["ii", "Sichuan Yi"],
    ["iu", "Inuktitut"],
    ["ie", "Interlingue"],
    ["ia", "Interlingua"],
    ["id", "Indonesian"],
    ["ik", "Inupiaq"],
    ["it", "Italian"],
    ["jv", "Javanese"],
    ["ja", "Japanese"],
    ["kl", "Kalaallisut"],
    ["kn", "Kannada"],
    ["ks", "Kashmiri"],
    ["kr", "Kanuri"],
    ["kk", "Kazakh"],
    ["km", "Central Khmer"],
    ["ki", "Kikuyu"],
    ["rw", "Kinyarwanda"],
    ["ky", "Kirghiz"],
    ["kv", "Komi"],
    ["kg", "Kongo"],
    ["ko", "Korean"],
    ["kj", "Kuanyama"],
    ["ku", "Kurdish"],
    ["lo", "Lao"],
    ["la", "Latin"],
    ["lv", "Latvian"],
    ["li", "Limburgan"],
    ["ln", "Lingala"],
    ["lt", "Lithuanian"],
    ["lb", "Luxembourgish"],
    ["lu", "Luba-Katanga"],
    ["lg", "Ganda"],
    ["mk", "Macedonian"],
    ["mh", "Marshallese"],
    ["ml", "Malayalam"],
    ["mi", "Maori"],
    ["mr", "Marathi"],
    ["ms", "Malay"],
    ["mg", "Malagasy"],
    ["mt", "Maltese"],
    ["mn", "Mongolian"],
    ["na", "Nauru"],
    ["nv", "Navajo"],
    ["nr", "Ndebele, South"],
    ["nd", "Ndebele, North"],
    ["ng", "Ndonga"],
    ["ne", "Nepali"],
    ["nn", "Norwegian Nynorsk"],
    ["nb", "Bokmål, Norwegian"],
    ["no", "Norwegian"],
    ["ny", "Chichewa"],
    ["oc", "Occitan"],
    ["oj", "Ojibwa"],
    ["or", "Oriya"],
    ["om", "Oromo"],
    ["os", "Ossetian"],
    ["pa", "Panjabi"],
    ["fa", "Persian"],
    ["pi", "Pali"],
    ["pl", "Polish"],
    ["pt", "Portuguese"],
    ["ps", "Pushto"],
    ["qu", "Quechua"],
    ["rm", "Romansh"],
    ["ro", "Romanian"],
    ["rn", "Rundi"],
    ["ru", "Russian"],
    ["sg", "Sango"],
    ["sa", "Sanskrit"],
    ["si", "Sinhala"],
    ["sk", "Slovak"],
    ["sl", "Slovenian"],
    ["se", "Northern Sami"],
    ["sm", "Samoan"],
    ["sn", "Shona"],
    ["sd", "Sindhi"],
    ["so", "Somali"],
    ["st", "Sotho, Southern"],
    ["es", "Spanish"],
    ["sc", "Sardinian"],
    ["sr", "Serbian"],
    ["ss", "Swati"],
    ["su", "Sundanese"],
    ["sw", "Swahili"],
    ["sv", "Swedish"],
    ["ty", "Tahitian"],
    ["ta", "Tamil"],
    ["tt", "Tatar"],
    ["te", "Telugu"],
    ["tg", "Tajik"],
    ["tl", "Tagalog"],
    ["th", "Thai"],
    ["bo", "Tibetan"],
    ["ti", "Tigrinya"],
    ["to", "Tonga (Tonga Islands)"],
    ["tn", "Tswana"],
    ["ts", "Tsonga"],
    ["tk", "Turkmen"],
    ["tr", "Turkish"],
    ["tw", "Twi"],
    ["ug", "Uighur"],
    ["uk", "Ukrainian"],
    ["ur", "Urdu"],
    ["uz", "Uzbek"],
    ["ve", "Venda"],
    ["vi", "Vietnamese"],
    ["vo", "Volapük"],
    ["cy", "Welsh"],
    ["wa", "Walloon"],
    ["wo", "Wolof"],
    ["xh", "Xhosa"],
    ["yi", "Yiddish"],
    ["yo", "Yoruba"],
    ["za", "Zhuang"],
    ["zu", "Zulu"]
]

LANGUAGE_DICT = dict(LANGUAGES)

############################## Optional Apps ###############################

OPTIONAL_APPS = [
    ('problem_builder', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('edx_sga', None),

    # edx-ora2
    ('submissions', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.assessment', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.fileupload', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.staffgrader', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.workflow', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),
    ('openassessment.xblock', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # edxval
    ('edxval', 'openedx.core.djangoapps.content.course_overviews.apps.CourseOverviewsConfig'),

    # Enterprise Apps (http://github.com/openedx/edx-enterprise)
    ('enterprise', None),
    ('consent', None),
    ('integrated_channels.integrated_channel', None),
    ('integrated_channels.degreed', None),
    ('integrated_channels.degreed2', None),
    ('integrated_channels.sap_success_factors', None),
    ('integrated_channels.cornerstone', None),
    ('integrated_channels.xapi', None),
    ('integrated_channels.blackboard', None),
    ('integrated_channels.canvas', None),
    ('integrated_channels.moodle', None),
]


def add_optional_apps(optional_apps, installed_apps):
    """
    Adds apps from optional_apps to installed_apps if they can be imported.

    :param optional_apps: List of tuples (str, str). The tuples should contain the name
        of the app and the name of the app which it should be inserted before.
    :param installed_apps: List of installed Django apps to modify (i.e. INSTALLED_APPS)
    """
    for app_name, insert_before in optional_apps:
        # First attempt to only find the module rather than actually importing it,
        # to avoid circular references - only try to import if it can't be found
        # by find_spec, which doesn't work with import hooks
        if importlib.util.find_spec(app_name) is None:
            try:
                __import__(app_name)
            except ImportError:
                continue

        try:
            installed_apps.insert(installed_apps.index(insert_before), app_name)
        except (IndexError, ValueError):
            installed_apps.append(app_name)

########################## Django Rest Framework ###########################

REST_FRAMEWORK = {
    # These default classes add observability around endpoints using defaults, and should
    # not be used anywhere else.
    # Notes on Order:
    # 1. `JwtAuthentication` does not check `is_active`, so email validation does not affect it. However,
    #    `SessionAuthentication` does. These work differently, and order changes in what way, which really stinks. See
    #    https://github.com/openedx/public-engineering/issues/165 for details.
    # 2. `JwtAuthentication` may also update the database based on contents. Since the LMS creates these JWTs, this
    #    shouldn't have any affect at this time. But it could, when and if another service started creating the JWTs.
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'openedx.core.djangolib.default_auth_classes.DefaultJwtAuthentication',
        'openedx.core.djangolib.default_auth_classes.DefaultSessionAuthentication',
    ],
    'DEFAULT_PAGINATION_CLASS': 'edx_rest_framework_extensions.paginators.DefaultPagination',
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    'EXCEPTION_HANDLER': 'openedx.core.lib.request_utils.ignored_error_exception_handler',
    'PAGE_SIZE': 10,
    'URL_FORMAT_OVERRIDE': None,
    'DEFAULT_THROTTLE_RATES': {
        'user': '60/minute',
        'service_user': '800/minute',
        'registration_validation': '30/minute',
        'high_service_user': '2000/minute',
    },
}

# .. setting_name: REGISTRATION_VALIDATION_RATELIMIT
# .. setting_default: 30/7d
# .. setting_description: Whenever a user tries to register on edx, the data entered during registration
#    is validated via RegistrationValidationView.
#    It's POST endpoint is rate-limited up to 30 requests per IP Address in a week by default.
#    It was introduced because an attacker can guess or brute force a series of names to enumerate valid users.
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/24664
REGISTRATION_VALIDATION_RATELIMIT = '30/7d'

# .. setting_name: REGISTRATION_RATELIMIT
# .. setting_default: 60/7d
# .. setting_description: New users are registered on edx via RegistrationView.
#    It's POST end-point is rate-limited up to 60 requests per IP Address in a week by default.
#    Purpose of this setting is to restrict an attacker from registering numerous fake accounts.
# .. setting_tickets: https://github.com/openedx/edx-platform/pull/27060
REGISTRATION_RATELIMIT = '60/7d'

################################## Celery ##################################

BROKER_HEARTBEAT = 60.0
BROKER_HEARTBEAT_CHECKRATE = 2

CELERY_BROKER_USE_SSL = False
CELERY_BROKER_HOSTNAME = ''
CELERY_BROKER_PASSWORD = ''
CELERY_BROKER_TRANSPORT = ''
CELERY_BROKER_USER = ''
CELERY_BROKER_VHOST = ''
CELERY_RESULT_BACKEND = 'django-cache'
CELERY_EVENT_QUEUE_TTL = None

CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Message configuration
CELERY_MESSAGE_COMPRESSION = 'gzip'

# Results configuration
CELERY_IGNORE_RESULT = False
CELERY_STORE_ERRORS_EVEN_IF_IGNORED = True

# Events configuration
CELERY_TRACK_STARTED = True

CELERY_SEND_EVENTS = True
CELERY_SEND_TASK_SENT_EVENT = True

# Queues configuration
CELERY_QUEUE_HA_POLICY = 'all'
CELERY_CREATE_MISSING_QUEUES = True

# Exchange configuration
CELERY_DEFAULT_EXCHANGE_TYPE = 'direct'
CELERY_DEFAULT_EXCHANGE = Derived(lambda settings: f'edx.{settings.SERVICE_VARIANT}.core')

CELERY_DEFAULT_QUEUE = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)
CELERY_DEFAULT_ROUTING_KEY = CELERY_DEFAULT_QUEUE = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

# Checks run in normal mode by the heartbeat djangoapp
HEARTBEAT_CHECKS = [
    'openedx.core.djangoapps.heartbeat.default_checks.check_modulestore',
    'openedx.core.djangoapps.heartbeat.default_checks.check_database',
]

# Other checks to run by default in "extended"/heavy mode
HEARTBEAT_EXTENDED_CHECKS = (
    'openedx.core.djangoapps.heartbeat.default_checks.check_celery',
)

HEARTBEAT_CELERY_TIMEOUT = 5

HEARTBEAT_CELERY_ROUTING_KEY = Derived(lambda settings: settings.HIGH_PRIORITY_QUEUE)

############################ RedirectMiddleware ############################

# Setting this to None causes Redirect data to never expire
# The cache is cleared when Redirect models are saved/deleted
REDIRECT_CACHE_TIMEOUT = None  # The length of time we cache Redirect model data
REDIRECT_CACHE_KEY_PREFIX = 'redirects'

########################### Django Debug Toolbar ###########################

# We don't enable Django Debug Toolbar universally, but whenever we do, we want
# to avoid patching settings.  Patched settings can cause circular import
# problems: https://django-debug-toolbar.readthedocs.org/en/1.0/installation.html#explicit-setup

DEBUG_TOOLBAR_PATCH_SETTINGS = False

################################### JWT ####################################

JWT_AUTH = {
    'JWT_VERIFY_EXPIRATION': True,

    'JWT_PAYLOAD_GET_USERNAME_HANDLER': lambda d: d.get('username'),
    'JWT_LEEWAY': 1,
    'JWT_DECODE_HANDLER': 'edx_rest_framework_extensions.auth.jwt.decoder.jwt_decode_handler',

    'JWT_AUTH_COOKIE': 'edx-jwt-cookie',

    # Number of seconds before JWTs expire
    'JWT_EXPIRATION': 30,
    'JWT_IN_COOKIE_EXPIRATION': 60 * 60,

    'JWT_LOGIN_CLIENT_ID': 'login-service-client-id',
    'JWT_LOGIN_SERVICE_USERNAME': 'login_service_user',

    'JWT_SUPPORTED_VERSION': '1.2.0',

    'JWT_ALGORITHM': 'HS256',
    'JWT_SECRET_KEY': SECRET_KEY,

    'JWT_SIGNING_ALGORITHM': 'RS512',
    'JWT_PRIVATE_SIGNING_JWK': None,
    'JWT_PUBLIC_SIGNING_JWK_SET': None,

    'JWT_ISSUER': 'http://127.0.0.1:8000/oauth2',
    'JWT_AUDIENCE': 'change-me',
    'JWT_ISSUERS': [
        {
            'ISSUER': 'http://127.0.0.1:8000/oauth2',
            'AUDIENCE': 'change-me',
            'SECRET_KEY': SECRET_KEY
        }
    ],
    'JWT_AUTH_COOKIE_HEADER_PAYLOAD': 'edx-jwt-cookie-header-payload',
    'JWT_AUTH_COOKIE_SIGNATURE': 'edx-jwt-cookie-signature',
    'JWT_AUTH_HEADER_PREFIX': 'JWT',
}

EDX_DRF_EXTENSIONS = {
    # Set this value to an empty dict in order to prevent automatically updating
    # user data from values in (possibly stale) JWTs.
    'JWT_PAYLOAD_USER_ATTRIBUTE_MAPPING': {},
}

################################# Features #################################

# .. setting_name: PLATFORM_NAME
# .. setting_default: Your Platform Name Here
# .. setting_description: The display name of the platform to be used in
#     templates/emails/etc.
PLATFORM_NAME = _('Your Platform Name Here')
PLATFORM_DESCRIPTION = _('Your Platform Description Here')

PLATFORM_FACEBOOK_ACCOUNT = "http://www.facebook.com/YourPlatformFacebookAccount"
PLATFORM_TWITTER_ACCOUNT = "@YourPlatformTwitterAccount"

ENABLE_JASMINE = False

# .. toggle_name: DISABLE_START_DATES
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When True, all courses will be active, regardless of start
#   date.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-07-24
# .. toggle_warning: This will cause ALL courses to be immediately visible.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/17913
## DO NOT SET TO True IN THIS FILE
## Doing so will cause all courses to be released on production
DISABLE_START_DATES = False

# .. toggle_name: ENABLE_DISCUSSION_SERVICE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When True, it will enable the Discussion tab in courseware for all courses. Setting this
#   to False will not contain inline discussion components and discussion tab in any courses.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-08-14
# .. toggle_warning: If the discussion panel is present in the course and the value for this flag is False then,
#   attempting to expand those components will cause errors. So, this should only be set to False with an LMS that
#   is running courses that do not contain discussion components.
#   For consistency in user-experience, keep the value in sync with the setting of the same name in the CMS.
ENABLE_DISCUSSION_SERVICE = True

# .. toggle_name: ENABLE_TEXTBOOK
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Add PDF and HTML textbook tabs to the courseware.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-03-27
# .. toggle_warning: For consistency in user-experience, keep the value in sync with the setting of the same name
#   in the CMS.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/3064
ENABLE_TEXTBOOK = True

# Allows to configure the LMS to provide CORS headers to serve requests from other
# domains
ENABLE_CORS_HEADERS = False

# Can be turned off to disable the help link in the navbar
# .. toggle_name: ENABLE_HELP_LINK
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When True, a help link is displayed on the main navbar. Set False to hide it.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-03-05
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/26106
ENABLE_HELP_LINK = True

# Enable URL that shows information about the status of various services
ENABLE_SERVICE_STATUS = False

# Don't autoplay videos for students/course authors
AUTOPLAY_VIDEOS = False

# Move the student/course author to next page when a video finishes. Set to
# True to show an auto-advance button in videos. If False, videos never
# auto-advance.
ENABLE_AUTOADVANCE_VIDEOS = False

# .. toggle_name: CUSTOM_COURSES_EDX
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable Custom Courses for edX, a feature that is more commonly known as
#   CCX. Documentation for configuring and using this feature is available at
#   https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/enable_ccx.html
# .. toggle_warning: When set to true, 'lms.djangoapps.ccx.overrides.CustomCoursesForEdxOverrideProvider' will
#    be added to MODULESTORE_FIELD_OVERRIDE_PROVIDERS
# .. toggle_use_cases: opt_in, circuit_breaker
# .. toggle_creation_date: 2015-04-10
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6636
CUSTOM_COURSES_EDX = False

# Settings for course import olx validation
ENABLE_COURSE_OLX_VALIDATION = False

# .. toggle_name: AUTOMATIC_AUTH_FOR_TESTING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to perform acceptance and load test. Auto auth view is responsible for load
#    testing and is controlled by this feature flag. Session verification (of CacheBackedAuthenticationMiddleware)
#    is a security feature, but it can be turned off by enabling this feature flag.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-07-25
# .. toggle_warning: If this has been set to True then the account activation email will be skipped.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/417
AUTOMATIC_AUTH_FOR_TESTING = False

# .. toggle_name: RESTRICT_AUTOMATIC_AUTH
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Prevent auto auth from creating superusers or modifying existing users. Auto auth is a
#   mechanism where superusers can simply modify attributes of other users by accessing the "/auto_auth url" with
#   the right
#   querystring parameters.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2018-05-07
# .. toggle_tickets: https://openedx.atlassian.net/browse/TE-2545
RESTRICT_AUTOMATIC_AUTH = True

# .. toggle_name: EMBARGO
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Turns on embargo functionality, which blocks users from
#   the site or courses based on their location. Embargo can restrict users by states
#   and whitelist/blacklist (IP Addresses (ie. 10.0.0.0), Networks (ie. 10.0.0.0/24)), or the user profile country.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-02-27
# .. toggle_target_removal_date: None
# .. toggle_warning: reverse proxy should be configured appropriately for example Client IP address headers
#   (e.g HTTP_X_FORWARDED_FOR) should be configured.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2749
EMBARGO = False

# .. toggle_name: ENABLE_MKTG_SITE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle to enable alternate urls for marketing links.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-03-24
# .. toggle_warning: When this is enabled, the MKTG_URLS setting should be defined. The use case of this feature
#   toggle is uncertain.
ENABLE_MKTG_SITE = False

# Expose Mobile REST API.
ENABLE_MOBILE_REST_API = False

# Let students save and manage their annotations
# .. toggle_name: settings.ENABLE_EDXNOTES
# .. toggle_implementation: SettingToggle
# .. toggle_default: False
# .. toggle_description: This toggle enables the students to save and manage their annotations in the
#   course using the notes service. The bulk of the actual work in storing the notes is done by
#   a separate service (see the edx-notes-api repo).
# .. toggle_warning: Requires the edx-notes-api service properly running and to have configured the django settings
#   EDXNOTES_INTERNAL_API and EDXNOTES_PUBLIC_API. If you update this setting, also update it in Studio.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-01-04
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/6321
ENABLE_EDXNOTES = False

# Toggle to enable coordination with the Publisher tool (keep in sync between the LMS and CMS)
ENABLE_PUBLISHER = False

# Milestones application flag
MILESTONES_APP = False

# Prerequisite courses feature flag
ENABLE_PREREQUISITE_COURSES = False

# .. toggle_name: LICENSING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Toggle platform-wide course licensing. The course.license attribute is then used to append
#   license information to the courseware.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-05-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7315
LICENSING = False

# .. toggle_name: CERTIFICATES_HTML_VIEW
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable course certificates on your instance of Open edX.
# .. toggle_warning: You must enable this feature flag in both Studio and the LMS and complete the configuration tasks
#   described here:
#   https://docs.openedx.org/en/latest/site_ops/install_configure_run_guide/configuration/enable_certificates.html  pylint: disable=line-too-long,useless-suppression
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-03-13
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/7113
CERTIFICATES_HTML_VIEW = False

# Teams feature
ENABLE_TEAMS = True

# Show video bumper
ENABLE_VIDEO_BUMPER = False

# How many seconds to show the bumper again, default is 7 days:
SHOW_BUMPER_PERIODICITY = 7 * 24 * 3600

# .. toggle_name: ENABLE_SPECIAL_EXAMS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enable to use special exams, aka timed and proctored exams.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-09-04
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/9744
ENABLE_SPECIAL_EXAMS = False

# .. toggle_name: SHOW_HEADER_LANGUAGE_SELECTOR
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set to True, language selector will be visible in the header.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-05-25
# .. toggle_warning: You should set the languages in the DarkLangConfig table to get this working. If you have
#   not set any languages in the DarkLangConfig table then the language selector will not be visible in the header.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15133
SHOW_HEADER_LANGUAGE_SELECTOR = False

# At edX it's safe to assume that English transcripts are always available
# This is not the case for all installations.
# The default value here and in xmodule/tests/test_video.py should be consistent.
FALLBACK_TO_ENGLISH_TRANSCRIPTS = True

# .. toggle_name: SHOW_FOOTER_LANGUAGE_SELECTOR
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When set to True, language selector will be visible in the footer.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-05-25
# .. toggle_warning: LANGUAGE_COOKIE_NAME is required to use footer-language-selector, set it if it has not been set.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/15133
SHOW_FOOTER_LANGUAGE_SELECTOR = False

# .. toggle_name: ENABLE_CSMH_EXTENDED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Write Courseware Student Module History (CSMH) to the extended table: this logs all
#   student activities to MySQL, in a separate database.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-11-05
# .. toggle_warning: Even though most Open edX instances run with a separate CSMH database, it may not always be
#   the case. When disabling this feature flag, remember to remove "lms.djangoapps.coursewarehistoryextended"
#   from the INSTALLED_APPS and the "StudentModuleHistoryExtendedRouter" from the DATABASE_ROUTERS. This is needed
#   in the LMS and CMS for migration consistency.
ENABLE_CSMH_EXTENDED = True

# Read from both the CSMH and CSMHE history tables.
# This is the default, but can be disabled if all history
# lives in the Extended table, saving the frontend from
# making multiple queries.
ENABLE_READING_FROM_MULTIPLE_HISTORY_TABLES = True

# .. toggle_name: ALLOW_PUBLIC_ACCOUNT_CREATION
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Allow public account creation. If this is disabled, users will no longer have access to
#   the signup page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-04-12
# .. toggle_tickets: https://openedx.atlassian.net/browse/YONK-513
ALLOW_PUBLIC_ACCOUNT_CREATION = True

# .. toggle_name: SHOW_REGISTRATION_LINKS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Allow registration links. If this is disabled, users will no longer see buttons to the
#   the signup page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-03-27
SHOW_REGISTRATION_LINKS = True

# Whether or not the dynamic EnrollmentTrackUserPartition should be registered.
ENABLE_ENROLLMENT_TRACK_USER_PARTITION = True

# .. toggle_name: ENABLE_PASSWORD_RESET_FAILURE_EMAIL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Whether to send an email for failed password reset attempts or not. This happens when a
#   user asks for a password reset but they don't have an account associated to their email. This is useful for
#   notifying users that they don't have an account associated with email addresses they believe they've registered
#   with. This setting can be overridden by a site-specific configuration.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2017-07-20
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1832
ENABLE_PASSWORD_RESET_FAILURE_EMAIL = False

# Enable feature to remove enrollments and users. Used to reset state of master's integration environments
ENABLE_ENROLLMENT_RESET = False

# .. toggle_name: settings.DISABLE_MOBILE_COURSE_AVAILABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to remove Mobile Course Available UI Flag from Studio's Advanced Settings
#   page else Mobile Course Available UI Flag will be available on Studio side.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-02-14
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/23073
DISABLE_MOBILE_COURSE_AVAILABLE = False

# .. toggle_name: ENABLE_CHANGE_USER_PASSWORD_ADMIN
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable changing a user password through django admin. This is disabled by
#   default because enabling allows a method to bypass password policy.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2020-02-21
# .. toggle_tickets: 'https://github.com/openedx/edx-platform/pull/21616'
ENABLE_CHANGE_USER_PASSWORD_ADMIN = False

### ORA Feature Flags ###

# .. toggle_name: ENABLE_ORA_ALL_FILE_URLS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: A "work-around" feature toggle meant to help in cases where some file uploads are not
#   discoverable.  If enabled, will iterate through all possible file key suffixes up to the max for displaying
#   file metadata in staff assessments.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-03-03
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
ENABLE_ORA_ALL_FILE_URLS = False

# .. toggle_name: ENABLE_ORA_USER_STATE_UPLOAD_DATA
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: A "work-around" feature toggle meant to help in cases where some file uploads are not
#   discoverable.  If enabled, will pull file metadata from StudentModule.state for display in staff assessments.
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2020-03-03
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/EDUCATOR-4951
# .. toggle_warning: This temporary feature toggle does not have a target removal date.
ENABLE_ORA_USER_STATE_UPLOAD_DATA = False

# .. toggle_name: ENABLE_INTEGRITY_SIGNATURE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Whether to display honor code agreement for learners before their first grade assignment.
# The honor code agreement replaces the ID verification requirement (https://github.com/edx/edx-name-affirmation).
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-02-15
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/MST-1348'
ENABLE_INTEGRITY_SIGNATURE = False

# .. toggle_name: ENABLE_LTI_PII_ACKNOWLEDGEMENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Enables the lti pii acknowledgement feature for a course
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2023-10
# .. toggle_target_removal_date: None
# .. toggle_tickets: 'https://2u-internal.atlassian.net/browse/MST-2055'
ENABLE_LTI_PII_ACKNOWLEDGEMENT = False

# .. toggle_name: MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: If enabled, the Library Content Block is marked as complete when users view it.
#   Otherwise (by default), all children of this block must be completed.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2022-03-22
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/28268
# .. toggle_warning: For consistency in user-experience, keep the value in sync between the LMS and CMS
MARK_LIBRARY_CONTENT_BLOCK_COMPLETE_ON_VIEW = False

# .. toggle_name: DISABLE_UNENROLLMENT
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to disable self-unenrollments via REST API.
#   This also hides the "Unenroll" button on the Learner Dashboard.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-10-11
# .. toggle_warning: For consistency in user experience, keep the value in sync with the setting of the same name
#   in the LMS and CMS.
# .. toggle_tickets: 'https://github.com/open-craft/edx-platform/pull/429'
DISABLE_UNENROLLMENT = False

# .. toggle_name: BADGES_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Set to True to enable badges functionality.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2024-04-02
# .. toggle_target_removal_date: None
BADGES_ENABLED = False

# .. toggle_name: ENABLE_CREDIT_ELIGIBILITY
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: When enabled, it is possible to define a credit eligibility criteria in the CMS. A "Credit
#   Eligibility" section then appears for those courses in the LMS.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2015-06-17
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/8550
ENABLE_CREDIT_ELIGIBILITY = True

# .. toggle_name: ENABLE_COPPA_COMPLIANCE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When True, enforces COPPA compliance and removes YOB field from registration form and account
# .. settings page. Also hide YOB banner from profile page.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-10-27
# .. toggle_tickets: 'https://openedx.atlassian.net/browse/VAN-622'
ENABLE_COPPA_COMPLIANCE = False

# .. toggle_name: settings.ENABLE_DISCUSSION_HOME_PANEL
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: Hides or displays a welcome panel under the Discussion tab, which includes a subscription
#   on/off setting for discussion digest emails. (Note: set to False by default in the CMS).
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2013-07-30
# .. toggle_warning: This should remain off in production until digest notifications are online.
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/520
ENABLE_DISCUSSION_HOME_PANEL: bool

# .. toggle_name: settings.ENABLE_MAX_FAILED_LOGIN_ATTEMPTS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This feature will keep track of the number of failed login attempts on a given user's
#   email. If the number of consecutive failed login attempts - without a successful login at some point - reaches
#   a configurable threshold (default 6), then the account will be locked for a configurable amount of seconds
#   (30 minutes) which will prevent additional login attempts until this time period has passed. If a user
#   successfully logs in, all the counter which tracks the number of failed attempts will be reset back to 0. If
#   set to False then account locking will be disabled for failed login attempts. (Note: set to False by default in the CMS).
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2014-01-30
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/2331
ENABLE_MAX_FAILED_LOGIN_ATTEMPTS: bool

###################### CAPA External Code Evaluation #######################

# Used with XQueue
XQUEUE_WAITTIME_BETWEEN_REQUESTS = 5  # seconds
XQUEUE_INTERFACE = {
    'url': 'http://localhost:18040',
    'basic_auth': ['edx', 'edx'],
    'django_auth': {
        'username': 'lms',
        'password': 'password'
    }
}

################################### CSRF ###################################

# If setting a cross-domain cookie, it's really important to choose
# a name for the cookie that is DIFFERENT than the cookies used
# by each subdomain.  For example, suppose the applications
# at these subdomains are configured to use the following cookie names:
#
# 1) foo.example.com --> "csrftoken"
# 2) baz.example.com --> "csrftoken"
# 3) bar.example.com --> "csrftoken"
#
# For the cross-domain version of the CSRF cookie, you need to choose
# a name DIFFERENT than "csrftoken"; otherwise, the new token configured
# for ".example.com" could conflict with the other cookies,
# non-deterministically causing 403 responses.
CROSS_DOMAIN_CSRF_COOKIE_NAME = ''

# When setting the domain for the "cross-domain" version of the CSRF
# cookie, you should choose something like: ".example.com"
# (note the leading dot), where both the referer and the host
# are subdomains of "example.com".
#
# Browser security rules require that
# the cookie domain matches the domain of the server; otherwise
# the cookie won't get set.  And once the cookie gets set, the client
# needs to be on a domain that matches the cookie domain, otherwise
# the client won't be able to read the cookie.
CROSS_DOMAIN_CSRF_COOKIE_DOMAIN = ''

ENABLE_CROSS_DOMAIN_CSRF_COOKIE = False

########################## Cross-domain Requests ###########################

if ENABLE_CORS_HEADERS:
    CORS_ALLOW_CREDENTIALS = True
    CORS_ORIGIN_WHITELIST = ()
    CORS_ORIGIN_ALLOW_ALL = False
    CORS_ALLOW_INSECURE = False

# .. setting_name: LOGIN_REDIRECT_WHITELIST
# .. setting_default: empty list ([])
# .. setting_description: While logout, if logout request has a redirect-url as query strings,
#   then the redirect-url is validated through LOGIN_REDIRECT_WHITELIST.
LOGIN_REDIRECT_WHITELIST = []

######################## Social Media Footer Links #########################

# The footer URLs dictionary maps social footer names
# to URLs defined in configuration.
SOCIAL_MEDIA_FOOTER_ACE_URLS = {
    'reddit': 'http://www.reddit.com/r/edx',
    'twitter': 'https://twitter.com/edXOnline',
    'linkedin': 'http://www.linkedin.com/company/edx',
    'facebook': 'http://www.facebook.com/EdxOnline',
}

# The social media logo urls dictionary maps social media names
# to the respective icons
SOCIAL_MEDIA_LOGO_URLS = {
    'reddit': 'http://email-media.s3.amazonaws.com/edX/2021/social_5_reddit.png',
    'twitter': 'http://email-media.s3.amazonaws.com/edX/2021/social_2_twitter.png',
    'linkedin': 'http://email-media.s3.amazonaws.com/edX/2021/social_3_linkedin.png',
    'facebook': 'http://email-media.s3.amazonaws.com/edX/2021/social_1_fb.png',
}

########################### Social Media Sharing ###########################

# Social Media Sharing on Student Dashboard
SOCIAL_SHARING_SETTINGS = {
    'CUSTOM_COURSE_URLS': False,  # This value should not differ between lms/cms
    'DASHBOARD_FACEBOOK': False,
    'CERTIFICATE_FACEBOOK': False,
    'CERTIFICATE_TWITTER': False,
    'DASHBOARD_TWITTER': False,
}

############################# Google Analytics #############################

GOOGLE_ANALYTICS_ACCOUNT = None

############################# Block Structures #############################

# .. setting_name: BLOCK_STRUCTURES_SETTINGS
# .. setting_default: dict of settings
# .. setting_description: Stores all the settings used by block structures and block structure
#   related tasks. See BLOCK_STRUCTURES_SETTINGS[XXX] documentation for details of each setting.
#   For more information, check https://github.com/openedx/edx-platform/pull/13388.
BLOCK_STRUCTURES_SETTINGS = dict(
    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['COURSE_PUBLISH_TASK_DELAY']
    # .. setting_default: 30
    # .. setting_description: Delay, in seconds, after a new edit of a course is published before
    #   updating the block structures cache. This is needed for a better chance at getting
    #   the latest changes when there are secondary reads in sharded mongoDB clusters.
    #   For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    COURSE_PUBLISH_TASK_DELAY=30,

    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['TASK_DEFAULT_RETRY_DELAY']
    # .. setting_default: 30
    # .. setting_description: Delay, in seconds, between retry attempts if a block structure task
    #   fails. For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    TASK_DEFAULT_RETRY_DELAY=30,

    # .. setting_name: BLOCK_STRUCTURES_SETTINGS['TASK_MAX_RETRIES']
    # .. setting_default: 5
    # .. setting_description: Maximum number of retries per block structure task.
    #   If the maximum number of retries is exceeded, then you can attempt to either manually run
    #   the celery task, or wait for it to be triggered again.
    #   For more information, check https://github.com/openedx/edx-platform/pull/13388 and
    #   https://github.com/openedx/edx-platform/pull/14571.
    TASK_MAX_RETRIES=5,
)

################################ Bulk Email ################################

# Suffix used to construct 'from' email address for bulk emails.
# A course-specific identifier is prepended.
BULK_EMAIL_DEFAULT_FROM_EMAIL = 'no-reply@example.com'

# Parameters for breaking down course enrollment into subtasks.
BULK_EMAIL_EMAILS_PER_TASK = 500

# Flag to indicate if individual email addresses should be logged as they are sent
# a bulk email message.
BULK_EMAIL_LOG_SENT_EMAILS = False

################################## Video ###################################

YOUTUBE = {
    # YouTube JavaScript API
    'API': 'https://www.youtube.com/iframe_api',

    'TEST_TIMEOUT': 1500,

    # URL to get YouTube metadata
    'METADATA_URL': 'https://www.googleapis.com/youtube/v3/videos/',

    # Web page mechanism for scraping transcript information from youtube video pages
    'TRANSCRIPTS': {
        'CAPTION_TRACKS_REGEX': r"captionTracks\"\:\[(?P<caption_tracks>[^\]]+)",
        'YOUTUBE_URL_BASE': 'https://www.youtube.com/watch?v=',
        'ALLOWED_LANGUAGE_CODES': ["en", "en-US", "en-GB"],
    },

    'IMAGE_API': 'http://img.youtube.com/vi/{youtube_id}/0.jpg',  # /maxresdefault.jpg for 1920*1080
}

YOUTUBE_API_KEY = 'PUT_YOUR_API_KEY_HERE'

########################### Video Image Storage ############################

VIDEO_IMAGE_SETTINGS = dict(
    VIDEO_IMAGE_MAX_BYTES=2 * 1024 * 1024,    # 2 MB
    VIDEO_IMAGE_MIN_BYTES=2 * 1024,       # 2 KB
    # Backend storage
    # STORAGE_CLASS='storages.backends.s3boto3.S3Boto3Storage',
    # STORAGE_KWARGS=dict(bucket='video-image-bucket'),
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='video-images/',
    BASE_URL=MEDIA_URL,

)

VIDEO_IMAGE_MAX_AGE = 31536000

######################## Video Transcripts Storage #########################

VIDEO_TRANSCRIPTS_SETTINGS = dict(
    VIDEO_TRANSCRIPTS_MAX_BYTES=3 * 1024 * 1024,    # 3 MB
    # Backend storage
    # STORAGE_CLASS='storages.backends.s3boto3.S3Boto3Storage',
    # STORAGE_KWARGS=dict(bucket='video-transcripts-bucket'),
    STORAGE_KWARGS=dict(
        location=MEDIA_ROOT,
    ),
    DIRECTORY_PREFIX='video-transcripts/',
    BASE_URL=MEDIA_URL,
)

VIDEO_TRANSCRIPTS_MAX_AGE = 31536000

TRANSCRIPT_LANG_CACHE_TIMEOUT = 60 * 60 * 24  # 24 hours

############################## Video Pipeline ##############################

VIDEO_UPLOAD_PIPELINE = {
    'VEM_S3_BUCKET': '',
    'BUCKET': '',
    'ROOT_PATH': '',
}

############################ Parental Controls #############################

# .. setting_name: PARENTAL_CONSENT_AGE_LIMIT
# .. setting_default: 13
# .. setting_description: The age at which a learner no longer requires parental consent,
#   or ``None`` if parental consent is never required.
PARENTAL_CONSENT_AGE_LIMIT = 13

########################### Instructor Downloads ###########################

# These keys are used for all of our asynchronous downloadable files, including
# the ones that contain information other than grades.
GRADES_DOWNLOAD = {
    'STORAGE_CLASS': 'django.core.files.storage.FileSystemStorage',
    'STORAGE_KWARGS': {
        'location': '/tmp/edx-s3/grades',
    },
    'STORAGE_TYPE': None,
    'BUCKET': None,
    'ROOT_PATH': None,
}

FINANCIAL_REPORTS = {
    'STORAGE_TYPE': 'localfs',
    'BUCKET': None,
    'ROOT_PATH': 'sandbox',
}

############################### Registration ###############################

# .. setting_name: REGISTRATION_EMAIL_PATTERNS_ALLOWED
# .. setting_default: None
# .. setting_description: Optional setting to restrict registration / account creation
#   to only emails that match a regex in this list. Set to ``None`` to allow any email (default).
REGISTRATION_EMAIL_PATTERNS_ALLOWED = None

# String length for the configurable part of the auto-generated username
AUTO_GENERATED_USERNAME_RANDOM_STRING_LENGTH = 4

SHOW_ACTIVATE_CTA_POPUP_COOKIE_NAME = 'show-account-activation-popup'

# .. toggle_name: SOME_FEATURE_NAME
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: Flag would be used to show account activation popup after the registration
# .. toggle_use_cases: open_edx
# .. toggle_tickets: https://github.com/openedx/edx-platform/pull/27661
# .. toggle_creation_date: 2021-06-10
SHOW_ACCOUNT_ACTIVATION_CTA = False

# .. setting_name: REGISTRATION_EXTRA_FIELDS
# .. setting_default: {'confirm_email': 'hidden', 'level_of_education': 'optional', 'gender': 'optional',
#   'year_of_birth': 'optional', 'mailing_address': 'optional', 'goals': 'optional', 'honor_code': 'required',
#   'terms_of_service': 'hidden', 'city': 'hidden', 'country': 'hidden'}
# .. setting_description: The signup form may contain extra fields that are presented to every user. For every field, we
#   can specifiy whether it should be "required": to display the field, and make it mandatory; "optional": to display
#   the optional field as part of a toggled input field list; "optional-exposed": to display the optional fields among
#   the required fields, and make it non-mandatory; "hidden": to not display the field.
#   When the terms of service are not visible and agreement to the honor code is required (the default), the signup page
#   includes a paragraph that links to the honor code page (defined my MKTG_URLS["HONOR"]). This page might not be
#   available for all Open edX platforms. In such cases, the "honor_code" registration field should be "hidden".
REGISTRATION_EXTRA_FIELDS = {
    'confirm_email': 'hidden',
    'level_of_education': 'optional',
    'gender': 'optional',
    'year_of_birth': 'optional',
    'mailing_address': 'optional',
    'goals': 'optional',
    'honor_code': 'required',
    'terms_of_service': 'hidden',
    'city': 'hidden',
    'country': 'hidden',
}

######################### Course Enrollment Modes ##########################

# The min_price key refers to the minimum price allowed for an instance
# of a particular type of course enrollment mode. This is not to be confused
# with the min_price field of the CourseMode model, which refers to the actual
# price of the CourseMode.
COURSE_ENROLLMENT_MODES = {
    "audit": {
        "id": 1,
        "slug": "audit",
        "display_name": _("Audit"),
        "min_price": 0,
    },
    "verified": {
        "id": 2,
        "slug": "verified",
        "display_name": _("Verified"),
        "min_price": 1,
    },
    "professional": {
        "id": 3,
        "slug": "professional",
        "display_name": _("Professional"),
        "min_price": 1,
    },
    "no-id-professional": {
        "id": 4,
        "slug": "no-id-professional",
        "display_name": _("No-Id-Professional"),
        "min_price": 0,
    },
    "credit": {
        "id": 5,
        "slug": "credit",
        "display_name": _("Credit"),
        "min_price": 0,
    },
    "honor": {
        "id": 6,
        "slug": "honor",
        "display_name": _("Honor"),
        "min_price": 0,
    },
    "masters": {
        "id": 7,
        "slug": "masters",
        "display_name": _("Master's"),
        "min_price": 0,
    },
    "executive-education": {
        "id": 8,
        "slug": "executive-educations",
        "display_name": _("Executive Education"),
        "min_price": 1
    },
    "unpaid-executive-education": {
        "id": 9,
        "slug": "unpaid-executive-education",
        "display_name": _("Unpaid Executive Education"),
        "min_price": 0
    },
    "paid-executive-education": {
        "id": 10,
        "slug": "paid-executive-education",
        "display_name": _("Paid Executive Education"),
        "min_price": 1
    },
    "unpaid-bootcamp": {
        "id": 11,
        "slug": "unpaid-bootcamp",
        "display_name": _("Unpaid Bootcamp"),
        "min_price": 0
    },
    "paid-bootcamp": {
        "id": 12,
        "slug": "paid-bootcamp",
        "display_name": _("Paid Bootcamp"),
        "min_price": 1
    },
}

CONTENT_TYPE_GATE_GROUP_IDS = {
    'limited_access': 1,
    'full_access': 2,
}

########################## Enterprise Api Client ###########################

ENTERPRISE_CATALOG_INTERNAL_ROOT_URL = 'http://enterprise.catalog.app:18160'

ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_KEY = "enterprise-backend-service-key"
ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_SECRET = "enterprise-backend-service-secret"
ENTERPRISE_BACKEND_SERVICE_EDX_OAUTH2_PROVIDER_URL = "http://127.0.0.1:8000/oauth2"


############################### ModuleStore ################################

ASSET_IGNORE_REGEX = r"(^\._.*$)|(^\.DS_Store$)|(^.*~$)"

# This Django setting was removed in Django 4.0, but it is still being referenced
# in the code. It would be worth investigating if we still need this and, if we do,
# leave a comment explaining why here. See https://github.com/openedx/edx-platform/issues/37744.
DEFAULT_HASHING_ALGORITHM = 'sha256'

DOC_STORE_CONFIG = {
    'db': 'edxapp',
    'host': 'localhost',
    'replicaSet': '',
    'port': 27017,
    'user': 'edxapp',
    'collection': 'modulestore',
    'ssl': False,
    # https://api.mongodb.com/python/2.9.1/api/pymongo/mongo_client.html#module-pymongo.mongo_client
    # default is never timeout while the connection is open,
    #this means it needs to explicitly close raising pymongo.errors.NetworkTimeout
    'socketTimeoutMS': 6000,
    'connectTimeoutMS': 2000,  # default is 20000, I believe raises pymongo.errors.ConnectionFailure
    # Not setting waitQueueTimeoutMS and waitQueueMultiple since pymongo defaults to nobody being allowed to wait
    'auth_source': None,
    # If 'asset_collection' defined, it'll be used as the collection name for asset metadata.
    # Otherwise, a default collection name will be used.
}

CONTENTSTORE = {
    'ENGINE': 'xmodule.contentstore.mongo.MongoContentStore',
    # connection strings are duplicated temporarily for
    # backward compatibility
    'OPTIONS': {
        'db': 'edxapp',
        'host': 'localhost',
        'password': 'password',
        'port': 27017,
        'user': 'edxapp',
        'ssl': False,
        'auth_source': None
    },
    'ADDITIONAL_OPTIONS': {},
    'DOC_STORE_CONFIG': DOC_STORE_CONFIG
}

MODULESTORE = {
    'default': {
        'ENGINE': 'xmodule.modulestore.mixed.MixedModuleStore',
        'OPTIONS': {
            'mappings': {},
            'stores': [
                {
                    'NAME': 'split',
                    'ENGINE': 'xmodule.modulestore.split_mongo.split_draft.DraftVersioningModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': Derived(lambda settings: settings.DATA_DIR),
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                },
                {
                    'NAME': 'draft',
                    'ENGINE': 'xmodule.modulestore.mongo.DraftMongoModuleStore',
                    'DOC_STORE_CONFIG': DOC_STORE_CONFIG,
                    'OPTIONS': {
                        'default_class': 'xmodule.hidden_block.HiddenBlock',
                        'fs_root': Derived(lambda settings: settings.DATA_DIR),
                        'render_template': 'common.djangoapps.edxmako.shortcuts.render_to_string',
                    }
                }
            ]
        }
    }
}

# Modulestore-level field override providers. These field override providers don't
# require student context.
MODULESTORE_FIELD_OVERRIDE_PROVIDERS = ()

############################# Micro-frontends ##############################

# .. setting_name: ACCOUNT_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based account settings page.
# .. setting_warning: Also set site's ENABLE_ACCOUNT_MICROFRONTEND and
#     account.redirect_to_microfrontend waffle flag
ACCOUNT_MICROFRONTEND_URL = None

# .. setting_name: LEARNING_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based courseware page.
LEARNING_MICROFRONTEND_URL = None

# .. setting_name: DISCUSSIONS_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based discussions page.
# .. setting_warning: Also set site's courseware.discussions_mfe waffle flag.
DISCUSSIONS_MICROFRONTEND_URL = None

# .. setting_name: DISCUSSIONS_MFE_FEEDBACK_URL
# .. setting_default: None
# .. setting_description: Base URL of the discussions micro-frontend google form based feedback.
DISCUSSIONS_MFE_FEEDBACK_URL = None

# .. toggle_name: ENABLE_DYNAMIC_REGISTRATION_FIELDS
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle adds fields configured in
# REGISTRATION_EXTRA_FIELDS to Authn MFE
# .. toggle_use_cases: temporary
# .. toggle_creation_date: 2022-04-21
# .. toggle_target_removal_date: None
# .. toggle_tickets: https://openedx.atlassian.net/browse/VAN-838
ENABLE_DYNAMIC_REGISTRATION_FIELDS = False

# .. setting_name: LEARNER_HOME_MICROFRONTEND_URL
# .. setting_default: None
# .. setting_description: Base URL of the micro-frontend-based learner home page.
LEARNER_HOME_MICROFRONTEND_URL = None

################################## Swift ###################################

SWIFT_USERNAME = None
SWIFT_KEY = None
SWIFT_TENANT_ID = None
SWIFT_TENANT_NAME = None
SWIFT_AUTH_URL = None
SWIFT_AUTH_VERSION = None
SWIFT_REGION_NAME = None
SWIFT_USE_TEMP_URLS = None
SWIFT_TEMP_URL_KEY = None
SWIFT_TEMP_URL_DURATION = 1800  # seconds

################################### SAML ###################################

SOCIAL_AUTH_SAML_SP_PRIVATE_KEY = ""
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT = ""
SOCIAL_AUTH_SAML_SP_PRIVATE_KEY_DICT = {}
SOCIAL_AUTH_SAML_SP_PUBLIC_CERT_DICT = {}

########################### django-fernet-fields ###########################

FERNET_KEYS = [
    'DUMMY KEY CHANGE BEFORE GOING TO PRODUCTION',
]

########################## django-simple-history ###########################

# disable indexing on date field its coming from django-simple-history.
SIMPLE_HISTORY_DATE_INDEX = False

########################### Django OAuth Toolkit ###########################

# This is required for the migrations in oauth_dispatch.models
# otherwise it fails saying this attribute is not present in Settings

# Although Studio does not enable OAuth2 Provider capability, the new approach
# to generating test databases will discover and try to create all tables
# and this setting needs to be present

OAUTH2_PROVIDER_APPLICATION_MODEL = 'oauth2_provider.Application'

############################## Profile Image ###############################

# The following PROFILE_IMAGE_* settings are included as common settings as
# they are indirectly accessed through the email opt-in API, which is
# technically accessible through the CMS via legacy URLs.

# WARNING: Certain django storage backends do not support atomic
# file overwrites (including the default, OverwriteStorage) - instead
# there are separate calls to delete and then write a new file in the
# storage backend.  This introduces the risk of a race condition
# occurring when a user uploads a new profile image to replace an
# earlier one (the file will temporarily be deleted).
PROFILE_IMAGE_BACKEND = {
    'class': 'openedx.core.storage.OverwriteStorage',
    'options': {
        'location': os.path.join(MEDIA_ROOT, 'profile-images/'),
        'base_url': os.path.join(MEDIA_URL, 'profile-images/'),
    },
}
PROFILE_IMAGE_DEFAULT_FILENAME = 'images/profiles/default'
PROFILE_IMAGE_DEFAULT_FILE_EXTENSION = 'png'
# This key is used in generating unguessable URLs to users'
# profile images. Once it has been set, changing it will make the
# platform unaware of current image URLs.
PROFILE_IMAGE_HASH_SEED = 'placeholder_secret_key'
PROFILE_IMAGE_MAX_BYTES = 1024 * 1024
PROFILE_IMAGE_MIN_BYTES = 100
PROFILE_IMAGE_SIZES_MAP = {
    'full': 500,
    'large': 120,
    'medium': 50,
    'small': 30
}

################################## XBlock ##################################

# .. setting_name: XBLOCK_EXTRA_MIXINS
# .. setting_default: ()
# .. setting_description: Custom mixins that will be dynamically added to every XBlock and XBlockAside instance.
#     These can be classes or dotted-path references to classes.
#     For example: `XBLOCK_EXTRA_MIXINS = ('my_custom_package.my_module.MyCustomMixin',)`
XBLOCK_EXTRA_MIXINS = ()

# .. setting_name: XBLOCK_FIELD_DATA_WRAPPERS
# .. setting_default: ()
# .. setting_description: Paths to wrapper methods which should be applied to every XBlock's FieldData.
XBLOCK_FIELD_DATA_WRAPPERS = ()

XBLOCK_FS_STORAGE_BUCKET = None
XBLOCK_FS_STORAGE_PREFIX = None

# .. setting_name: XBLOCK_SETTINGS
# .. setting_default: {}
# .. setting_description: Dictionary containing server-wide configuration of XBlocks on a per-type basis.
#     By default, keys should match the XBlock `block_settings_key` attribute/property. If the attribute/property
#     is not defined, use the XBlock class name. Check `xmodule.services.SettingsService`
#     for more reference.
XBLOCK_SETTINGS = {}

# .. setting_name: XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE
# .. setting_default: default
# .. setting_description: The django cache key of the cache to use for storing anonymous user state for XBlocks.
XBLOCK_RUNTIME_V2_EPHEMERAL_DATA_CACHE = 'default'

# These are the Mixins that will be added to every Blocklike upon instantiation.
# DO NOT EXPAND THIS LIST!! We want it eventually to be EMPTY. Why? Because dynamically adding functions/behaviors to
# objects at runtime is confusing for both developers and static tooling (pylint/mypy). Instead...
#  - to add special Blocklike behaviors just for your site: override `XBLOCK_EXTRA_MIXINS` with your own XBlockMixins.
#  - to add new functionality to all Blocklikes: add it to the base Blocklike class in the core openedx/XBlock repo.
XBLOCK_MIXINS = (
    # TODO: For each of these, either
    #  (a) merge their functionality into the base Blocklike class, or
    #  (b) refactor their functionality out of the Blocklike objects and into the edx-platform block runtimes.
    LmsBlockMixin,
    InheritanceMixin,
    XModuleMixin,
    EditInfoMixin,
)

######################## Built-in Blocks Extraction ########################

# The following Django settings flags have been introduced temporarily to facilitate
# the rollout of the extracted built-in Blocks. Flags will use to toggle between
# the old and new block quickly without putting course content or user state at risk.
#
# Ticket: https://github.com/openedx/edx-platform/issues/35308

# .. toggle_name: USE_EXTRACTED_WORD_CLOUD_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Word Cloud XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34840 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_WORD_CLOUD_BLOCK = True

# .. toggle_name: USE_EXTRACTED_ANNOTATABLE_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted annotatable XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34841 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_ANNOTATABLE_BLOCK = True

# .. toggle_name: USE_EXTRACTED_POLL_QUESTION_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted poll question XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until https://github.com/openedx/edx-platform/issues/34839 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_POLL_QUESTION_BLOCK = True

# .. toggle_name: USE_EXTRACTED_LTI_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted LTI XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_LTI_BLOCK = True

# .. toggle_name: USE_EXTRACTED_HTML_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted HTML XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_HTML_BLOCK = True

# .. toggle_name: USE_EXTRACTED_DISCUSSION_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Discussion XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_DISCUSSION_BLOCK = False

# .. toggle_name: USE_EXTRACTED_PROBLEM_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Problem XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_PROBLEM_BLOCK = False

# .. toggle_name: USE_EXTRACTED_VIDEO_BLOCK
# .. toggle_default: False
# .. toggle_implementation: DjangoSetting
# .. toggle_description: Enables the use of the extracted Video XBlock, which has been shifted to the 'openedx/xblocks-contrib' repo.
# .. toggle_use_cases: temporary
# .. toggle_warning: Not production-ready until relevant subtask https://github.com/openedx/edx-platform/issues/34827 is done.
# .. toggle_creation_date: 2024-11-10
# .. toggle_target_removal_date: 2025-06-01
USE_EXTRACTED_VIDEO_BLOCK = False

############################## Marketing Site ##############################

EDXMKTG_LOGGED_IN_COOKIE_NAME = 'edxloggedin'
EDXMKTG_USER_INFO_COOKIE_NAME = 'edx-user-info'
EDXMKTG_USER_INFO_COOKIE_VERSION = 1

MKTG_URLS = {}
MKTG_URL_OVERRIDES = {}
MKTG_URL_LINK_MAP = {}

SUPPORT_SITE_LINK = ''

ACTIVATION_EMAIL_SUPPORT_LINK = Derived(lambda settings: settings.SUPPORT_SITE_LINK)
ID_VERIFICATION_SUPPORT_LINK = Derived(lambda settings: settings.SUPPORT_SITE_LINK)
LOGIN_ISSUE_SUPPORT_LINK = Derived(lambda settings: settings.SUPPORT_SITE_LINK)
PASSWORD_RESET_SUPPORT_LINK = Derived(lambda settings: settings.SUPPORT_SITE_LINK)

################################# ChatGPT ##################################

CHAT_COMPLETION_API = ''
CHAT_COMPLETION_API_KEY = ''
LEARNER_ENGAGEMENT_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_ENGAGEMENT_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_ACTIVE_CONTRACT = ''
LEARNER_PROGRESS_PROMPT_FOR_NON_ACTIVE_CONTRACT = ''

# How long to cache OpenAPI schemas and UI, in seconds.
OPENAPI_CACHE_TIMEOUT = 60 * 60

################################### AWS ####################################

AWS_QUERYSTRING_AUTH = True
AWS_STORAGE_BUCKET_NAME = 'edxuploads'
AWS_S3_CUSTOM_DOMAIN = 'edxuploads.s3.amazonaws.com'

AWS_SES_REGION_NAME = 'us-east-1'
AWS_SES_REGION_ENDPOINT = 'email.us-east-1.amazonaws.com'

AWS_ACCESS_KEY_ID = None
AWS_SECRET_ACCESS_KEY = None

################################ Optimizely ################################

OPTIMIZELY_PROJECT_ID = None
OPTIMIZELY_FULLSTACK_SDK_KEY = None

################################# Zendesk ##################################
ZENDESK_URL = ''
ZENDESK_CUSTOM_FIELDS = {}
ZENDESK_OAUTH_ACCESS_TOKEN = ''
# A mapping of string names to Zendesk Group IDs
# To get the IDs of your groups you can go to
# {zendesk_url}/api/v2/groups.json
ZENDESK_GROUP_ID_MAPPING = {}

############################## Python sandbox ##############################

CODE_JAIL = {
    # from https://github.com/openedx/codejail/blob/master/codejail/django_integration.py#L24, '' should be same as None
    'python_bin': '/edx/app/edxapp/venvs/edxapp-sandbox/bin/python',
    # User to run as in the sandbox.
    'user': 'sandbox',

    # Configurable limits.
    'limits': {
        # How many CPU seconds can jailed code use?
        'CPU': 1,
        # Limit the memory of the jailed process to something high but not
        # infinite (512MiB in bytes)
        'VMEM': 536870912,
        # Time in seconds that the jailed process has to run.
        'REALTIME': 3,
        'PROXY': 0,
    },

    # Overrides to default configurable 'limits' (above).
    # Keys should be course run ids (or, in the special case of code running
    # on the /debug/run_python page, the key is 'debug_run_python').
    # Values should be dictionaries that look like 'limits'.
    "limit_overrides": {},
}

# Some courses are allowed to run unsafe code. This is a list of regexes, one
# of them must match the course id for that course to run unsafe code.
#
# For example:
#
#   COURSES_WITH_UNSAFE_CODE = [
#       r"Harvard/XY123.1/.*"
#   ]
COURSES_WITH_UNSAFE_CODE = []

# Code jail REST service
ENABLE_CODEJAIL_REST_SERVICE = False

# .. setting_name: CODE_JAIL_REST_SERVICE_REMOTE_EXEC
# .. setting_default: 'xmodule.capa.safe_exec.remote_exec.send_safe_exec_request_v0'
# .. setting_description: Set the python package.module.function that is reponsible of
#   calling the remote service in charge of jailed code execution
CODE_JAIL_REST_SERVICE_REMOTE_EXEC = 'xmodule.capa.safe_exec.remote_exec.send_safe_exec_request_v0'

# .. setting_name: CODE_JAIL_REST_SERVICE_HOST
# .. setting_default: 'http://127.0.0.1:8550'
# .. setting_description: Set the codejail remote service host
CODE_JAIL_REST_SERVICE_HOST = 'http://127.0.0.1:8550'

# .. setting_name: CODE_JAIL_REST_SERVICE_CONNECT_TIMEOUT
# .. setting_default: 0.5
# .. setting_description: Set the number of seconds LMS will wait to establish an internal
#   connection to the codejail remote service.
CODE_JAIL_REST_SERVICE_CONNECT_TIMEOUT = 0.5  # time in seconds

# .. setting_name: CODE_JAIL_REST_SERVICE_READ_TIMEOUT
# .. setting_default: 3.5
# .. setting_description: Set the number of seconds LMS/CMS will wait for a response from the
#   codejail remote service endpoint.
CODE_JAIL_REST_SERVICE_READ_TIMEOUT = 3.5  # time in seconds

####################### Locale/Internationalization ########################

# Locale/Internationalization
CELERY_TIMEZONE = 'UTC'

# Languages supported for custom course certificate templates
CERTIFICATE_TEMPLATE_LANGUAGES = {
    'en': 'English',
    'es': 'Español',
}

STATICI18N_FILENAME_FUNCTION = 'statici18n.utils.legacy_filename'
STATICI18N_OUTPUT_DIR = "js/i18n"
STATICI18N_ROOT = Derived(lambda settings: settings.PROJECT_ROOT / "static")

################################# Pipeline #################################

PIPELINE = {
    'PIPELINE_ENABLED': True,
    # Don't use compression by default
    'CSS_COMPRESSOR': None,
    # Don't wrap JavaScript as there is code that depends upon updating the global namespace
    'DISABLE_WRAPPER': True,
    # Specify the UglifyJS binary to use
    'UGLIFYJS_BINARY': 'node_modules/.bin/uglifyjs',
}

STATICFILES_STORAGE_KWARGS = {}

STATICFILES_IGNORE_PATTERNS = [
    "*.py",
    "*.pyc",

    # It would be nice if we could do, for example, "**/*.scss",
    # but these strings get passed down to the `fnmatch` module,
    # which doesn't support that. :(
    # http://docs.python.org/2/library/fnmatch.html
    "sass/*.scss",
    "sass/*/*.scss",
    "sass/*/*/*.scss",
    "sass/*/*/*/*.scss",

    # Ignore tests
    "spec",
    "spec_helpers",

    # Symlinks used by js-test-tool
    "xmodule_js",
]

########################## Django Webpack Loader ###########################

WEBPACK_LOADER = {
    'DEFAULT': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': Derived(lambda settings: os.path.join(settings.STATIC_ROOT, 'webpack-stats.json')),
    },
    'WORKERS': {
        'BUNDLE_DIR_NAME': 'bundles/',
        'STATS_FILE': Derived(lambda settings: os.path.join(settings.STATIC_ROOT, 'webpack-worker-stats.json')),
    }
}

############################## django-require ##############################

# The baseUrl to pass to the r.js optimizer, relative to STATIC_ROOT.
REQUIRE_BASE_URL = "./"

# Whether to run django-require in debug mode.
REQUIRE_DEBUG = False

# The name of a build profile to use for your project, relative to REQUIRE_BASE_URL.
# A sensible value would be 'app.build.js'. Leave blank to use the built-in default build profile.
# Set to False to disable running the default profile (e.g. if only using it to build Standalone
# Modules)
REQUIRE_BUILD_PROFILE = Derived(lambda settings: f"{settings.SERVICE_VARIANT}/js/build.js")

########################### Student Verification ###########################

VERIFY_STUDENT = {
    "DAYS_GOOD_FOR": 365,  # How many days is a verficiation good for?
    # The variable represents the window within which a verification is considered to be "expiring soon."
    "EXPIRING_SOON_WINDOW": 28,
}

################################## ORA 2 ###################################

# Default File Upload Storage bucket and prefix. Used by the FileUpload Service.
FILE_UPLOAD_STORAGE_BUCKET_NAME = 'SET-ME-PLEASE (ex. bucket-name)'
FILE_UPLOAD_STORAGE_PREFIX = 'submissions_attachments'


############################## Authentication ##############################

##### ACCOUNT LOCKOUT DEFAULT PARAMETERS #####
# .. setting_name: MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED
# .. setting_default: 6
# .. setting_description: Specifies the maximum failed login attempts allowed to users. Once the user reaches this
#   failure threshold then the account will be locked for a configurable amount of seconds (30 minutes) which will
#   prevent additional login attempts until this time period has passed. This setting is related with
#   MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS and only used when ENABLE_MAX_FAILED_LOGIN_ATTEMPTS is enabled.
MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED = 6

# .. setting_name: MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS
# .. setting_default: 30 * 60
# .. setting_description: Specifies the lockout period in seconds for consecutive failed login attempts. Once the user
#   reaches the threshold of the login failure, then the account will be locked for the given amount of seconds
#   (30 minutes) which will prevent additional login attempts until this time period has passed. This setting is
#   related with MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED and only used when ENABLE_MAX_FAILED_LOGIN_ATTEMPTS is enabled.
MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS = 30 * 60

PASSWORD_POLICY_COMPLIANCE_ROLLOUT_CONFIG = {
    'ENFORCE_COMPLIANCE_ON_LOGIN': False
}

SESSION_INACTIVITY_TIMEOUT_IN_SECONDS = None

# List of logout URIs for each IDA that the learner should be logged out of when they logout of the LMS
# or CMS. Only applies to IDA for which the social auth flow uses DOT (Django OAuth Toolkit).
IDA_LOGOUT_URI_LIST = []

### External auth usage -- prefixes for ENROLLMENT_DOMAIN
SHIBBOLETH_DOMAIN_PREFIX = 'shib:'

# This is the domain that is used to set shared cookies between various sub-domains.
SHARED_COOKIE_DOMAIN = Derived(lambda settings: settings.SESSION_COOKIE_DOMAIN)

################################ Analytics #################################

ANALYTICS_DASHBOARD_URL = 'http://localhost:18110/courses'
ANALYTICS_DASHBOARD_NAME = 'Your Platform Name Here Insights'

################################ Discovery #################################

# which access.py permission name to check in order to determine if a course is visible in
# the course catalog. We default this to the legacy permission 'see_exists'.
COURSE_CATALOG_VISIBILITY_PERMISSION = 'see_exists'

# which access.py permission name to check in order to determine if a course about page is
# visible. We default this to the legacy permission 'see_exists'.
COURSE_ABOUT_VISIBILITY_PERMISSION = 'see_exists'

DEFAULT_COURSE_VISIBILITY_IN_CATALOG = "both"

# .. toggle_name: DEFAULT_MOBILE_AVAILABLE
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: This specifies if the courses are available for mobile by default. To make any individual
#   course available for mobile one can set the value of Mobile Course Available to true in Advanced Settings from the
#   studio when this is False.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2021-01-26
# .. toggle_tickets: https://openedx.atlassian.net/browse/OSPR-1985
DEFAULT_MOBILE_AVAILABLE = False

COURSE_CATALOG_URL_ROOT = 'http://localhost:8008'
COURSE_CATALOG_API_URL = f'{COURSE_CATALOG_URL_ROOT}/api/v1'

################################## Search ##################################

# Use None for the default search engine
SEARCH_ENGINE = None

############################### Credentials ################################

CREDENTIALS_INTERNAL_SERVICE_URL = 'http://localhost:8005'
CREDENTIALS_PUBLIC_SERVICE_URL = 'http://localhost:8005'

# time between scheduled runs, in seconds
NOTIFY_CREDENTIALS_FREQUENCY = 14400

CREDENTIALS_SERVICE_USERNAME = 'credentials_service_user'

################################## Themes ##################################

# .. setting_name: COMPREHENSIVE_THEME_DIRS
# .. setting_default: []
# .. setting_description: A list of paths to directories, each of which will
#   be searched for comprehensive themes. Do not override this Django setting directly.
#   Instead, set the COMPREHENSIVE_THEME_DIRS environment variable, using colons (:) to
#   separate paths.
COMPREHENSIVE_THEME_DIRS = os.environ.get("COMPREHENSIVE_THEME_DIRS", "").split(":")

# .. setting_name: DEFAULT_SITE_THEME
# .. setting_default: None
# .. setting_description: Theme to use when no site or site theme is defined, for example
#   "dark-theme". Set to None if you want to use openedx default theme.
# .. setting_warning: The theme folder needs to be in 'edx-platform/themes' or define the path
#   to the theme folder in COMPREHENSIVE_THEME_DIRS. To be effective, ENABLE_COMPREHENSIVE_THEMING
#   has to be enabled.
DEFAULT_SITE_THEME = None

# .. toggle_name: ENABLE_COMPREHENSIVE_THEMING
# .. toggle_implementation: DjangoSetting
# .. toggle_default: False
# .. toggle_description: When enabled, this toggle activates the use of the custom theme
#   defined by DEFAULT_SITE_THEME.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2016-06-30
ENABLE_COMPREHENSIVE_THEMING = False

################################ Ecommerce #################################

ECOMMERCE_PUBLIC_URL_ROOT = 'http://localhost:8002'
ECOMMERCE_API_URL = 'http://localhost:8002/api/v2'
ECOMMERCE_API_SIGNING_KEY = 'SET-ME-PLEASE'

################################ Enterprise ################################

# The default value of this needs to be a 16 character string
ENTERPRISE_CUSTOMER_CATALOG_DEFAULT_CONTENT_FILTER = {}

# The setting key maps to the channel code (e.g. 'SAP' for success factors), Channel code is defined as
# part of django model of each integrated channel in edx-enterprise.
# The absence of a key/value pair translates to NO LIMIT on the number of "chunks" transmitted per cycle.
INTEGRATED_CHANNELS_API_CHUNK_TRANSMISSION_LIMIT = {}

ENTERPRISE_SERVICE_WORKER_USERNAME = 'enterprise_worker'
ENTERPRISE_API_CACHE_TIMEOUT = 3600  # Value is in seconds

BASE_COOKIE_DOMAIN = 'localhost'

ENTERPRISE_MARKETING_FOOTER_QUERY_PARAMS = {}

################################## Exams ###################################

EXAMS_SERVICE_URL = 'http://localhost:18740/api/v1'

############################## Credit Courses ##############################

# Initial delay used for retrying tasks.
# Additional retries use longer delays.
# Value is in seconds.
CREDIT_TASK_DEFAULT_RETRY_DELAY = 30

# Maximum number of retries per task for errors that are not related
# to throttling.
CREDIT_TASK_MAX_RETRIES = 5

# Secret keys shared with credit providers.
# Used to digitally sign credit requests (us --> provider)
# and validate responses (provider --> us).
# Each key in the dictionary is a credit provider ID, and
# the value is the 32-character key.
CREDIT_PROVIDER_SECRET_KEYS = {}

# Maximum age in seconds of timestamps we will accept
# when a credit provider notifies us that a student has been approved
# or denied for credit.
CREDIT_PROVIDER_TIMESTAMP_EXPIRATION = 15 * 60

################################ Completion ################################

# Once a user has watched this percentage of a video, mark it as complete:
# (0.0 = 0%, 1.0 = 100%)
COMPLETION_VIDEO_COMPLETE_PERCENTAGE = 0.95

############################### Rate Limits ################################

##### LOGISTRATION RATE LIMIT SETTINGS #####
LOGISTRATION_RATELIMIT_RATE = '100/5m'
LOGISTRATION_PER_EMAIL_RATELIMIT_RATE = '30/5m'
LOGISTRATION_API_RATELIMIT = '20/m'
LOGIN_AND_REGISTER_FORM_RATELIMIT = '100/5m'
RESET_PASSWORD_TOKEN_VALIDATE_API_RATELIMIT = '30/7d'
RESET_PASSWORD_API_RATELIMIT = '30/7d'
OPTIONAL_FIELD_API_RATELIMIT = '10/h'

##### PASSWORD RESET RATE LIMIT SETTINGS #####
PASSWORD_RESET_IP_RATE = '1/m'
PASSWORD_RESET_EMAIL_RATE = '2/h'

################################### Help ###################################

HELP_TOKENS_LANGUAGE_CODE = Derived(lambda settings: settings.LANGUAGE_CODE)
HELP_TOKENS_VERSION = Derived(lambda settings: doc_version())
HELP_TOKENS_INI_FILE = Derived(lambda settings: REPO_ROOT / settings.SERVICE_VARIANT / "envs" / "help_tokens.ini")
HELP_TOKENS_BOOKS = {
    'learner': 'https://docs.openedx.org/en/latest/learners',
    'course_author': 'https://docs.openedx.org/en/latest/educators',
}

################################ Retirement ################################

# .. setting_name: RETIRED_USERNAME_PREFIX
# .. setting_default: retired__user_
# .. setting_description: Set the prefix part of hashed usernames for retired users. Used by the derived
#     setting RETIRED_USERNAME_FMT.
RETIRED_USERNAME_PREFIX = 'retired__user_'

# .. setting_name: RETIRED_EMAIL_PREFIX
# .. setting_default: retired__user_
# .. setting_description: Set the prefix part of hashed emails for retired users. Used by the derived
#     setting RETIRED_EMAIL_FMT.
RETIRED_EMAIL_PREFIX = 'retired__user_'

# .. setting_name: RETIRED_EMAIL_DOMAIN
# .. setting_default: retired.invalid
# .. setting_description: Set the domain part of hashed emails for retired users. Used by the derived
#     setting RETIRED_EMAIL_FMT.
RETIRED_EMAIL_DOMAIN = 'retired.invalid'

# .. setting_name: RETIRED_USERNAME_FMT
# .. setting_default: retired__user_{}
# .. setting_description: Set the format a retired user username field gets transformed into, where {}
#     is replaced with the hash of the original username. This is a derived setting that depends on
#     RETIRED_USERNAME_PREFIX value.
RETIRED_USERNAME_FMT = Derived(lambda settings: settings.RETIRED_USERNAME_PREFIX + '{}')

# .. setting_name: RETIRED_EMAIL_FMT
# .. setting_default: retired__user_{}@retired.invalid
# .. setting_description: Set the format a retired user email field gets transformed into, where {} is
#     replaced with the hash of the original email. This is a derived setting that depends on
#     RETIRED_EMAIL_PREFIX and RETIRED_EMAIL_DOMAIN values.
RETIRED_EMAIL_FMT = Derived(lambda settings: settings.RETIRED_EMAIL_PREFIX + '{}@' + settings.RETIRED_EMAIL_DOMAIN)

# .. setting_name: RETIRED_USER_SALTS
# .. setting_default: ['abc', '123']
# .. setting_description: Set a list of salts used for hashing usernames and emails on users retirement.
# .. setting_warning: Only the last item in this list is used as a salt for all new retirements, but
#     historical salts are preserved in order to guarantee that all hashed usernames and emails can still
#     be checked.
RETIRED_USER_SALTS = ['abc', '123']

# .. setting_name: RETIREMENT_SERVICE_WORKER_USERNAME
# .. setting_default: RETIREMENT_SERVICE_USER
# .. setting_description: Set the username of the retirement service worker user. Retirement scripts
#     authenticate with LMS as this user with oauth client credentials.
RETIREMENT_SERVICE_WORKER_USERNAME = 'RETIREMENT_SERVICE_USER'

# These states are the default, but are designed to be overridden in configuration.
# .. setting_name: RETIREMENT_STATES
# .. setting_default:
#     [
#         'PENDING',
#         'LOCKING_ACCOUNT',
#         'LOCKING_COMPLETE',
#         'RETIRING_FORUMS',
#         'FORUMS_COMPLETE',
#         'RETIRING_EMAIL_LISTS',
#         'EMAIL_LISTS_COMPLETE',
#         'RETIRING_ENROLLMENTS',
#         'ENROLLMENTS_COMPLETE',
#         'RETIRING_NOTES',
#         'NOTES_COMPLETE',
#         'RETIRING_LMS',
#         'LMS_COMPLETE',
#         'ERRORED',
#         'ABORTED',
#         'COMPLETE',
#     ]
# .. setting_description: Set a list that defines the name and order of states for the retirement
#     workflow.
# .. setting_warning: These states are stored in the database and it is the responsibility of the
#     administrator to populate the state list since the states can vary across different installations.
#     There must be, at minimum, a PENDING state at the beginning, and COMPLETED, ERRORED, and ABORTED
#     states at the end of the list.
RETIREMENT_STATES = [
    'PENDING',

    'LOCKING_ACCOUNT',
    'LOCKING_COMPLETE',

    # Use these states only when ENABLE_DISCUSSION_SERVICE is True.
    'RETIRING_FORUMS',
    'FORUMS_COMPLETE',

    # TODO - Change these states to be the LMS-only email opt-out - PLAT-2189
    'RETIRING_EMAIL_LISTS',
    'EMAIL_LISTS_COMPLETE',

    'RETIRING_ENROLLMENTS',
    'ENROLLMENTS_COMPLETE',

    # Use these states only when ENABLE_STUDENT_NOTES is True.
    'RETIRING_NOTES',
    'NOTES_COMPLETE',

    'RETIRING_LMS',
    'LMS_COMPLETE',

    'ERRORED',
    'ABORTED',
    'COMPLETE',
]

USERNAME_REPLACEMENT_WORKER = "REPLACE WITH VALID USERNAME"

################################# edx-rbac #################################

SYSTEM_WIDE_ROLE_CLASSES = []

############################### Brand Logos ################################

LOGO_IMAGE_EXTRA_TEXT = ''
LOGO_URL = None
LOGO_URL_PNG = None
LOGO_TRADEMARK_URL = None
FAVICON_URL = None
DEFAULT_EMAIL_LOGO_URL = 'https://edx-cdn.org/v3/default/logo.png'

############################## Course Import ###############################

COURSE_OLX_VALIDATION_STAGE = 1
COURSE_OLX_VALIDATION_IGNORE_LIST = None


############################## Documentation ###############################

CALCULATOR_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/add_calculator.html"
DISCUSSIONS_HELP_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_discussions.html"
EDXNOTES_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/enable_notes.html"
PROGRESS_HELP_URL = "https://docs.openedx.org/en/latest/educators/references/data/progress_page.html"
TEAMS_HELP_URL = "https://docs.openedx.org/en/latest/educators/navigation/advanced_features.html#use-teams-in-your-course"
TEXTBOOKS_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/manage_textbooks.html"
WIKI_HELP_URL = "https://docs.openedx.org/en/latest/educators/concepts/communication/about_course_wiki.html"
CUSTOM_PAGES_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/manage_custom_page.html"
ORA_SETTINGS_HELP_URL = "https://docs.openedx.org/en/latest/educators/how-tos/course_development/exercise_tools/Manage_ORA_Assignment.html"

########################## API Access Management ###########################

API_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/'
AUTH_DOCUMENTATION_URL = 'https://course-catalog-api-guide.readthedocs.io/en/latest/authentication/index.html'

API_ACCESS_FROM_EMAIL = 'api-requests@example.com'
API_ACCESS_MANAGER_EMAIL = 'api-access@example.com'

############################## Notifications ###############################

NOTIFICATIONS_EXPIRY = 60
EXPIRED_NOTIFICATIONS_DELETE_BATCH_SIZE = 10000
NOTIFICATION_CREATION_BATCH_SIZE = 76
NOTIFICATIONS_DEFAULT_FROM_EMAIL = "no-reply@example.com"
NOTIFICATION_DIGEST_LOGO = DEFAULT_EMAIL_LOGO_URL
NOTIFICATION_IMMEDIATE_EMAIL_BUFFER_MINUTES = 15  # in minutes
NOTIFICATION_TYPE_ICONS = {}
DEFAULT_NOTIFICATION_ICON_URL = ""

# These settings are used to override the default notification preferences values for apps and types.
# Here is complete documentation about how to use them: openedx/core/djangoapps/notifications/docs/settings.md
NOTIFICATION_APPS_OVERRIDE = {}
NOTIFICATION_TYPES_OVERRIDE = {}

############################# AI Translations ##############################

AI_TRANSLATIONS_API_URL = 'http://localhost:18760/api/v1'

################################ Event Bus #################################


def should_send_learning_badge_events(settings):
    return settings.BADGES_ENABLED

# .. setting_name: EVENT_BUS_PRODUCER_CONFIG
# .. setting_default: all events disabled
# .. setting_description: Dictionary of event_types mapped to dictionaries of topic to topic-related configuration.
#    Each topic configuration dictionary contains
#    * `enabled`: a toggle denoting whether the event will be published to the topic. These should be annotated
#       according to
#       https://docs.openedx.org/projects/edx-toggles/en/latest/how_to/documenting_new_feature_toggles.html
#    * `event_key_field` which is a period-delimited string path to event data field to use as event key.
#    Note: The topic names should not include environment prefix as it will be dynamically added based on
#    EVENT_BUS_TOPIC_PREFIX setting.
EVENT_BUS_PRODUCER_CONFIG = {
    "org.openedx.learning.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.course_key",
            "enabled": Derived(should_send_learning_badge_events),
        },
    },
    "org.openedx.learning.ccx.course.passing.status.updated.v1": {
        "learning-badges-lifecycle": {
            "event_key_field": "course_passing_status.course.ccx_course_key",
            "enabled": Derived(should_send_learning_badge_events),
        },
    },
}

### event tracking

EVENT_TRACKING_ENABLED = True
EVENT_TRACKING_PROCESSORS = []
EVENT_TRACKING_SEGMENTIO_EMIT_WHITELIST = []

# FIXME: Should we be doing this truncation?
TRACK_MAX_EVENT = 50000

TRACKING_BACKENDS = {
    'logger': {
        'ENGINE': 'common.djangoapps.track.backends.logger.LoggerBackend',
        'OPTIONS': {
            'name': 'tracking'
        }
    }
}

EVENT_TRACKING_BACKENDS = {
    'tracking_logs': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'logger': {
                    'ENGINE': 'eventtracking.backends.logger.LoggerBackend',
                    'OPTIONS': {
                        'name': 'tracking',
                        'max_event_size': TRACK_MAX_EVENT,
                    }
                }
            },
            'processors': [
                {'ENGINE': 'common.djangoapps.track.shim.LegacyFieldMappingProcessor'},
                {'ENGINE': 'common.djangoapps.track.shim.PrefixedEventProcessor'}
            ]
        }
    },
    'segmentio': {
        'ENGINE': 'eventtracking.backends.routing.RoutingBackend',
        'OPTIONS': {
            'backends': {
                'segment': {'ENGINE': 'eventtracking.backends.segment.SegmentBackend'}
            },
            'processors': [
                {
                    'ENGINE': 'eventtracking.processors.whitelist.NameWhitelistProcessor',
                    'OPTIONS': {
                        'whitelist': []
                    }
                },
                {
                    'ENGINE': 'common.djangoapps.track.shim.GoogleAnalyticsProcessor'
                }
            ]
        }
    }
}

# We're already logging events, and we don't want to capture user
# names/passwords.  Heartbeat events are likely not interesting.
TRACKING_IGNORE_URL_PATTERNS = [r'^/event', r'^/login', r'^/heartbeat']

############################## Miscellaneous ###############################

COURSE_MODE_DEFAULTS = {
    'android_sku': None,
    'bulk_sku': None,
    'currency': 'usd',
    'description': None,
    'expiration_datetime': None,
    'ios_sku': None,
    'min_price': 0,
    'name': _('Audit'),
    'sku': None,
    'slug': 'audit',
    'suggested_prices': '',
}

DEFAULT_COURSE_ABOUT_IMAGE_URL = 'images/pencils.jpg'

DISABLE_ACCOUNT_ACTIVATION_REQUIREMENT_SWITCH = "verify_student_disable_account_activation_requirement"

# If this is true, random scores will be generated for the purpose of debugging the profile graphs
GENERATE_PROFILE_SCORES = False

# The space is required for space-dependent languages like Arabic and Farsi.
# However, backward compatibility with Ficus older releases is still maintained (space is still not valid)
# in the AccountCreationForm and the user_api through the ENABLE_UNICODE_USERNAME feature flag.
USERNAME_REGEX_PARTIAL = r'[\w .@_+-]+'
USERNAME_PATTERN = fr'(?P<username>{USERNAME_REGEX_PARTIAL})'

DISCUSSION_RATELIMIT = '100/m'
SKIP_RATE_LIMIT_ON_ACCOUNT_AFTER_DAYS = 0

ONE_CLICK_UNSUBSCRIBE_RATE_LIMIT = '100/m'
EMAIL_CHANGE_RATE_LIMIT = ''
SECONDARY_EMAIL_RATE_LIMIT = ''

LMS_ROOT_URL = None
LMS_INTERNAL_ROOT_URL = Derived(lambda settings: settings.LMS_ROOT_URL)

LMS_ENROLLMENT_API_PATH = "/api/enrollment/v1/"
ENTERPRISE_ENROLLMENT_API_URL = Derived(
    lambda settings: (settings.LMS_INTERNAL_ROOT_URL or '') + settings.LMS_ENROLLMENT_API_PATH
)

VIDEO_CDN_URL = {
    # 'EXAMPLE_COUNTRY_CODE': "http://example.com/edx/video?s3_url="
}

SOFTWARE_SECURE_VERIFICATION_ROUTING_KEY = Derived(lambda settings: settings.HIGH_PRIORITY_QUEUE)

# Queue to use for updating persistent grades
RECALCULATE_GRADES_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

# Queue to use for updating grades due to grading policy change
POLICY_CHANGE_GRADES_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

# Rate limit for regrading tasks that a grading policy change can kick off
POLICY_CHANGE_TASK_RATE_LIMIT = '900/h'

# Queue to use for individual learner course regrades
SINGLE_LEARNER_COURSE_REGRADE_ROUTING_KEY = Derived(lambda settings: settings.DEFAULT_PRIORITY_QUEUE)

STATIC_ROOT_BASE = None

# .. setting_name: STATIC_URL_BASE
# .. setting_default: "None"
# .. setting_description: The LMS and CMS use this to construct ``STATIC_URL`` by appending
#   a slash (if needed), and for the CMS, ``studio/`` afterwards.
STATIC_URL_BASE = None

# .. setting_name: COMPREHENSIVE_THEME_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: A list of the paths to themes locale directories e.g.
#   "COMPREHENSIVE_THEME_LOCALE_PATHS" : ["/edx/src/edx-themes/conf/locale"].
COMPREHENSIVE_THEME_LOCALE_PATHS = []

# .. setting_name: PREPEND_LOCALE_PATHS
# .. setting_default: []
# .. setting_description: A list of the paths to locale directories to load first e.g.
#   "PREPEND_LOCALE_PATHS" : ["/edx/my-locales/"].
PREPEND_LOCALE_PATHS = []

# Used with Email sending
RETRY_ACTIVATION_EMAIL_MAX_ATTEMPTS = 5
RETRY_ACTIVATION_EMAIL_TIMEOUT = 0.5

# Software Secure request retry settings
# Time in seconds before a retry of the task should be 60 mints.
SOFTWARE_SECURE_REQUEST_RETRY_DELAY = 60 * 60
# Maximum of 6 retries before giving up.
SOFTWARE_SECURE_RETRY_MAX_ATTEMPTS = 6

MARKETING_EMAILS_OPT_IN = False

# License for serving content in China
ICP_LICENSE = None
ICP_LICENSE_INFO = {}

ELASTIC_SEARCH_CONFIG = [
    {
        'use_ssl': False,
        'host': 'localhost',
        'port': 9200
    }
]

LOGGING_ENV = 'sandbox'

EDX_ROOT_URL = ''

PARTNER_SUPPORT_EMAIL = ''

LOCAL_LOGLEVEL = "INFO"

LOG_DIR = '/edx/var/log/edx'

DJFS = {
    'type': 'osfs',
    'directory_root': '/edx/var/edxapp/django-pyfs/static/django-pyfs',
    'url_root': '/static/django-pyfs',
}

# Embargo
EMBARGO_SITE_REDIRECT_URL = None

# shoppingcart Payment
PAYMENT_SUPPORT_EMAIL = 'billing@example.com'

# Platform for Privacy Preferences header
P3P_HEADER = 'CP="Open EdX does not have a P3P policy."'

# .. setting_name: CUSTOM_RESOURCE_TEMPLATES_DIRECTORY
# .. setting_default: None
# .. setting_description: Path to an existing directory of YAML files containing
#    html content to be used with the subclasses of xmodule.x_module.ResourceTemplates.
#    Default example templates can be found in xmodule/templates/html.
#    Note that the extension used is ".yaml" and not ".yml".
#    See xmodule.x_module.ResourceTemplates for usage.
#   "CUSTOM_RESOURCE_TEMPLATES_DIRECTORY" : null
CUSTOM_RESOURCE_TEMPLATES_DIRECTORY = None

# Affiliate cookie tracking
AFFILIATE_COOKIE_NAME = 'dev_affiliate_id'

ENTRANCE_EXAM_MIN_SCORE_PCT = 50

# Initialize to 'release', but read from JSON in production.py
EDX_PLATFORM_REVISION = 'release'

# .. setting_name: PROCTORING_BACKENDS
# .. setting_description: A dictionary describing all available proctoring provider configurations.
#     Structure:
#         {
#             "<provider_name>": {
#                 "show_review_rules": <bool>,
#                 "requires_escalation_email": <bool>,
#                 ... additional provider-specific options ...
#             },
#             "<another_provider_name>": { ... }
#             ...
#             "DEFAULT": "<provider_name>",
#         }
#
#     Keys:
#
#     **show_review_rules** (bool):
#         Whether studio would show a "Review Rules" field as part of proctoring configuration.
#         Default is True.
#
#     **requires_escalation_email** (bool):
#         Providers with this flag set to True require that an escalation email address be
#         specified in the advanced course settings. Default is False.
# .. setting_default: {
#        'DEFAULT': 'null',
#        'null': {}
#    }
PROCTORING_BACKENDS = {
    'DEFAULT': 'null',
    # The null key needs to be quoted because
    # null is a language independent type in YAML
    'null': {}
}

DEPRECATED_ADVANCED_COMPONENT_TYPES = []

SYSLOG_SERVER = ''
FEEDBACK_SUBMISSION_EMAIL = ''

# keys for  big blue button live provider
COURSE_LIVE_GLOBAL_CREDENTIALS = {}

BEAMER_PRODUCT_ID = ""

# For geolocation ip database
GEOIP_PATH = REPO_ROOT / "common/static/data/geoip/GeoLite2-Country.mmdb"

# .. toggle_name: WIKI_ENABLED
# .. toggle_implementation: DjangoSetting
# .. toggle_default: True
# .. toggle_description: This setting allows us to have a collaborative tool to contribute or
#   modify content of course related materials.
# .. toggle_use_cases: open_edx
# .. toggle_creation_date: 2012-07-13
WIKI_ENABLED = True

# .. setting_name: MAINTENANCE_BANNER_TEXT
# .. setting_default: None
# .. setting_description: Specifies the text that is rendered on the maintenance banner.
#   (Note: set to 'Sample banner message' by default in the CMS).
# .. setting_warning: Depends on the `open_edx_util.display_maintenance_warning` waffle switch.
#   The banner is only rendered when the switch is activated.
MAINTENANCE_BANNER_TEXT: str | None

# .. setting_name: STUDIO_NAME
# .. setting_default: Your Platform Studio
# .. setting_description: The name that will appear on the landing page of Studio, as well as in various emails and
#   templates. (Note: set to 'Studio' by default in the LMS).
STUDIO_NAME: str
