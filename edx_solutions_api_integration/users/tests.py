# coding: utf-8
# pylint: disable=E1101
# pylint: disable=E1103

"""
Run these tests @ Devstack:
    paver test_system -s lms --fasttest
        --fail_fast --verbose --test_id=lms/djangoapps/edx_solutions_api_integration/users
"""
import ddt
import uuid
import mock
import before_after
import six
import json

from datetime import datetime

from completion.models import BlockCompletion
from completion.waffle import WAFFLE_NAMESPACE, ENABLE_COMPLETION_TRACKING
from dateutil.relativedelta import relativedelta
from random import randint
from urllib import urlencode

from requests.exceptions import ConnectionError
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.test.utils import override_settings
from django.test.client import Client
from django.utils import timezone
from django.db import transaction
from django.utils.translation import ugettext as _

from courseware import module_render
from courseware.model_data import FieldDataCache
from django_comment_common.models import Role, FORUM_ROLE_MODERATOR, ForumsConfig
from waffle.testutils import override_switch

from edx_notifications.data import NotificationType, NotificationMessage
from edx_notifications.lib.consumer import get_notifications_count_for_user
from edx_notifications.lib.publisher import register_notification_type, publish_notification_to_user
from edx_solutions_organizations.models import Organization
from edx_solutions_projects.models import Project, Workgroup
from instructor.access import allow_access
from social_engagement.models import StudentSocialEngagementScore
from student.tests.factories import UserFactory, CourseEnrollmentFactory, GroupFactory
from student.models import anonymous_id_for_user, CourseEnrollment
from gradebook.models import StudentGradebook

from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, mixed_store_config, SharedModuleStoreTestCase

from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (
    ModuleStoreTestCase,
    TEST_DATA_SPLIT_MODULESTORE
)
from django.contrib.auth.models import User
from edx_solutions_api_integration.courseware_access import get_course_key
from edx_solutions_api_integration.test_utils import (
    get_non_atomic_database_settings,
    CourseGradingMixin,
    APIClientMixin,
    SignalDisconnectTestMixin,
    OAuth2TokenMixin,
)

from notification_prefs import NOTIFICATION_PREF_KEY


def _fake_get_user_social_stats(user_id, course_id, end_date=None):  # pylint: disable=W0613
    """
    Fake get_user_social_stats method
    """
    if not end_date:
        raise Exception('Expected None end_date parameter')

    return {
        str(user_id): {'foo': 'bar'}
    }


def _fake_get_user_social_stats_with_end(user_id, course_id, end_date=None):  # pylint: disable=W0613,C0103
    """
    Fake get_user_social_stats_with_end method
    """
    if not end_date:
        raise Exception('Expected non-None end_date parameter')

    return {
        str(user_id): {'foo': 'bar'}
    }


def _fake_get_service_unavailability(user_id, course_id, end_date=None):
    """
    Fake get_service_unavailability method
    """
    raise ConnectionError


@override_switch(
    '{}.{}'.format(WAFFLE_NAMESPACE, ENABLE_COMPLETION_TRACKING),
    active=True,
)
@override_settings(DEBUG=True)
@override_settings(PASSWORD_MIN_LENGTH=4)
@mock.patch.dict("django.conf.settings.FEATURES", {'ENFORCE_PASSWORD_POLICY': True})
@ddt.ddt
class UsersApiTests(SignalDisconnectTestMixin, ModuleStoreTestCase, CacheIsolationTestCase, APIClientMixin):
    """ Test suite for Users API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def get_module_for_user(self, user, course, problem):
        """Helper function to get useful module at self.location in self.course_id for user"""
        mock_request = mock.MagicMock()
        mock_request.user = user
        field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
            course.id, user, course, depth=2)
        module = module_render.get_module(  # pylint: disable=protected-access
            user,
            mock_request,
            problem.location,
            field_data_cache,
            course.id
        )
        return module

    def setUp(self):
        super(UsersApiTests, self).setUp()
        self.test_username = str(uuid.uuid4())
        self.test_password = 'Test.Me64!'
        self.test_email = str(uuid.uuid4()) + '@test.org'
        self.test_first_name = str(uuid.uuid4())
        self.test_last_name = str(uuid.uuid4())
        self.test_city = str(uuid.uuid4())
        self.courses_base_uri = '/api/server/courses'
        self.groups_base_uri = '/api/server/groups'
        self.org_base_uri = '/api/server/organizations/'
        self.workgroups_base_uri = '/api/server/workgroups/'
        self.projects_base_uri = '/api/server/projects/'
        self.users_base_uri = '/api/server/users'
        self.sessions_base_uri = '/api/server/sessions'
        self.test_bogus_course_id = 'foo/bar/baz'
        self.test_bogus_content_id = 'i4x://foo/bar/baz/Chapter1'

        self.test_course_data = '<html>{}</html>'.format(str(uuid.uuid4()))
        self.course_start_date = timezone.now() + relativedelta(days=-1)
        self.course_end_date = timezone.now() + relativedelta(days=60)

        self.user = UserFactory()
        cache.clear()

        self._create_courses()


    def _create_courses(self, store=ModuleStoreEnum.Type.split):
        with modulestore().default_store(store):
            self.course = CourseFactory.create(
                display_name="TEST COURSE",
                start=self.course_start_date,
                end=self.course_end_date,
                org='USERTEST',
                run='USERTEST1',
            )
            self.course_content = ItemFactory.create(
                category="videosequence",
                parent_location=self.course.location,
                due=self.course_end_date,
                display_name="View_Sequence",
            )
            self.course2 = CourseFactory.create(display_name="TEST COURSE2", org='TESTORG2', run='USERTEST2')
            self.course2_content = ItemFactory.create(
                category="videosequence",
                parent_location=self.course2.location,
                due=self.course_end_date,
                display_name="View_Sequence2",
            )

            Role.objects.get_or_create(
                name=FORUM_ROLE_MODERATOR,
                course_id=self.course.id)


    def _create_test_user(self):
        """Helper method to create a new test user"""
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        return user_id

    def test_user_is_staff(self):
        """
        Test if a user is a staff member
        """
        created_user_ids = {'staff': [], 'non_staff': []}
        test_uri = self.users_base_uri
        for i in xrange(1, 4):
            is_staff = True if i % 2 == 0 else False
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': 'PassWord1',
                'first_name': 'John',
                'last_name': 'Doe',
                'city': 'Boston',
                'is_staff': is_staff,
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            if is_staff:
                created_user_ids['staff'].append(response.data['id'])
            else:
                created_user_ids['non_staff'].append(response.data['id'])

        response = self.do_get('{}?ids={}'.format(test_uri, ','.join(map(str, created_user_ids['staff']))))
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(response.data['results']), len(created_user_ids['staff']))
        # all users should be staff
        for user in response.data['results']:
            self.assertEqual(user['is_staff'], True)

        response = self.do_get('{}?ids={}'.format(test_uri, ','.join(map(str, created_user_ids['non_staff']))))
        self.assertEqual(response.status_code, 200)

        self.assertEqual(len(response.data['results']), len(created_user_ids['non_staff']))
        # all users should not be staff
        for user in response.data['results']:
            self.assertEqual(user['is_staff'], False)

    def test_user_list_get(self):  # pylint: disable=R0915
        test_uri = self.users_base_uri
        users = []
        # create a 25 new users
        for i in xrange(1, 26):
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': self.test_password,
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i),
                'city': 'Boston',
                'title': "The King",
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            users.append(response.data['id'])

        # create organizations and add users to them
        total_orgs = 30
        for i in xrange(total_orgs):
            data = {
                'name': '{} {}'.format('Org', i),
                'display_name': '{} {}'.format('Org display name', i),
                'users': users
            }
            response = self.do_post(self.org_base_uri, data)
            self.assertEqual(response.status_code, 201)

        # fetch data without any filters applied
        response = self.do_get('{}?page=1'.format(test_uri))
        self.assertEqual(response.status_code, 200)

        # test default page size
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 20)
        self.assertEqual(response.data['num_pages'], 2)

        # fetch users data with page outside range
        response = self.do_get('{}?ids={}&page=5'.format(test_uri, '2,3,7,11,6,21,34'))
        self.assertEqual(response.status_code, 404)
        # fetch user data by single id
        response = self.do_get('{}?ids={}'.format(test_uri, '23'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(len(response.data['results'][0]['organizations']), total_orgs)
        self.assertIsNotNone(response.data['results'][0]['organizations'][0]['name'])
        self.assertIsNotNone(response.data['results'][0]['organizations'][0]['id'])
        self.assertIsNotNone(response.data['results'][0]['organizations'][0]['url'])
        self.assertIsNone(response.data['results'][0]['last_login'])
        self.assertIsNotNone(response.data['results'][0]['created'])
        # fetch user data by multiple ids
        response = self.do_get('{}?page_size=5&ids={}'.format(test_uri, '2,3,7,11,6,21,34'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 6)
        self.assertEqual(len(response.data['results']), 5)
        self.assertEqual(response.data['num_pages'], 2)
        self.assertIn('page=2', response.data['next'])
        self.assertEqual(response.data['previous'], None)
        # fetch user data by username
        response = self.do_get('{}?username={}'.format(test_uri, 'test_user1'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        # fetch user data by email
        response = self.do_get('{}?email={}'.format(test_uri, 'test2@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIsNotNone(response.data['results'][0]['id'])
        # fetch by username with a non existing user
        response = self.do_get('{}?email={}'.format(test_uri, 'john@example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 0)
        # add some additional fields and filter the response to only these fields
        response = self.do_get('{}?email=test2@example.com&fields=profile_image,city,title'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(
            response.data['results'][0]['profile_image'],
            {
                'image_url_full': '{}/static/default_500.png'.format(settings.LMS_ROOT_URL),
                'image_url_large': '{}/static/default_120.png'.format(settings.LMS_ROOT_URL),
                'image_url_medium': '{}/static/default_50.png'.format(settings.LMS_ROOT_URL),
                'image_url_small': '{}/static/default_30.png'.format(settings.LMS_ROOT_URL),
                'has_image': False
            }
        )
        self.assertEqual(response.data['results'][0]['city'], 'Boston')
        self.assertEqual(response.data['results'][0]['title'], 'The King')
        if 'id' in response.data['results'][0]:
            self.fail("Dynamic field filtering error in UserSerializer")

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_list_get_filters(self, store):
        test_uri = self.users_base_uri

        organizations = []
        organizations.append(Organization.objects.create(display_name='ABC Organization'))
        organizations.append(Organization.objects.create(display_name='XYZ Organization'))

        course1 = CourseFactory.create(org='edX', number='CS101', run='2016_Q1', default_store=store)
        course2 = CourseFactory.create(org='mit', number='CS101', run='2016_Q2', default_store=store)

        users = []
        users.append(UserFactory.create(first_name='John', last_name='Doe', email='john.doe@example.com'))
        users.append(UserFactory.create(first_name='Micheal', last_name='Mcdonald', email='mic.mcdonald@example.com'))
        users.append(UserFactory.create(first_name='Steve', last_name='Jobs', email='steve.jobs@edx.org'))

        for user in users[:2]:
            user.organizations.add(organizations[0])
            CourseEnrollmentFactory.create(user=user, course_id=course1.id)

        users[2].organizations.add(organizations[1])
        CourseEnrollmentFactory.create(user=users[2], course_id=course2.id)

        # fetch user data by exact name match
        response = self.do_get('{}?name={}'.format(test_uri, 'John Doe'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Doe')

        # fetch user data by partial name match
        response = self.do_get('{}?name={}&match=partial'.format(test_uri, 'Jo'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Doe')
        self.assertEqual(response.data['results'][1]['full_name'], 'Steve Jobs')

        # fetch user data by exact first_name/last_name match
        response = self.do_get('{}?name={}'.format(test_uri, 'John'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['first_name'], 'John')

        # fetch user data by partial first_name/last_name match
        response = self.do_get('{}?name={}&match=partial'.format(test_uri, 'Jo'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['first_name'], 'John')
        self.assertEqual(response.data['results'][1]['last_name'], 'Jobs')

        # fetch user data by exact email match
        response = self.do_get('{}?email={}'.format(test_uri, 'steve.jobs@edx.org'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Steve Jobs')
        self.assertEqual(response.data['results'][0]['email'], 'steve.jobs@edx.org')

        # fetch user data by partial email match
        response = self.do_get('{}?email={}&match=partial'.format(test_uri, 'example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Doe')
        self.assertEqual(response.data['results'][1]['full_name'], 'Micheal Mcdonald')

        # fetch user data by partial organization display_name match
        response = self.do_get('{}?organization_display_name={}&match=partial'.format(test_uri, 'ABC'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Doe')
        self.assertEqual(response.data['results'][1]['full_name'], 'Micheal Mcdonald')

        # fetch user data by exact course id match
        course2_id = {'courses': '{}'.format(unicode(course2.id))}
        course2_filter_uri = '{}?{}'.format(test_uri, urlencode(course2_id))
        response = self.do_get(course2_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Steve Jobs')

        # fetch user data by partial course id match
        response = self.do_get('{}?courses={}&match=partial'.format(test_uri, 'edx,mit'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 3)
        self.assertEqual(response.data['results'][0]['full_name'], 'John Doe')
        self.assertEqual(response.data['results'][1]['full_name'], 'Micheal Mcdonald')
        self.assertEqual(response.data['results'][2]['full_name'], 'Steve Jobs')
        enrollment = CourseEnrollment.objects.get(user=users[0],
                                                  course_id=get_course_key(response.data['results'][0]['courses_enrolled'][0]))
        self.assertTrue(enrollment.is_active)
        enrollment = CourseEnrollment.objects.get(user=users[1],
                                                  course_id=get_course_key(response.data['results'][1]['courses_enrolled'][0]))
        self.assertTrue(enrollment.is_active)
        enrollment = CourseEnrollment.objects.get(user=users[2],
                                                  course_id=get_course_key(response.data['results'][2]['courses_enrolled'][0]))
        self.assertTrue(enrollment.is_active)

        # fetch user data by partial course ids and name match
        response = self.do_get('{}?courses={}&match=partial&name={}'.format(test_uri, 'edx,mit', 'job'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Steve Jobs')

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_list_get_multiple_filters(self, store):
        test_uri = self.users_base_uri

        organizations = []
        organizations.append(Organization.objects.create(display_name='ABC Organization'))
        organizations.append(Organization.objects.create(display_name='XYZ Organization'))

        course1 = CourseFactory.create(org='edX', number='CS101', run='2016_Q1', default_store=store)
        course2 = CourseFactory.create(org='mit', number='CS101', run='2016_Q2', default_store=store)

        users = []
        users.append(UserFactory.create(first_name='John', last_name='Doe', email='john.doe@example.com'))
        users.append(UserFactory.create(first_name='Micheal', last_name='Mcdonald', email='mic.mcdonald@example.com'))
        users.append(UserFactory.create(first_name='Steve', last_name='Jobs', email='steve.jobs@edx.org'))
        users.append(UserFactory.create(first_name='Jonathan', last_name='Fay', email='jonathan.fay@example.com'))

        for user in users[:2]:
            user.organizations.add(organizations[0])
            CourseEnrollmentFactory.create(user=user, course_id=course1.id)

        users[2].organizations.add(organizations[1])
        CourseEnrollmentFactory.create(user=users[2], course_id=course2.id)
        CourseEnrollmentFactory.create(user=users[3], course_id=course2.id)

        # fetch user data by partial name, email or organization display_name match
        response = self.do_get('{}?search_query_string={}&match=partial'.format(test_uri, 'Mcd'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Micheal Mcdonald')
        self.assertEqual(response.data['results'][0]['email'], 'mic.mcdonald@example.com')
        self.assertEqual(response.data['results'][0]['organizations'][0]['display_name'], 'ABC Organization')

        # fetch user data by partial name, email or organization display_name and course id match
        response = self.do_get(
            '{}?search_query_string={}&courses={}&match=partial'.format(
                test_uri, 'mic.mcdonald@example.com', 'edX'
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Micheal Mcdonald')
        self.assertEqual(response.data['results'][0]['email'], 'mic.mcdonald@example.com')
        self.assertEqual(len(response.data['results'][0]['organizations']), 1)
        self.assertEqual(response.data['results'][0]['organizations'][0]['display_name'], 'ABC Organization')
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(course1.id))
        enrollment = CourseEnrollment.objects.get(user=users[1],
                                                  course_id=get_course_key(response.data['results'][0]['courses_enrolled'][0]))
        self.assertTrue(enrollment.is_active)

        # fetch user data by partial name and email match
        response = self.do_get('{}?name={}&email={}&match=partial'.format(test_uri, 'Mcd', 'example.com'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Micheal Mcdonald')
        self.assertEqual(response.data['results'][0]['email'], 'mic.mcdonald@example.com')

        # fetch user data by partial name, email and course_id match
        response = self.do_get(
            '{}?name={}&email={}&courses={}&match=partial'.format(test_uri, 'Jo', 'example.com', 'mit')
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Jonathan Fay')
        self.assertEqual(response.data['results'][0]['email'], 'jonathan.fay@example.com')

        # fetch user data by partial organization display_name and course id match
        response = self.do_get(
            '{}?organization_display_name={}&courses={}&match=partial'.format(test_uri, 'XYZ', 'edx')
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 0)

        # fetch user data by partial name, email, organization display_name and course id match
        response = self.do_get(
            '{}?name={}&email={}&organization_display_name={}&courses={}&match=partial'.format(
                test_uri, 'Mic', 'example.com', 'Organization', 'edx'
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['full_name'], 'Micheal Mcdonald')
        self.assertEqual(response.data['results'][0]['email'], 'mic.mcdonald@example.com')
        self.assertEqual(len(response.data['results'][0]['organizations']), 1)
        self.assertEqual(response.data['results'][0]['organizations'][0]['display_name'], 'ABC Organization')
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(course1.id))

        response = self.do_get('{}/{}/courses'.format(test_uri, response.data['results'][0]['id']))
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], unicode(course1.id))

    def test_user_list_get_courses_enrolled_per_course(self):
        test_uri = self.users_base_uri
        # create a 2 new users
        users = UserFactory.create_batch(2)

        # create course enrollments
        CourseEnrollmentFactory.create(user=users[0], course_id=self.course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=self.course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=self.course2.id)

        # fetch enrollments for first course
        course_id = {'courses': '{}'.format(unicode(self.course.id))}
        course_filter_uri = '{}?{}'.format(test_uri, urlencode(course_id))
        response = self.do_get(course_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(self.course.id))
        self.assertEqual(response.data['results'][1]['courses_enrolled'][0], unicode(self.course2.id))
        self.assertEqual(response.data['results'][1]['courses_enrolled'][1], unicode(self.course.id))

        # fetch enrollments for second course
        course2_id = {'courses': '{}'.format(unicode(self.course2.id))}
        course2_filter_uri = '{}?{}'.format(test_uri, urlencode(course2_id))
        response = self.do_get(course2_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(self.course2.id))
        self.assertEqual(response.data['results'][0]['courses_enrolled'][1], unicode(self.course.id))

    def test_user_list_get_courses_enrolled(self):
        test_uri = self.users_base_uri
        # create a 2 new users
        users = UserFactory.create_batch(2)

        # create course enrollments
        CourseEnrollmentFactory.create(user=users[1], course_id=self.course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=self.course2.id)

        # fetch user 1
        response = self.do_get('{}?ids={}'.format(test_uri, users[0].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['courses_enrolled'], [])

        # fetch user 2
        response = self.do_get('{}?ids={}'.format(test_uri, users[1].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(self.course2.id))
        self.assertEqual(response.data['results'][0]['courses_enrolled'][1], unicode(self.course.id))

    def test_user_list_get_roles(self):
        test_uri = self.users_base_uri
        # create a 3 new users
        users = UserFactory.create_batch(2)
        for idx, user in enumerate(users):
            if idx > 0:
                allow_access(self.course, user, 'staff')
            else:
                allow_access(self.course, user, 'instructor')
                allow_access(self.course, user, 'observer')

        # fetch users
        user_ids = ','.join([str(user.id) for user in users])
        response = self.do_get('{}?ids={}'.format(test_uri, user_ids))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(len(response.data['results'][0]['roles']), 2)
        self.assertItemsEqual(response.data['results'][0]['roles'], [u'instructor', u'observer'])
        self.assertEqual(len(response.data['results'][1]['roles']), 1)
        self.assertItemsEqual(response.data['results'][1]['roles'], [u'staff'])

    def test_user_list_get_with_has_organization_filter(self):
        test_uri = self.users_base_uri
        users = []
        # create a 7 new users
        for i in xrange(1, 8):
            data = {
                'email': 'test_orgfilter{}@example.com'.format(i),
                'username': 'test_user_orgfilter{}'.format(i),
                'password': self.test_password,
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i)
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            users.append(response.data['id'])

        # create organizations and add users to them
        total_orgs = 4
        for i in xrange(1, total_orgs):
            data = {
                'name': '{} {}'.format('Org', i),
                'display_name': '{} {}'.format('Org display name', i),
                'users': users[:i]
            }
            response = self.do_post(self.org_base_uri, data)
            self.assertEqual(response.status_code, 201)

        # fetch users without any organization association
        response = self.do_get('{}?has_organizations=true'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 3)
        self.assertIsNotNone(response.data['results'][0]['is_active'])

        response = self.do_get('{}?has_organizations=false'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 4)

    def test_user_list_get_with_organizations_filter(self):
        test_uri = self.users_base_uri
        # create a 8 new users
        users = UserFactory.create_batch(8)

        # create organization and add 4 users to it
        organizations = []
        for i in xrange(2):
            organization = Organization.objects.create(
                name='Test Organization{}'.format(i),
                display_name='Test Org Display Name{}'.format(i),
            )
            organizations.append(organization)

        organizations[0].users.add(*users)
        organizations[1].users.add(*users[:4])

        # fetch users for organization 1
        response = self.do_get('{}?organizations={}'.format(test_uri, organizations[1].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 4)
        self.assertIsNotNone(response.data['results'][0]['is_active'])

        # fetch users in multiple organization
        organization_ids = ','.join([str(organization.id) for organization in organizations])
        response = self.do_get('{}?organizations={}'.format(test_uri, organization_ids))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 8)
        self.assertIsNotNone(response.data['results'][0]['is_active'])

    def test_user_list_get_with_course_enrollment_filter(self):
        test_uri = self.users_base_uri
        # create a 8 new users
        users = UserFactory.create_batch(8)

        # create course enrollments
        for user in users[:4]:
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=self.course2.id)

        # fetch users enrolled in course 1
        course_id = {'courses': '{}'.format(unicode(self.course.id))}
        course_filter_uri = '{}?{}'.format(test_uri, urlencode(course_id))
        response = self.do_get(course_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 4)
        self.assertIsNotNone(response.data['results'][0]['is_active'])

        # fetch users enrolled in course 1 and 2
        course_id = {'courses': '{},{}'.format(unicode(self.course.id), unicode(self.course2.id))}
        course_filter_uri = '{}?{}'.format(test_uri, urlencode(course_id))
        response = self.do_get(course_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 8)
        self.assertIsNotNone(response.data['results'][0]['is_active'])

    def test_user_list_get_with_name_filter(self):
        test_uri = self.users_base_uri
        # create a 8 new users
        users = UserFactory.create_batch(2)
        users.append(UserFactory.create_batch(2, first_name="John", last_name="Doe"))

        # fetch users by name
        response = self.do_get('{}?name=John Doe'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['first_name'], 'John')
        self.assertEqual(response.data['results'][0]['last_name'], 'Doe')

    def test_user_list_post(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        confirm_uri = test_uri + '/' + str(response.data['id'])
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['email'], self.test_email)
        self.assertEqual(response.data['username'], local_username)
        self.assertEqual(response.data['first_name'], self.test_first_name)
        self.assertEqual(response.data['last_name'], self.test_last_name)
        self.assertIsNotNone(response.data['created'])

    def test_user_list_post_inactive(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name, 'is_active': False}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['is_active'], False)

    def test_user_list_post_duplicate(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))

        def post_duplicate_and_assert_409(email, username):
            """
            Posts user data with and asserts that return status code was 409 CONFLICT
            """
            data = {'email': email, 'username': username, 'password': self.test_password}
            expected_message = "Username '{username}' or email '{email}' already exists".format(
                username=username, email=email
            )
            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.data['message'], expected_message)
            self.assertEqual(response.data['field_conflict'], 'username or email')

        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        with transaction.atomic():
            # try creating a user with same email and username
            post_duplicate_and_assert_409(self.test_email, local_username)

        with transaction.atomic():
            # try creating a user with same username but different email address
            post_duplicate_and_assert_409(str(uuid.uuid4()) + '@test.org', local_username)

    @mock.patch.dict("student.models.settings.FEATURES", {"ENABLE_DISCUSSION_EMAIL_DIGEST": True})
    def test_user_list_post_discussion_digest_email(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        user = User.objects.get(id=response.data['id'])
        self.assertIsNotNone(UserPreference.get_value(user, NOTIFICATION_PREF_KEY))

    def test_user_detail_get(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['id'], 0)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['email'], self.test_email)
        self.assertEqual(response.data['username'], local_username)
        self.assertEqual(response.data['first_name'], self.test_first_name)
        self.assertEqual(response.data['last_name'], self.test_last_name)
        self.assertEqual(response.data['is_active'], True)
        self.assertIsNone(response.data['last_login'])
        self.assertEqual(len(response.data['resources']), 2)

    def test_user_detail_get_undefined(self):
        test_uri = '{}/123456789'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_detail_post(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email,
                'username': local_username, 'password': self.test_password,
                'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = test_uri + '/' + str(response.data['id'])
        auth_data = {'username': local_username, 'password': self.test_password}
        self.do_post(self.sessions_base_uri, auth_data)
        self.assertEqual(response.status_code, 201)
        data = {'is_active': False, 'is_staff': True}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['is_active'], False)
        self.assertEqual(response.data['is_staff'], True)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['email'], self.test_email)
        self.assertEqual(response.data['username'], local_username)
        self.assertEqual(response.data['first_name'], self.test_first_name)
        self.assertEqual(response.data['last_name'], self.test_last_name)
        self.assertEqual(response.data['full_name'], '{} {}'.format(self.test_first_name, self.test_last_name))
        self.assertEqual(response.data['is_active'], False)
        self.assertIsNotNone(response.data['created'])

    def test_user_detail_invalid_email(self):
        test_uri = '{}/{}'.format(self.users_base_uri, self.user.id)
        data = {
            'email': 'fail'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid email address', response.content)

    def test_user_detail_duplicate_email(self):
        user2 = UserFactory()
        test_uri = '{}/{}'.format(self.users_base_uri, self.user.id)
        test_uri2 = '{}/{}'.format(self.users_base_uri, user2.id)
        data = {
            'email': self.test_email
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_post(test_uri2, data)
        self.assertEqual(response.status_code, 400)
        self.assertIn('A user with that email address already exists.', response.content)

    def test_user_detail_email_updated(self):
        test_uri = '{}/{}'.format(self.users_base_uri, self.user.id)
        new_email = 'test@example.com'
        data = {
            'email': new_email
        }
        self.assertNotEqual(self.user.email, new_email)
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        self.user = User.objects.get(id=self.user.id)
        self.assertEqual(self.user.email, new_email)

    def test_user_detail_updated_as_null(self):
        """
        Test scenario where city, country and gender can be set to null regardless of the previous value.
        """
        test_uri = '{}/{}'.format(self.users_base_uri, self.user.id)
        data = {
            'country': None,
            'city': None,
            'gender': None,
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        self.user = User.objects.get(id=self.user.id)

        self.assertEqual(self.user.profile.country, None)
        self.assertEqual(self.user.profile.city, None)
        self.assertEqual(self.user.profile.gender, None)

    def test_user_detail_missing_attributes_not_updated(self):
        """
        Test scenario where if city, country, gender and title is missing in the params, they should not be updated.
        """
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(111, 999))
        user_data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name, 'city': self.test_city,
            'country': 'US', 'level_of_education': 'b', 'year_of_birth': '1991',
            'gender': 'male', 'title': 'Software Engineer', 'avatar_url': None
        }
        response = self.do_post(test_uri, user_data)
        self.assertEqual(response.status_code, 201)
        user_id = response.data['id']
        updated_year_of_birth = 1992

        data = {
            'year_of_birth': updated_year_of_birth,
        }
        response = self.do_post(test_uri + '/' + str(user_id), data)
        self.assertEqual(response.status_code, 200)

        self.user = User.objects.get(id=user_id)

        self.assertEqual(self.user.profile.country, user_data['country'])
        self.assertEqual(self.user.profile.city, user_data['city'])
        self.assertEqual(self.user.profile.gender, user_data['gender'])
        self.assertEqual(self.user.profile.title, user_data['title'])
        self.assertEqual(self.user.profile.year_of_birth, updated_year_of_birth)

    def test_user_detail_post_duplicate_username(self):
        """
        Create two users, then pass the same first username in request in order to update username of second user.
        Must return bad request against username, Already exist!
        """
        lst_username = []
        test_uri = self.users_base_uri
        for i in xrange(2):
            local_username = self.test_username + str(i)
            lst_username.append(local_username)
            data = {
                'email': self.test_email, 'username': local_username, 'password': self.test_password,
                'first_name': self.test_first_name, 'last_name': self.test_last_name, 'city': self.test_city,
                'country': 'PK', 'level_of_education': 'b', 'year_of_birth': '2000', "gender": 'male',
                "title": 'Software developer'
            }
            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)

        data["username"] = lst_username[0]

        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)

        # Pass an invalid username in order to update username.
        # Must return bad request against. invalid username!

        data["username"] = '@'
        response = self.do_post(test_uri, data)
        message = _(
            'Username should only consist of A-Z and 0-9, with no spaces.')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data['message'], message)

    def test_user_detail_post_invalid_password(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email,
                'username': local_username, 'password': self.test_password,
                'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = test_uri + '/' + str(response.data['id'])
        data = {'password': 'x'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_user_detail_post_user_profile_added_updated(self):
        """
        Create a user, then add the user profile
        Must be added
        """
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name, 'city': self.test_city,
            'country': 'PK', 'level_of_education': 'b', 'year_of_birth': '2000',
            'gender': 'male', 'title': 'Software Engineer'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        self.is_user_profile_created_updated(response, data)

        # Testing profile updating scenario.
        # Must be updated

        data['first_name'] = "First Name"
        data['last_name'] = "Surname"
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.is_user_profile_created_updated(response, data)

        data["country"] = "US"
        data["year_of_birth"] = "1990"
        data["title"] = ""
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.is_user_profile_created_updated(response, data)

    def test_user_detail_post_profile_added_invalid_year(self):
        """
        Create a user, then add the user profile with invalid year of birth
        Profile Must be added with year_of_birth will be none
        """
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name, 'city': self.test_city,
            'country': 'PK', 'level_of_education': 'b', 'year_of_birth': 'abcd',
            'gender': 'male', 'title': 'Software Engineer'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri_1 = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri_1)
        data["year_of_birth"] = 'None'
        self.is_user_profile_created_updated(response, data)

    def test_user_detail_post_invalid_user(self):
        test_uri = '{}/123124124'.format(self.users_base_uri)
        data = {'is_active': False}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_user_groups_list_post(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri += '/groups'
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(len(response.data['uri']), 0)
        confirm_uri = test_uri + '/' + str(group_id)
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['group_id'], str(group_id))
        self.assertEqual(response.data['user_id'], str(user_id))

    def test_user_groups_list_post_duplicate(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri = test_uri + '/groups'
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)

    def test_user_groups_list_post_invalid_user(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = '{}/897698769/groups'.format(self.users_base_uri)
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_user_groups_list_get(self):
        test_uri = self.groups_base_uri
        group_name = 'Alpha Group'
        data = {'name': group_name, 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name, 'title': 'The King'
        }
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri = test_uri + '/groups'
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['groups']), 0)
        self.assertEqual(response.data['groups'][0]['id'], group_id)
        self.assertEqual(response.data['groups'][0]['name'], str(group_name))

    def test_user_groups_list_get_with_query_params(self):  # pylint: disable=R0915
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email, 'username': local_username, 'password': self.test_password,
            'first_name': self.test_first_name, 'last_name': self.test_last_name
        }
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}'.format(test_uri, str(user_id))
        fail_user_id_group_uri = '{}/{}/groups'.format(self.users_base_uri, '22')

        group_url = self.groups_base_uri
        group_name = 'Alpha Group'
        group_xblock_id = 'location:GroupTester+TG101+1+group-project+079879fdabae47f6848f38a58f41f2c7'
        group_test_value = 'values 2'
        group_data = {
            'xblock_id': group_xblock_id,
            'key2': group_test_value
        }
        data = {'name': group_name, 'type': 'Engineer', 'data': group_data}
        response = self.do_post(group_url, data)
        group_id = response.data['id']
        user_groups_uri = '{}/groups'.format(test_uri)
        data = {'group_id': group_id}
        response = self.do_post(user_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        group_name = 'Beta Group'
        data = {'name': group_name, 'type': 'Architect'}
        response = self.do_post(group_url, data)
        group_id = response.data['id']
        data = {'group_id': group_id}
        response = self.do_post(user_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        course_id = unicode(self.course.id)
        response = self.do_post('{}/{}/courses/'.format(group_url, group_id), {'course_id': course_id})
        self.assertEqual(response.status_code, 201)

        response = self.do_get(fail_user_id_group_uri)
        self.assertEqual(response.status_code, 404)

        response = self.do_get(user_groups_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 2)

        group_type_uri = '{}?type={}'.format(user_groups_uri, 'Engineer')
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 1)

        course = {'course': course_id}
        group_type_uri = '{}?{}'.format(user_groups_uri, urlencode(course))
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 1)
        self.assertEqual(response.data['groups'][0]['id'], group_id)

        group_data_filters = {
            'data__xblock_id': group_xblock_id,
            'data__key2': group_test_value
        }
        group_type_uri = '{}?{}'.format(user_groups_uri, urlencode(group_data_filters))
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 1)

        group_type_uri = '{}?{}'.format(user_groups_uri, urlencode({'data__key2': group_test_value}))
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 1)

        group_type_uri = '{}?{}'.format(user_groups_uri, urlencode({'data__xblock_id': 'invalid_value',
                                                                    'data__key2': group_test_value}))
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 0)

        group_type_uri = '{}?{}'.format(user_groups_uri, urlencode({'data__key2': 'invalid_value'}))
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 0)

        error_type_uri = '{}?type={}'.format(user_groups_uri, 'error_type')
        response = self.do_get(error_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['groups']), 0)

    def test_user_groups_list_get_invalid_user(self):
        test_uri = '{}/123124/groups'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_groups_detail_get(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = test_uri + '/' + str(response.data['id']) + '/groups'
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['uri']), 0)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['group_id'], group_id)
        self.assertEqual(response.data['user_id'], user_id)

    def test_user_groups_detail_delete(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(response.data['id']) + '/groups'
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(group_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)
        response = self.do_delete(
            test_uri)  # Relationship no longer exists, should get a 204 all the same
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_groups_detail_get_invalid_user(self):
        test_uri = '{}/123124/groups/12321'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_groups_detail_get_undefined(self):
        test_uri = self.groups_base_uri
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(test_uri, data)
        group_id = response.data['id']
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}/groups/{}'.format(self.users_base_uri, user_id, group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_courses_list_post(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}/courses'.format(test_uri, str(user_id))
        data = {'course_id': unicode(self.course.id)}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = test_uri + '/' + unicode(self.course.id)
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['id'], unicode(self.course.id))
        self.assertTrue(response.data['is_active'])

    def test_user_courses_list_post_duplicate(self):
        # creating user
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']

        # adding it to a cohort
        test_uri = '{}/{}/courses'.format(test_uri, str(user_id))
        data = {'course_id': unicode(self.course.id)}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        # and trying to add it second time
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)
        self.assertIn("already added to cohort", response.data['message'])

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_list_post_undefined_user(self, store):
        course = CourseFactory.create(org='TUCLPUU', run='TUCLPUU1', default_store=store)
        test_uri = self.users_base_uri
        user_id = '234234'
        test_uri = '{}/{}/courses'.format(test_uri, str(user_id))
        data = {'course_id': unicode(course.id)}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_user_courses_list_post_undefined_course(self):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}/courses'.format(test_uri, str(user_id))
        data = {'course_id': '234asdfapsdf/2sdfs/sdf'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)
        data = {'course_id': 'really-invalid-course-id-oh-boy-watch-out'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    @override_settings(DATABASES=get_non_atomic_database_settings())
    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_list_get(self, store):
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}/courses'.format(test_uri, str(user_id))

        data = {'course_id': unicode(self.course.id)}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        course_with_out_date_values = CourseFactory.create(org='TUCLG', run='TUCLG1', default_store=store)
        data = {'course_id': unicode(course_with_out_date_values.id)}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        confirm_uri = test_uri + '/' + unicode(course_with_out_date_values.id)
        self.assertIn(confirm_uri, response.data[0]['uri'])
        self.assertEqual(response.data[0]['id'], unicode(course_with_out_date_values.id))
        self.assertTrue(response.data[0]['is_active'])
        self.assertEqual(response.data[0]['name'], course_with_out_date_values.display_name)
        self.assertEqual(response.data[0]['start'], course_with_out_date_values.start)
        self.assertEqual(response.data[0]['end'], course_with_out_date_values.end)
        self.assertEqual(
            datetime.strftime(response.data[1]['start'], '%Y-%m-%d %H:%M:%S'),
            datetime.strftime(self.course.start, '%Y-%m-%d %H:%M:%S')
        )
        self.assertEqual(
            datetime.strftime(response.data[1]['end'], '%Y-%m-%d %H:%M:%S'),
            datetime.strftime(self.course.end, '%Y-%m-%d %H:%M:%S')
        )

    def test_user_courses_list_get_undefined_user(self):
        test_uri = '{}/2134234/courses'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_post_position_course_as_descriptor(self, store):
        with modulestore().default_store(store):
            course = CourseFactory.create(org='TUCDPPCAD', run='TUCDPPCAD1')
            chapter1 = ItemFactory.create(  # pylint: disable=W0612
                category="chapter",
                parent_location=course.location,
                display_name="Chapter 1"
            )
            chapter2 = ItemFactory.create(  # pylint: disable=W0612
                category="chapter",
                parent_location=course.location,
                display_name="Chapter 2"
            )
            chapter3 = ItemFactory.create(
                category="chapter",
                parent_location=course.location,
                display_name="Chapter 3"
            )
            sequential1 = ItemFactory.create(  # pylint: disable=W0612
                category="sequential",
                parent_location=chapter3.location,
                display_name="Sequential 1"
            )
            sequential2 = ItemFactory.create(
                category="sequential",
                parent_location=chapter3.location,
                display_name="Sequential 2"
            )
            vertical1 = ItemFactory.create(  # pylint: disable=W0612
                category="vertical",
                parent_location=sequential2.location,
                display_name="Vertical 1"
            )
            vertical2 = ItemFactory.create(  # pylint: disable=W0612
                category="vertical",
                parent_location=sequential2.location,
                display_name="Vertical 2"
            )
            vertical3 = ItemFactory.create(
                category="vertical",
                parent_location=sequential2.location,
                display_name="Vertical 3"
            )

        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = test_uri + '/' + str(user_id) + '/courses'
        data = {'course_id': unicode(course.id)}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + unicode(course.id)
        self.assertEqual(response.status_code, 201)

        position_data = {
            'positions': [
                {
                    'parent_content_id': unicode(course.id),
                    'child_content_id': str(chapter3.location)
                },
                {
                    'parent_content_id': unicode(chapter3.scope_ids.usage_id),
                    'child_content_id': str(sequential2.location)
                },
                {
                    'parent_content_id': unicode(sequential2.scope_ids.usage_id),
                    'child_content_id': str(vertical3.location)
                }
            ]
        }
        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.data['positions'][0], unicode(chapter3.scope_ids.usage_id))
        self.assertEqual(response.data['positions'][1], unicode(sequential2.scope_ids.usage_id))
        self.assertEqual(response.data['positions'][2], unicode(vertical3.scope_ids.usage_id))

        response = self.do_get(response.data['uri'])
        self.assertEqual(response.data['position_tree']['chapter']['id'], unicode(chapter3.scope_ids.usage_id))
        self.assertEqual(response.data['position_tree']['sequential']['id'], unicode(sequential2.scope_ids.usage_id))
        self.assertEqual(response.data['position_tree']['vertical']['id'], unicode(vertical3.scope_ids.usage_id))

    def test_user_courses_detail_post_invalid_course(self):
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.test_bogus_course_id)
        response = self.do_post(test_uri, data={})
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_post_position_invalid_user(self, store):
        with modulestore().default_store(store):
            course = CourseFactory.create(org='TUCDPPIU', run='TUCDPPIU1')
            chapter1 = ItemFactory.create(
                category="chapter",
                parent_location=course.location,
                display_name="Chapter 1"
            )
        user_id = 2342334
        course_id = 'asd/fa/9sd8fasdf'
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, user_id, course_id)
        position_data = {
            'positions': [
                {
                    'parent_content_id': course_id,
                    'child_content_id': str(chapter1.location)

                }
            ]
        }
        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_post_position_course_as_content(self, store):
        with modulestore().default_store(store):
            course = CourseFactory.create(org='TUCDPPCAS', run='TUCDPPCAS1')
            chapter1 = ItemFactory.create(
                category="chapter",
                parent_location=course.location,
                display_name="Chapter 1"
            )
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = test_uri + '/' + str(user_id) + '/courses'
        data = {'course_id': unicode(course.id)}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(course.id)
        self.assertEqual(response.status_code, 201)
        position_data = {
            'positions': [
                {
                    'parent_content_id': str(course.location),
                    'child_content_id': str(chapter1.location)

                }
            ]
        }
        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.data['positions'][0], unicode(chapter1.scope_ids.usage_id))

    def test_user_courses_detail_post_position_invalid_course(self):
        test_uri = '{}/{}/courses'.format(self.users_base_uri, self.user.id)
        data = {'course_id': unicode(self.course.id)}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + unicode(self.course.id)
        self.assertEqual(response.status_code, 201)
        position_data = {
            'positions': [
                {
                    'parent_content_id': self.test_bogus_course_id,
                    'child_content_id': self.test_bogus_content_id
                }
            ]
        }
        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.status_code, 400)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_get(self, store):
        with modulestore().default_store(store):
            course = CourseFactory.create(
                display_name="UserCoursesDetailTestCourse",
                start=self.course_start_date,
                end=self.course_end_date,
                org='TUCDG',
                run='TUCDG1',
            )
            chapter1 = ItemFactory.create(
                category="chapter",
                parent_location=course.location,
                display_name="Overview"
            )
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = test_uri + '/' + str(user_id) + '/courses'
        data = {'course_id': unicode(course.id)}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + unicode(course.id)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['course_id'], unicode(course.id))
        self.assertEqual(response.data['user_id'], user_id)

        # Now add the user's position in the course
        position_data = {
            'positions': [
                {
                    'parent_content_id': unicode(course.id),
                    'child_content_id': unicode(chapter1.scope_ids.usage_id)

                }
            ]
        }
        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.data['positions'][0], unicode(chapter1.scope_ids.usage_id))
        response = self.do_get(test_uri)
        self.assertGreater(response.data['position'], 0)  # Position in the GET response is an integer!
        self.assertEqual(response.data['position_tree']['chapter']['id'], unicode(chapter1.scope_ids.usage_id))

    def test_user_courses_detail_get_invalid_course(self):
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_courses_detail_get_undefined_user(self):
        test_uri = '{}/2134234/courses/a8df7/asv/d98'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_get_undefined_enrollment(self, store):
        course = CourseFactory.create(org='TUCDGUE', run='TUCDGUE1', default_store=store)
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, user_id, course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_delete(self, store):
        course = CourseFactory.create(org='TUCDD', run='TUCDD1', default_store=store)
        test_uri = self.users_base_uri
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name}
        response = self.do_post(test_uri, data)
        user_id = response.data['id']
        post_uri = test_uri + '/' + str(user_id) + '/courses'
        data = {'course_id': unicode(course.id)}
        response = self.do_post(post_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = post_uri + '/' + str(course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        response = self.do_post(post_uri, data)  # Re-enroll the student in the course
        self.assertEqual(response.status_code, 409)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)
        response = self.do_post(post_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_courses_detail_delete_undefined_user(self, store):
        course = CourseFactory.create(org='TUCDDUU', run='TUCDDUU1', default_store=store)
        user_id = '2134234'
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, user_id, course.id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_courses_detail_delete_undefined_course(self):
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.test_bogus_course_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_user_course_grades_course_not_found(self):
        test_uri = '{}/{}/courses/some/unknown/course/grades'.format(self.users_base_uri, self.user.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_course_grades_user_not_found(self, store):
        course = CourseFactory.create(org='TUCGUNF', run='TUCGUNF1', default_store=store)
        test_uri = '{}/99999999/courses/{}/grades'.format(self.users_base_uri, course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_course_grades_user_not_enrolled(self, store):
        course = CourseFactory.create(org='TUCGUNF', run='TUCGUNF1', default_store=store)
        test_uri = '{}/{}/courses/{}/grades'.format(self.users_base_uri, self.user.id, course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        # now enroll user
        post_uri = '{}/{}/courses'.format(self.users_base_uri, self.user.id)
        data = {'course_id': unicode(course.id)}
        response = self.do_post(post_uri, data)
        self.assertEqual(response.status_code, 201)

        # get user grades after enrollment
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    def test_user_preferences_user_list_get_not_found(self):
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, '999999')
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_preferences_list_get_default(self):
        # By default newly created users will have one initial preference settings:
        # 'pref-lang' = 'en'
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data['pref-lang'], 'en')
        self.assertIsNotNone(response.data['notification_pref'])

    def test_user_preferences_list_post_user_not_found(self):
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, '999999')
        response = self.do_post(test_uri, {"foo": "bar"})
        self.assertEqual(response.status_code, 404)

    def test_user_preferences_list_post_bad_request(self):
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 400)
        # also test with a non-simple key/value set of strings
        response = self.do_post(test_uri, {"an_array": ['1', '2']})
        self.assertEqual(response.status_code, 400)
        response = self.do_post(test_uri, {"an_int": 1})
        self.assertEqual(response.status_code, 400)
        response = self.do_post(test_uri, {"a_float": 1.00})
        self.assertEqual(response.status_code, 400)
        response = self.do_post(test_uri, {"a_boolean": False})
        self.assertEqual(response.status_code, 400)

    def test_user_preferences_list_post(self):
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_post(test_uri, {"foo": "bar"})
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertIsNotNone(response.data['notification_pref'])
        self.assertEqual(response.data['pref-lang'], 'en')
        self.assertEqual(response.data['foo'], 'bar')

    def test_user_preferences_list_update(self):
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_post(test_uri, {"foo": "bar"})
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, {"foo": "updated"})
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)
        self.assertIsNotNone(response.data['notification_pref'])
        self.assertEqual(response.data['pref-lang'], 'en')
        self.assertEqual(response.data['foo'], 'updated')

    def test_user_preferences_detail_get(self):
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_post(test_uri, {"foo": "bar"})
        self.assertEqual(response.status_code, 201)
        test_uri = '{}/{}'.format(test_uri, 'foo')
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['foo'], 'bar')

    def test_user_preferences_detail_get_invalid_user(self):
        test_uri = '{}/12345/preferences/foo'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_preferences_detail_delete(self):
        user_id = self._create_test_user()
        test_uri = '{}/{}/preferences'.format(self.users_base_uri, user_id)
        response = self.do_post(test_uri, {"foo": "bar"})
        self.assertEqual(response.status_code, 201)
        test_uri = '{}/{}'.format(test_uri, 'foo')
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_user_preferences_detail_delete_invalid_user(self):
        test_uri = '{}/12345/preferences/foo'.format(self.users_base_uri)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 404)

    def is_user_profile_created_updated(self, response, data):
        """This function compare response with user profile data """

        first_name = data.get('first_name', self.test_first_name)
        last_name = data.get('last_name', self.test_last_name)
        fullname = '{} {}'.format(first_name, last_name)
        self.assertEqual(response.data['full_name'], fullname)
        self.assertEqual(response.data['city'], data["city"])
        self.assertEqual(response.data['country'], data["country"])
        self.assertEqual(response.data['gender'], data["gender"])
        self.assertEqual(response.data['title'], data["title"])
        self.assertEqual(
            response.data['level_of_education'], data["level_of_education"])
        self.assertEqual(
            str(response.data['year_of_birth']), data["year_of_birth"])
        # This one's technically on the user model itself, but can be updated.
        self.assertEqual(response.data['email'], data['email'])

    def test_user_organizations_list(self):
        user_id = self.user.id
        anonymous_id = anonymous_id_for_user(self.user, self.course.id)
        for i in xrange(1, 7):
            data = {
                'name': 'Org ' + str(i),
                'display_name': 'Org display name' + str(i),
                'users': [user_id]
            }
            response = self.do_post(self.org_base_uri, data)
            self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/organizations/'.format(self.users_base_uri, user_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 6)
        self.assertEqual(len(response.data['results']), 6)
        self.assertEqual(response.data['num_pages'], 1)

        # test with anonymous user id
        test_uri = '{}/{}/organizations/'.format(self.users_base_uri, anonymous_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 6)

        # test with invalid user
        response = self.do_get('{}/4356340/organizations/'.format(self.users_base_uri))
        self.assertEqual(response.status_code, 404)

    def test_user_workgroups_list(self):
        test_workgroups_uri = self.workgroups_base_uri  # pylint: disable=W0612
        project_1 = Project.objects.create(
            course_id=unicode(self.course.id),
            content_id=unicode(self.course_content.scope_ids.usage_id),
        )
        p1_workgroup_1 = Workgroup.objects.create(  # pylint: disable=W0612
            name='Workgroup 1',
            project=project_1
        )

        project_2 = Project.objects.create(
            course_id=unicode(self.course2.id),
            content_id=unicode(self.course2_content.scope_ids.usage_id),
        )
        p2_workgroup_1 = Workgroup.objects.create(  # pylint: disable=W0612
            name='Workgroup 2',
            project=project_2
        )
        for __ in xrange(1, 12):
            test_user = UserFactory()
            users_uri = '{}{}/users/'.format(self.workgroups_base_uri, 1)
            data = {"id": test_user.id}
            response = self.do_post(users_uri, data)
            self.assertEqual(response.status_code, 201)
            if test_user.id > 6:
                users_uri = '{}{}/users/'.format(self.workgroups_base_uri, 2)
                data = {"id": test_user.id}
                response = self.do_post(users_uri, data)
                self.assertEqual(response.status_code, 201)

        # test with anonymous user id
        anonymous_id = anonymous_id_for_user(test_user, self.course.id)
        test_uri = '{}/{}/workgroups/?page_size=1'.format(self.users_base_uri, anonymous_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['num_pages'], 2)

        # test with course_id filter and integer user id
        course_id = {'course_id': unicode(self.course.id)}
        response = self.do_get('{}/{}/workgroups/?{}'.format(self.users_base_uri, test_user.id, urlencode(course_id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertIsNotNone(response.data['results'][0]['name'])
        self.assertIsNotNone(response.data['results'][0]['project'])

        # test with invalid user
        response = self.do_get('{}/4356340/workgroups/'.format(self.users_base_uri))
        self.assertEqual(response.status_code, 404)

        # test with valid user but has no workgroup
        another_user_id = self._create_test_user()
        response = self.do_get('{}/{}/workgroups/'.format(self.users_base_uri, another_user_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(len(response.data['results']), 0)

    def test_user_count_by_city(self):
        test_uri = self.users_base_uri

        # create a 25 new users
        for i in xrange(1, 26):
            if i < 10:
                city = 'San Francisco'
            elif i < 15:
                city = 'Denver'
            elif i < 20:
                city = 'Dallas'
            else:
                city = 'New York City'
            data = {
                'email': 'test{}@example.com'.format(i), 'username': 'test_user{}'.format(i),
                'password': self.test_password,
                'first_name': self.test_first_name, 'last_name': self.test_last_name, 'city': city,
                'country': 'PK', 'level_of_education': 'b', 'year_of_birth': '2000', 'gender': 'male',
                'title': 'Software Engineer'
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            response = self.do_get(response.data['uri'])
            self.assertEqual(response.status_code, 200)
            self.is_user_profile_created_updated(response, data)

        response = self.do_get('{}/metrics/cities/'.format(self.users_base_uri))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 4)
        self.assertEqual(response.data['results'][0]['city'], 'San Francisco')
        self.assertEqual(response.data['results'][0]['count'], 9)

        # filter counts by city
        response = self.do_get('{}/metrics/cities/?city=new york city'.format(self.users_base_uri))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['city'], 'New York City')
        self.assertEqual(response.data['results'][0]['count'], 6)

    def test_users_social_metrics_check_service_availability(self):
        test_uri = '{}/{}/courses/{}/metrics/social/?include_stats=true'.format(self.users_base_uri, self.user.id, self.course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    def test_users_social_metrics_get_service_unavailability(self):
        test_uri = '{}/{}/courses/{}/metrics/social/'.format(self.users_base_uri, self.user.id, self.course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    def test_users_social_metrics_get_invalid_user(self):
        test_uri = '{}/12345/courses/{}/metrics/social/'.format(self.users_base_uri, self.course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_social_metrics(self):
        test_uri = '{}/{}/courses/{}/metrics/social/?include_stats=true'.format(
            self.users_base_uri, self.user.id, self.course.id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    def test_users_social_metrics_end_date(self):
        user_score = 30
        course = CourseFactory.create(org='TUCGLG', run='TUCGLG1', end=datetime(2012, 1, 1))
        CourseEnrollmentFactory(user=self.user, course_id=course.id)
        StudentSocialEngagementScore.objects.get_or_create(
            user=self.user, course_id=course.id, defaults={'score': user_score}
        )
        test_uri = '{}/{}/courses/{}/metrics/social/?include_stats=true'.format(
            self.users_base_uri, self.user.id, course.id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], user_score)
        self.assertEqual(response.data['course_avg'], user_score)

    def test_user_social_metrics_engagement_scores(self):
        other_user = UserFactory()

        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=other_user, course_id=self.course.id)

        user_score = StudentSocialEngagementScore(user=self.user, course_id=self.course.id, score=75)
        user_score.save()
        other_score = StudentSocialEngagementScore(user=other_user, course_id=self.course.id, score=125)
        other_score.save()
        course_avg_score = (user_score.score + other_score.score) / 2

        test_uri = '{}/{}/courses/{}/metrics/social/'.format(self.users_base_uri, self.user.id, self.course.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['score'], user_score.score)
        self.assertEqual(response.data['course_avg'], course_avg_score)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_users_roles_list_get(self, store):
        allow_access(self.course, self.user, 'staff')
        course2 = CourseFactory.create(
            display_name="TEST COURSE2",
            start=datetime(2014, 6, 16, 14, 30),
            end=datetime(2020, 1, 16, 14, 30),
            org='TURLG',
            run='TURLG1',
            default_store=store,
        )
        allow_access(course2, self.user, 'instructor')
        course3 = CourseFactory.create(
            display_name="TEST COURSE3",
            start=datetime(2014, 6, 16, 14, 30),
            end=datetime(2020, 1, 16, 14, 30),
            org='TURLG2',
            run='TURLG2',
            default_store=store,
        )
        allow_access(course3, self.user, 'staff')
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)

        # filter roleset by course
        course_id = {'course_id': '{}'.format(unicode(course3.id))}
        course_filter_uri = '{}?{}'.format(test_uri, urlencode(course_id))
        response = self.do_get(course_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        # filter roleset by role
        role = {'role': 'instructor'}
        role_filter_uri = '{}?{}'.format(test_uri, urlencode(role))
        response = self.do_get(role_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        role = {'role': 'invalid_role'}
        role_filter_uri = '{}?{}'.format(test_uri, urlencode(role))
        response = self.do_get(role_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

    def test_users_roles_list_get_invalid_user(self):
        test_uri = '{}/23423/roles/'.format(self.users_base_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_list_get_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        course_id = {'course_id': '{}'.format(unicode(self.test_bogus_course_id))}
        test_uri = '{}?{}'.format(test_uri, urlencode(course_id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_list_post(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        data = {'course_id': unicode(self.course.id), 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)

        # Confirm this user also has forum moderation permissions
        role = Role.objects.get(course_id=self.course.id, name=FORUM_ROLE_MODERATOR)
        has_role = role.users.get(id=self.user.id)
        self.assertTrue(has_role)

    def test_users_roles_list_post_invalid_user(self):
        test_uri = '{}/2131/roles/'.format(self.users_base_uri)
        data = {'course_id': unicode(self.course.id), 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_list_post_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'course_id': self.test_bogus_course_id, 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_roles_list_post_invalid_role(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'course_id': unicode(self.course.id), 'role': 'invalid_role'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_users_roles_list_put(self, store):
        course2 = CourseFactory.create(
            display_name="TEST COURSE2",
            start=datetime(2014, 6, 16, 14, 30),
            end=datetime(2020, 1, 16, 14, 30),
            org='TURLP2',
            run='TURLP2',
            default_store=store,
        )
        Role.objects.get_or_create(
            name=FORUM_ROLE_MODERATOR,
            course_id=course2.id)

        course3 = CourseFactory.create(
            display_name="TEST COURSE3",
            start=datetime(2014, 6, 16, 14, 30),
            end=datetime(2020, 1, 16, 14, 30),
            org='TURLP3',
            run='TURLP3',
            default_store=store,
        )
        Role.objects.get_or_create(
            name=FORUM_ROLE_MODERATOR,
            course_id=course3.id)

        course4 = CourseFactory.create(
            display_name="COURSE4 NO MODERATOR",
            start=datetime(2014, 6, 16, 14, 30),
            end=datetime(2020, 1, 16, 14, 30),
            org='TURLP4',
            run='TURLP4',
            default_store=store,
        )

        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 0)

        data = {'ignore_roles': ['staff'], 'roles': [
            {'course_id': unicode(self.course.id), 'role': 'instructor'},
            {'course_id': unicode(course2.id), 'role': 'instructor'},
            {'course_id': unicode(course3.id), 'role': 'instructor'},
            {'course_id': unicode(course3.id), 'role': 'staff'},
        ]}

        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 3)
        for role in response.data['results']:
            self.assertEqual(role['role'], 'instructor')

        data = {'roles': [
            {'course_id': unicode(self.course.id), 'role': 'staff'},
            {'course_id': unicode(course2.id), 'role': 'staff'},
        ]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)
        for role in response.data['results']:
            self.assertEqual(role['role'], 'staff')

        # Add a role that does not have a corresponding moderator role configured
        allow_access(course4, self.user, 'staff')
        # Now modify the existing no-moderator role using the API, which tries to set the moderator role
        # Also change one of the existing moderator roles, but call it using the deprecated string version
        data = {'roles': [
            {'course_id': course4.id.to_deprecated_string(), 'role': 'instructor'},
            {'course_id': course2.id.to_deprecated_string(), 'role': 'instructor'},
        ]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 200)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 2)

    def test_users_roles_list_put_invalid_user(self):
        test_uri = '{}/2131/roles/'.format(self.users_base_uri)
        data = {'roles': [{'course_id': unicode(self.course.id), 'role': 'instructor'}]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_list_put_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'course_id': unicode(self.course.id), 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {'roles': [{'course_id': self.test_bogus_course_id, 'role': 'instructor'}]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 400)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['course_id'], unicode(self.course.id))

    def test_users_roles_list_put_invalid_roles(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'roles': []}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 400)
        data = {'roles': [{'course_id': unicode(self.course.id), 'role': 'invalid-role'}]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_roles_courses_detail_delete(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'course_id': unicode(self.course.id), 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 1)

        delete_uri = '{}instructor/courses/{}'.format(test_uri, unicode(self.course.id))
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 204)

        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 0)

        # Confirm this user no longer has forum moderation permissions
        role = Role.objects.get(course_id=self.course.id, name=FORUM_ROLE_MODERATOR)
        try:
            role.users.get(id=self.user.id)
            self.assertTrue(False)
        except ObjectDoesNotExist:
            pass

    def test_users_roles_courses_detail_delete_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        delete_uri = '{}instructor/courses/{}'.format(test_uri, self.test_bogus_course_id)
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_courses_detail_delete_invalid_user(self):
        test_uri = '{}/124134/roles/'.format(self.users_base_uri)
        delete_uri = '{}instructor/courses/{}'.format(test_uri, unicode(self.course.id))
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_roles_courses_detail_delete_invalid_role(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        delete_uri = '{}invalid_role/courses/{}'.format(test_uri, unicode(self.course.id))
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_mark_notification_as_read(self):
        user_id = self._create_test_user()

        msg_type = NotificationType(
            name='open-edx.edx_notifications.lib.tests.test_publisher',
            renderer='edx_notifications.renderers.basic.BasicSubjectBodyRenderer',
        )
        register_notification_type(msg_type)
        msg = NotificationMessage(
            namespace='test-runner',
            msg_type=msg_type,
            payload={
                'foo': 'bar'
            }
        )

        # now do happy path
        sent_user_msg = publish_notification_to_user(user_id, msg)

        # verify unread count
        self.assertEqual(get_notifications_count_for_user(user_id, filters={'read': False}), 1)

        # mark as read
        test_uri = '{}/{}/notifications/{}/'.format(self.users_base_uri, user_id, sent_user_msg.msg.id)
        response = self.do_post(test_uri, {"read": True})
        self.assertEqual(response.status_code, 201)

        # then verify unread count, which should be 0
        self.assertEqual(get_notifications_count_for_user(user_id, filters={'read': False}), 0)

    @mock.patch("edx_solutions_api_integration.users.views.module_render.get_module_for_descriptor")
    def test_user_courses_detail_get_undefined_course_module(self, mock_get_module_for_descriptor):
        # Enroll test user in test course
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        # Get user course details when course_module is None
        mock_get_module_for_descriptor.return_value = None

        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.course.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['position'], None)

    def test_users_list_post_missing_email(self):
        # Test with missing email in the request data
        data = {'username': self.test_username, 'password': self.test_password}
        response = self.do_post(self.users_base_uri, data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username=self.test_username).exists())

    def test_users_list_post_missing_username(self):
        # Test with missing username in the request data
        data = {'email': self.test_email, 'password': self.test_password}
        response = self.do_post(self.users_base_uri, data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username=self.test_username).exists())

    def test_users_list_post_missing_password(self):
        # Test with missing password in the request data
        data = {'email': self.test_email, 'username': self.test_username}
        response = self.do_post(self.users_base_uri, data)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.filter(username=self.test_username).exists())

    def test_create_integration_test_user_no_courses(self):
        data = {
            'email': self.test_email,
            'username': self.test_username,
            'password': self.test_password,
        }
        response = self.do_post('{}/integration-test-users/'.format(self.users_base_uri), data)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.filter(username=self.test_username).exists())
        self.assertEqual(response.data['courses'], [])
        self.assertEqual(
            CourseEnrollment.objects.filter(user__username=self.test_username, course_id=self.course.id).count(),
            0
        )

    def test_create_integration_test_user(self):
        data = {
            'email': self.test_email,
            'username': self.test_username,
            'password': self.test_password,
            'courses': [
                six.text_type(self.course.id),
                'course-v1:non+existent+course',
                'course-v2:doesnt+exist',
            ],
        }
        response = self.do_post('{}/integration-test-users/'.format(self.users_base_uri), data)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(User.objects.filter(username=self.test_username).exists())
        self.assertEqual(response.data['courses'], [six.text_type(self.course.id)])
        enrollment = CourseEnrollment.objects.get(
            user__username=self.test_username,
            course_id=self.course.id
        )
        self.assertTrue(enrollment.is_active)

    def test_users_groups_list_missing_group_id(self):
        # Test with missing group_id in request data
        test_uri = '{}/{}/groups/'.format(self.users_base_uri, self.user.id)
        data = {'group_id': ''}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_groups_detail_delete_invalid_user_id(self):
        # Test with invalid user_id
        test_group = GroupFactory.create()
        test_uri = '{}/{}/groups/{}'.format(self.users_base_uri, '1234567', test_group.id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_users_courses_list_post_missing_course_id(self):
        # Test with missing course_id in request data
        test_uri = '{}/{}/courses/'.format(self.users_base_uri, self.user.id)
        data = {'course_id': ''}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_notifications_detail_missing_read_value(self):
        test_uri = '{}/{}/notifications/{}/'.format(self.users_base_uri, self.user.id, '1')
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 400)

    def test_users_courses_detail_post_missing_positions(self):
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.course.id)
        response = self.do_post(test_uri, data={})
        self.assertEqual(response.status_code, 400)

    def test_users_courses_detail_post_missing_parent_content_id(self):
        position_data = {'positions': [{'child_content_id': str(self.course.location)}]}
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.course.id)

        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.status_code, 400)

    def test_users_courses_detail_post_missing_child_content_id(self):
        position_data = {'positions': [{'parent_content_id': str(self.course.id)}]}
        test_uri = '{}/{}/courses/{}'.format(self.users_base_uri, self.user.id, self.course.id)

        response = self.do_post(test_uri, data=position_data)
        self.assertEqual(response.status_code, 400)

    def test_users_roles_list_put_missing_roles(self):
        # Test with missing roles in request data
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        response = self.do_put(test_uri, {})
        self.assertEqual(response.status_code, 400)

    def test_users_roles_list_put_missing_role_value(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'roles': [{'course_id': unicode(self.course.id)}]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_roles_list_put_missing_course_id(self):
        test_uri = '{}/{}/roles/'.format(self.users_base_uri, self.user.id)
        data = {'roles': [{'role': 'instructor'}]}
        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_users_courses_grades_detail_race_condition(self):
        """
        This unit test is written to create the race condition which caused IntegrityError in UsersCoursesGradesDetail
        api when two threads try ko execute the generate_user_gradebook function at the same time. One thread
        creates a new record in database and when other tries to create the record, error occurs.

        Here we halt thread 1 execution at the point before it calls generate_user_gradebook method, the
        thread 2 executes. Thread 2 will create new record and when thread 1 resume, it will find the new
        record in the database which is handled with the get_or_create method in the api.
        """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        def get_users_courses_grades_detail(*args):
            test_uri = '{}/{}/courses/{}/grades'.format(self.users_base_uri, self.user.id, unicode(self.course.id))
            response = self.do_get(test_uri)
            self.assertEqual(response.status_code, 200)

        with before_after.before('gradebook.utils.generate_user_gradebook', get_users_courses_grades_detail):
            get_users_courses_grades_detail()

    def test_user_detail_post_unicode_data(self):
        test_first_name = u'Miké'
        test_last_name = u'Meÿers'

        test_uri = '{}/{}'.format(self.users_base_uri, self.user.id)
        data = {
            'first_name': test_first_name,
            'last_name': test_last_name
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['first_name'], test_first_name)
        self.assertEqual(response.data['last_name'], test_last_name)
        self.assertEqual(response.data['full_name'], u'{} {}'.format(test_first_name, test_last_name))

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_user_delete(self, store):
        test_uri = self.users_base_uri

        organizations = []
        organizations.append(Organization.objects.create(display_name='ABC Organization'))
        organizations.append(Organization.objects.create(display_name='XYZ Organization'))

        course1 = CourseFactory.create(org='edX', number='CS101', run='2016_Q1', default_store=store)
        course2 = CourseFactory.create(org='mit', number='CS101', run='2016_Q2', default_store=store)

        user_ids = []
        # create 30 new users
        for i in xrange(1, 31):
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': self.test_password,
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i),
                'city': 'Boston',
                'title': "The King",
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            user_ids.append(response.data['id'])

        # add first half to ABC Organization
        for user in User.objects.filter(id__in=user_ids[:15]):
            user.organizations.add(organizations[0])
            CourseEnrollmentFactory.create(user=user, course_id=course1.id)
        # add second half to XYZ Organization
        for user in User.objects.filter(id__in=user_ids[15:]):
            user.organizations.add(organizations[0])
            CourseEnrollmentFactory.create(user=user, course_id=course2.id)

        # delete 1 user by id
        response = self.do_delete('{}?ids={}'.format(test_uri, user_ids[0]))
        self.assertEqual(response.status_code, 204)
        self.assertIsNone(User.objects.filter(id=user_ids[0]).first())

        # delete multiple users by id
        response = self.do_delete('{}?ids={}'.format(test_uri, ','.join([str(i) for i in user_ids[1:10]])))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(User.objects.filter(id__in=user_ids[1:10]).count(), 0)

        # delete 1 user by username
        response = self.do_delete('{}?username={}'.format(test_uri, 'test_user12'))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(User.objects.filter(username='test_user12').count(), 0)

        # require either username or ids
        response = self.do_delete('{}?name=John&match=partial'.format(test_uri))
        self.assertEqual(response.status_code, 400)

        # other parameters are ignored
        response = self.do_delete('{}?ids={}&page=10&page_size=2&name=John&match=partial'.format(
            test_uri, ','.join([str(i) for i in user_ids[10:15]])
        ))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(User.objects.filter(id__in=user_ids[10:15]).count(), 0)

        # assert that only the last 15 users exist
        self.assertEqual(User.objects.filter(id__in=user_ids[:15]).count(), 0)
        self.assertEqual(User.objects.filter(id__in=user_ids[15:]).count(), 15)



@ddt.ddt
class TokenBasedUsersApiTests(CacheIsolationTestCase, APIClientMixin, OAuth2TokenMixin):
    """ Test suite for Token Based Users API views """

    def setUp(self):
        super(TokenBasedUsersApiTests, self).setUp()

        self.token_based_user_uri = '/api/server/users/validate-token/'

        self.client = Client()
        self.user = UserFactory.create()
        self.bearer_token = self.create_oauth2_token(self.user)

    def test_token_based_user_details_get(self):

        response = self.client.get(self.token_based_user_uri,
                                   HTTP_AUTHORIZATION="Bearer {0}".format(self.bearer_token))

        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['id'], 0)
        self.assertEqual(response.data['email'], self.user.email)
        self.assertEqual(response.data['username'], self.user.username)
        self.assertEqual(response.data['is_active'], True)

    def test_fake_token_user_details_get(self):
        response = self.client.get(self.token_based_user_uri,
                                   HTTP_AUTHORIZATION="Bearer {0}".format('fake-bearer-token'))

        self.assertEqual(response.status_code, 401)


@ddt.ddt
class UsersGradesApiTests(
    SignalDisconnectTestMixin,
    ModuleStoreTestCase,
    CacheIsolationTestCase,
    APIClientMixin,
    CourseGradingMixin,
):

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super(UsersGradesApiTests, self).setUp()
        self.user = UserFactory()
        self.users_base_uri = '/api/server/users'

    def test_user_courses_grades_list_get(self):  # pylint: disable=R0915
        grading_course = self.setup_course_with_grading()
        CourseEnrollmentFactory.create(user=self.user, course_id=grading_course.id)
        module = self.get_module_for_user(self.user, grading_course, grading_course.midterm_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': self.user.id}
        module.system.publish(module, 'grade', grade_dict)

        test_uri = '{}/{}/courses/{}/grades'.format(self.users_base_uri, self.user.id, unicode(grading_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        courseware_summary = response.data['courseware_summary']
        self.assertEqual(len(courseware_summary), 2)
        self.assertEqual(courseware_summary[0]['display_name'], 'Chapter 1')

        sections = courseware_summary[0]['sections']
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['display_name'], 'Sequence 1')
        self.assertEqual(sections[0]['graded'], False)

        sections = courseware_summary[1]['sections']
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]['display_name'], 'Sequence 2')
        self.assertEqual(sections[0]['graded'], True)

        grade_summary = response.data['grade_summary']
        self.assertGreater(len(grade_summary['section_breakdown']), 0)
        grading_policy = response.data['grading_policy']
        self.assertGreater(len(grading_policy['GRADER']), 0)
        self.assertIsNotNone(grading_policy['GRADE_CUTOFFS'])
        self.assertAlmostEqual(response.data['current_grade'], 0.5, 1)
        self.assertAlmostEqual(response.data['proforma_grade'], 1, 1)

        test_uri = '{}/{}/courses/grades'.format(self.users_base_uri, self.user.id)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['course_id'], unicode(grading_course.id))
        self.assertEqual(response.data[0]['current_grade'], 0.5, 1)
        self.assertEqual(response.data[0]['proforma_grade'], 1, 1)
        self.assertEqual(response.data[0]['complete_status'], False)

    def test_user_courses_grades_list_get_after_enrollment(self):  # pylint: disable=R0915
        grading_course = self.setup_course_with_grading()

        # getting grades without user being enrolled in the course should raise 404
        test_uri = '{}/{}/courses/{}/grades'.format(self.users_base_uri, self.user.id, unicode(grading_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        # enroll user in the course
        test_uri = '{}/{}/courses'.format(self.users_base_uri, self.user.id)
        response = self.do_post(test_uri, {'course_id': unicode(grading_course.id)})
        self.assertEqual(response.status_code, 201)

        # now we should be able to fetch grades of user
        test_uri = '{}/{}/courses/{}/grades'.format(self.users_base_uri, self.user.id, unicode(grading_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        courseware_summary = response.data['courseware_summary']
        self.assertEqual(len(courseware_summary), 2)
        self.assertEqual(courseware_summary[0]['display_name'], 'Chapter 1')

        sections = courseware_summary[0]['sections']
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['display_name'], 'Sequence 1')
        self.assertEqual(sections[0]['graded'], False)

        sections = courseware_summary[1]['sections']
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0]['display_name'], 'Sequence 2')
        self.assertEqual(sections[0]['graded'], True)

        grade_summary = response.data['grade_summary']
        self.assertGreater(len(grade_summary['section_breakdown']), 0)
        grading_policy = response.data['grading_policy']
        self.assertGreater(len(grading_policy['GRADER']), 0)
        self.assertIsNotNone(grading_policy['GRADE_CUTOFFS'])
        self.assertAlmostEqual(response.data['current_grade'], 0, 0)
        self.assertAlmostEqual(response.data['proforma_grade'], 0, 0)

        test_uri = '{}/{}/courses/grades'.format(self.users_base_uri, self.user.id)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['course_id'], unicode(grading_course.id))
        self.assertEqual(response.data[0]['current_grade'], 0, 0)
        self.assertEqual(response.data[0]['proforma_grade'], 0, 0)
        self.assertEqual(response.data[0]['complete_status'], False)


@override_switch(
    '{}.{}'.format(WAFFLE_NAMESPACE, ENABLE_COMPLETION_TRACKING),
    active=True,
)
@ddt.ddt
class UsersProgressApiTests(
    SignalDisconnectTestMixin, SharedModuleStoreTestCase, APIClientMixin, CourseGradingMixin
):
    """ Test suite for User Progress API views """

    ENABLED_SIGNALS = ['course_published']
    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super(UsersProgressApiTests, cls).setUpClass()
        cls.base_courses_uri = '/api/server/courses'
        cls.base_users_uri = '/api/server/users'
        cls.language = "en-us"
        cls.course_start_date = timezone.now() + relativedelta(days=-1)
        cls.course_end_date = timezone.now() + relativedelta(days=60)
        cls.course = CourseFactory.create(
            start=cls.course_start_date,
            end=cls.course_end_date,
            language=cls.language,
        )
        cls.test_data = '<html>{}</html>'.format(str(uuid.uuid4()))

        cls.chapter = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            due=cls.course_end_date,
            display_name="Overview",
        )

        cls.course_project = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            display_name="Group Project",
        )

        cls.course_project2 = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            display_name="Group Project2"
        )

        cls.course_content2 = ItemFactory.create(
            category="sequential",
            parent_location=cls.chapter.location,
            display_name="Sequential",
        )

        cls.content_child2 = ItemFactory.create(
            category="vertical",
            parent_location=cls.course_content2.location,
            display_name="Vertical Sequence",
        )

        cls.course_content = ItemFactory.create(
            category="videosequence",
            parent_location=cls.content_child2.location,
            display_name="Video_Sequence",
        )

        cls.content_child = ItemFactory.create(
            category="video",
            parent_location=cls.course_content.location,
            display_name="Video",
        )

        cls.content_subchild = ItemFactory.create(
            category="video",
            parent_location=cls.content_child2.location,
            display_name="Child Video",
        )

        cls.user = UserFactory()
        cache.clear()

    def test_users_progress_list(self):
        """ Test progress value returned by users progress list api """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        BlockCompletion.objects.submit_completion(
            user=self.user,
            course_key=self.course.id,
            block_key=self.content_child.scope_ids.usage_id,
            completion=1.0,
        )

        user_grade, user_proforma_grade = 0.9, 0.91
        section_breakdown = [
            {
                "category": "Homework",
                "percent": 1.0,
                "detail": "Homework 1 - New Subsection - 100% (1/1)",
                "label": "Grade 01"
            }
        ]
        grade_summary = {"section_breakdown": section_breakdown}
        StudentGradebook.objects.create(
            user=self.user, course_id=self.course.id,
            grade=user_grade, proforma_grade=user_proforma_grade,
            grade_summary=json.dumps(grade_summary)
        )

        test_uri = '{}/{}/courses/progress'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        response_obj = response.data[0]

        self.assertIn('created', response_obj)
        self.assertIn('is_active', response_obj)
        self.assertIn('progress', response_obj)
        self.assertIn('proficiency', response_obj)
        self.assertIn('course', response_obj)

        self.assertEqual(response.data[0]['progress'], 50.0)
        self.assertEqual(response.data[0]['proficiency'], 90)
        self.assertEqual(response.data[0]['is_active'], True)

        self.assertIn('course_image_url', response_obj['course'])
        self.assertIn('display_name', response_obj['course'])
        self.assertIn('start', response_obj['course'])
        self.assertIn('end', response_obj['course'])
        self.assertIn('id', response_obj['course'])

        self.assertEqual(response_obj['course']['language'], self.language)

    def test_users_progress_list_for_active_courses(self):
        """ Test progress value returned by users progress list api """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        test_uri = '{}/{}/courses/progress?is_active=true'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        test_uri = '{}/{}/courses/progress?is_active=false'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_users_no_progress_in_course(self):
        """ 
        Test progress value returned by users progress list api 
        User is enrolled in a course but nothing done. Progress should be zero. 
        """
        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)

        test_uri = '{}/{}/courses/progress'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        self.assertEqual(response.data[0]['proficiency'], 0)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_users_progress_in_mobile_only_courses(self, store):
        """ 
        Test progress value returned by users progress list api 
        Only the mobile available courses will be returned if the flag has been set in the params. 
        """
        with modulestore().default_store(store):
            mobile_course = CourseFactory.create(mobile_available=True)
            mobile_course_content = ItemFactory.create(
                category="chapter",
                parent_location=mobile_course.location,
                display_name="Overview"
            )

        CourseEnrollmentFactory.create(user=self.user, course_id=self.course.id)
        CourseEnrollmentFactory.create(user=self.user, course_id=mobile_course.id)

        BlockCompletion.objects.submit_completion(
            user=self.user,
            course_key=self.course.id,
            block_key=self.content_child.scope_ids.usage_id,
            completion=1.0,
        )

        BlockCompletion.objects.submit_completion(
            user=self.user,
            course_key=self.course.id,
            block_key=mobile_course_content.scope_ids.usage_id,
            completion=1.0,
        )

        test_uri = '{}/{}/courses/progress'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        test_uri = '{}/{}/courses/progress?mobile_only=true'.format(self.base_users_uri, self.user.id)
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)


@ddt.ddt
class UserAttributesApiTests(ModuleStoreTestCase, APIClientMixin):
    """ Test suite for User Attributes API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super(UserAttributesApiTests, self).setUp()
        self.test_server_prefix = 'https://testserver'
        self.test_username = str(uuid.uuid4())
        self.test_password = 'Test.Me64!'
        self.test_email = str(uuid.uuid4()) + '@test.org'
        self.test_first_name = str(uuid.uuid4())
        self.test_last_name = str(uuid.uuid4())
        self.test_city = str(uuid.uuid4())
        self.base_courses_uri = '/api/server/courses'
        self.base_users_uri = '/api/server/users'
        self.base_organizations_uri = '/api/server/organizations/'
        self.test_organization_name = str(uuid.uuid4())
        self.test_organization_display_name = 'Test Org'
        self.test_organization_contact_name = 'John Org'
        self.test_organization_contact_email = 'john@test.org'
        self.test_organization_contact_phone = '+1 332 232 24234'
        self.test_organization_logo_url = 'org_logo.jpg'
        self.test_organization_attributes = "{'1': 'phone', '2': 'address'}"

        self.course = CourseFactory.create()

        self.client = Client()
        self.user = UserFactory.create(username='test', email='test@edx.org', password='test_password')
        self.client.login(username=self.user.username, password='test_password')

        cache.clear()

    def setup_test_organization(self, org_data=None):
        """
        Creates a new organization with given org_data
        if org_data is not present it would create organization with test values
        :param org_data: Dictionary witch each item represents organization attribute
        :return: newly created organization
        """
        org_data = org_data if org_data else {}
        data = {
            'name': org_data.get('name', self.test_organization_name),
            'display_name': org_data.get('display_name', self.test_organization_display_name),
            'contact_name': org_data.get('contact_name', self.test_organization_contact_name),
            'contact_email': org_data.get('contact_email', self.test_organization_contact_email),
            'contact_phone': org_data.get('contact_phone', self.test_organization_contact_phone),
            'logo_url': org_data.get('logo_url', self.test_organization_logo_url),
            'attributes': org_data.get('attributes', self.test_organization_attributes),
            'users': org_data.get('users', []),
            'groups': org_data.get('groups', []),
        }
        response = self.do_post(self.base_organizations_uri, data)
        self.assertEqual(response.status_code, 201)
        return response.data

    def _add_sample_attributes_in_organization(self, organization_id):
        test_uri = '{}{}/attributes'.format(self.base_organizations_uri, organization_id)
        data = {
            'name': 'phone'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {
            'name': 'address'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

    def test_users_attribute_values_add(self):
        organization = self.setup_test_organization()

        test_uri = '{}{}/attributes'.format(self.base_organizations_uri, organization['id'])
        data = {
            'name': 'phone'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/attributes/'.format(self.base_users_uri, self.user.id)
        data = {
            'key': 'phone_1',
	        'value': '123456789',
	        'organization_id': organization['id']
        }

        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

    def test_users_attribute_values_get(self):
        organization = self.setup_test_organization()

        test_uri = '{}{}/attributes'.format(self.base_organizations_uri, organization['id'])
        data = {
            'name': 'phone'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/attributes/'.format(self.base_users_uri, self.user.id)
        data = {
            'key': 'phone_1',
	        'value': '123456789',
	        'organization_id': organization['id']
        }

        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, self.user.id, organization['id'])

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '123456789')

    def test_users_attribute_values_update(self):
        organization = self.setup_test_organization()

        test_uri = '{}{}/attributes'.format(self.base_organizations_uri, organization['id'])
        data = {
            'name': 'phone'
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/attributes/'.format(self.base_users_uri, self.user.id)
        data = {
            'key': 'phone_1',
	        'value': '123456789',
	        'organization_id': organization['id']
        }

        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, self.user.id, organization['id'])

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '123456789')

        test_uri = '{}/{}/attributes/'.format(self.base_users_uri, self.user.id)
        data = {
            'key': 'phone_1',
	        'value': '123000000',
	        'organization_id': organization['id']
        }

        response = self.do_put(test_uri, data)
        self.assertEqual(response.status_code, 200)

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, self.user.id, organization['id'])

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '123000000')

    def test_create_user_with_attributes(self):
        organization = self.setup_test_organization()
        self._add_sample_attributes_in_organization(organization['id'])

        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name,
                'attribute_keys': 'address_1,phone_1', 'attribute_values': 'ABC Town,123456789',
                'organization_id': organization['id']}
        response = self.do_post(self.base_users_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)

        confirm_uri = self.base_users_uri + '/' + str(response.data['id'])
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['email'], self.test_email)
        self.assertEqual(response.data['username'], local_username)
        self.assertEqual(response.data['first_name'], self.test_first_name)
        self.assertEqual(response.data['last_name'], self.test_last_name)
        self.assertIsNotNone(response.data['created'])

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, response.data['id'], organization['id'])

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '123456789')

    def test_user_attributes_update(self):
        organization = self.setup_test_organization()
        self._add_sample_attributes_in_organization(organization['id'])

        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password':
                self.test_password, 'first_name': self.test_first_name, 'last_name': self.test_last_name,
                'attribute_keys': 'address_1,phone_1', 'attribute_values': 'ABC Town,123456789',
                'organization_id': organization['id']}
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        confirm_uri = self.base_users_uri + '/' + str(user_id)
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['email'], self.test_email)
        self.assertEqual(response.data['username'], local_username)
        self.assertEqual(response.data['first_name'], self.test_first_name)
        self.assertEqual(response.data['last_name'], self.test_last_name)
        self.assertIsNotNone(response.data['created'])

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, user_id, organization['id'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '123456789')

        test_uri = self.base_users_uri + '/' + str(user_id)
        data = {'attribute_keys': 'phone_1', 'attribute_values': '1234567890',
                'organization_id': organization['id']}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)

        test_uri = '{}/{}/attributes/?key_list=phone_1&organization_id={}'.format(
            self.base_users_uri, user_id, organization['id'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['key'], 'phone_1')
        self.assertEqual(response.data[0]['value'], '1234567890')
