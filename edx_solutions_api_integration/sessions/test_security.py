# pylint: disable=W0612
"""
Tests for session api with advance security features
"""
import json
import uuid
from datetime import datetime, timedelta

from django.core.cache import cache
from django.test import TestCase
from django.test.client import Client
from django.test.utils import override_settings
from django.utils.translation import ugettext as _
from freezegun import freeze_time
from mock import patch
from pytz import UTC
from student.models import UserProfile
from student.tests.factories import UserFactory
from util.password_policy_validators import create_validator_config

TEST_API_KEY = str(uuid.uuid4())


@override_settings(EDX_API_KEY=TEST_API_KEY)
@patch.dict("django.conf.settings.FEATURES", {'ENABLE_MAX_FAILED_LOGIN_ATTEMPTS': True,
                                              'PREVENT_CONCURRENT_LOGINS': False})
class SessionApiSecurityTest(TestCase):
    """
    Test edx_solutions_api_integration.session.session_list view
    """

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
        self.client = Client()
        cache.clear()
        self.session_url = '/api/server/sessions'
        self.user_url = '/api/server/users'

    @override_settings(MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED=10)
    def test_login_ratelimited_success(self):
        """
        Try (and fail) logging in with fewer attempts than the limit of 10
        and verify that you can still successfully log in afterwards.
        """
        for i in range(9):
            password = 'test_password{}'.format(i)
            response, mock_audit_log = self._do_request(self.session_url, 'test', password, secure=True)
            self.assertEqual(response.status_code, 401)

        # now try logging in with a valid password and check status
        response, mock_audit_log = self._do_request(self.session_url, 'test', 'test_password', secure=True)
        self._assert_response(response, status=201)

    @override_settings(MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED=10)
    def test_login_blockout(self):
        """
        Try (and fail) logging in with 10 attempts
        and verify that user is blocked out.
        """
        for i in range(10):
            password = 'test_password{}'.format(i)
            response, mock_audit_log = self._do_request(self.session_url, 'test', password, secure=True)
            self.assertEqual(response.status_code, 401)

        # check to see if this response indicates blockout
        response, mock_audit_log = self._do_request(self.session_url, 'test', 'test_password', secure=True)
        message = _('This account has been temporarily locked due to excessive login failures. Try again later.')
        self._assert_response(response, status=403, message=message)

    @override_settings(MAX_FAILED_LOGIN_ATTEMPTS_ALLOWED=10,
                       MAX_FAILED_LOGIN_ATTEMPTS_LOCKOUT_PERIOD_SECS=1800)
    def test_blockout_reset_time_period(self):
        """
        Try logging in 10 times to block user and then login with right
        credentials(after 30 minutes) to verify blocked out time expired and
        user can login successfully.
        """
        for i in range(10):
            password = 'test_password{}'.format(i)
            response, mock_audit_log = self._do_request(self.session_url, 'test', password, secure=True)
            self.assertEqual(response.status_code, 401)
            self._assert_audit_log(mock_audit_log, 'warn',
                                   ["API::User authentication failed with user-id - {}".format(self.user.id)])
            self._assert_not_in_audit_log(mock_audit_log, 'warn', ['test'])

        # check to see if this response indicates blockout
        response, mock_audit_log = self._do_request(self.session_url, 'test', 'test_password', secure=True)
        message = _('This account has been temporarily locked due to excessive login failures. Try again later.')
        self._assert_response(response, status=403, message=message)

        # now reset the time to 30 from now in future
        reset_time = datetime.now(UTC) + timedelta(seconds=1800)
        with freeze_time(reset_time):
            response, mock_audit_log = self._do_request(self.session_url, 'test', 'test_password', secure=True)
            self._assert_response(response, status=201)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.MinimumLengthValidator', {'min_length': 4})
    ])
    def test_with_short_password(self):
        """
        Try (and fail) user creation with shorter password
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'abc', email='test@edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password is too short. It must contain at least 4 characters.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.MaximumLengthValidator', {'max_length': 12})
    ])
    def test_with_long_password(self):
        """
        Try (and fail) user creation with longer password
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'test_password', email='test@edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password is too long. It must contain no more than 12 characters.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.NumericValidator', {'min_numeric': 2}),
        create_validator_config('util.password_policy_validators.LowercaseValidator', {'min_lower': 2}),
        create_validator_config('util.password_policy_validators.UppercaseValidator', {'min_upper': 2}),
        create_validator_config('util.password_policy_validators.PunctuationValidator', {'min_punctuation': 2})
    ])
    def test_password_without_uppercase(self):
        """
        Try (and fail) user creation since password should have atleast
        2 upper characters
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'test.pa64!', email='test@edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password must contain at least 2 uppercase letters.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.NumericValidator', {'min_numeric': 2}),
        create_validator_config('util.password_policy_validators.LowercaseValidator', {'min_lower': 2}),
        create_validator_config('util.password_policy_validators.UppercaseValidator', {'min_upper': 2}),
        create_validator_config('util.password_policy_validators.PunctuationValidator', {'min_punctuation': 2})
    ])
    def test_password_without_lowercase(self):
        """
        Try (and fail) user creation since password should have atleast
       2 lower characters
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'TEST.PA64!', email='test@edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password must contain at least 2 lowercase letters.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.NumericValidator', {'min_numeric': 2}),
        create_validator_config('util.password_policy_validators.LowercaseValidator', {'min_lower': 2}),
        create_validator_config('util.password_policy_validators.UppercaseValidator', {'min_upper': 2}),
        create_validator_config('util.password_policy_validators.PunctuationValidator', {'min_punctuation': 2})
    ])
    def test_password_without_punctuation(self):
        """
        Try (and fail) user creation without any punctuation in password
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'test64Ss', email='test@edx.org',  # pylint: disable=W0612,C0301
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password must contain at least 2 uppercase letters.; '
                    'This password must contain at least 2 punctuation marks.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.NumericValidator', {'min_numeric': 2}),
        create_validator_config('util.password_policy_validators.LowercaseValidator', {'min_lower': 2}),
        create_validator_config('util.password_policy_validators.UppercaseValidator', {'min_upper': 2}),
        create_validator_config('util.password_policy_validators.PunctuationValidator', {'min_punctuation': 2})
    ])
    def test_password_without_numeric(self):
        """
        Try (and fail) user creation without any numeric characters in password
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'test.paSs!', email='test@edx.org',  # pylint: disable=W0612,C0301
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Password: This password must contain at least 2 numbers.; '
                    'This password must contain at least 2 uppercase letters.')
        self._assert_response(response, status=400, message=message)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        create_validator_config('util.password_policy_validators.NumericValidator', {'min_numeric': 2}),
        create_validator_config('util.password_policy_validators.LowercaseValidator', {'min_lower': 2}),
        create_validator_config('util.password_policy_validators.UppercaseValidator', {'min_upper': 2}),
        create_validator_config('util.password_policy_validators.PunctuationValidator', {'min_punctuation': 2})
    ])
    def test_password_with_complexity(self):
        """
        This should pass since it has everything needed for a complex password
        """
        response, mock_audit_log = self._do_request(self.user_url, str(uuid.uuid4()), 'Test.Me64!',
                                                    email='test@edx.org', first_name='John',
                                                    last_name='Doe', secure=True,
                                                    patched_audit_log='edx_solutions_api_integration.users.views.AUDIT_LOG')  # pylint: disable=C0301
        self._assert_response(response, status=201)
        self._assert_audit_log(mock_audit_log, 'info', ['API::New account created with user-id'])
        self._assert_not_in_audit_log(mock_audit_log, 'info', ['test@edx.org'])

    def test_user_with_invalid_email(self):
        """
        Try (and fail) user creation with invalid email address
        """
        response, mock_audit_log = self._do_request(self.user_url, 'test', 'Test.Me64!', email='test-edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Valid e-mail is required.')
        self._assert_response(response, status=400, message=message)

    def test_user_with_invalid_username(self):
        """
        Try (and fail) user creation with invalid username
        """
        response, mock_audit_log = self._do_request(self.user_url, 'user name', 'Test.Me64!', email='test@edx.org',
                                                    first_name='John', last_name='Doe', secure=True)
        message = _('Username should only consist of A-Z and 0-9, with no spaces.')
        self._assert_response(response, status=400, message=message)

    def test_user_with_unknown_username(self):
        """
        Try (and fail) user login with unknown credentials
        """
        response, mock_audit_log = self._do_request(self.session_url, 'unknown', 'UnKnown.Pass', secure=True)
        self._assert_response(response, status=404)
        self._assert_audit_log(mock_audit_log, 'warn', ['API::Failed login attempt with unknown email/username'])

    def test_successful_logout(self):
        """
        Try login of user first and then logout user successfully and test audit log
        """
        response, mock_audit_log = self._do_request(self.session_url, 'test', 'test_password', secure=True)
        self._assert_response(response, status=201)
        self._assert_audit_log(mock_audit_log, 'info',
                               ["API::User logged in successfully with user-id - {}".format(self.user.id)])
        self._assert_not_in_audit_log(mock_audit_log, 'info', ['test'])
        response_dict = json.loads(response.content.decode("utf-8"))

        response, mock_audit_log = self._do_request(self.session_url + '/' + response_dict['token'], 'test',
                                                    'test_password', secure=True, request_method='DELETE')
        self._assert_response(response, status=204)
        self._assert_audit_log(mock_audit_log, 'info',
                               ['API::User session terminated for user-id - {}'.format(self.user.id)])

    def _do_request(self, url, username, password, **kwargs):
        """
        Make Post/Delete/Get requests with params
        """
        post_params, extra, = {'username': username, 'password': password}, {}
        patched_audit_log = 'edx_solutions_api_integration.sessions.views.AUDIT_LOG'
        request_method = kwargs.get('request_method', 'POST')
        if kwargs.get('email'):
            post_params['email'] = kwargs.get('email')
        if kwargs.get('first_name'):
            post_params['first_name'] = kwargs.get('first_name')
        if kwargs.get('last_name'):
            post_params['last_name'] = kwargs.get('last_name')
        if kwargs.get('secure', False):
            extra['wsgi.url_scheme'] = 'https'
        if kwargs.get('patched_audit_log'):
            patched_audit_log = kwargs.get('patched_audit_log')

        headers = {'X-Edx-Api-Key': TEST_API_KEY, 'Content-Type': 'application/json'}

        with patch(patched_audit_log) as mock_audit_log:
            if request_method == 'POST':
                result = self.client.post(url, post_params, headers=headers, **extra)
            elif request_method == 'DELETE':
                result = self.client.delete(url, post_params, headers=headers, **extra)
        return result, mock_audit_log

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

        # Return if response has not content
        if response.status_code == 204:
            return

        response_dict = json.loads(response.content.decode("utf-8"))

        if message is not None:
            msg = ("'%s' did not contain '%s'" %
                   (response_dict['message'], message))
            self.assertTrue(message in response_dict['message'], msg)

    def _assert_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        self.assertEqual(name, level)
        self.assertEqual(len(args), 1)
        format_string = args[0]
        for log_string in log_strings:
            self.assertIn(log_string, format_string)

    def _assert_not_in_audit_log(self, mock_audit_log, level, log_strings):
        """
        Check that the audit log has received the expected call as its last call.
        """
        method_calls = mock_audit_log.method_calls
        name, args, _kwargs = method_calls[-1]
        self.assertEqual(name, level)
        self.assertEqual(len(args), 1)
        format_string = args[0]
        for log_string in log_strings:
            self.assertNotIn(log_string, format_string)
