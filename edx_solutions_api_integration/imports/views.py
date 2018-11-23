"""
Views for importing data into the LMS that requires a lot of LMS functionality to process.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.db import IntegrityError
from django.db.models import Q
from django.utils.translation import ugettext as _
from rest_framework.response import Response

from edx_solutions_api_integration.courseware_access import get_course
from edx_solutions_api_integration.models import CourseGroupRelationship
from edx_solutions_api_integration.users.views import _manage_role
from edx_solutions_organizations.models import Organization
from rest_framework.decorators import list_route

from notification_prefs.views import enable_notifications
from openedx.core.djangoapps.course_groups.cohorts import add_cohort, get_cohort_by_name
from openedx.core.djangoapps.course_groups.models import CohortMembership, CourseCohort, CourseUserGroup
from student.models import CourseEnrollment, PasswordHistory, UserProfile

from edx_solutions_api_integration.permissions import SecureViewSet

AUDIT_LOG = logging.getLogger("audit")


class ImportParticipantsViewSet(SecureViewSet):
    @list_route(methods=['post'])
    def new(self, request):
        """Import new participants into the LMS, a company and course."""
        errors, response = [], {}
        data = self._validate_data(request, errors, new=True)

        if not errors:
            self._create_user(data, errors, response)
            self._enroll_user(data, errors)

        response.update({'errors': errors})
        return Response(response)

    @list_route(methods=['post'])
    def existing(self, request):
        """Import existing participants into a course."""
        errors, response = [], {}
        data = self._validate_data(request, errors, new=False)

        if not errors:
            self._enroll_user(data, errors)

        response.update({'errors': errors})
        return Response(response)

    def _validate_data(self, request, errors, new):
        """
        Validate imported data.
        :param new: if `True`, new user will be created and enrolled, otherwise existing user will be enrolled
        :returns `request.data` copy with added `course`, `course_key` and ('company` if `new` else `user_object`)
        """
        validated_data = request.data.copy()
        internal = validated_data.get('internal', False)
        statuses = validated_data.get('statuses', [])
        company_id = validated_data.get('company_id', '')
        course_id = validated_data.get('course_id', '')
        status = validated_data.get('status', '').lower()
        user = validated_data.get('user', {})
        username, email = user.get('username', ''), user.get('email', '')

        # Check for empty fields.
        for key, value in user.items():
            if not isinstance(value, bool) and value.strip() == '':
                if key != 'username':
                    self._add_error(errors, _("Empty field: {}").format(key), _("Processing Participant"), email)

        # Ensure valid status.
        if status not in statuses:
            self._add_error(
                errors,
                _("Status '{}' doesn't exist").format(status),
                _('Enrolling Participant in Course'),
                email
            )

        if new:
            # Ensure email/username integrity.
            if User.objects.filter(Q(email=email) | Q(username=username)).exists():
                self._add_error(
                    errors,
                    _('Email "{}" or username "{}" already exists').format(email, username),
                    _('Registering Participant'),
                    email
                )

            # Check that the company exists.
            try:
                validated_data['company'] = Organization.objects.get(id=company_id)
            except Organization.DoesNotExist:
                self._add_error(
                    errors,
                    _("Company {} doesn't exist").format(company_id),
                    _('Enrolling Participant in Company'),
                    email
                )
        else:
            # Ensure user with provided email exists.
            try:
                validated_data['user_object'] = User.objects.get(email=email)
            except User.DoesNotExist:
                self._add_error(
                    errors,
                    _('User with email "{}" does not exist').format(email),
                    _('Retrieving existing Participant'),
                    email
                )

        # Check that the course exists.
        validated_data['course'], validated_data['course_key'], __ = get_course(request, user, course_id)
        if not validated_data['course']:
            self._add_error(
                errors,
                _("Course {} doesn't exist").format(course_id),
                _('Enrolling Participant in Course'),
                email
            )

        # Check if course is internal (if required).
        if internal and not CourseGroupRelationship.objects.filter(
                course_id=course_id,
                group__type="tag:internal"
        ).exists():
            self._add_error(
                errors,
                _("Course {} is not Internal").format(course_id),
                _('Enrolling Participant in Course'),
                email
            )

        return validated_data

    def _create_user(self, data, errors, response):
        """Register user and add him to a company."""
        user = data.get('user')
        email = user.get('email')
        company = data.get('company')

        # Create the user and their profile.
        try:
            # User
            user = User.objects.create(**user)
            user.set_password(user.password)
            user.save()
            data['user_object'] = user
            # Profile
            UserProfile.objects.create(user=user, name=u'{} {}'.format(user.first_name, user.last_name))
            # Notifications
            if settings.FEATURES.get('ENABLE_DISCUSSION_EMAIL_DIGEST'):
                enable_notifications(user)
            # Password History
            password_history_entry = PasswordHistory()
            password_history_entry.create(user)
        except Exception as exc:
            self._add_error(errors, str(exc.message), _('Registering Participant'), email)
        else:
            response['user_id'] = user.id
            AUDIT_LOG.info(u"API::New account created with user-id - {0}".format(user.id))

        # Associate with company.
        try:
            company.users.add(user)
        except Exception as exc:
            self._add_error(errors, str(exc.message), _('Enrolling Participant in Company'), email)

    def _enroll_user(self, data, errors):
        """Enroll user in a course and add him to a cohort."""
        user = data.get('user_object')
        email = user.email
        roles = data.get('roles', {})
        ignore_roles = data.get('ignore_roles', [])
        permissions = data.get('permissions', {})
        status = data.get('status', '').lower()
        course = data.get('course')
        course_key = data.get('course_key')

        # Enroll in course.
        try:
            CourseEnrollment.enroll(user, course_key)
        except IntegrityError:
            # If the enrollment already exists, it's possible we weren't able to add to the cohort yet,
            # so ignore the error and continue.
            pass
        try:
            cohort = get_cohort_by_name(course_key, CourseUserGroup.default_cohort_name)
        except CourseUserGroup.DoesNotExist:
            cohort = add_cohort(course_key, CourseUserGroup.default_cohort_name, CourseCohort.RANDOM)
        try:
            CohortMembership.objects.create(course_user_group=cohort, user=user)
        except IntegrityError:
            # This situation can occur if user is already present in the course or if his enrollment was removed
            # manually from Django Admin. We can ignore this error.
            pass

        # Assign role and permission in course.
        try:
            if status != "participant":
                # Add role.
                role = roles[status]
                if role not in ignore_roles:
                    _manage_role(course, user, role, 'allow')

                # Add permission for role.
                permission = permissions[role]
                permission_groups = Group.objects.get(groupprofile__name=permission)
                permission_groups.user_set.add(user.id)
        except Exception as exc:
            self._add_error(errors, str(exc.message), _("Setting Participant's Status"), email)

    @staticmethod
    def _add_error(errors, reason, activity, email):
        """Helper method for creating readable logs."""
        error = _("Reason: {}, Activity: {}, Participant: {}").format(reason, activity, email or _("No email"))
        errors.append(error)
        return error
