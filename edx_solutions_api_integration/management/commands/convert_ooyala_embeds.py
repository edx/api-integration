import logging
from optparse import make_option
import datetime

from pytz import UTC

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from edx_solutions_api_integration.tasks import convert_ooyala_embeds

logger = logging.getLogger(__name__)  # pylint: disable=locally-disabled, invalid-name


class Command(BaseCommand):
    """
    Command to update Ooyala Xblock embeds to corresponding Brightcove embeds
    """
    help = 'Convert Ooyala embeds to corresponding Brightcove embeds'
    batch_size = 100
    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            dest="user_id",
            help="Staff User ID",
        ),
        parser.add_argument(
            "--course-ids",
            dest="course_ids",
            help="Course IDs to process Ooyala instances in",
        ),

    def handle(self, *args, **options):
        course_ids = options.get('course_ids')
        user_id = options.get('user_id')

        if not user_id:
            raise CommandError("--user-id parameter is missing. Please provide a staff user id")
        else:
            try:
                User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise CommandError("Invalid user id: {}. Please provide a valid staff user id".format(user_id))

        if course_ids:
            course_ids = course_ids.split(',')
            logger.info('Ooyala embeds update task queued for Course: {}'.format(course_ids))
            convert_ooyala_embeds.delay(user_id, course_ids)
        else:
            # run on all open courses
            open_courses = CourseOverview.objects.filter(
                Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
                Q(end__isnull=True)
            ).values_list('id', flat=True)

            logger.info('Ooyala embeds update command: queuing task for {} Open Courses'.format(len(open_courses)))

            for course_ids in self.chunks(open_courses, self.batch_size):
                convert_ooyala_embeds.delay(user_id, course_ids)

    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]
