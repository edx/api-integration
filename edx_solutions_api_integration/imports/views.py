"""
Views for importing data into the LMS that requires a lot of LMS functionality to process.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
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
        internal = request.data.get('internal', False)
        statuses = request.data.get('statuses', [])
        roles = request.data.get('roles', {})
        ignore_roles = request.data.get('ignore_roles', [])
        permissions = request.data.get('permissions', {})
        company_id = request.data.get('company_id', '')
        course_id = request.data.get('course_id', '')
        status = request.data.get('status', '').lower()
        user = request.data.get('user', {})
        username, email = user.get('username', ''), user.get('email', '')

        # Check for empty fields.
        for key, value in user.items():
            if not isinstance(value, bool) and value.strip() == '':
                if key == 'email':
                    email = _("No email")
                if key != 'username':
                    self._add_error(errors, _("Empty field: {}").format(key), _("Processing Participant"), email)

        # Ensure valid status.
        if status not in statuses:
            self._add_error(errors, _("Status doesn't exist"), _('Enrolling Participant in Course'), email)

        # Validate email.
        try:
            validate_email(email)
        except ValidationError:
            self._add_error(errors, _('Valid e-mail is required'), _('Registering Participant'), email)
        else:
            # Ensure email/username integrity.
            if User.objects.filter(Q(email=email) | Q(username=username)).exists():
                self._add_error(errors, _('Email or username already exists'), _('Registering Participant'), email)

        # Check that the company exists.
        try:
            company = Organization.objects.get(id=company_id)
        except Organization.DoesNotExist:
            self._add_error(errors, _("Company doesn't exist"), _('Enrolling Participant in Company'), email)

        # Check that the course exists.
        course, course_key, __ = get_course(request, user, course_id)
        if not course:
            self._add_error(errors, _("Course doesn't exist"), _('Enrolling Participant in Course'), email)

        # Check if course is internal (if required).
        if internal and not CourseGroupRelationship.objects.filter(
                course_id=course_id,
                group__type="tag:internal"
        ).exists():
            self._add_error(errors, _("Course is not Internal"), _('Enrolling Participant in Course'), email)

        if not errors:
            # Create the user and their profile.
            try:
                # User
                user = User.objects.create(**user)
                user.set_password(user.password)
                user.save()
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
            CohortMembership.objects.create(course_user_group=cohort, user=user)

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

        response.update({'errors': errors})
        return Response(response)

    @list_route(methods=['post'])
    def existing(self, request):
        """Import existing participants in the LMS into a company and course."""
        # TODO: this will be done in a later ticket, using factored out logic from the `new` endpoint above.
        pass


    def _add_error(self, errors, reason, activity, participant):
        error = _("Reason: {}, Activity: {}, Participant: {}").format(reason, activity, participant)
        errors.append(error)
        return error
