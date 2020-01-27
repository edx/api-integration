"""
Management command to update urls hardcoded in xblock from http to https
./manage.py lms update_urls_to_https  --settings=aws --user-id=1
"""

import datetime
import logging
import urllib2 as urllib
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils.timezone import utc
from edx_solutions_api_integration.models import APIUser as User
from edx_solutions_api_integration.tasks import update_http_to_https
from optparse import make_option
from pytz import UTC

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to update urls hardcoded in xblock from http to https
    """
    help = 'Updates urls hardcoded in xblock from http to https'
    batch_size = 100

    option_list = BaseCommand.option_list + (
        make_option(
            "--user-id",
            dest="user_id",
            help="Staff User ID",
        ),
    )

    def handle(self, *args, **options):
        user_id = options.get('user_id')

        if not user_id:
            raise CommandError("--user-id parameter is missing. Please provide a staff user id")
        try:
            User.objects.get(id=user_id)
        except User.DoesNotExist:
            raise CommandError("Invalid user id: {}. Please provide a valid staff user id".format(user_id))

        open_courses = CourseOverview.objects.filter(
            Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
            Q(end__isnull=True)
        ).values_list('id', flat=True)

        logger.info('Http to Https update command: queuing task for {} Open Courses'.format(len(open_courses)))

        for course_ids in self.chunks(open_courses, self.batch_size):
            update_http_to_https.delay(
                course_ids, user_id
            )

    def chunks(self, l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]
