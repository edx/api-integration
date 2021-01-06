"""
Views for mobile APIs
"""
from django.shortcuts import get_object_or_404
from edx_solutions_api_integration.courses.views import (
    CoursesOverview, CoursesStaticTabsDetail, CoursesStaticTabsList)
from edx_solutions_api_integration.courseware_access import (course_exists,
                                                             get_course_key)
from edx_solutions_api_integration.mobile_api.serializers import MobileOrganizationSerializer
from edx_solutions_api_integration.models import APIUser as User
from edx_solutions_api_integration.permissions import (IsStaffOrEnrolled,
                                                       MobileListAPIView,
                                                       MobilePermissionMixin)
from edx_solutions_api_integration.users.views import (UsersCourseProgressList,
                                                       UsersCoursesDetail,
                                                       UsersOrganizationsList,
                                                       UsersSocialMetrics)
from edx_solutions_api_integration.utils import (
    cache_course_data, cache_course_user_data,
    get_aggregate_exclusion_user_ids, get_cached_data)
from gradebook.models import StudentGradebook
from openedx.core.lib.api.permissions import IsStaffOrOwner
from rest_framework import status
from rest_framework.response import Response
from student.models import CourseEnrollment


class MobileUsersOrganizationsList(MobilePermissionMixin, UsersOrganizationsList):
    """
    View to return list of organizations a user belongs to.
    """
    serializer_class = MobileOrganizationSerializer

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )

    def get_queryset(self):
        return super().get_queryset().prefetch_related('mobile_apps', 'theme')


class MobileUsersCourseProgressList(MobilePermissionMixin, UsersCourseProgressList):
    """
    View to return a list of courses user enrolled in and the progress for a user
    """
    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )


class MobileCoursesOverview(MobilePermissionMixin, CoursesOverview):
    """
    View to return course an HTML representation of the overview for the specified course if user is enrolled in.

    **Optional Params**
        parse: when TRUE returns a collection of JSON objects representing parts of the course overview.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileUsersCoursesDetail(MobilePermissionMixin, UsersCoursesDetail):
    """
    View that allow clients to interact with a specific User-Course relationship (aka, enrollment)
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileCoursesStaticTabsList(MobilePermissionMixin, CoursesStaticTabsList):
    """
    View that returns a collection of custom pages in the course.
    CoursesStaticTabsList has an optional detail parameter that when
    true includes the custom page content in the response.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileCoursesStaticTabsDetail(MobilePermissionMixin, CoursesStaticTabsDetail):
    """
    View that returns a custom page in the course, including the page content.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileUsersDiscussionMetrics(MobilePermissionMixin, UsersSocialMetrics):
    """
    View to return user discussion metrics and engagement score.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )


class MobileUsersCoursesGrades(MobileListAPIView):
    """
    View for returning a user's course grades and the course average score.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )

    def get(self, request):
        """
        - URI: /mobile/v1/users/courses/grades?username={username}&course_id={course_id}
        - GET: return a JSON response of the user's course grade and course average

        * course_id: __required__, The course ID for the course to retrieve grades for
        * username: __optional__, A staff user can retrieve grades for different users
        by using this parameter. A regular user can only retrieve their own grades.
        """
        username = request.GET.get('username', None)
        course_id = request.GET.get('course_id', None)

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        user = self.request.user
        course_key = get_course_key(course_id)

        if username is not None and user.username != username:
            # Raise a 404 if the specified user doesn't exist or
            # if they aren't enrolled in this course
            user = get_object_or_404(User, username=username)
            if not CourseEnrollment.is_enrolled(user, course_key):
                return Response({}, status=status.HTTP_404_NOT_FOUND)

        grades = self._get_user_course_grades(user, course_id)

        return Response({
            'username': username or self.request.user.username,
            'course_key': course_id,
            'course_grade': grades['course_grade'],
            'course_average_grade': grades['course_average_grade']
        }, status=status.HTTP_200_OK)

    def _get_user_course_grades(self, user, course_id):
        """
        Get the user's grade for the given course.

        Note: For performance reasons, we use the cached gradebook data here.
        Once persistent grades are enabled on the solutions fork, we'll use CourseGradeFactory instead.
        """
        course_key = get_course_key(course_id)
        data = get_cached_data('grade', course_id, user.id)
        params = {'exclude_users': get_aggregate_exclusion_user_ids(course_key, roles=None)}

        if not data:
            course_avg = StudentGradebook.course_grade_avg(course_key, **params)
            user_grade = StudentGradebook.get_user_grade(course_key, user.id)

            data = {'user_grade': user_grade, 'course_avg': course_avg}
            cache_course_data('grade', course_id, {'course_avg': course_avg})
            cache_course_user_data('grade', course_id, user.id, {'user_grade': user_grade})

        return {
            'course_grade': data.get('user_grade'),
            'course_average_grade': data.get('course_avg')
        }
