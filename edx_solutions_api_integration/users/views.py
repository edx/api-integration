""" API implementation for user-oriented interactions. """

import json
import logging

from django.contrib.auth.models import Group
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import validate_email, validate_slug, ValidationError
from django.db import IntegrityError
from django.db.models import Count, Q
from django.conf import settings
from django.http import Http404
from django.utils.translation import get_language, ugettext_lazy as _
from requests.exceptions import ConnectionError
from rest_framework import filters
from rest_framework.response import Response
from rest_framework import status
import six

from courseware import module_render
from courseware.model_data import FieldDataCache
from django_comment_common.models import Role, FORUM_ROLE_MODERATOR
from gradebook.models import StudentGradebook
from gradebook.utils import generate_user_gradebook
from social_engagement.models import StudentSocialEngagementScore
from instructor.access import revoke_access, update_forum_role
from openedx.core.djangoapps.lang_pref import LANGUAGE_KEY
from lms.lib.comment_client.utils import CommentClientRequestError, CommentClientMaintenanceError
from lms.lib.comment_client.user import get_user_social_stats
from notification_prefs.views import enable_notifications
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import UsageKey, CourseKey
from opaque_keys.edx.locations import Location, SlashSeparatedCourseKey
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.course_groups.models import CourseUserGroup, CourseCohort
from openedx.core.djangoapps.course_groups.cohorts import (
    get_cohort_by_name,
    add_cohort,
    add_user_to_cohort,
    remove_user_from_cohort,
)
from openedx.core.djangoapps.user_api.models import UserPreference
from openedx.core.djangoapps.user_api.preferences.api import set_user_preference
from edx_notifications.lib.consumer import mark_notification_read
from course_metadata.models import CourseAggregatedMetaData, CourseSetting
from progress.models import StudentProgress
from student.models import CourseEnrollment, CourseEnrollmentException, PasswordHistory, UserProfile
from student.roles import (
    CourseAccessRole,
    CourseInstructorRole,
    CourseObserverRole,
    CourseStaffRole,
    CourseAssistantRole,
    UserBasedRole,
)
from util.bad_request_rate_limiter import BadRequestRateLimiter
from util.password_policy_validators import (
    validate_password_length, validate_password_complexity,
    validate_password_dictionary
)
from xmodule.modulestore import InvalidLocationError

from progress.serializers import CourseModuleCompletionSerializer
from edx_solutions_api_integration.courseware_access import get_course, get_course_child, get_course_key, course_exists
from edx_solutions_api_integration.permissions import SecureAPIView, SecureListAPIView, IdsInFilterBackend, \
    HasOrgsFilterBackend
from edx_solutions_api_integration.models import GroupProfile, APIUser as User
from edx_solutions_organizations.serializers import BasicOrganizationSerializer
from edx_solutions_api_integration.utils import (
    generate_base_uri,
    dict_has_items,
    extract_data_params,
    get_user_from_request_params,
    get_aggregate_exclusion_user_ids,
    get_profile_image_urls_by_username,
    str2bool,
    css_param_to_list,
    get_aggregate_exclusion_user_ids,
    cache_course_data,
    cache_course_user_data,
    get_cached_data,
)
from edx_solutions_projects.serializers import BasicWorkgroupSerializer
from edx_solutions_api_integration.users.serializers import (
    UserSerializer,
    UserCountByCitySerializer,
    UserRolesSerializer,
    CourseProgressSerializer,
)

log = logging.getLogger(__name__)
AUDIT_LOG = logging.getLogger("audit")


def _serialize_user_profile(response_data, user_profile):
    """This function serialize user profile """
    response_data['title'] = user_profile.title
    response_data['full_name'] = user_profile.name
    response_data['city'] = user_profile.city
    response_data['country'] = user_profile.country.code
    response_data['level_of_education'] = user_profile.level_of_education
    response_data['year_of_birth'] = user_profile.year_of_birth
    response_data['gender'] = user_profile.gender
    response_data['profile_image'] = get_profile_image_urls_by_username(
        response_data['username'], user_profile.profile_image_uploaded_at
    )
    return response_data


def _serialize_user(response_data, user):
    """
    Loads the object data into the response dict
    This should probably evolve to use DRF serializers
    """
    response_data['email'] = user.email
    response_data['username'] = user.username
    response_data['first_name'] = user.first_name
    response_data['last_name'] = user.last_name
    response_data['id'] = user.id
    response_data['is_active'] = user.is_active
    response_data['created'] = user.date_joined
    response_data['is_staff'] = user.is_staff
    response_data['last_login'] = user.last_login
    return response_data


def _save_content_position(request, user, course_key, position):
    """
    Records the indicated position for the specified course
    Really no reason to generalize this out of user_courses_detail aside from pylint complaining
    """
    parent_content_id = position.get('parent_content_id')
    child_content_id = position.get('child_content_id')
    if unicode(course_key) == parent_content_id:
        parent_descriptor, parent_key, parent_content = get_course(request, user, parent_content_id, load_content=True)  # pylint: disable=W0612,C0301
    else:
        parent_descriptor, parent_key, parent_content = get_course_child(request, user, course_key, parent_content_id, load_content=True)  # pylint: disable=W0612,C0301
    if not parent_descriptor:
        return None

    # no need to fetch the actual child descriptor (avoid round trip to Mongo database), we just need
    # the id
    child_key = None
    try:
        child_key = UsageKey.from_string(child_content_id)
    except InvalidKeyError:
        try:
            child_key = Location.from_deprecated_string(child_content_id)
        except (InvalidLocationError, InvalidKeyError):
            pass
    if not child_key:
        return None

    # call an optimized version
    _save_child_position(parent_content, child_key)

    return child_content_id


def _save_child_position(parent_descriptor, target_child_location):
    """
    Faster version than what is in the LMS since we don't need to load/traverse children descriptors,
    we just compare id's from the array of children
    """
    for position, child_location in enumerate(getattr(parent_descriptor, 'children', []), start=1):
        if unicode(child_location) == unicode(target_child_location):
            # Only save if position changed
            if position != parent_descriptor.position:
                parent_descriptor.position = position
                parent_descriptor.save()


def _manage_role(course_descriptor, user, role, action):
    """
    Helper method for managing course/forum roles
    """
    supported_roles = ('instructor', 'staff', 'observer', 'assistant')
    forum_moderator_roles = ('instructor', 'staff', 'assistant')
    if role not in supported_roles:
        raise ValueError
    if action is 'allow':
        existing_role = CourseAccessRole.objects.filter(
            user=user,
            role=role,
            course_id=course_descriptor.id,
            org=course_descriptor.org
        )
        if not existing_role:
            new_role = CourseAccessRole(user=user, role=role, course_id=course_descriptor.id, org=course_descriptor.org)
            new_role.save()
        if role in forum_moderator_roles:
            try:
                dep_string = course_descriptor.id.to_deprecated_string()
                ssck = SlashSeparatedCourseKey.from_deprecated_string(dep_string)
                update_forum_role(ssck, user, FORUM_ROLE_MODERATOR, 'allow')
            except Role.DoesNotExist:
                try:
                    update_forum_role(course_descriptor.id, user, FORUM_ROLE_MODERATOR, 'allow')
                except Role.DoesNotExist:
                    pass

    elif action is 'revoke':
        revoke_access(course_descriptor, user, role)
        if role in forum_moderator_roles:
            # There's a possibilty that the user may play more than one role in a course
            # And that more than one of these roles allow for forum moderation
            # So we need to confirm the removed role was the only role for this user for this course
            # Before we can safely remove the corresponding forum moderator role
            user_instructor_courses = UserBasedRole(user, CourseInstructorRole.ROLE).courses_with_role()
            user_staff_courses = UserBasedRole(user, CourseStaffRole.ROLE).courses_with_role()
            user_assistant_courses = UserBasedRole(user, CourseAssistantRole.ROLE).courses_with_role()
            queryset = user_instructor_courses | user_staff_courses | user_assistant_courses
            queryset = queryset.filter(course_id=course_descriptor.id)
            if len(queryset) == 0:
                try:
                    dep_string = course_descriptor.id.to_deprecated_string()
                    ssck = SlashSeparatedCourseKey.from_deprecated_string(dep_string)
                    update_forum_role(ssck, user, FORUM_ROLE_MODERATOR, 'revoke')
                except Role.DoesNotExist:
                    try:
                        update_forum_role(course_descriptor.id, user, FORUM_ROLE_MODERATOR, 'revoke')
                    except Role.DoesNotExist:
                        pass


class UsersList(SecureListAPIView):
    """
    ### The UsersList view allows clients to retrieve/append a list of User entities
    - URI: ```/api/users/```
    - GET: Provides paginated list of users, it supports email, username, name, organizations, courses enrolled,
           has_organizations, organization_display_name and id filters
        Possible use cases
        GET /api/users?ids=23
        GET /api/users?ids=11,12,13&page=2
        GET /api/users?organizations=1,2,3
        GET /api/users?courses={course_id},{course_id2}
        GET /api/users?courses={rse_id}&match=partial
        GET /api/users?email={john@example.com}
        GET /api/users?email={john@example}&match=partial
        GET /api/users?name={john doe}
        GET /api/users?name={joh}&match=partial
        GET /api/users?organization_display_name={xyz}&match=partial
        GET /api/users?username={john}
            * email: string, filters user set by email address
            * username: string, filters user set by username
            * name: string, filters user set by full name
        GET /api/users?has_organizations={true}
            * has_organizations: boolean, filters user set with organization association
        GET /api/users?has_organizations={false}
            * has_organizations: boolean, filters user set with no organization association

        Example JSON output {'count': '25', 'next': 'https://testserver/api/users?page=2', num_pages='3',
        'previous': None, 'results':[]}
        'next' and 'previous' keys would have value of None if there are not next or previous page after current page.

    - POST: Provides the ability to append to the User entity set
        * email: __required__, The unique email address for the User being created
        * username: __required__, The unique username for the User being created
        * password: __required__, String which matches enabled formatting constraints
        * title
        * first_name
        * last_name
        * is_active, Boolean flag controlling the User's account activation status
        * is_staff, Boolean flag controlling the User's administrative access/permissions
        * city
        * country, Two-character country code
        * level_of_education
        * year_of_birth, Four-digit integer value
        * gender, Single-character value (M/F)
    - POST Example:

            {
                "email" : "honor@edx.org",
                "username" : "honor",
                "password" : "edx!@#",
                "title" : "Software Engineer",
                "first_name" : "Honor",
                "last_name" : "Student",
                "is_active" : False,
                "is_staff" : False,
                "city" : "Boston",
                "country" : "US",
                "level_of_education" : "hs",
                "year_of_birth" : "1996",
                "gender" : "F"
            }
    ### Use Cases/Notes:
    * Password formatting policies can be enabled through the "ENFORCE_PASSWORD_POLICY" feature flag
    * The first_name and last_name fields are additionally concatenated and stored in the 'name' field of UserProfile
    * Values for level_of_education can be found in the LEVEL_OF_EDUCATION_CHOICES enum, located
        in common/student/models.py
    """
    queryset = User.objects.all()
    serializer_class = UserSerializer
    filter_backends = (filters.DjangoFilterBackend, IdsInFilterBackend, HasOrgsFilterBackend)
    filter_fields = ('username', )

    def get_queryset(self):
        """
        Optionally filter users by organizations and course enrollments
        """
        queryset = self.queryset

        name = self.request.query_params.get('name', None)
        match = self.request.query_params.get('match', None)
        email = self.request.query_params.get('email', None)
        org_ids = self.request.query_params.get('organizations', None)
        courses = css_param_to_list(self.request, 'courses')
        organization_display_name = self.request.query_params.get('organization_display_name', None)

        if org_ids:
            org_ids = map(int, org_ids.split(','))
            queryset = queryset.filter(organizations__id__in=org_ids).distinct()

        if match == 'partial':
            if name:
                queryset = queryset.filter(profile__name__icontains=name)

            if email:
                queryset = queryset.filter(email__icontains=email)

            if organization_display_name is not None:
                queryset = queryset.filter(organizations__display_name__icontains=organization_display_name)

            if courses:
                courses_filter_list = [Q(courseenrollment__course_id__icontains=course) for course in courses]
                courses_filter_list = reduce(lambda a, b: a | b, courses_filter_list)
                queryset = queryset.filter(courses_filter_list)
        else:
            if name:
                queryset = queryset.filter(profile__name=name)

            if email:
                queryset = queryset.filter(email=email)

            if courses:
                courses = map(CourseKey.from_string, courses)
                queryset = queryset.filter(courseenrollment__course_id__in=courses).distinct()
                
        queryset = queryset.prefetch_related(
            'organizations',
            'courseaccessrole_set',
            'courseenrollment_set')\
            .select_related('profile')
        return queryset

    def get(self, request, *args, **kwargs):
        """
        GET /api/users?ids=11,12,13.....&page=2
        """
        return self.list(request, *args, **kwargs)

    def post(self, request):  # pylint: disable=R0915
        """
        POST /api/users/
        """
        response_data = {}
        base_uri = generate_base_uri(request)

        email = request.data.get('email')
        username = request.data.get('username')
        if username is None:
            return Response({'message': _('username is missing')}, status.HTTP_400_BAD_REQUEST)

        password = request.data.get('password')
        if settings.FEATURES.get('ENFORCE_PASSWORD_POLICY', True) and password is None:
            return Response({'message': _('password is missing')}, status.HTTP_400_BAD_REQUEST)

        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')
        is_active = request.data.get('is_active', None)
        is_staff = request.data.get('is_staff', False)
        city = request.data.get('city', '')
        country = request.data.get('country', '')
        level_of_education = request.data.get('level_of_education', '')
        year_of_birth = request.data.get('year_of_birth', '')
        gender = request.data.get('gender', '')
        title = request.data.get('title', '')
        # enforce password complexity as an optional feature
        if settings.FEATURES.get('ENFORCE_PASSWORD_POLICY', False):
            try:
                validate_password_length(password)
                validate_password_complexity(password)
                validate_password_dictionary(password)
            except ValidationError, err:
                response_data['message'] = _('Password: ') + '; '.join(err.messages)
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_email(email)
        except ValidationError:
            response_data['message'] = _('Valid e-mail is required.')
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        try:
            validate_slug(username)
        except ValidationError:
            response_data['message'] = _('Username should only consist of A-Z and 0-9, with no spaces.')
            return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

        # Create the User, UserProfile, and UserPreference records
        try:
            user = User.objects.create(email=email, username=username, is_staff=is_staff)
        except IntegrityError:
            response_data['message'] = _("Username '{username}' or email '{email}' already exists").format(  # pylint: disable=E1101
                username=username, email=email
            )
            response_data['field_conflict'] = "username or email"
            return Response(response_data, status=status.HTTP_409_CONFLICT)

        user.set_password(password)
        user.first_name = first_name
        user.last_name = last_name
        if is_active is not None:
            user.is_active = is_active
        if is_staff is not None:
            user.is_staff = is_staff
        user.save()

        # Be sure to always create a UserProfile record when adding users
        # Bad things happen with the UserSerializer if one does not exist
        profile = UserProfile(user=user)
        profile.name = u'{} {}'.format(first_name, last_name)
        profile.city = city
        profile.country = country
        profile.level_of_education = level_of_education
        profile.gender = gender
        profile.title = title
        profile.is_staff = is_staff

        try:
            profile.year_of_birth = int(year_of_birth)
        except ValueError:
            # If they give us garbage, just ignore it instead
            # of asking them to put an integer.
            profile.year_of_birth = None

        profile.save()

        set_user_preference(user, LANGUAGE_KEY, get_language())
        if settings.FEATURES.get('ENABLE_DISCUSSION_EMAIL_DIGEST'):
            enable_notifications(user)

        # add this account creation to password history
        # NOTE, this will be a NOP unless the feature has been turned on in configuration
        password_history_entry = PasswordHistory()
        password_history_entry.create(user)

        # add to audit log
        AUDIT_LOG.info(u"API::New account created with user-id - {0}".format(user.id))  # pylint: disable=W1202

        # CDODGE:  @TODO: We will have to extend this to look in the CourseEnrollmentAllowed table and
        # auto-enroll students when they create a new account. Also be sure to remove from
        # the CourseEnrollmentAllow table after the auto-registration has taken place
        response_data = _serialize_user(response_data, user)
        response_data['uri'] = '{}/{}'.format(base_uri, str(user.id))
        return Response(response_data, status=status.HTTP_201_CREATED)


class UsersDetail(SecureAPIView):
    """
    ### The UsersDetail view allows clients to interact with a specific User entity
    - URI: ```/api/users/{user_id}```
    - GET: Returns a JSON representation of the specified User entity
    - POST: Provides the ability to modify specific fields for this User entity
        * email: __required__, The unique email address for the User being created
        * username: __required__, The unique username for the User being created
        * password: __required__, String which matches enabled formatting constraints
        * title
        * first_name
        * last_name
        * is_active, Boolean flag controlling the User's account activation status
        * is_staff, Boolean flag controlling the User's administrative access/permissions
        * city
        * country, Two-character country code
        * level_of_education
        * year_of_birth, Four-digit integer value
        * gender, Single-character value (M/F)
    - POST Example:

            {
                "email" : "honor@edx.org",
                "username" : "honor",
                "password" : "edx!@#",
                "title" : "Software Engineer",
                "first_name" : "Honor",
                "last_name" : "Student",
                "is_active" : False,
                "is_staff" : False,
                "city" : "Boston",
                "country" : "US",
                "level_of_education" : "hs",
                "year_of_birth" : "1996",
                "gender" : "F"
            }
    ### Use Cases/Notes:
    * Use the UsersDetail view to obtain the current state for a specific User
    * For POSTs, you may include only those parameters you wish to modify, for example:
        ** Modifying the 'city' without changing the 'level_of_education' field
        ** New passwords will be subject to both format and history checks, if enabled
    * A GET response will additionally include a list of URIs to available sub-resources:
        ** Related Courses (/api/users/{user_id}/courses)
        ** Related Groups(/api/users/{user_id}/groups)
    """

    def get(self, request, user_id):
        """
        GET /api/users/{user_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)
        try:
            existing_user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response(response_data, status=status.HTTP_404_NOT_FOUND)

        _serialize_user(response_data, existing_user)
        response_data['uri'] = base_uri
        response_data['resources'] = []
        resource_uri = '{}/groups'.format(base_uri)
        response_data['resources'].append({'uri': resource_uri})
        resource_uri = '{}/courses'.format(base_uri)
        response_data['resources'].append({'uri': resource_uri})

        existing_user_profile = UserProfile.objects.get(user_id=user_id)
        if existing_user_profile:
            _serialize_user_profile(response_data, existing_user_profile)

        return Response(response_data, status=status.HTTP_200_OK)

    def post(self, request, user_id):  # pylint: disable=R0915
        """
        POST /api/users/{user_id}
        """
        response_data = {}
        response_data['uri'] = generate_base_uri(request)
        first_name = request.data.get('first_name')  # Used in multiple spots below
        last_name = request.data.get('last_name')  # Used in multiple spots below
        # Add some rate limiting here by re-using the RateLimitMixin as a helper class
        limiter = BadRequestRateLimiter()
        if limiter.is_rate_limit_exceeded(request):
            AUDIT_LOG.warning("API::Rate limit exceeded in password_reset")
            response_data['message'] = _('Rate limit exceeded in password_reset.')
            return Response(response_data, status=status.HTTP_403_FORBIDDEN)
        try:
            existing_user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            limiter.tick_bad_request_counter(request)
            existing_user = None
        if existing_user is None:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        # Ok, valid User, now update the provided fields
        if first_name:
            existing_user.first_name = first_name
        if last_name:
            existing_user.last_name = last_name
        is_active = request.data.get('is_active')
        if is_active is not None:
            existing_user.is_active = is_active
            response_data['is_active'] = existing_user.is_active
        is_staff = request.data.get('is_staff')
        if is_staff is not None:
            existing_user.is_staff = is_staff
            response_data['is_staff'] = existing_user.is_staff
        email = request.data.get('email')
        if email is not None:
            email_fail = False
            try:
                validate_email(email)
            except ValidationError:
                email_fail = True
                response_data['message'] = _('Invalid email address {}.').format(repr(email))  # pylint: disable=E1101
            if email != existing_user.email:
                try:
                    # Email addresses need to be unique in the LMS, though Django doesn't enforce it directly.
                    User.objects.get(email=email)
                    email_fail = True
                    response_data['message'] = _('A user with that email address already exists.')
                except ObjectDoesNotExist:
                    pass
            if email_fail:
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
            existing_user.email = email
        existing_user.save()

        username = request.data.get('username', None)
        if username:
            try:
                validate_slug(username)
            except ValidationError:
                response_data['message'] = _('Username should only consist of A-Z and 0-9, with no spaces.')
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)

            existing_username = User.objects.filter(username=username).filter(~Q(id=user_id))
            if existing_username:
                response_data['message'] = "User '%s' already exists" % (username)
                response_data['field_conflict'] = "username"
                return Response(response_data, status=status.HTTP_409_CONFLICT)

            existing_user.username = username
            response_data['username'] = existing_user.username
            existing_user.save()

        password = request.data.get('password')
        if password:
            old_password_hash = existing_user.password
            _serialize_user(response_data, existing_user)
            if settings.FEATURES.get('ENFORCE_PASSWORD_POLICY', False):
                try:
                    validate_password_length(password)
                    validate_password_complexity(password)
                    validate_password_dictionary(password)
                except ValidationError, err:
                    # bad user? tick the rate limiter counter
                    AUDIT_LOG.warning("API::Bad password in password_reset.")
                    response_data['message'] = _('Password: ') + '; '.join(err.messages)
                    return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
            # also, check the password reuse policy
            err_msg = None
            if not PasswordHistory.is_allowable_password_reuse(existing_user, password):
                if existing_user.is_staff:
                    num_distinct = settings.ADVANCED_SECURITY_CONFIG['MIN_DIFFERENT_STAFF_PASSWORDS_BEFORE_REUSE']
                else:
                    num_distinct = settings.ADVANCED_SECURITY_CONFIG['MIN_DIFFERENT_STUDENT_PASSWORDS_BEFORE_REUSE']
                err_msg = _(  # pylint: disable=E1101
                    "You are re-using a password that you have used recently. You must "
                    "have {0} distinct password(s) before reusing a previous password."
                ).format(num_distinct)  # pylint: disable=E1101
            # also, check to see if passwords are getting reset too frequent
            if PasswordHistory.is_password_reset_too_soon(existing_user):
                num_days = settings.ADVANCED_SECURITY_CONFIG['MIN_TIME_IN_DAYS_BETWEEN_ALLOWED_RESETS']
                err_msg = _(  # pylint: disable=E1101
                    "You are resetting passwords too frequently. Due to security policies, "
                    "{0} day(s) must elapse between password resets"
                ).format(num_days)  # pylint: disable=E1101

            if err_msg:
                # We have an password reset attempt which violates some security policy,
                status_code = status.HTTP_403_FORBIDDEN
                response_data['message'] = err_msg
                return Response(response_data, status=status_code)

            existing_user.is_active = True
            existing_user.set_password(password)
            existing_user.save()
            update_user_password_hash = existing_user.password

            if update_user_password_hash != old_password_hash:
                # add this account creation to password history
                # NOTE, this will be a NOP unless the feature has been turned on in configuration
                password_history_entry = PasswordHistory()
                password_history_entry.create(existing_user)

        # Also update the UserProfile record for this User
        existing_user_profile = UserProfile.objects.get(user_id=user_id)
        if existing_user_profile:
            if first_name and last_name:
                existing_user_profile.name = u'{} {}'.format(first_name, last_name)

            # nullable attributes
            existing_user_profile.title = request.data.get('title', existing_user_profile.title)
            existing_user_profile.city = request.data.get('city', existing_user_profile.city)
            existing_user_profile.country = request.data.get('country', existing_user_profile.country)
            existing_user_profile.gender = request.data.get('gender', existing_user_profile.gender)

            avatar_url = request.data.get('avatar_url')
            if avatar_url:
                existing_user_profile.avatar_url = avatar_url
            level_of_education = request.data.get('level_of_education')
            if level_of_education:
                existing_user_profile.level_of_education = level_of_education
            birth_year = request.data.get('year_of_birth')
            try:
                birth_year = int(birth_year)
            except (ValueError, TypeError):
                # If they give us garbage, just ignore it instead
                # of asking them to put an integer.
                birth_year = None
            existing_user_profile.year_of_birth = birth_year if birth_year else existing_user_profile.year_of_birth

            existing_user_profile.save()
        return Response(response_data, status=status.HTTP_200_OK)


class UsersGroupsList(SecureAPIView):
    """
    ### The UsersGroupsList view allows clients to interact with the set of Group entities related to the specified User
    - URI: ```/api/users/{user_id}/groups/```
    - GET: Returns a JSON representation (array) of the set of related Group entities
        * type: Set filtering parameter
        * course: Set filtering parameter to groups associated to a course or courses
        - URI: ```/api/users/{user_id}/groups/?type=series,seriesX&course=slashes%3AMITx%2B999%2BTEST_COURSE```
        * xblock_id: filters group data and returns those groups where xblock_id matches given xblock_id
    - POST: Append a Group entity to the set of related Group entities for the specified User
        * group_id: __required__, The identifier for the Group being added
    - POST Example:

            {
                "group_id" : 123
            }
    ### Use Cases/Notes:
    * Use the UsersGroupsList view to manage Group membership for a specific User
    * For example, you could display a list of all of a User's groups in a dashboard or administrative view
    * Optionally include the 'type' parameter to retrieve a subset of groups with a matching 'group_type' value
    """

    def post(self, request, user_id):
        """
        POST /api/users/{user_id}/groups
        """
        response_data = {}
        group_id = request.data.get('group_id')
        if not group_id:
            return Response({'message': _('group_id is missing')}, status.HTTP_400_BAD_REQUEST)

        base_uri = generate_base_uri(request)
        response_data['uri'] = '{}/{}'.format(base_uri, str(group_id))
        try:
            existing_user = User.objects.get(id=user_id)
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        try:
            existing_user.groups.get(id=existing_group.id)
            response_data['uri'] = '{}/{}'.format(base_uri, existing_group.id)
            response_data['message'] = "Relationship already exists."
            return Response(response_data, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            existing_user.groups.add(existing_group.id)
            response_data['uri'] = '{}/{}'.format(base_uri, existing_group.id)
            response_data['group_id'] = str(existing_group.id)
            response_data['user_id'] = str(existing_user.id)
            return Response(response_data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        """
        GET /api/users/{user_id}/groups?type=workgroup
        """
        try:
            existing_user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        group_type = css_param_to_list(request, 'type')
        course = css_param_to_list(request, 'course')
        data_params = extract_data_params(request)
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        groups = existing_user.groups.all()
        if group_type:
            groups = groups.filter(groupprofile__group_type__in=group_type)
        if course:
            groups = groups.filter(coursegrouprelationship__course_id__in=course)
        if data_params:
            groups = [group for group in groups if dict_has_items(group.groupprofile.data, data_params)]
        response_data['groups'] = []
        group_profiles = GroupProfile.objects.filter(group__in=groups)
        for group_profile in group_profiles:
            group_data = {}
            group_data['id'] = group_profile.group_id
            group_data['name'] = group_profile.name
            response_data['groups'].append(group_data)
        return Response(response_data, status=status.HTTP_200_OK)


class UsersGroupsDetail(SecureAPIView):
    """
    ### The UsersGroupsDetail view allows clients to interact with a specific User-Group relationship
    - URI: ```/api/users/{user_id}/groups/{group_id}```
    - GET: Returns a JSON representation of the specified User-Group relationship
    - DELETE: Removes an existing User-Group relationship
    ### Use Cases/Notes:
    * Use the UsersGroupsDetail to validate that a User is a member of a specific Group
    * Cancelling a User's membership in a Group is as simple as calling DELETE on the URI
    """

    def get(self, request, user_id, group_id):
        """
        GET /api/users/{user_id}/groups/{group_id}
        """
        response_data = {}
        try:
            existing_user = User.objects.get(id=user_id, is_active=True)
            existing_relationship = existing_user.groups.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data['user_id'] = existing_user.id
        response_data['group_id'] = existing_relationship.id
        response_data['uri'] = generate_base_uri(request)
        return Response(response_data, status=status.HTTP_200_OK)

    def delete(self, request, user_id, group_id):  # pylint: disable=W0612,W0613
        """
        DELETE /api/users/{user_id}/groups/{group_id}
        """
        try:
            existing_user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)

        existing_user.groups.remove(group_id)
        existing_user.save()
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class UsersCoursesList(SecureAPIView):
    """
    ### The UsersCoursesList view allows clients to interact with the set of Course entities related to the
        specified User
    - URI: ```/api/users/{user_id}/courses/```
    - GET: Returns a JSON representation (array) of the set of related Course entities
    - POST: Append a Group entity to the set of related Group entities for the specified User
        * course_id: __required__, The identifier (aka, location/key) for the Course being added
    - POST Example:

            {
                "course_id" : "edx/demo/course"
            }
    ### Use Cases/Notes:
    * POST to the UsersCoursesList view to create a new Course enrollment for the specified User (aka, Student)
    * Perform a GET to generate a list of all active Course enrollments for the specified User
    """
    def post(self, request, user_id):
        """
        POST /api/users/{user_id}/courses/
        """
        response_data = {}
        user_id = user_id
        course_id = request.data.get('course_id')
        if not course_id:
            return Response({'message': _('course_id is missing')}, status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=user_id)
            course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612,C0301
            if not course_descriptor:
                return Response({}, status=status.HTTP_404_NOT_FOUND)
        except (ObjectDoesNotExist, ValueError):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        base_uri = generate_base_uri(request)
        course_enrollment = CourseEnrollment.enroll(user, course_key)
        # Ensure the user is in a cohort. Add it explicitly in the default_cohort
        try:
            default_cohort = get_cohort_by_name(course_key, CourseUserGroup.default_cohort_name)
        except CourseUserGroup.DoesNotExist:
            default_cohort = add_cohort(course_key, CourseUserGroup.default_cohort_name, CourseCohort.RANDOM)
        try:
            add_user_to_cohort(default_cohort, user.username)
        except ValueError:
            msg_tpl = _('Student {student} already added to cohort {cohort_name} for course {course}')
            # pylint reports msg_tpl to not have `format` member, which is obviously a type resolution issue
            # related - http://stackoverflow.com/questions/10025710/pylint-reports-as-not-callable
            # pylint: disable=no-member
            response_data = {
                'message': msg_tpl.format(student=user.username, cohort_name=default_cohort.name, course=course_key)
            }
            return Response(response_data, status=status.HTTP_409_CONFLICT)

        log.debug('User "{}" has been automatically added in cohort "{}" for course "{}"'.format(  # pylint: disable=W1202
            user.username, default_cohort.name, course_descriptor.display_name)
        )  # pylint: disable=C0330
        response_data['uri'] = '{}/{}'.format(base_uri, course_key)
        response_data['id'] = unicode(course_key)
        response_data['name'] = course_descriptor.display_name
        response_data['is_active'] = course_enrollment.is_active
        return Response(response_data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        """
        GET /api/users/{user_id}/courses/
        """
        base_uri = generate_base_uri(request)
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        enrollments = CourseEnrollment.enrollments_for_user(user=user)
        response_data = []
        for enrollment in enrollments:
            if enrollment.course_overview:
                course_data = {
                    "id": unicode(enrollment.course_overview.id),
                    "uri": '{}/{}'.format(base_uri, unicode(enrollment.course_overview.id)),
                    "is_active": enrollment.is_active,
                    "name": enrollment.course_overview.display_name,
                    "start": enrollment.course_overview.start,
                    "end": enrollment.course_overview.end
                }
                response_data.append(course_data)

        return Response(response_data, status=status.HTTP_200_OK)


def _get_current_position_loc(parent_module):
    """
    An optimized lookup for the current position. The LMS version can cause unnecessary round trips to
    the Mongo database
    """

    if not hasattr(parent_module, 'position'):
        return None

    if not parent_module.children:
        return None

    index = 0

    if parent_module.position:
        index = parent_module.position - 1   # position is 1 indexed

    if 0 <= index < len(parent_module.children):
        return parent_module.children[index]

    return parent_module.children[0]


class UsersCoursesDetail(SecureAPIView):
    """
    ### The UsersCoursesDetail view allows clients to interact with a specific User-Course
        relationship (aka, enrollment)
    - URI: ```/api/users/{user_id}/courses/{course_id}```
    - POST: Stores the last-known location for the Course, for the specified User
        * position: The parent-child identifier set for the Content being set as the last-known position, consisting of:
        ** parent_content_id, normally the Course identifier
        ** child_content_id, normally the Chapter identifier
    - POST Example:
        {
            "positions" : [
                {
                    "parent_content_id" : "edX/CS301/2017_T1",
                    "child_content_id" : "i4x://edX/CS301/chapter/64f24c3c2d16492ba566f296ee0726a7"
                }
            ]
        }
    - GET: Returns a JSON representation of the specified User-Course relationship
    - DELETE: Inactivates (but does not remove) a Course relationship for the specified User
    ### Use Cases/Notes:
    * Use the UsersCoursesDetail view to manage EXISTING Course enrollments
    * Use GET to confirm that a User is actively enrolled in a particular course
    * Use DELETE to unenroll a User from a Course (inactivates the enrollment)
    * Use POST to record the last-known position within a Course (essentially, a bookmark)
    * Note: To create a new Course enrollment, see UsersCoursesList
    """

    def post(self, request, course_id, *args, **kwargs):
        """
        POST /api/users/{user_id}/courses/{course_id}
        """
        user = get_user_from_request_params(self.request, self.kwargs)
        if not user:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        base_uri = generate_base_uri(request)

        response_data = {}
        response_data['uri'] = base_uri
        if not course_exists(request, user, course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data['user_id'] = user.id
        response_data['course_id'] = course_id

        positions = request.data.get('positions')
        if not positions:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)

        course_key = get_course_key(course_id)
        response_data['positions'] = []
        for position in positions:
            content_position = _save_content_position(
                request,
                user,
                course_key,
                position
            )
            if not content_position:
                return Response(response_data, status=status.HTTP_400_BAD_REQUEST)
            response_data['positions'].append(content_position)
        return Response(response_data, status=status.HTTP_200_OK)

    def get(self, request, course_id, *args, **kwargs):
        """
        GET /api/users/{user_id}/courses/{course_id}
        """
        response_data = {}
        base_uri = generate_base_uri(request)

        user = get_user_from_request_params(self.request, self.kwargs)
        if not user:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        course_key = get_course_key(course_id)
        if not CourseEnrollment.is_enrolled(user, course_key):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        response_data['user_id'] = user.id
        response_data['course_id'] = course_id
        response_data['uri'] = base_uri

        course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612
        field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
            course_key,
            user,
            course_descriptor,
            depth=0)
        course_module = module_render.get_module_for_descriptor(
            user,
            request,
            course_descriptor,
            field_data_cache,
            course_key)
        response_data['position'] = getattr(course_module, 'position', None)
        response_data['mobile_available'] = getattr(course_module, 'mobile_available', None)
        response_data['position_tree'] = {}
        response_data['language'] = course_descriptor.language

        parent_module = course_module
        while parent_module is not None:
            current_child_loc = _get_current_position_loc(parent_module)
            if current_child_loc:
                response_data['position_tree'][current_child_loc.category] = {}
                response_data['position_tree'][current_child_loc.category]['id'] = unicode(current_child_loc)
                _, _, parent_module = get_course_child(
                    request, user, course_key, unicode(current_child_loc), load_content=True
                )
            else:
                parent_module = None
        return Response(response_data, status=status.HTTP_200_OK)

    def delete(self, request, course_id, *args, **kwargs):
        """
        DELETE /api/users/{user_id}/courses/{course_id}
        """
        user = get_user_from_request_params(self.request, self.kwargs)
        if not user:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        if not course_exists(request, user, course_id):
            return Response({}, status=status.HTTP_204_NO_CONTENT)
        course_key = get_course_key(course_id)
        try:
            cohort = CourseUserGroup.objects.get(
                course_id=course_key,
                users__id=user.id,
                group_type=CourseUserGroup.COHORT,
            )
            remove_user_from_cohort(cohort, user.username)
        except ObjectDoesNotExist:
            pass
        CourseEnrollment.unenroll(user, course_key)
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class UsersCoursesGradesList(SecureAPIView):
    """
    ### The UsersCoursesGradesList view allows clients to interact with the User's gradebook across courses
    - URI: ```/api/users/{user_id}/courses/grades?courses={course_id1,course_id2}```
    - GET: Returns a JSON representation of the matched set from the gradebook
        * courses (optional): Set filtering parameter consisting of comma-separated course identifiers

    ### Use Cases/Notes:
    * Use the UsersCoursesGradesList view to interact with the User's gradebook across multiple Course enrollments
    * Use GET to retrieve all of the Course gradebook entries for the specified User
    """
    def get(self, request, user_id):  # pylint: disable=unused-argument
        """
        GET /api/users/{user_id}/courses/grades
        """
        grade_complete_match_range = getattr(settings, 'GRADEBOOK_GRADE_COMPLETE_PROFORMA_MATCH_RANGE', 0.01)
        queryset = StudentGradebook.objects.filter(user=user_id)
        response_data = []
        for record in queryset:
            complete_status = False
            if record.grade and (record.proforma_grade <= record.grade + grade_complete_match_range):
                complete_status = True
            response_data.append(
                {
                    'course_id': unicode(record.course_id),
                    'current_grade': record.grade,
                    'proforma_grade': record.proforma_grade,
                    'complete_status': complete_status
                }
            )
        return Response(response_data, status=status.HTTP_200_OK)


class UsersCoursesGradesDetail(SecureAPIView):
    """
    ### The UsersCoursesGradesDetail view allows clients to interact with the User's gradebook for a particular Course
    - URI: ```/api/users/{user_id}/courses/{course_id}/grades```
    - GET: Returns a JSON representation of the specified Course gradebook
    ### Use Cases/Notes:
    * Use the UsersCoursesDetail view to manage the User's gradebook for a Course enrollment
    * Use GET to retrieve the Course gradebook for the specified User
    """

    def get(self, request, user_id, course_id):
        """
        GET /api/users/{user_id}/courses/{course_id}/grades
        """
        # The pre-fetching of groups is done to make auth checks not require an
        # additional DB lookup (this kills the Progress page in particular).
        try:
            student = User.objects.prefetch_related("groups").get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        course_key = get_course_key(course_id)

        if not CourseEnrollment.is_enrolled(student, course_key):
            return Response(
                {
                    'message': _("Student not enrolled in given course")
                }, status=status.HTTP_404_NOT_FOUND
            )

        try:
            gradebook_entry = StudentGradebook.objects.get(
                user=student,
                course_id__exact=course_key,
            )
        except ObjectDoesNotExist:
            gradebook_entry = generate_user_gradebook(course_key, student)

        progress_summary = None
        grade_summary = None
        grading_policy = None
        current_grade = 0
        proforma_grade = 0
        try:
            current_grade = gradebook_entry.grade
            proforma_grade = gradebook_entry.proforma_grade
            progress_summary = json.loads(gradebook_entry.progress_summary)
            grade_summary = json.loads(gradebook_entry.grade_summary)
            grading_policy = json.loads(gradebook_entry.grading_policy)
        except (ValueError, TypeError):
            # add to audit log
            AUDIT_LOG.info(
                u"API:: unable to parse gradebook entry for user-id - %s and course-id - '%s'",
                user_id,
                course_key
            )
        response_data = {
            'courseware_summary': progress_summary,
            'grade_summary': grade_summary,
            'grading_policy': grading_policy,
            'current_grade': current_grade,
            'proforma_grade': proforma_grade
        }
        return Response(response_data)


class UsersPreferences(SecureAPIView):
    """
    ### The UsersPreferences view allows clients to interact with the set of Preference key-value pairs related
        to the specified User
    - URI: ```/api/users/{user_id}/preferences/```
    - GET: Returns a JSON representation (dict) of the set of User preferences
    - POST: Append a new UserPreference key-value pair to the set of preferences for the specified User
        * "keyname": __required__, The identifier (aka, key) for the UserPreference being added.  Values must be strings
    - POST Example:

            {
                "favorite_color" : "blue"
            }
    ### Use Cases/Notes:
    * POSTing a non-string preference value will result in a 400 Bad Request response from the server
    * POSTing a duplicate preference will cause the existing preference to be overwritten (effectively a PUT operation)
    """

    def get(self, request, user_id):  # pylint: disable=W0613
        """
        GET returns the preferences for the specified user
        """

        response_data = {}

        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)

        for preference in user.preferences.all():
            response_data[preference.key] = preference.value

        return Response(response_data)

    def post(self, request, user_id):
        """
        POST adds a new entry into the UserPreference table
        """

        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        if not len(request.data):
            return Response({}, status=status.HTTP_400_BAD_REQUEST)

        # do a quick inspection to make sure we're only getting strings as values
        for key in request.data.keys():
            value = request.data[key]
            if not isinstance(value, basestring):
                return Response({}, status=status.HTTP_400_BAD_REQUEST)

        status_code = status.HTTP_200_OK
        for key in request.data.keys():
            value = request.data[key]

            # see if the key already exists
            found = None
            for preference in user.preferences.all():
                if preference.key == key:
                    found = preference
                    break

            if found:
                found.value = value
                found.save()
            else:
                preference = UserPreference.objects.create(user_id=user_id, key=key, value=value)
                preference.save()
                status_code = status.HTTP_201_CREATED

        return Response({}, status_code)


class UsersPreferencesDetail(SecureAPIView):
    """
    ### The UsersPreferencesDetail view allows clients to interact with the User's preferences
    - URI: ```/api/users/{user_id}/preferences/{preference_id}```
    - DELETE: Removes the specified preference from the user's record
    ### Use Cases/Notes:
    * Use DELETE to remove the last-visited course for a user (for example)
    """

    def get(self, request, user_id, preference_id):  # pylint: disable=W0613
        """
        GET returns the specified preference for the specified user
        """
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        response_data = {}
        try:
            response_data[preference_id] = user.preferences.get(key=preference_id).value
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        return Response(response_data)

    def delete(self, request, user_id, preference_id):  # pylint: disable=W0613
        """
        DELETE /api/users/{user_id}/preferences/{preference_id}
        """
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status.HTTP_404_NOT_FOUND)
        for preference in user.preferences.all():
            if preference.key == preference_id:
                UserPreference.objects.get(user_id=user_id, key=preference.key).delete()
                break
        return Response({}, status=status.HTTP_204_NO_CONTENT)


class UsersOrganizationsList(SecureListAPIView):
    """
    ### The UserOrganizationsList view allows clients to retrieve a list of organizations a user
    belongs to
    - URI: ```/api/users/{user_id}/organizations/```
    - GET: Provides paginated list of organizations for a user
    """

    serializer_class = BasicOrganizationSerializer

    def get_queryset(self):
        user = get_user_from_request_params(self.request, self.kwargs)
        if not user:
            return []

        return user.organizations.all()


class UsersWorkgroupsList(SecureListAPIView):
    """
    ### The UsersWorkgroupsList view allows clients to retrieve a list of workgroups a user
    belongs to
    - URI: ```/api/users/{user_id}/workgroups/```
    - GET: Provides paginated list of workgroups for a user
    To filter the user's workgroup set by course
    GET ```/api/users/{user_id}/workgroups/?course_id={course_id}```
    """

    serializer_class = BasicWorkgroupSerializer

    def get_queryset(self):
        user_id = self.kwargs['user_id']
        course_id = self.request.query_params.get('course_id', None)
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise Http404

        queryset = user.workgroups.all()

        if course_id:
            queryset = queryset.filter(project__course_id=course_id)
        return queryset


class UsersCoursesCompletionsList(SecureListAPIView):
    """
    ### The UsersCoursesCompletionsList view allows clients to retrieve a list of course completions
    for a user
    - URI: ```/api/users/{user_id}/courses/{course_id}/completions/```
    - GET: Provides paginated list of course completions a user
    To filter the user's course completions by course content
    GET ```/api/users/{user_id}/courses/{course_id}/completions/?content_id={content_id}```
    """

    serializer_class = CourseModuleCompletionSerializer

    def get_queryset(self):
        user_id = self.kwargs['user_id']
        course_id = self.kwargs.get('course_id', None)
        content_id = self.request.query_params.get('content_id', None)
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise Http404

        queryset = user.course_completions.all()

        if course_id:
            queryset = queryset.filter(course_id=course_id)
        if content_id:
            queryset = queryset.filter(content_id=content_id)

        return queryset


class UsersSocialMetrics(SecureListAPIView):
    """
    ### The UsersSocialMetrics view allows clients to query about the activity of a user in the
    forums
    - URI: ```/api/users/{user_id}/courses/{course_id}/metrics/social/```
    - GET: Returns a list of social metrics for that user in the specified course
    """

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-arguments
        include_stats = str2bool(request.query_params.get('include_stats', 'false'))
        user = get_user_from_request_params(self.request, self.kwargs)
        course_id = kwargs.get('course_id', None)
        if course_id is None:
            course_id = self.request.query_params.get('course_id', None)

        if not course_exists(request, self.request.user, course_id):
            raise Http404

        course_key = get_course_key(course_id)
        cached_social_data = get_cached_data('social', course_id, user.id)
        if not cached_social_data:
            social_engagement_score = self._get_user_score(course_key, user)
            course_avg = self._get_course_average_score(course_key)
            data = {'course_avg': course_avg, 'score': social_engagement_score}
            cache_course_data('social', course_id, {'course_avg': course_avg})
            cache_course_user_data('social', course_id, user.id, {'score': social_engagement_score})
        else:
            data = cached_social_data
        if include_stats:
            data['stats'] = self._get_user_discussion_metrics(request, user, course_id)

        return Response(data, status.HTTP_200_OK)

    @staticmethod
    def _get_user_discussion_metrics(request, user, course_id):
        """ Fetches discussion metrics from the forums client."""
        # load the course so that we can see when the course end date is
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            raise Http404

        # be robust to the try of course_id we get from caller
        try:
            # assume new style
            course_key = CourseKey.from_string(course_id)
            slash_course_id = course_key.to_deprecated_string()
        except:  # pylint: disable=W0702
            # assume course_id passed in is legacy format
            slash_course_id = course_id

        try:
            # get the course social stats, passing along a course end date to remove any activity after the course
            # closure from the stats
            user_id = str(user.id)
            data = (get_user_social_stats(user_id, slash_course_id, end_date=course_descriptor.end))[user_id]
        except (CommentClientRequestError, CommentClientMaintenanceError, ConnectionError), error:
            logging.error("Forum service returned an error: %s", str(error))

            data = {
                'err_msg': str(error),
                'num_threads': 0,
                'num_thread_followers': 0,
                'num_replies': 0,
                'num_flagged': 0,
                'num_comments': 0,
                'num_threads_read': 0,
                'num_downvotes': 0,
                'num_upvotes': 0,
                'num_comments_generated': 0
            }

        return data

    @staticmethod
    def _get_user_score(course_key, user):
        score = StudentSocialEngagementScore.get_user_engagement_score(course_key, user.id)
        if score is None:
            return 0
        return score

    @staticmethod
    def _get_course_average_score(course_key):
        exclude_users = get_aggregate_exclusion_user_ids(course_key)
        return StudentSocialEngagementScore.get_course_average_engagement_score(course_key, exclude_users)


class UsersMetricsCitiesList(SecureListAPIView):
    """
    ### The UsersMetricsCitiesList view allows clients to retrieve ordered list of user
    count by city
    - URI: ```/api/users/metrics/cities/```
    - GET: Provides paginated list of user count
    To get user count a particular city filter can be applied
    GET ```/api/users/metrics/cities/?city={city}```
    """

    serializer_class = UserCountByCitySerializer

    def get_queryset(self):
        city = self.request.query_params.get('city', None)
        queryset = User.objects.all()
        if city:
            queryset = queryset.filter(profile__city__iexact=city)

        queryset = queryset.values('profile__city').annotate(count=Count('profile__city'))\
            .filter(count__gt=0).order_by('-count')
        return queryset


class UsersRolesList(SecureListAPIView):
    """
    ### The UsersRolesList view allows clients to interact with the User's roleset
    - URI: ```/api/users/{user_id}/courses/{course_id}/roles```
    - GET: Returns a JSON representation of the specified Course roleset
    - POST: Adds a new role to the User's roleset
    - PUT: Replace the existing roleset with the provided roleset

    ### Use Cases/Notes:
    * Use the UsersRolesList view to manage a User's TA status
    * Use GET to retrieve the set of roles a User plays for a particular course
    * Use POST to grant a role to a particular User
    * Use PUT to perform a batch replacement of all roles assigned to a User
    """

    serializer_class = UserRolesSerializer

    def get_queryset(self):
        user_id = self.kwargs.get('user_id')
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise Http404

        instructor_courses = UserBasedRole(user, CourseInstructorRole.ROLE).courses_with_role()
        staff_courses = UserBasedRole(user, CourseStaffRole.ROLE).courses_with_role()
        observer_courses = UserBasedRole(user, CourseObserverRole.ROLE).courses_with_role()
        assistant_courses = UserBasedRole(user, CourseAssistantRole.ROLE).courses_with_role()
        queryset = instructor_courses | staff_courses | observer_courses | assistant_courses

        course_id = self.request.query_params.get('course_id', None)
        if course_id:
            if not course_exists(self.request, user, course_id):
                raise Http404
            course_key = get_course_key(course_id)
            queryset = queryset.filter(course_id=course_key)

        role = self.request.query_params.get('role', None)
        if role:
            queryset = queryset.filter(role=role)

        return queryset

    def post(self, request, user_id):
        """
        POST /api/users/{user_id}/roles/
        """
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise Http404

        course_id = request.data.get('course_id', None)
        course_descriptor, course_key, course_content = get_course(self.request, self.request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)

        role = request.data.get('role', None)
        try:
            _manage_role(course_descriptor, user, role, 'allow')
        except ValueError:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        return Response(request.data, status=status.HTTP_201_CREATED)

    def put(self, request, user_id):
        """
        PUT /api/users/{user_id}/roles/
        """
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise Http404

        roles = request.data.get('roles', [])
        if not roles:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        ignore_roles = request.data.get('ignore_roles', [])
        current_roles = self.get_queryset()
        for current_role in current_roles:
            if current_role.role not in ignore_roles:
                course_descriptor, course_key, course_content = get_course(request, user, unicode(current_role.course_id))  # pylint: disable=W0612,C0301
                if course_descriptor:
                    _manage_role(course_descriptor, user, current_role.role, 'revoke')
        for role in roles:
            role_value = role.get('role')
            if not role_value:
                return Response({}, status=status.HTTP_400_BAD_REQUEST)

            if role_value not in ignore_roles:
                try:
                    course_id = role.get('course_id')
                    course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612,C0301
                    if not course_descriptor:
                        raise ValueError  # ValueError is also thrown by the following role setters
                    _manage_role(course_descriptor, user, role_value, 'allow')
                except ValueError:
                    # Restore the current roleset to the User
                    for current_role in current_roles:
                        course_descriptor, course_key, course_content = get_course(
                            request, user, unicode(current_role.course_id))  # pylint: disable=W0612
                        _manage_role(course_descriptor, user, current_role.role, 'allow')
                    return Response({}, status=status.HTTP_400_BAD_REQUEST)
        return Response(request.data, status=status.HTTP_200_OK)


class UsersRolesCoursesDetail(SecureAPIView):
    """
    ### The UsersRolesCoursesDetail view allows clients to interact with a specific User/Course Role
    - URI: ```/api/users/{user_id}/roles/{role}/courses/{course_id}/```
    - DELETE: Removes an existing Course Role specification
    ### Use Cases/Notes:
    * Use the DELETE operation to revoke a particular role for the specified user
    """
    def delete(self, request, user_id, role, course_id):  # pylint: disable=W0613
        """
        DELETE /api/users/{user_id}/roles/{role}/courses/{course_id}
        """
        course_descriptor, course_key, course_content = get_course(self.request, self.request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        try:
            _manage_role(course_descriptor, user, role, 'revoke')
        except ValueError:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        return Response({}, status=status.HTTP_204_NO_CONTENT)


class UsersNotificationsDetail(SecureAPIView):
    """
    Allows for a caller to mark a user's notification as read,
    passed in by msg_id. Note that the user_msg_id must belong
    to the user_id passed in
    """
    def post(self, request, user_id, msg_id):
        """
        POST /api/users/{user_id}/notifications/{msg_id}

        payload:
            {
                'read': 'True' or 'False'
            }
        """

        read = request.data.get('read')
        if not read:
            return Response({'message': _('read field is missing')}, status.HTTP_400_BAD_REQUEST)

        mark_notification_read(int(user_id), int(msg_id), read=bool(read))

        return Response({}, status=status.HTTP_201_CREATED)


class UsersCourseProgressList(SecureListAPIView):
    """
    The UsersCourseProgressList view allows you to retrieve a list of courses user enrolled in and the progress
    for a user
    - URI: ```/api/users/{user_id}/courses/progress```
    To get course of mobile only
    GET ```/api/users/{user_id}/courses/progress?mobile_only=true```
    """
    pagination_class = None

    def get(self, request, *args, **kwargs):  # pylint: disable=unused-argument
        user = get_user_from_request_params(self.request, self.kwargs)

        mobile_only = self.request.query_params.get('mobile_only', None)
        enrollments = CourseEnrollment.objects.filter(user=user).values('course_id', 'created', 'is_active')

        course_keys = []
        for course_enrollment in enrollments:
            course_keys.append(CourseKey.from_string(course_enrollment['course_id']))

        student_progress = StudentProgress.objects.filter(user=user).values('course_id', 'completions')
        course_meta_data = CourseAggregatedMetaData.objects.filter(id__in=course_keys).values('id', 'total_assessments')
        course_overview = CourseOverview.objects.filter(id__in=course_keys)
        if str2bool(mobile_only):
            course_overview = course_overview.filter(mobile_available=True)
        course_overview = course_overview.values(
            'id', 'start', 'end', 'course_image_url', 'display_name', 'mobile_available', 'language'
        )

        filtered_course_overview = [overview["id"] for overview in course_overview]

        enrollments = [
                enrollment
                for enrollment in enrollments
                if enrollment['course_id'] in filtered_course_overview
            ]

        serializer = CourseProgressSerializer(enrollments, many=True, context={
            'student_progress': student_progress,
            'course_overview': course_overview,
            'course_metadata': course_meta_data,
        })

        return Response(serializer.data, status=status.HTTP_200_OK)


class UsersListWithEnrollment(UsersList):  # pylint: disable=too-many-ancestors
    """
    View to create Users and enroll them in a list of courses.  In addition to
    the options provided by UsersList.post, this view accepts an optional
    "courses" attribute in the request body, which is a list of course keys to
    enroll in.

    The response will be annotated with a "courses" attribute which reflects
    the list of courses the user was successfully enrolled in.  Failure to
    enroll in a given course will not propagate an error to the caller, it will
    just cause that course's key to be omitted from the response.
    """

    def post(self, request):
        """
        POST /api/users/integration_test_users/
        """
        AUDIT_LOG.warning(
            "API::Creating and enrolling user with UsersListWithEnrollment. "
            "This should not be used in production"
        )
        response = super(UsersListWithEnrollment, self).post(request)
        if response.status_code == status.HTTP_201_CREATED:
            user = User.objects.get(username=request.data['username'])
            response.data['courses'] = []
            for course_key_string in request.data.get('courses', []):
                try:
                    course_key = CourseKey.from_string(course_key_string)
                    CourseEnrollment.enroll(user=user, course_key=course_key, check_access=True)
                except (InvalidKeyError, CourseEnrollmentException) as exc:
                    AUDIT_LOG.warning(
                        "API::Could not enroll %s in %s because of %s",
                        user,
                        course_key_string,
                        exc
                    )
                else:
                    response.data['courses'].append(course_key_string)
        return response
