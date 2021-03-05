"""
Tests for course related use cases in mobile APIs
"""
from mobile_api.testutils import MobileAPITestCase


class TestCourseOverviewApi(MobileAPITestCase):
    """
    Tests for /api/server/mobile/v1/courses/{course_id}/overview?username=<user_name>
    """
    REVERSE_INFO = {'name': 'mobile-courses-overview', 'params': ['course_id']}

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

    def test_not_enrolled_user(self):
        """
        Test scenario when requested user is not enrolled in the course
        """
        self.login()

        response = self.api_response(expected_response_code=None, data={'username': self.user.username})
        self.assertEqual(response.status_code, 403)

    def test_user_enrolled_course(self):
        """
        Test scenario when requested user is enrolled in the course
        """
        self.login()
        self.enroll()

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.status_code, 200)
