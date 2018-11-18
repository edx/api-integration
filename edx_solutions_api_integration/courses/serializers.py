""" Django REST Framework Serializers """
from django.conf import settings
from rest_framework import serializers
from rest_framework.reverse import reverse

from completion.models import BlockCompletion
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.lib.courses import course_image_url

from edx_solutions_api_integration.utils import get_profile_image_urls_by_username


class GradeSerializer(serializers.Serializer):
    """ Serializer for model interactions """
    grade = serializers.FloatField()


class BaseCourseLeadersSerializer(serializers.Serializer):
    """ Base Serializer for course leaderboard """
    id = serializers.IntegerField(source='user__id')  # pylint: disable=invalid-name
    username = serializers.CharField(source='user__username')
    title = serializers.CharField(source='user__profile__title')
    profile_image = serializers.SerializerMethodField()

    def get_profile_image(self, data):
        """
        Returns metadata about a user's profile image
        """
        return get_profile_image_urls_by_username(
            data['user__username'], data['user__profile__profile_image_uploaded_at']
        )


class CourseProficiencyLeadersSerializer(BaseCourseLeadersSerializer):
    """ Serializer for course proficiency leaderboard """
    # Percentage grade (versus letter grade)
    grade = serializers.FloatField()
    recorded = serializers.DateTimeField(source='modified')


class CourseCompletionsLeadersSerializer(BaseCourseLeadersSerializer):
    """ Serializer for course completions leaderboard """
    completions = serializers.SerializerMethodField('get_completion_percentage')

    def get_completion_percentage(self, obj):
        """
        formats get completions as percentage
        """
        return (obj['percent'] or 0) * 100


class CourseSocialLeadersSerializer(BaseCourseLeadersSerializer):
    """ Serializer for course leaderboard """
    score = serializers.IntegerField()
    recorded = serializers.DateTimeField(source='modified')


class CourseSerializer(serializers.Serializer):
    """ Serializer for Courses """
    id = serializers.CharField()  # pylint: disable=invalid-name
    name = serializers.CharField(source='display_name')
    category = serializers.SerializerMethodField()
    number = serializers.CharField(source='display_number_with_default')
    org = serializers.CharField(source='display_org_with_default')
    uri = serializers.SerializerMethodField()
    course_image_url = serializers.SerializerMethodField()
    mobile_available = serializers.BooleanField()
    due = serializers.SerializerMethodField('get_due_date')
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()

    def get_uri(self, course):
        """
        Builds course detail uri
        """
        return reverse('course-detail', args=[course.id], request=self.context.get('request'))

    def get_course_image_url(self, course):
        """
        Builds course image url
        """
        return course.course_image_url if isinstance(course, CourseOverview) else course_image_url(course)

    def get_category(self, course):
        """
        category: The type of content. In this case, the value is always "course".
        """
        return 'course'

    def get_due_date(self, course):
        """
        due:  The due date. For courses, the value is always null.
        """
        return None


class OrganizationCourseSerializer(CourseSerializer):
    """ Serializer for Organization Courses """
    name = serializers.CharField(source='display_name')
    enrolled_users = serializers.SerializerMethodField()

    class Meta(object):
        """ Serializer/field specification """
        fields = ('id', 'name', 'number', 'org', 'start', 'end', 'due', 'enrolled_users', )

    def get_enrolled_users(self, obj):
        return self.context['enrollments'][unicode(obj.id)] if unicode(obj.id) in self.context['enrollments'] else []


class UserGradebookSerializer(serializers.Serializer):
    """ Serializer for users passed in a specific course """
    id = serializers.IntegerField(source='user_id')
    email = serializers.CharField(source='user.email')
    username = serializers.CharField(source='user.username')
    first_name = serializers.CharField(source='user.first_name')
    last_name = serializers.CharField(source='user.last_name')
    is_active = serializers.BooleanField(source='user.is_active')
    created = serializers.DateTimeField(source='user.date_joined')
    complete_status = serializers.SerializerMethodField()

    def get_complete_status(self, gradebook):
        grade_complete_match_range = getattr(settings, 'GRADEBOOK_GRADE_COMPLETE_PROFORMA_MATCH_RANGE', 0.01)
        complete_status = False
        if gradebook.grade and (gradebook.proforma_grade <= gradebook.grade + grade_complete_match_range):
            complete_status = True
        return complete_status


class BlockCompletionSerializer(serializers.ModelSerializer):
    """
    Serialize Block Completions.

    Include extra fields for backwards compatibility with CourseModuleCompletions.
    """

    class Meta(object):  # pylint: disable=missing-docstring,too-few-public-methods
        model = BlockCompletion

    user_id = serializers.IntegerField(source='user.id')
    content_id = serializers.CharField(source='block_key')
    course_id = serializers.CharField(source='course_key')
    stage = serializers.SerializerMethodField()

    def get_stage(self, _obj):  # pylint: disable=no-self-use
        """
        BlockCompletions do not support stages.  Always return None.
        """
        return None
