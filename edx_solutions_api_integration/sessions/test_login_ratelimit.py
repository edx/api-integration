# pylint: disable=W0612
"""
Tests for rate limiting features
"""
import json
from datetime import datetime, timedelta

from django.core.cache import cache
from django.utils.translation import ugettext as _
from edx_solutions_api_integration.test_utils import APIClientMixin
from freezegun import freeze_time
from mock import patch
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from pytz import UTC
from student.models import UserProfile
from student.tests.factories import UserFactory


@patch.dict("django.conf.settings.FEATURES", {'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': False,
                                              'PREVENT_CONCURRENT_LOGINS': False})
class SessionApiRateLimitingProtectionTest(CacheIsolationTestCase, APIClientMixin):
    """
    Test edx_solutions_api_integration.session.login.ratelimit
    """
    ENABLED_CACHES = ['default']

    def setUp(self):  # pylint: disable=E7601
        """
        Create one user and save it to the database
        """
        self.user = UserFactory.build(username='test', email='test@edx.org')
        self.user.set_password('test_password')
        self.user.save()
        profile = UserProfile(user=self.user)
        profile.city = 'Boston'
        profile.save()

        # Create the test client
        cache.clear()
        self.session_url = '/api/server/sessions'

    def test_login_ratelimiting_protection(self):
        """ Try (and fail) login user 30 times on invalid password """

        for i in range(30):
            password = 'test_password{}'.format(i)
            data = {'username': 'test', 'password': password}
            response = self.do_post(self.session_url, data)
            self.assertEqual(response.status_code, 401)

        # then the rate limiter should kick in and give a HttpForbidden response
        data = {'username': 'test', 'password': 'test_password'}
        response = self.do_post(self.session_url, data)
        message = _('Rate limit exceeded in api login.')
        self._assert_response(response, status=403, message=message)

    def test_login_ratelimiting_unblock(self):
        """ Try (and fail) login user 30 times on invalid password """
        for i in range(30):
            password = 'test_password{}'.format(i)
            data = {'username': 'test', 'password': password}
            response = self.do_post(self.session_url, data)
            self.assertEqual(response.status_code, 401)

        # then the rate limiter should kick in and give a HttpForbidden response
        data = {'username': 'test', 'password': 'test_password'}
        response = self.do_post(self.session_url, data)
        message = _('Rate limit exceeded in api login.')
        self._assert_response(response, status=403, message=message)

        # now reset the time to 5 mins from now in future in order to unblock
        reset_time = datetime.now(UTC) + timedelta(seconds=300)
        with freeze_time(reset_time):
            response = self.do_post(self.session_url, data)
            self._assert_response(response, status=201)

    def _assert_response(self, response, status=200, message=None):
        """
        Assert that the response had status 200 and returned a valid
        JSON-parseable dict.

        If success is provided, assert that the response had that
        value for 'success' in the JSON dict.

        If message is provided, assert that the response contained that
        value for 'message' in the JSON dict.
        """
        self.assertEqual(response.status_code, status)
        response_dict = json.loads(response.content)

        if message is not None:
            msg = ("'%s' did not contain '%s'" %
                   (response_dict['message'], message))
            self.assertTrue(message in response_dict['message'], msg)
