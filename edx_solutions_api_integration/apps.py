"""
app configuration
"""
from django.apps import AppConfig
from django.conf import settings


class SolutionsAppApiIntegrationConfig(AppConfig):

    name = 'edx_solutions_api_integration'
    verbose_name = 'Solutions apps orchestration layer'

    def ready(self):

        # import signal handlers
        import edx_solutions_api_integration.receivers  # pylint: disable=unused-import

        if settings.FEATURES.get('EDX_SOLUTIONS_API', False) and \
                settings.FEATURES.get('DISABLE_SOLUTIONS_APPS_SIGNALS', False):
            disable_solutions_apps_signals()


def disable_solutions_apps_signals():
    """
    Disables signals receivers in solutions apps
    """
    from edx_solutions_api_integration.test_utils import SignalDisconnectTestMixin
    SignalDisconnectTestMixin.disconnect_signals()
