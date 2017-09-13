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
    MobilePermissionMixin,
    MobileSecureAPIView,
    IsStaffOrEnrolled,
)
from edx_solutions_api_integration.users.views import (
    UsersOrganizationsList,
    UsersCourseProgressList,
    UsersCoursesDetail,
)
from edx_solutions_api_integration.courses.views import CourseModuleCompletionList
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


class MobileUsersCoursesDetail(MobileSecureAPIView, UsersCoursesDetail):
    """
    View that allow clients to interact with a specific User-Course relationship (aka, enrollment)
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrEnrolled, )


class MobileCoursesStaticTabsList(MobileSecureAPIView, CoursesStaticTabsList):
    """
    View that returns a collection of custom pages in the course.
    CoursesStaticTabsList has an optional detail parameter that when
    true includes the custom page content in the response.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrEnrolled, )


class MobileCoursesStaticTabsDetail(MobileSecureAPIView, CoursesStaticTabsDetail):
    """
    View that returns a custom page in the course, including the page content.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrEnrolled, )


class MobileCourseModuleCompletion(MobilePermissionMixin, CourseModuleCompletionList, ):
     """
     Mimics CourseModuleCompletionList view but has mobile permissions.
     """
