""" Django REST Framework Serializers """
import json

from rest_framework import serializers
from django.core.exceptions import ObjectDoesNotExist

from edx_solutions_api_integration.models import APIUser
from edx_solutions_organizations.serializers import BasicOrganizationSerializer
from edx_solutions_api_integration.utils import get_profile_image_urls_by_username, string_list_to_list
from student.roles import CourseAccessRole


class DynamicFieldsModelSerializer(serializers.ModelSerializer):
    """
    A ModelSerializer that takes an additional `fields` argument that
    controls which fields should be displayed.
    """

    def __init__(self, *args, **kwargs):
        # Instantiate the superclass normally
        super(DynamicFieldsModelSerializer, self).__init__(*args, **kwargs)

        if 'request' in self.context:
            fields = self.context['request'].query_params.get('fields', None)
            if not fields and 'default_fields' in self.context:
                additional_fields = self.context['request'].query_params.get('additional_fields', "")
                fields = ','.join([self.context['default_fields'], additional_fields])
            if fields:
                fields = fields.split(',')
                # Drop any fields that are not specified in the `fields` argument.
                allowed = set(fields)
                existing = set(self.fields.keys())
                for field_name in existing - allowed:
                    self.fields.pop(field_name)


class UserSerializer(DynamicFieldsModelSerializer):
    """ Serializer for User model interactions """
    organizations = BasicOrganizationSerializer(many=True, required=False)
    created = serializers.DateTimeField(source='date_joined', required=False)
    profile_image = serializers.SerializerMethodField()
    city = serializers.CharField(source='profile.city')
    title = serializers.CharField(source='profile.title')
    country = serializers.CharField(source='profile.country')
    full_name = serializers.CharField(source='profile.name')
    courses_enrolled = serializers.SerializerMethodField()
    roles = serializers.SerializerMethodField('get_user_roles')
    grades = serializers.SerializerMethodField('get_user_grades')
    progress = serializers.SerializerMethodField('get_user_progress')

    def get_profile_image(self, user):
        """
        Returns metadata about a user's profile image
        """
        try:
            profile_image_uploaded_at = user.profile.profile_image_uploaded_at
        except ObjectDoesNotExist:
            profile_image_uploaded_at = None
        return get_profile_image_urls_by_username(user.username, profile_image_uploaded_at)

    def get_courses_enrolled(self, user):
        """ Serialize user enrolled courses """
        enrollments = user.courseenrollment_set.all()
        return [unicode(enrollment.course_id) for enrollment in enrollments]

    def get_user_roles(self, user):
        """ returns list of user roles """
        access_roles = user.courseaccessrole_set.all()
        if 'course_id' in self.context:
            course_id = self.context['course_id']
            roles = [access_role.role for access_role in access_roles if access_role.course_id == course_id]
        else:
            roles = [access_role.role for access_role in access_roles]

        return roles

    def get_user_grades(self, user):
        """ returns user proforma_grade, grade and grade_summary """
        grade, proforma_grade, section_breakdown = None, None, None
        gradebooks = user.studentgradebook_set.all()
        if 'course_id' in self.context and gradebooks:
            course_id = self.context['course_id']
            course_gradebook = next(
                (
                    gradebook
                    for gradebook in gradebooks
                    if gradebook.course_id == course_id
                ), None
            )
            if course_gradebook:
                try:
                    grade = course_gradebook.grade
                    proforma_grade = course_gradebook.proforma_grade
                    grade_summary = json.loads(course_gradebook.grade_summary)
                    if "section_breakdown" in grade_summary:
                        section_breakdown = grade_summary["section_breakdown"]
                except (ObjectDoesNotExist, ValueError):
                    pass

        return {'grade': grade, 'proforma_grade': proforma_grade, 'section_breakdown': section_breakdown}

    def get_user_progress(self, user):
        """ returns user progress against course """
        completion_percentage = 0
        progress = user.studentprogress_set.all()
        if 'course_id' in self.context and progress:
            course_id = self.context['course_id']
            actual_completions = next(
                (
                    progress_item.completions
                    for progress_item in progress
                    if progress_item.course_id == course_id
                 ), 0
            )
            if self.context['course_meta_data']:
                total_possible_completions = self.context['course_meta_data'].total_assessments
                if total_possible_completions > 0:
                    completion_percentage = min(100 * (actual_completions / float(total_possible_completions)), 100)
        return completion_percentage

    class Meta(object):
        """ Serializer/field specification """
        model = APIUser
        fields = (
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "created",
            "is_active",
            "profile_image",
            "city",
            "title",
            "country",
            "full_name",
            "is_staff",
            "last_login",
            "courses_enrolled",
            "organizations",
            "roles",
            "grades",
            "progress",
        )
        read_only_fields = ("id", "email", "username")


class SimpleUserSerializer(DynamicFieldsModelSerializer):
    """ Serializer for user model """
    created = serializers.DateTimeField(source='date_joined', required=False)

    class Meta(object):
        """ Serializer/field specification """
        model = APIUser
        fields = ("id", "email", "username", "first_name", "last_name", "created", "is_active")
        read_only_fields = ("id", "email", "username")


class UserCountByCitySerializer(serializers.Serializer):
    """ Serializer for user count by city """
    city = serializers.CharField(source='profile__city')
    count = serializers.IntegerField()


class UserRolesSerializer(serializers.Serializer):
    """ Serializer for user roles """
    course_id = serializers.CharField()
    role = serializers.CharField()


class CourseProgressSerializer(serializers.Serializer):
    """ Serializer for course progress """
    created = serializers.DateTimeField()
    is_active = serializers.BooleanField()
    progress = serializers.SerializerMethodField()
    course = serializers.SerializerMethodField()

    def get_progress(self, enrollment):
        completion_percentage = 0

        actual_completions = next(
            (
                progress['completions']
                for progress in self.context['student_progress']
                if progress['course_id'] == enrollment['course_id']
             ), 0
        )
        total_possible_completions = next(
            (
                metadata['total_assessments']
                for metadata in self.context['course_metadata']
                if metadata['id'] == enrollment['course_id']
            ), 0
        )

        if total_possible_completions > 0:
            completion_percentage = min(100 * (actual_completions / float(total_possible_completions)), 100)
        return completion_percentage

    def get_course(self, enrollment):
        course_overview = next(
            (
                course_overview
                for course_overview in self.context['course_overview']
                if course_overview['id'] == enrollment['course_id']
            ), None
        )
        return course_overview
