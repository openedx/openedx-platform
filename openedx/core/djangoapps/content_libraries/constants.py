""" Constants used for the content libraries. """
from django.utils.translation import gettext_lazy as _

# These are all the licenses we support so far.
ALL_RIGHTS_RESERVED = ''
CC_4_BY = 'CC:4.0:BY'
CC_4_BY_NC = 'CC:4.0:BY:NC'
CC_4_BY_NC_ND = 'CC:4.0:BY:NC:ND'
CC_4_BY_NC_SA = 'CC:4.0:BY:NC:SA'
CC_4_BY_ND = 'CC:4.0:BY:ND'
CC_4_BY_SA = 'CC:4.0:BY:SA'

LICENSE_OPTIONS = (
    (ALL_RIGHTS_RESERVED, _('All Rights Reserved.')),
    (CC_4_BY, _('Creative Commons Attribution 4.0')),
    (CC_4_BY_NC, _('Creative Commons Attribution-NonCommercial 4.0')),
    (CC_4_BY_NC_ND, _('Creative Commons Attribution-NonCommercial-NoDerivatives 4.0')),
    (CC_4_BY_NC_SA, _('Creative Commons Attribution-NonCommercial-ShareAlike 4.0')),
    (CC_4_BY_ND, _('Creative Commons Attribution-NoDerivatives 4.0')),
    (CC_4_BY_SA, _('Creative Commons Attribution-ShareAlike 4.0'))
)
