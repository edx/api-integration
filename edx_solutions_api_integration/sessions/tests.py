# pylint: disable=E1101
# pylint: disable=E1103

"""
Run these tests @ Devstack:
    rake fasttest_lms[common/djangoapps/edx_solutions_api_integration/tests/test_session_views.py]
"""
from random import randint
import uuid
import mock
from datetime import datetime

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from edx_solutions_api_integration.test_utils import APIClientMixin


@mock.patch.dict("django.conf.settings.FEATURES", {'ENFORCE_PASSWORD_POLICY': False,
                                                   'ADVANCED_SECURITY': False,
                                                   'PREVENT_CONCURRENT_LOGINS': False})
class SessionsApiTests(TestCase, APIClientMixin):
    """ Test suite for Sessions API views """

    def setUp(self):  # pylint: disable=E7601
        self.test_username = str(uuid.uuid4())
        self.test_password = str(uuid.uuid4())
        self.test_email = str(uuid.uuid4()) + '@test.org'
        self.base_users_uri = '/api/server/users'
        self.base_sessions_uri = '/api/server/sessions'

        cache.clear()

    def test_session_list_post_valid(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        user_id = response.data['id']
        # get a copy of the User object, so we can compare timestamps
        user1 = User.objects.get(id=user_id)
        # last_login of user should be None since it never logged in
        self.assertIsNone(user1.last_login)

        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        user2 = User.objects.get(id=user_id)

        self.assertEqual(response.status_code, 201)
        self.assertGreater(len(response.data['token']), 0)
        confirm_uri = self.base_sessions_uri + '/' + response.data['token']
        self.assertIn(confirm_uri, response.data['uri'])
        self.assertGreater(response.data['expires'], 0)
        self.assertGreater(len(response.data['user']), 0)
        self.assertEqual(str(response.data['user']['username']), local_username)
        self.assertEqual(response.data['user']['id'], user_id)

        # make sure the last_login timestamp was updated at the login operation
        self.assertTrue(isinstance(user2.last_login, datetime))
        self.assertNotEqual(user1.last_login, user2.last_login)

    def test_session_list_post_invalid(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        bad_password = "12345"
        data = {'email': self.test_email, 'username': local_username, 'password': bad_password}
        response = self.do_post(self.base_users_uri, data)
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        self.assertEqual(response.status_code, 401)

    def test_session_list_post_valid_inactive(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        user = User.objects.get(username=local_username)
        user.is_active = False
        user.save()
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        self.assertEqual(response.status_code, 403)

    def test_session_list_post_invalid_notfound(self):
        data = {'username': 'user_12321452334', 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_session_detail_get(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        test_uri = self.base_sessions_uri + '/' + response.data['token']
        post_token = response.data['token']
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['token'], post_token)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_double_sessions_same_user(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)

        # log in once
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        session1 = response.data['token']

        # test that first session is valid
        test_uri = self.base_sessions_uri + '/' + session1
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        # log in again with the same user
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        session2 = response.data['token']

        # assert that the two sessions keys are not the same
        self.assertNotEqual(session1, session2)

        # test that first session is still valid
        test_uri = self.base_sessions_uri + '/' + session1
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        # test that second session is valid
        test_uri = self.base_sessions_uri + '/' + session2
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        # terminate first session
        test_uri = self.base_sessions_uri + '/' + session1
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        # test that second session is valid
        test_uri = self.base_sessions_uri + '/' + session2
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

        # terminate second session
        test_uri = self.base_sessions_uri + '/' + session2
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_session_detail_get_undefined(self):
        test_uri = self.base_sessions_uri + "/123456789"
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_session_detail_delete(self):
        local_username = self.test_username + str(randint(11, 99))
        local_username = local_username[3:-1]  # username is a 32-character field
        data = {'email': self.test_email, 'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_users_uri, data)
        self.assertEqual(response.status_code, 201)
        data = {'username': local_username, 'password': self.test_password}
        response = self.do_post(self.base_sessions_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = self.base_sessions_uri + str(response.data['token'])
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_session_detail_delete_invalid_session(self):
        test_uri = self.base_sessions_uri + "214viouadblah124324blahblah"
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
