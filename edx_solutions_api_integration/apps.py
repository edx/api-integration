"""
app configuration
"""
from django.apps import AppConfig


class SolutionsAppApiIntegrationConfig(AppConfig):

    name = 'edx_solutions_api_integration'
    verbose_name = 'Solutions apps orchestration layer'

    def ready(self):

        # import signal handlers
        import edx_solutions_api_integration.receivers  # pylint: disable=unused-import
