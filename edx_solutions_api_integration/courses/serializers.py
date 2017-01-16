""" Django REST Framework Serializers """
from openedx.core.lib.courses import course_image_url

from edx_solutions_api_integration.utils import generate_base_uri
from rest_framework import serializers


class GradeSerializer(serializers.Serializer):
    """ Serializer for model interactions """
    grade = serializers.FloatField()


class CourseLeadersSerializer(serializers.Serializer):
    """ Serializer for course leaderboard """
    id = serializers.IntegerField(source='user__id')  # pylint: disable=invalid-name
    username = serializers.CharField(source='user__username')
    title = serializers.CharField(source='user__profile__title')
    avatar_url = serializers.CharField(source='user__profile__avatar_url')
    # Percentage grade (versus letter grade)
    grade = serializers.FloatField()
    recorded = serializers.DateTimeField(source='modified')


class CourseCompletionsLeadersSerializer(serializers.Serializer):
    """ Serializer for course completions leaderboard """
    id = serializers.IntegerField(source='user__id')  # pylint: disable=invalid-name
    username = serializers.CharField(source='user__username')
    title = serializers.CharField(source='user__profile__title')
    avatar_url = serializers.CharField(source='user__profile__avatar_url')
    completions = serializers.SerializerMethodField('get_completion_percentage')

    def get_completion_percentage(self, obj):
        """
        formats get completions as percentage
        """
        total_completions = self.context['total_completions'] or 0
        completions = obj['completions'] or 0
        completion_percentage = 0
        if total_completions > 0:
            completion_percentage = min(100 * (completions / float(total_completions)), 100)
        return completion_percentage


class CourseSerializer(serializers.Serializer):
    """ Serializer for Courses """
    id = serializers.CharField()  # pylint: disable=invalid-name
    name = serializers.CharField()
    category = serializers.CharField()
    number = serializers.CharField()
    org = serializers.CharField()
    uri = serializers.SerializerMethodField()
    course_image_url = serializers.SerializerMethodField()
    resources = serializers.SerializerMethodField()
    due = serializers.DateTimeField()
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()

    def get_uri(self, course):
        """
        Builds course detail uri
        """
        return course.get('uri', None) if isinstance(course, dict) else \
            "{}/{}".format(generate_base_uri(self.context['request']), course.id)

    def get_course_image_url(self, course):
        """
        Builds course image url
        """
        return course.get('course_image_url', None) if isinstance(course, dict) else course_image_url(course)

    def get_resources(self, course):
        """
        Builds course resource list
        """
        return course.get('resources', []) if isinstance(course, dict) else []


class OrganizationCourseSerializer(CourseSerializer):
    """ Serializer for Organization Courses """
    name = serializers.CharField(source='display_name')
    enrolled_users = serializers.ListField(child=serializers.IntegerField())

    class Meta(object):
        """ Serializer/field specification """
        fields = ('id', 'name', 'number', 'org', 'start', 'end', 'due', 'enrolled_users', )
