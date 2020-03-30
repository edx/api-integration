"""
Views for mobile APIs
"""
from edx_solutions_api_integration.courses.views import (
    CoursesOverview,
    CoursesStaticTabsList,
    CoursesStaticTabsDetail,
)
from edx_solutions_api_integration.mobile_api.serializers import MobileOrganizationSerializer
from edx_solutions_api_integration.permissions import (
    MobileListAPIView,
    IsStaffOrEnrolled,
    MobilePermissionMixin
)
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from rest_framework import status
from rest_framework.response import Response

from edx_solutions_api_integration.courseware_access import (
    course_exists,
    get_course_key
)
from edx_solutions_api_integration.models import APIUser as User
from edx_solutions_api_integration.users.views import (
    UsersSocialMetrics,
    UsersOrganizationsList,
    UsersCourseProgressList,
    UsersCoursesDetail,
)
from openedx.core.lib.api.permissions import IsStaffOrOwner
from student.models import CourseEnrollment
from gradebook.models import StudentGradebook


class MobileUsersOrganizationsList(MobilePermissionMixin, UsersOrganizationsList):
    """
    View to return list of organizations a user belongs to.
    """
    serializer_class = MobileOrganizationSerializer

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, )

    def get_queryset(self):
        return super(MobileUsersOrganizationsList, self).get_queryset().prefetch_related('mobile_apps', 'theme')


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
        try:
            record = StudentGradebook.objects.get(user=user, course_id=course_key)
            course_grade = record.grade
        except StudentGradebook.DoesNotExist:
            course_grade = 0

        course_average = self._get_course_average_grade(course_key)
        return {
            'course_grade': course_grade,
            'course_average_grade': course_average
        }

    def _get_course_average_grade(self, course_key):
        """
        Get the average grade for all the users in the specified course.

        Note: For performance reasons, we use the cached gradebook data here.
        Once persistent grades are enabled on the solutions fork, we'll use CourseGradeFactory instead.
        """
        cache_key = 'course_grade_avg_{}'.format(course_key)
        cache_ttl = 60 * 30  # 30 minutes

        course_avg = cache.get(cache_key)
        if course_avg is not None:
            return course_avg

        course_avg = StudentGradebook.course_grade_avg(course_key)
        cache.set(cache_key, course_avg, cache_ttl)

        return course_avg
