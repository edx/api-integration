"""
Tests for user related use cases in mobile APIs
"""
import ddt
from capa.tests.response_xml_factory import MultipleChoiceResponseXMLFactory
from datetime import datetime, timedelta

from mobileapps.models import Theme, MobileApp
from oauth2_provider import models as dot_models
import urllib

from mobile_api.testutils import MobileAPITestCase
from edx_solutions_organizations.models import Organization
from openedx.core.djangolib.testing.utils import get_mock_request
from student.tests.factories import UserFactory, CourseEnrollmentFactory
from lms.djangoapps.grades.tests.utils import answer_problem
from gradebook.models import StudentGradebook
from xmodule.modulestore.tests.factories import ItemFactory


def _create_oauth2_token(user):
    """
    Create an OAuth2 Access Token for the specified user,
    to test OAuth2-based API authentication
    Returns the token as a string.
    """
    # Use django-oauth-toolkit (DOT) models to create the app and token:
    dot_app = dot_models.Application.objects.create(
        name='test app',
        user=UserFactory.create(),
        client_type='confidential',
        authorization_grant_type='authorization-code',
        redirect_uris='http://none.none'
    )
    dot_access_token = dot_models.AccessToken.objects.create(
        user=user,
        application=dot_app,
        expires=datetime.utcnow() + timedelta(weeks=1),
        scope='read',
        token='s3cur3t0k3n12345678901234567890'
    )
    return dot_access_token.token


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

    def test_mobileapps_in_organizations_list(self):
        self.login()
        organization1 = Organization.objects.create(display_name='ABC Organization')
        organization2 = Organization.objects.create(display_name='XYZ Organization')

        organization1.users.add(self.user)
        organization2.users.add(self.user)

        mobile_app1 = MobileApp.objects.create(name='Mobileapp 1', current_version='1.0', updated_by=self.user)
        mobile_app2 = MobileApp.objects.create(name='Mobileapp 2', current_version='1.0', updated_by=self.user)

        mobile_app1.organizations.add(organization1)
        mobile_app1.organizations.add(organization2)
        mobile_app2.organizations.add(organization2)

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['num_pages'], 1)

        self.assertEqual(response.data['results'][0]['display_name'], 'ABC Organization')
        self.assertEqual(len(response.data['results'][0]['mobile_apps']), 1)
        self.assertEqual(response.data['results'][0]['mobile_apps'][0]['name'], 'Mobileapp 1')

        self.assertEqual(response.data['results'][1]['display_name'], 'XYZ Organization')
        self.assertEqual(len(response.data['results'][1]['mobile_apps']), 2)
        self.assertEqual(response.data['results'][1]['mobile_apps'][0]['name'], 'Mobileapp 1')
        self.assertEqual(response.data['results'][1]['mobile_apps'][1]['name'], 'Mobileapp 2')

    def test_mobileapps_data_in_organizations_list(self):
        self.login()
        organization1 = Organization.objects.create(display_name='ABC Organization')
        organization1.users.add(self.user)

        mobile_app1 = MobileApp.objects.create(name='Mobileapp 1', current_version='1.0', updated_by=self.user)
        mobile_app1.organizations.add(organization1)

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['num_pages'], 1)

        self.assertEqual(response.data['results'][0]['display_name'], 'ABC Organization')
        self.assertEqual(len(response.data['results'][0]['mobile_apps']), 1)
        self.assertEqual(response.data['results'][0]['mobile_apps'][0]['name'], 'Mobileapp 1')

        self.assertIn('ios_app_id', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('ios_bundle_id', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('android_app_id', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('ios_download_url', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('android_download_url', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('deployment_mechanism', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('current_version', response.data['results'][0]['mobile_apps'][0])
        self.assertIn('is_active', response.data['results'][0]['mobile_apps'][0])
        self.assertNotIn('provider_key', response.data['results'][0]['mobile_apps'][0])
        self.assertNotIn('provider_secret', response.data['results'][0]['mobile_apps'][0])

    def test_themes_in_organizations_list(self):
        self.login()
        organization1 = Organization.objects.create(display_name='ABC Organization')
        organization2 = Organization.objects.create(display_name='XYZ Organization')

        organization1.users.add(self.user)
        organization2.users.add(self.user)

        Theme.objects.create(name='Theme 1', organization=organization1, active=True)
        Theme.objects.create(name='Theme 2', organization=organization2, active=True)
        Theme.objects.create(name='Theme 3', organization=organization1)
        Theme.objects.create(name='Theme 4', organization=organization1)

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['num_pages'], 1)

        self.assertEqual(response.data['results'][0]['display_name'], 'ABC Organization')
        self.assertEqual(response.data['results'][0]['theme']['name'], 'Theme 1')
        self.assertEqual(response.data['results'][0]['theme']['active'], True)

        self.assertEqual(response.data['results'][1]['display_name'], 'XYZ Organization')
        self.assertEqual(response.data['results'][1]['theme']['name'], 'Theme 2')
        self.assertEqual(response.data['results'][1]['theme']['active'], True)

    def test_themes_data_in_organizations_list(self):
        self.login()
        organization1 = Organization.objects.create(display_name='ABC Organization')
        organization1.users.add(self.user)

        Theme.objects.create(name='Theme 1', organization=organization1, active=True)

        response = self.api_response(data={'username': self.user.username})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['num_pages'], 1)

        self.assertEqual(response.data['results'][0]['display_name'], 'ABC Organization')
        self.assertEqual(response.data['results'][0]['theme']['name'], 'Theme 1')
        self.assertEqual(response.data['results'][0]['theme']['active'], True)
        self.assertNotIn('organization', response.data['results'][0]['theme'])


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


@ddt.ddt
class TestUserCourseGradesApi(MobileAPITestCase):
    """
    Tests for /api/server/mobile/v1/users/courses/grades?username=<username>&course_id=<course_id>
    """
    REVERSE_INFO = {'name': 'mobile-users-courses-grades', 'params': {}}

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
        CourseEnrollmentFactory.create(user=other_user, course_id=self.course.id)

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

    def users_and_problem_setup(self):
        self.chapter = ItemFactory.create(
            parent=self.course,
            category="chapter",
            display_name="Test Chapter"
        )
        self.sequence = ItemFactory.create(
            parent=self.chapter,
            category='sequential',
            display_name="Test Sequential 1",
            graded=True,
            format="Homework"
        )
        self.vertical = ItemFactory.create(
            parent=self.sequence,
            category='vertical',
            display_name='Test Vertical 1'
        )
        problem_xml = MultipleChoiceResponseXMLFactory().build_xml(
            question_text='The correct answer is Choice 3',
            choices=[False, False, True, False],
            choice_names=['choice_0', 'choice_1', 'choice_2', 'choice_3']
        )
        self.problem = ItemFactory.create(
            parent=self.vertical,
            category="problem",
            display_name="Test Problem",
            data=problem_xml
        )
        self.user2 = UserFactory.create(username='user2', password='user2')
        self.user3 = UserFactory.create(username='user3', password='user3')
        self.request = get_mock_request(self.user)

    def test_user_course_grades_and_course_average(self):
        self.users_and_problem_setup()

        # Just two users for now
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user2, course_id=self.course.id)

        # Ensure the the baseline score and average are 0
        self.login_and_enroll()
        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': self.user.username}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_grade'], 0)
        self.assertEqual(response.data['course_average_grade'], 0)

        # Answer some problems
        self.request.user = self.user
        answer_problem(self.course, self.request, self.problem, score=1, max_value=1)
        self.request.user = self.user2
        answer_problem(self.course, self.request, self.problem, score=1, max_value=5)

        # Calculate the expected course average
        user_grade = 0.1
        StudentGradebook.objects.update_or_create(
            user=self.user,
            course_id=self.course.id,
            defaults={
                'grade': user_grade,
                'proforma_grade': 1,
                'is_passed': True,
            }
        )
        user2_grade = 0.3
        StudentGradebook.objects.update_or_create(
            user=self.user2,
            course_id=self.course.id,
            defaults={
                'grade': user2_grade,
                'proforma_grade': 1,
                'is_passed': True,
            }
        )
        course_avg_grade = (user_grade + user2_grade) / float(2)
        self.assertTrue(course_avg_grade > 0)

        self.login_and_enroll()
        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': self.user.username}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_grade'], user_grade)
        self.assertEqual(response.data['course_average_grade'], course_avg_grade)

        # Enroll another user, with a higher grade,
        # and check that the average grade is updated
        self.request.user = self.user3
        CourseEnrollmentFactory.create(user=self.user3, course_id=self.course.id)
        answer_problem(self.course, self.request, self.problem, score=10, max_value=1)
        user3_grade = 0.5
        StudentGradebook.objects.update_or_create(
            user=self.user3,
            course_id=self.course.id,
            defaults={
                'grade': user3_grade,
                'proforma_grade': 1,
                'is_passed': True,
            }
        )
        new_course_avg_grade = (user_grade + user2_grade + user3_grade) / float(3)

        response = self.api_response(
            expected_response_code=None,
            data={'course_id': unicode(self.course.id), 'username': self.user.username}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_grade'], user_grade)
        self.assertEqual(response.data['course_average_grade'], new_course_avg_grade)
        self.assertTrue(new_course_avg_grade > course_avg_grade)

    def test_grades_view_oauth2(self):
        """
        Test the grades view using OAuth2 Authentication
        """
        self.users_and_problem_setup()
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        query_string = urllib.urlencode({'course_id': self.course.id, 'username': self.user.username})
        url = '/api/server/mobile/v1/users/courses/grades/?{}'.format(query_string)
        self.request.user = self.user
        answer_problem(self.course, self.request, self.problem, score=1, max_value=1)
        # Try with no authentication:
        self.client.logout()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)
        user_grade = 0.8
        StudentGradebook.objects.update_or_create(
            user=self.user,
            course_id=self.course.id,
            defaults={
                'grade': user_grade,
                'proforma_grade': 1,
                'is_passed': True,
            }
        )
        # Now, try with a valid token header:
        token = _create_oauth2_token(self.user)
        response = self.client.get(url, HTTP_AUTHORIZATION="Bearer {0}".format(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_grade'], user_grade)
