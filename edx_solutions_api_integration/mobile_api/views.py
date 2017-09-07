"""
Views for mobile APIs
"""
from edx_solutions_api_integration.courses.views import CoursesOverview
from edx_solutions_api_integration.permissions import (
    MobileListAPIView,
    MobileSecureAPIView,
    IsStaffOrEnrolled,
)
from edx_solutions_api_integration.users.views import (
    UsersOrganizationsList,
    UsersCourseProgressList,
)
from openedx.core.lib.api.permissions import IsStaffOrOwner


class MobileUsersOrganizationsList(MobileListAPIView, UsersOrganizationsList):
    """
    View to return list of organizations a user belongs to.
    """
    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )


class MobileUsersCourseProgressList(MobileListAPIView, UsersCourseProgressList):
    """
    View to return a list of courses user enrolled in and the progress for a user
    """
    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )


class MobileCoursesOverview(MobileSecureAPIView, CoursesOverview):
    """
    View to return course an HTML representation of the overview for the specified course if user is enrolled in.

    **Optional Params**
        parse: when TRUE returns a collection of JSON objects representing parts of the course overview.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrEnrolled, )
