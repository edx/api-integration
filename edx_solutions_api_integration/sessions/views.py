# pylint: disable=E1101

""" API implementation for session-oriented interactions. """
import logging
from importlib import import_module

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import (BACKEND_SESSION_KEY, HASH_SESSION_KEY,
                                 SESSION_KEY, authenticate, load_backend)
from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ObjectDoesNotExist
from django.template.context_processors import csrf
from django.utils import timezone
from django.utils.translation import ugettext as _
from edx_solutions_api_integration.models import PasswordHistory
from edx_solutions_api_integration.permissions import SecureAPIView
from edx_solutions_api_integration.users.serializers import SimpleUserSerializer
from edx_solutions_api_integration.utils import generate_base_uri
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from student.models import LoginFailures
from util.request_rate_limiter import BadRequestRateLimiter

AUDIT_LOG = logging.getLogger("audit")


class SessionsList(SecureAPIView):
    """
    **Use Case**

        SessionsList creates a new session with the edX LMS.

    **Example Request**

        POST  {"username": "staff", "password": "edx"}

    **Response Values**

        * token: A unique token value for the session created.
        * expires: The number of seconds until the new session expires.
        * user: The following data about the user for whom the session is
          created.
            * id: The unique user identifier.
            * email: The user's email address.
            * username: The user's edX username.
            * first_name: The user's first name, if defined.
            * last_name: The user's last name, if defined.
            * creaed:  The time and date the user account was created.
            * organizations: An array of organizations the user is associated
              with.
        * uri: The URI to use to get details about the new session.
    """

    def post(self, request):
        """
        Login user
        """
        return self.login_user(request)

    # pylint: disable=too-many-statements
    @staticmethod
    def login_user(request, session_id=None):
        """ Create a new session and login the user, or upgrade an existing session """
        response_data = {}
        # Add some rate limiting here by re-using the RateLimitMixin as a helper class
        limiter = BadRequestRateLimiter()
        if limiter.is_rate_limit_exceeded(request):
            response_data['message'] = _('Rate limit exceeded in api login.')
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)

        base_uri = generate_base_uri(request)

        username = request.data.get('username', None)
        if username is None:
            return Response({'message': _('username is missing')}, status=status.HTTP_400_BAD_REQUEST)

        password = request.data.get('password', None)
        if password is None:
            return Response({'message': _('password is missing')}, status=status.HTTP_400_BAD_REQUEST)

        try:
            existing_user = User.objects.get(username=username)
        except ObjectDoesNotExist:
            existing_user = None

        # see if account has been locked out due to excessive login failures
        if existing_user and LoginFailures.is_feature_enabled():
            if LoginFailures.is_user_locked_out(existing_user):
                response_status = status.HTTP_403_FORBIDDEN
                response_data['message'] = _('This account has been temporarily locked due to excessive login failures. '  # pylint: disable=C0301
                                             'Try again later.')
                return Response(response_data, status=response_status)

        # see if the user must reset his/her password due to any policy settings
        if existing_user and PasswordHistory.should_user_reset_password_now(existing_user):
            response_status = status.HTTP_403_FORBIDDEN
            response_data['message'] = _(
                'Your password has expired due to password policy on this account. '
                'You must reset your password before you can log in again.'
            )
            return Response(response_data, status=response_status)

        if existing_user:
            user = authenticate(username=existing_user.username, password=password)
            if user is not None:

                # successful login, clear failed login attempts counters, if applicable
                if LoginFailures.is_feature_enabled():
                    LoginFailures.clear_lockout_counter(user)

                if user.is_active:
                    #
                    # Create a new session directly with the SESSION_ENGINE
                    # We don't call the django.contrib.auth login() method
                    # because it is bound with the HTTP request.
                    #
                    # Since we are a server-to-server API, we shouldn't
                    # be stateful with respect to the HTTP request
                    # and anything that might come with it, as it could
                    # violate our RESTfulness
                    #
                    engine = import_module(settings.SESSION_ENGINE)
                    if session_id is None:
                        session = engine.SessionStore()
                        session.create()
                        success_status = status.HTTP_201_CREATED
                    else:
                        session = engine.SessionStore(session_id)
                        success_status = status.HTTP_200_OK
                        if SESSION_KEY in session:
                            # Someone is already logged in. The user ID of whoever is logged in
                            # now might be different than the user ID we've been asked to login,
                            # which would be bad. But even if it is the same user, we should not
                            # be asked to login a user who is already logged in. This likely
                            # indicates some sort of programming/validation error and possibly
                            # even a potential security issue - so return 403.
                            return Response({}, status=status.HTTP_403_FORBIDDEN)

                    # These values are expected to be set in any new session
                    session[SESSION_KEY] = user.id
                    session[BACKEND_SESSION_KEY] = user.backend
                    if hasattr(user, 'get_session_auth_hash'):
                        session_auth_hash = user.get_session_auth_hash()
                    else:
                        session_auth_hash = ''
                    session[HASH_SESSION_KEY] = session_auth_hash

                    session.save()

                    response_data['token'] = session.session_key
                    response_data['expires'] = session.get_expiry_age()
                    user_dto = SimpleUserSerializer(user)
                    response_data['user'] = user_dto.data
                    response_data['uri'] = '{}/{}'.format(base_uri, session.session_key)
                    response_status = success_status

                    # generate a CSRF tokens for any web clients that may need to
                    # call into the LMS via Ajax (for example Notifications)
                    response_data['csrftoken'] = str(csrf(request)['csrf_token'])

                    # update the last_login fields in the auth_user table for this user
                    user.last_login = timezone.now()
                    user.save()

                    # add to audit log
                    AUDIT_LOG.info("API::User logged in successfully with user-id - {}".format(user.id))  # pylint: disable=W1202
                else:
                    response_status = status.HTTP_403_FORBIDDEN
            else:
                limiter.tick_request_counter(request)

                # tick the failed login counters if the user exists in the database
                if LoginFailures.is_feature_enabled():
                    LoginFailures.increment_lockout_counter(existing_user)

                response_status = status.HTTP_401_UNAUTHORIZED
                AUDIT_LOG.warn("API::User authentication failed with user-id - {}".format(existing_user.id))  # pylint: disable=W1202
        else:
            AUDIT_LOG.warn("API::Failed login attempt with unknown email/username")
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)


class SessionsDetail(SecureAPIView):
    """
    **Use Case**

        SessionsDetail gets a details about a specific API session, as well as
        enables you to delete an API session or "upgrade" a session by logging
        in the user.


    **Example Requests**

        GET /api/session/{session_id}

        POST /api/session/{session_id}

        DELETE /api/session/{session_id}/delete

    **GET Response Values**

        * token: A unique token value for the session.
        * expires: The number of seconds until the session expires.
        * user_id: The unique user identifier.
        * uri: The URI to use to get details about the session.
    """

    def get(self, request, session_id):
        """
        Returns session
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        engine = import_module(settings.SESSION_ENGINE)
        session = engine.SessionStore(session_id)
        try:
            user_id = session[SESSION_KEY]
            backend_path = session[BACKEND_SESSION_KEY]
            backend = load_backend(backend_path)
            user = backend.get_user(user_id) or AnonymousUser()
        except KeyError:
            user = AnonymousUser()
        if user.is_authenticated:
            response_data['token'] = session.session_key
            response_data['expires'] = session.get_expiry_age()
            response_data['uri'] = base_uri
            response_data['user_id'] = user.id
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response(response_data, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, session_id):
        """ Login and upgrade an existing session from anonymous to authenticated. """
        return SessionsList.login_user(request, session_id)

    def delete(self, request, session_id):  # pylint: disable=W0613
        """
        Deletes session
        """
        engine = import_module(settings.SESSION_ENGINE)
        session = engine.SessionStore(session_id)
        if session is None or SESSION_KEY not in session:
            return Response({}, status=status.HTTP_204_NO_CONTENT)
        user_id = session[SESSION_KEY]
        session.delete()

        AUDIT_LOG.info("API::User session terminated for user-id - {}".format(user_id))  # pylint: disable=W1202
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class AssetsToken(APIView):
    """
    Assets token can be used to request locked LMS assets by passing them in request param
    e.g; /c4x/ToolsORG/Tools101/asset/Getting_started.pdf?access_token={asset_token}

    It is created by encrypting a valid user session id.
    """
    def get(self, request, session_id):
        response_data = {}
        engine = import_module(settings.SESSION_ENGINE)
        session = engine.SessionStore(session_id)
        try:
            user_id = session[SESSION_KEY]
            backend_path = session[BACKEND_SESSION_KEY]
            backend = load_backend(backend_path)
            user = backend.get_user(user_id) or AnonymousUser()
        except KeyError:
            user = AnonymousUser()
        if user.is_authenticated:
            response_data['assets_token'] = Fernet(bytes(settings.ASSETS_TOKEN_ENCRYPTION_KEY))\
                .encrypt(bytes(session.session_key))
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            return Response(response_data, status=status.HTTP_404_NOT_FOUND)
