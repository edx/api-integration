""" Django REST Framework Serializers """
from openedx.core.lib.courses import course_image_url

from rest_framework import serializers
from rest_framework.reverse import reverse

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


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
    name = serializers.CharField(source='display_name')
    category = serializers.SerializerMethodField()
    number = serializers.CharField(source='display_number_with_default')
    org = serializers.CharField(source='display_org_with_default')
    uri = serializers.SerializerMethodField()
    course_image_url = serializers.SerializerMethodField()
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
    enrolled_users = serializers.ListField(child=serializers.IntegerField())

    class Meta(object):
        """ Serializer/field specification """
        fields = ('id', 'name', 'number', 'org', 'start', 'end', 'due', 'enrolled_users', )
