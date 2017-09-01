"""
Tests for user related use cases in mobile APIs
"""
import ddt
from mobile_api.testutils import (
    MobileAPITestCase,
)
from student.tests.factories import UserFactory
from edx_solutions_organizations.models import Organization


class TestUserOrganizationsApi(MobileAPITestCase):
    """
    Tests for /api/server/mobile/v1/users/organizations/?username=<user_name>
    """
    REVERSE_INFO = {'name': 'mobile-users-orgs-list', 'params': {}}

    def test_with_unauthenticated_user(self):
        """
        Tests scenario calling API when not authenticated user
        """
        response = self.api_response(expected_response_code=None)
        self.assertEqual(response.status_code, 401)

    def test_with_invalid_username(self):
        """
        Test scenario when requested user and logged in user are not same
        """
        self.login()

        response = self.api_response(expected_response_code=None, data={'username': 'other_user'})
        self.assertEqual(response.status_code, 403)

    def test_user_without_orgs(self):
        """
        Test scenario when requested user is not member of any organization
        """
        self.login()

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 0)

    def test_user_with_orgs(self):
        """
        Test scenario when requested user is member of an organization
        """
        org_name = 'ABC Organization'
        org = Organization.objects.create(display_name=org_name)
        self.user.organizations.add(org)
        self.login()

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['display_name'], org_name)


@ddt.ddt
class TestUserDiscussionMetricsApi(MobileAPITestCase):
    """
    Tests for /api/server/mobile/v1/users/discussion_metrics/?username=<user_name>&course_id=<course_id>
    """
    REVERSE_INFO = {'name': 'mobile-users-discussion-metrics', 'params': {}}

    def test_with_unauthenticated_user(self):
        """
        Tests scenario calling API when not authenticated user.
        """
        response = self.api_response(expected_response_code=None)
        self.assertEqual(response.status_code, 401)

    def test_with_invalid_course_id(self):
        """
        Test scenario when calling API without valid course_id.
        """
        self.login()

        response = self.api_response(expected_response_code=None, data={'username': self.user.username})
        self.assertEqual(response.status_code, 403)

    @ddt.data(True, False)
    def test_with_another_users_username(self, is_staff):
        """
        Test scenario when requested user and logged in user are not same.
        """
        other_user = UserFactory()

        if is_staff:
            self.user.is_staff = True
            self.user.save()

        self.login()

        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': other_user.username}
        )
        if is_staff:
            self.assertEqual(response.status_code, 200)
        else:
            self.assertEqual(response.status_code, 403)

    @ddt.data(True, False)
    def test_with_unenrolled_user(self, is_staff):
        """
        Test scenario when logged in user is not enrolled in the course.
        """
        if is_staff:
            self.user.is_staff = True
            self.user.save()

        self.login()

        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': self.user.username}
        )

        if is_staff:
            self.assertEqual(response.status_code, 200)
        else:
            self.assertEqual(response.status_code, 403)

    def test_with_enrolled_user(self):
        """
        Test scenario when logged in user is enrolled in the course.
        """
        self.login_and_enroll()

        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': self.user.username}
        )

        self.assertEqual(response.status_code, 200)
