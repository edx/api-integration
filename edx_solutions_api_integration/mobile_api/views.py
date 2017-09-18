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
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

from courseware.courses import get_course
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
from edx_solutions_api_integration.utils import get_aggregate_exclusion_user_ids
from lms.djangoapps.ccx.utils import prep_course_for_grading
from lms.djangoapps.grades.new.course_grade import CourseGradeFactory
from openedx.core.lib.api.permissions import IsStaffOrOwner
from openedx.core.djangoapps.course_groups.cohorts import get_cohort
from student.models import CourseEnrollment


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


class MobileUsersCoursesGrades(MobileListAPIView):
    """
    View for returning a user's course grades and the course cohort's average score.
    """

    def __init__(self):
        self.permission_classes += (IsStaffOrOwner, IsStaffOrEnrolled, )

    def get(self, request):
        """
        - URI: /mobile/v1/users/courses/grades?username={username}&course_id={course_id}
        - GET: return a JSON response of the user's course grade and cohort average

        * course_id: __required__, The course ID for the course to retrieve grades for
        * username: __optional__, A staff user can retrieve grades for different users
        by using this parameter. A regular user can only retrieve their own grades.
        """
        username = request.GET.get('username', None)
        course_id = request.GET.get('course_id', None)

        if not course_exists(request, request.user, course_id):
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
            'cohort_average_grade': grades['cohort_average_grade']
        }, status=status.HTTP_200_OK)

    def _get_user_course_grades(self, user, course_id):
        course_key = get_course_key(course_id)
        course = get_course(course_key)
        prep_course_for_grading(course, self.request)
        course_grade = CourseGradeFactory().create(user, course).percent
        cohort_average = self._get_cohort_average_grade(user, course)
        return {
            'course_grade': course_grade,
            'cohort_average_grade': cohort_average
        }

    def _get_cohort_average_grade(self, user, course):
        """ Get the cohort's average grade for the user's specified course """
        cohort = get_cohort(user, course.id, assign=False)
        if cohort is None:
            return None
        # Get all the users' grades in the cohort and return their average
        exclude_users = get_aggregate_exclusion_user_ids(course.id)
        cohort_users = cohort.users.exclude(id__in=exclude_users)
        cohort_grades = [
            grade[1].percent  # grade is a (student, course_grade, err_msg) tuple
            for grade in CourseGradeFactory().iter(course, cohort_users)
        ]
        return sum(cohort_grades) / float(len(cohort_grades))
