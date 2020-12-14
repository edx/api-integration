# pylint: disable=E1103

"""
Tests for groups module
"""
import uuid
from random import randint
from urllib.parse import urlencode

import mock
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.cache import cache
from django.test.utils import override_settings
from django.utils import timezone
from edx_solutions_api_integration.models import (GroupProfile,
                                                  GroupRelationship)
from edx_solutions_api_integration.test_utils import APIClientMixin
from edx_solutions_organizations.models import Organization
from edx_solutions_projects.models import Project
from student.tests.factories import GroupFactory
from xmodule.modulestore.tests.django_utils import (
    TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase)
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory


@mock.patch.dict("django.conf.settings.FEATURES", {'ADVANCED_SECURITY': False,
                                                   'PREVENT_CONCURRENT_LOGINS': False})
class GroupsApiTests(ModuleStoreTestCase, APIClientMixin):
    """ Test suite for Groups API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def setUp(self):
        super().setUp()
        self.test_username = str(uuid.uuid4())
        self.test_password = str(uuid.uuid4())
        self.test_email = str(uuid.uuid4()) + '@test.org'
        self.test_group_name = str(uuid.uuid4())
        self.test_first_name = str(uuid.uuid4())
        self.test_last_name = str(uuid.uuid4())
        self.base_users_uri = '/api/server/users'
        self.base_groups_uri = '/api/server/groups'
        self.base_workgroups_uri = '/api/server/workgroups/'

        self.test_course_data = '<html>{}</html>'.format(str(uuid.uuid4()))
        self.course = CourseFactory.create()
        self.test_course_id = str(self.course.id)
        self.course_end_date = timezone.now() + relativedelta(days=60)
        self.course_content = ItemFactory.create(
            category="videosequence",
            parent_location=self.course.location,
            due=self.course_end_date,
            display_name="View_Sequence"
        )

        self.test_organization = Organization.objects.create(
            name="Test Organization",
            display_name='Test Org',
            contact_name='John Org',
            contact_email='john@test.org',
            contact_phone='+1 332 232 24234'
        )

        self.test_project = Project.objects.create(
            course_id=str(self.course.id),
            content_id=str(self.course_content.scope_ids.usage_id)
        )
        cache.clear()

    def test_group_list_post(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        confirm_uri = self.base_groups_uri + '/' + str(response.data['id'])
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertGreater(len(response.data['name']), 0)

    def test_group_list_get_with_profile(self):  # pylint: disable=R0915
        group_type = 'series'
        display_name = 'My first series'
        profile_data = {'display_name': display_name}
        data = {
            'name': self.test_group_name,
            'type': group_type,
            'data': profile_data
        }
        response = self.do_post(self.base_groups_uri, data)
        self.assertGreater(response.data['id'], 0)
        group_id = response.data['id']

        # query for list of groups, but don't put the type filter
        test_uri = self.base_groups_uri
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 400)

        # try again with filter
        test_uri = '{}?type={}'.format(self.base_groups_uri, group_type)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['num_pages'], 1)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], group_id)
        self.assertEqual(response.data['results'][0]['type'], group_type)
        self.assertEqual(response.data['results'][0]['name'], self.test_group_name)
        response_profile_data = response.data['results'][0]['data']
        self.assertEqual(response_profile_data['display_name'], display_name)

        # query the group detail
        test_uri = '{}/{}'.format(self.base_groups_uri, str(group_id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], group_id)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['name'], self.test_group_name)
        self.assertEqual(response.data['type'], group_type)
        response_profile_data = response.data['data']
        self.assertEqual(response_profile_data['display_name'], display_name)

        # update the profile
        updated_group_type = 'seriesX'
        updated_display_name = 'My updated series'
        profile_data = {'display_name': updated_display_name}
        data = {
            'name': self.test_group_name,
            'type': updated_group_type,
            'data': profile_data
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)

        # requery the filter
        test_uri = self.base_groups_uri + '?type=series'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 0)

        test_uri = '{}?type={}'.format(self.base_groups_uri, updated_group_type)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['id'], group_id)
        self.assertEqual(response.data['results'][0]['type'], updated_group_type)
        self.assertEqual(response.data['results'][0]['name'], self.test_group_name)
        response_profile_data = response.data['results'][0]['data']
        self.assertEqual(response_profile_data['display_name'], updated_display_name)

    def test_group_list_post_invalid_name(self):
        data = {'name': '', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_group_list_post_missing_type(self):
        data = {'name': ''}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_group_list_get_uses_base_group_name(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        profile = GroupProfile.objects.get(group_id=group_id)
        profile.name = ''
        profile.save()
        test_uri = '{}?type=test'.format(self.base_groups_uri)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['name'], '{:04d}: {}'.format(group_id, self.test_group_name))
        profile.name = None
        profile.save()
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['results'][0]['name'], '{:04d}: {}'.format(group_id, self.test_group_name))

    def test_group_detail_get(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        group_id = response.data['id']
        test_uri = self.base_groups_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], group_id)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['name'], self.test_group_name)

    def test_group_detail_get_uses_base_group_name(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        group_id = response.data['id']
        profile = GroupProfile.objects.get(group_id=group_id)
        profile.name = ''
        profile.save()
        test_uri = self.base_groups_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], group_id)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['name'], '{:04d}: {}'.format(group_id, self.test_group_name))

    def test_group_detail_get_with_missing_profile(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        group_id = response.data['id']
        GroupProfile.objects.get(group_id=group_id).delete()
        test_uri = self.base_groups_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], group_id)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['name'], '{:04d}: {}'.format(group_id, self.test_group_name))

    def test_group_detail_get_undefined(self):
        test_uri = self.base_groups_uri + '/123456789'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_detail_post(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = response.data['uri']
        self.assertEqual(response.status_code, 201)
        group_name = 'Updated Name'
        group_type = 'seriesX'
        data = {
            'name': group_name,
            'type': group_type,
            'data': {
                'display_name': 'My updated series'
            }
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['id'], group_id)
        self.assertEqual(response.data['name'], group_name)
        self.assertEqual(response.data['uri'], test_uri)

    def test_group_detail_post_invalid_group(self):
        test_uri = '{}/23209232'.format(self.base_groups_uri)
        group_type = 'seriesX'
        data = {
            'name': self.test_group_name,
            'type': group_type,
            'data': {
                'display_name': 'My updated series'
            }
        }
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_detail_delete(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email,
            'username': local_username,
            'password': self.test_password,
            'first_name': 'Joe',
            'last_name': 'Smith'
        }
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']

        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        group_id = response.data['id']

        test_uri = '{}/{}/users'.format(self.base_groups_uri, group_id)
        data = {'user_id': user_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        test_uri = '{}/{}'.format(self.base_groups_uri, group_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_detail_delete_invalid_group(self):
        test_uri = '{}/23209232'.format(self.base_groups_uri)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_group_users_list_post(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email,
            'username': local_username,
            'password': self.test_password,
            'first_name': 'Joe',
            'last_name': 'Smith'
        }
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = self.base_groups_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        test_uri = test_uri + '/users'
        data = {'user_id': user_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

    def test_group_users_list_post_multiple(self):
        user_id = []
        for i in range(2):
            local_username = self.test_username + str(i)
            data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
            response = self.do_post(self.base_users_uri, data)
            user_id.append(response.data['id'])
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = self.base_groups_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri = test_uri + '/users'
        data = {'user_id': ','.join(map(str, user_id))}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(len(user_id), len(response.data['users']))

    def test_group_users_list_post_invalid_group(self):
        test_uri = self.base_groups_uri + '/1239878976'
        test_uri = test_uri + '/users'
        data = {'user_id': "98723896"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_users_list_post_invalid_user(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = '{}/{}/users'.format(self.base_groups_uri, str(response.data['id']))
        data = {'user_id': "98723896"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_users_list_get(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {
            'email': self.test_email,
            'username': local_username,
            'password': self.test_password,
            'first_name': 'Joe',
            'last_name': 'Smith'
        }
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = self.base_groups_uri + '/' + str(group_id)
        response = self.do_get(test_uri)
        test_uri = test_uri + '/users'
        data = {'user_id': user_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        users = response.data['users']
        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['id'], user_id)
        self.assertEqual(users[0]['username'], local_username)
        self.assertEqual(users[0]['email'], self.test_email)
        self.assertEqual(users[0]['first_name'], 'Joe')
        self.assertEqual(users[0]['last_name'], 'Smith')

    def test_group_users_list_get_with_is_active_flag(self):

        group_data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, group_data)
        group_id = response.data['id']
        is_active = True

        for num in range(0, 5):  # pylint: disable=C7620

            if num == 3:
                is_active = False

            data = {
                'email': '{}{}'.format(num, self.test_email),
                'username': '{}{}'.format(num, self.test_username),
                'password': self.test_password,
                'first_name': self.test_first_name,
                'last_name': self.test_last_name,
                'is_active': is_active
            }

            # associating a user with a group
            response = self.do_post(self.base_users_uri, data)
            self.assertEqual(response.status_code, 201)
            user_id = response.data['id']
            test_uri = self.base_groups_uri + '/' + str(group_id) + '/users'
            response = self.do_post(test_uri, data={'user_id': user_id})
            self.assertEqual(response.status_code, 201)

        # getting users without is_active in query params
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        users = response.data['users']
        self.assertEqual(len(users), 5)

        # getting users with is_active=false
        test_uri_inactive_user = test_uri + '/?is_active=false'
        response = self.do_get(test_uri_inactive_user)
        users = response.data['users']
        self.assertEqual(len(users), 2)

        # getting users with is_active=true
        test_uri_active_user = test_uri + '/?is_active=true'
        response = self.do_get(test_uri_active_user)
        self.assertEqual(response.status_code, 200)
        users = response.data['users']
        self.assertEqual(len(users), 3)

    def test_group_users_list_get_invalid_group(self):
        test_uri = self.base_groups_uri + '/1231241/users'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_users_detail_get(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = self.base_groups_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri = test_uri + '/users'
        data = {'user_id': user_id}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(user_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['uri']), 0)
        self.assertIn(test_uri, response.data['uri'])
        self.assertEqual(response.data['group_id'], group_id)
        self.assertEqual(response.data['user_id'], user_id)

    def test_group_users_detail_delete(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = self.base_groups_uri + '/' + str(response.data['id'])
        response = self.do_get(test_uri)
        test_uri = test_uri + '/users'
        data = {'user_id': user_id}
        response = self.do_post(test_uri, data)
        test_uri = test_uri + '/' + str(user_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)  # Idempotent
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_users_detail_delete_invalid_group(self):
        test_uri = self.base_groups_uri + '/123987102/users/123124'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_group_users_detail_delete_invalid_user(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = self.base_groups_uri + '/' + str(response.data['id'])
        test_uri = test_uri + '/users/123124'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_group_users_detail_get_undefined(self):
        local_username = self.test_username + str(randint(11, 99))
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = self.base_groups_uri + '/' + str(group_id) + '/users/' + str(user_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_list_post_hierarchical(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = delta_response.data['id']
        relationship_type = 'h'  # Hierarchical
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(len(response.data['uri']), 0)
        confirm_uri = test_uri + '/' + str(response.data['group_id'])
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['group_id'], str(group_id))
        self.assertEqual(response.data['relationship_type'], relationship_type)

    def test_group_groups_list_post_linked(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = delta_response.data['id']
        relationship_type = 'g'  # Graph
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(len(response.data['uri']), 0)
        confirm_uri = test_uri + '/' + str(response.data['group_id'])
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['group_id'], str(group_id))
        self.assertEqual(response.data['relationship_type'], relationship_type)

    def test_group_groups_list_post_linked_duplicate(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = delta_response.data['id']
        relationship_type = 'g'  # Graph
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, data)
        # Duplicate responses are idemnotent in this case
        self.assertEqual(response.status_code, 201)

    def test_group_groups_list_post_invalid_group(self):
        test_uri = self.base_groups_uri + '/123098/groups'
        relationship_type = 'g'  # Graph
        data = {'group_id': '232987', 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_list_post_invalid_relationship_type(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = delta_response.data['id']
        relationship_type = "z"  # Graph
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 406)

    def test_group_groups_list_get(self):
        data = {'name': 'Bravo Group', 'type': 'test'}
        bravo_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(bravo_response.status_code, 201)
        bravo_group_id = bravo_response.data['id']
        bravo_groups_uri = bravo_response.data['uri'] + '/groups'

        data = {'name': 'Charlie Group', 'type': 'test'}
        charlie_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(charlie_response.status_code, 201)
        charlie_group_id = charlie_response.data['id']
        relationship_type = 'h'  # Hierarchical
        data = {'group_id': charlie_group_id, 'relationship_type': relationship_type}
        response = self.do_post(bravo_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {'name': 'Foxtrot Group', 'type': 'test'}
        foxtrot_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(foxtrot_response.status_code, 201)
        foxtrot_group_id = foxtrot_response.data['id']
        relationship_type = 'g'  # Graph
        data = {'group_id': foxtrot_group_id, 'relationship_type': relationship_type}
        response = self.do_post(bravo_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {'name': 'Tango Group', 'type': 'test'}
        tango_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(tango_response.status_code, 201)
        tango_group_id = tango_response.data['id']
        tango_uri = tango_response.data['uri']
        data = {'group_id': bravo_group_id, 'relationship_type': relationship_type}
        tango_groups_uri = tango_uri + '/groups'
        response = self.do_post(tango_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(bravo_groups_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        group_idlist = (charlie_group_id, foxtrot_group_id, tango_group_id)
        relationship_count = 0
        for relationship in response.data:
            relationship_count = relationship_count + 1
            group_id = relationship['id']
            self.assertGreater(group_id, 0)
            self.assertFalse(bravo_group_id == group_id)
            self.assertTrue(relationship['relationship_type'] in ["h", "g"])
            self.assertGreater(len(relationship['uri']), 0)
        self.assertEqual(relationship_count, len(group_idlist))

    def test_group_groups_list_get_with_profile_type(self):
        data = {'name': 'Bravo Group', 'type': 'test'}
        bravo_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(bravo_response.status_code, 201)
        bravo_group_id = bravo_response.data['id']
        bravo_groups_uri = bravo_response.data['uri'] + '/groups?type=test_group'

        data = {'name': 'Charlie Group', 'type': 'test_group'}
        charlie_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(charlie_response.status_code, 201)
        charlie_group_id = charlie_response.data['id']
        relationship_type = 'h'  # Hierarchical
        data = {'group_id': charlie_group_id, 'relationship_type': relationship_type}
        response = self.do_post(bravo_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {'name': 'Foxtrot Group', 'type': 'test_group'}
        foxtrot_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(foxtrot_response.status_code, 201)
        foxtrot_group_id = foxtrot_response.data['id']
        relationship_type = 'g'  # Graph
        data = {'group_id': foxtrot_group_id, 'relationship_type': relationship_type}
        response = self.do_post(bravo_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        data = {'name': 'Tango Group', 'type': 'test'}
        tango_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(tango_response.status_code, 201)
        tango_uri = tango_response.data['uri']
        data = {'group_id': bravo_group_id, 'relationship_type': relationship_type}
        tango_groups_uri = tango_uri + '/groups'
        response = self.do_post(tango_groups_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(bravo_groups_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        group_idlist = (charlie_group_id, foxtrot_group_id)
        relationship_count = 0
        for relationship in response.data:
            relationship_count = relationship_count + 1
            group_id = relationship['id']
            self.assertGreater(group_id, 0)
            self.assertFalse(bravo_group_id == group_id)
            self.assertTrue(relationship['relationship_type'] in ["h", "g"])
            self.assertGreater(len(relationship['uri']), 0)
        self.assertEqual(relationship_count, len(group_idlist))

    def test_group_groups_list_get_notfound(self):
        test_uri = self.base_groups_uri + '/213213123/groups'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_detail_get_hierarchical(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        alpha_group_id = alpha_response.data['id']
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        delta_group_id = delta_response.data['id']
        relationship_type = 'h'  # Hierarchical
        data = {'group_id': delta_group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri']
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['uri']), 0)
        self.assertEqual(response.data['uri'], test_uri)
        self.assertEqual(response.data['from_group_id'], str(alpha_group_id))
        self.assertEqual(response.data['to_group_id'], str(delta_group_id))
        self.assertEqual(response.data['relationship_type'], relationship_type)

    def test_group_groups_detail_get_linked(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        alpha_group_id = alpha_response.data['id']
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        delta_group_id = delta_response.data['id']
        self.assertEqual(delta_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        relationship_type = 'g'  # Graph
        data = {'group_id': delta_group_id, 'relationship_type': relationship_type}
        delta_group = GroupRelationship.objects.get(group_id=delta_group_id)
        delta_group.parent_group_id = None
        delta_group.save()
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri']
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['uri']), 0)
        self.assertEqual(response.data['uri'], test_uri)
        self.assertEqual(response.data['from_group_id'], str(alpha_group_id))
        self.assertEqual(response.data['to_group_id'], str(delta_group_id))
        self.assertEqual(response.data['relationship_type'], relationship_type)

    def test_group_groups_detail_get_notfound(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups/1234'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_detail_delete_hierarchical(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        data = {'name': 'Gamma Group', 'type': 'test'}
        gamma_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(gamma_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = gamma_response.data['id']
        relationship_type = 'h'
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri']
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        try:
            self.assertIsNone(response.data['message'])
        except KeyError:
            pass
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_detail_delete_linked(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        alpha_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(alpha_response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'test'}
        beta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(beta_response.status_code, 201)
        data = {'name': 'Delta Group', 'type': 'test'}
        delta_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(delta_response.status_code, 201)
        data = {'name': 'Gamma Group', 'type': 'test'}
        gamma_response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(gamma_response.status_code, 201)
        test_uri = alpha_response.data['uri'] + '/groups'
        group_id = gamma_response.data['id']
        relationship_type = 'g'
        data = {'group_id': group_id, 'relationship_type': relationship_type}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri']
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        try:
            self.assertIsNone(response.data['message'])
        except KeyError:
            pass
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_groups_detail_delete_invalid(self):
        test_uri = self.base_groups_uri + '/1231234232/groups/1'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_courses_list_post(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': self.test_course_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = test_uri + '/' + str(self.course.id)
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['group_id'], str(group_id))
        self.assertEqual(response.data['course_id'], self.test_course_id)

    def test_group_courses_list_post_duplicate(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': self.test_course_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)

    def test_group_courses_list_post_invalid_group(self):
        test_uri = self.base_groups_uri + '/1239878976/courses'
        data = {'course_id': "98723896"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_courses_list_post_invalid_course(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': "invalid/course/id"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)
        data = {'course_id': "really-invalid-course-id"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_group_courses_list_get(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': self.test_course_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = test_uri + '/' + str(self.course.id)
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['group_id'], str(group_id))
        self.assertEqual(response.data['course_id'], self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['course_id'], self.test_course_id)
        self.assertEqual(response.data[0]['display_name'], self.course.display_name)

    def test_group_courses_list_get_invalid_group(self):
        test_uri = self.base_groups_uri + '/1231241/courses'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_courses_detail_get(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': self.test_course_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = '{}/{}/courses/{}'.format(self.base_groups_uri, group_id, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        confirm_uri = '{}/{}/courses/{}'.format(
            self.base_groups_uri,
            group_id,
            self.test_course_id
        )
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertEqual(response.data['group_id'], group_id)
        self.assertEqual(response.data['course_id'], self.test_course_id)

    def test_group_courses_detail_delete(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri'] + '/courses'
        data = {'course_id': self.test_course_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri']
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)  # Idempotent
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_courses_detail_delete_invalid_group(self):
        test_uri = self.base_groups_uri + '/123987102/courses/org.invalid/course_000/Run_000'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_group_courses_detail_delete_invalid_course(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = response.data['uri'] + '/courses/org.invalid/course_000/Run_000'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_group_courses_detail_get_undefined(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = '{}/courses/{}'.format(response.data['uri'], str(self.course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_group_organizations_list_get(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        self.test_organization.groups.add(group_id)
        test_uri = response.data['uri'] + '/organizations/'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.test_organization.id)
        self.assertEqual(response.data[0]['name'], self.test_organization.name)

    def test_group_organizations_list_get_invalid_group(self):
        test_uri = self.base_groups_uri + '/1231241/organizations/'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_groups_workgroups_list(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        test_workgroups_uri = self.base_workgroups_uri
        for i in range(1, 12):
            project_id = self.test_project.id
            data = {
                'name': 'Workgroup ' + str(i),
                'project': project_id
            }
            response = self.do_post(test_workgroups_uri, data)
            self.assertEqual(response.status_code, 201)
            test_uri = '{}{}/'.format(test_workgroups_uri, str(response.data['id']))
            users_uri = '{}groups/'.format(test_uri)
            data = {"id": group_id}
            response = self.do_post(users_uri, data)
            self.assertEqual(response.status_code, 201)

        # test to get list of workgroups
        test_uri = '{}/{}/workgroups/?page_size=10'.format(self.base_groups_uri, group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 11)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['num_pages'], 2)

        # test with course_id filter
        course_id = {'course_id': str(self.course.id)}
        groups_uri = '{}/{}/workgroups/?{}'.format(self.base_groups_uri, group_id, urlencode(course_id))
        response = self.do_get(groups_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 11)
        self.assertIsNotNone(response.data['results'][0]['name'])
        self.assertIsNotNone(response.data['results'][0]['project'])

        # test with invalid group id
        response = self.do_get('{}/4356340/workgroups/'.format(self.base_groups_uri))
        self.assertEqual(response.status_code, 404)

    def test_groups_users_list_missing_user_id(self):
        # Create a test group
        group = GroupFactory.create()

        # Test with user_id missing in request data
        test_uri = '{}/{}/users/'.format(self.base_groups_uri, group.id)
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 400)

    def test_groups_groups_list_missing_group_id(self):
        # Create test group
        from_group = GroupFactory.create()

        # Test with missing group_id in the request data
        test_uri = '{}/{}/groups/'.format(self.base_groups_uri, from_group.id)
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 400)

    def test_groups_groups_list_missing_relationship_type(self):
        # Create test groups
        from_group = GroupFactory.create()
        to_group = GroupFactory.create()

        # Test with missing relationship_type in the request data
        test_uri = '{}/{}/groups/'.format(self.base_groups_uri, from_group.id)
        response = self.do_post(test_uri, {"group_id": to_group.id})
        self.assertEqual(response.status_code, 400)

    def test_groups_groups_detail_invalid_group_id(self):
        related_group = GroupFactory.create()

        # Test with invalid from_group
        test_uri = '{}/{}/groups/{}'.format(self.base_groups_uri, '1234567', related_group.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_groups_courses_list_missing_course_id(self):

        # Create test group
        test_group = GroupFactory.create()

        # Test with missing course_id in the request data
        test_uri = '{}/{}/courses/'.format(self.base_groups_uri, test_group.id)
        data = {"course_id": ""}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)
