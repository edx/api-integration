"""
Views for mobile APIs
"""

from edx_solutions_api_integration.permissions import MobileListAPIView
from edx_solutions_api_integration.users.views import (
    UsersOrganizationsList,
)
from openedx.core.lib.api.permissions import IsStaffOrOwner


class MobileUsersOrganizationsList(MobileListAPIView, UsersOrganizationsList):
    """
    View to return list of organizations a user belongs to.
    """
    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )
