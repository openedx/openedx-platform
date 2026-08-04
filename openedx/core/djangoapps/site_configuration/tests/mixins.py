"""
Mixins for TestCase classes that need to account for multiple sites
"""


from openedx.core.djangoapps.site_configuration.tests.factories import SiteConfigurationFactory, SiteFactory


class SiteMixin:
    """
    Mixin for setting up Site framework models
    """
    def setUp(self):
        super().setUp()

        self.site = SiteFactory.create()
        site_config = {
            "SITE_NAME": self.site.domain,
            "course_email_from_addr": "fake@example.com",
            "course_email_template_name": "fake_email_template",
            "course_org_filter": "fakeX"
        }
        self.site_configuration = SiteConfigurationFactory.create(
            site=self.site,
            site_values=site_config
        )

        self.site_other = SiteFactory.create(
            domain='testserver.fakeother',
            name='testserver.fakeother'
        )
        site_config_other = {
            "SITE_NAME": self.site_other.domain,
            "SESSION_COOKIE_DOMAIN": self.site_other.domain,
            "course_org_filter": "fakeOtherX",
            "SHOW_ECOMMERCE_REPORTS": True,
            "MKTG_URLS": {
                "ROOT": "https://marketing.fakeother",
                "ABOUT": "/fake-about"
            }
        }
        self.site_configuration_other = SiteConfigurationFactory.create(
            site=self.site_other,
            site_values=site_config_other
        )

        # Initialize client with default site domain
        self.use_site(self.site)

    def set_up_site(self, domain, site_configuration_values):
        """
        Create Site and SiteConfiguration models and initialize test client with the created site
        """
        site = SiteFactory.create(
            domain=domain,
            name=domain
        )
        __ = SiteConfigurationFactory.create(
            site=site,
            site_values=site_configuration_values
        )
        self.use_site(site)
        return site

    def use_site(self, site):
        """
        Initializes the test client with the domain of the given site
        """
        self.client = self.client_class(SERVER_NAME=site.domain)
