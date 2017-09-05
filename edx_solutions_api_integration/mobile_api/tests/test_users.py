"""
Tests for user related use cases in mobile APIs
"""
from mobile_api.testutils import (
    MobileAPITestCase,
)
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
