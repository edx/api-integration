import logging
from optparse import make_option
import datetime

from pytz import UTC

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.test.utils import override_settings

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from edx_solutions_api_integration.tasks import update_image_explorer_schema

logger = logging.getLogger(__name__)  # pylint: disable=locally-disabled, invalid-name


class Command(BaseCommand):
    """
    Command to update Image Explorer schema to version 2 and convert pixel
    coordinates to percentages
    """
    help = 'Update Image Explorer xblock schema'
    batch_size = 100
    option_list = BaseCommand.option_list + (
        make_option(
            "--user-id",
            dest="user_id",
            help="Staff User ID",
        ),
        make_option(
            "--course-id",
            dest="course_id",
            help="Single Course ID to process IE instances in",
        ),
    )

    @override_settings(CELERY_ALWAYS_EAGER=True)
    def handle(self, *args, **options):
        course_id = options.get('course_id')
        user_id = options.get('user_id')

        if not user_id:
            raise CommandError("--user-id parameter is missing. Please provide a staff user id")
        else:
            try:
                User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise CommandError("Invalid user id: {}. Please provide a valid staff user id".format(user_id))

        if course_id:
            logger.info('IE schema update task queued for Course: {}'.format(course_id))
            update_image_explorer_schema.delay(user_id, [course_id])

        else:
            # run on all open courses
            open_courses = CourseOverview.objects.filter(
                Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
                Q(end__isnull=True)
            ).values_list('id', flat=True)

            logger.info('IE schema update command: queuing task for {} Open Courses'.format(len(open_courses)))

            for course_ids in self.chunks(open_courses, self.batch_size):
                update_image_explorer_schema.delay(user_id, course_ids)

    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]
