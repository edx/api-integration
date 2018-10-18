"""
Views for importing data into the LMS that requires a lot of LMS functionality to process.
"""

from rest_framework.decorators import list_route

from edx_solutions_api_integration.permissions import SecureViewSet


class ImportParticipantsViewSet(SecureViewSet):

    @list_route(methods=['post'])
    def new(self, request):
        """Import new participants into the LMS, a company and course."""
        pass

    @list_route(methods=['post'])
    def existing(self, request):
        """Import existing participants in the LMS into a company and course."""
        pass
