"""
Views for mobile APIs
"""
from edx_solutions_api_integration.courses.views import (
    CoursesOverview,
    CoursesStaticTabsList,
    CoursesStaticTabsDetail,
)
from edx_solutions_api_integration.permissions import (
    MobileListAPIView,
    MobileSecureAPIView,
    IsStaffOrEnrolled,
)
from edx_solutions_api_integration.users.views import (
    UsersSocialMetrics,
    UsersOrganizationsList,
    UsersCourseProgressList,
    UsersCoursesDetail,
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
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileUsersCoursesDetail(MobileSecureAPIView, UsersCoursesDetail):
    """
    View that allow clients to interact with a specific User-Course relationship (aka, enrollment)
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileCoursesStaticTabsList(MobileSecureAPIView, CoursesStaticTabsList):
    """
    View that returns a collection of custom pages in the course.
    CoursesStaticTabsList has an optional detail parameter that when
    true includes the custom page content in the response.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileCoursesStaticTabsDetail(MobileSecureAPIView, CoursesStaticTabsDetail):
    """
    View that returns a custom page in the course, including the page content.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileUsersDiscussionMetrics(MobileSecureAPIView, UsersSocialMetrics):
    """
    View to return user discussion metrics and engagement score.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )
