"""
Signal handlers supporting various course metadata use cases
"""
from django.dispatch import receiver

from xmodule.modulestore.django import SignalHandler

from edx_solutions_api_integration.models import CourseGroupRelationship, CourseContentGroupRelationship
from student.models import ENROLL_STATUS_CHANGE
from edx_solutions_api_integration.utils import invalid_user_data_cache


@receiver(SignalHandler.course_deleted)
def on_course_deleted(sender, **kwargs):  # pylint: disable=W0613
    """
    Listens for a 'course_deleted' signal and when observed
    removes model entries for the specified course
    """
    course_key = kwargs['course_key']
    CourseGroupRelationship.objects.filter(course_id=course_key).delete()
    CourseContentGroupRelationship.objects.filter(course_id=course_key).delete()


@receiver(ENROLL_STATUS_CHANGE)
def on_course_enrollment_change(sender, event=None, user=None, **kwargs):  # pylint: disable=unused-argument
    """
    Updates course enrollment count cache.
    """
    course_id = kwargs.get('course_id', None)
    if course_id:
        invalid_user_data_cache("course_enrollments", course_id)
        invalid_user_data_cache("cities_count", course_id)
