"""
Management command to check users progress and sends notifications
./manage.py lms send_progress_leaderboard_notifications  --settings=production --time-range=30
"""

import logging
import sys

from completion_aggregator.models import Aggregator
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from edx_notifications.data import NotificationMessage
from edx_notifications.lib.publisher import (
    publish_notification_to_user,
    get_notification_type
)

from ...models import LeaderBoard


log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Command to check users progress and sends notifications
    """
    help = "Time range in minute for which we need to check progress updates in past",

    def add_arguments(self, parser):
        parser.add_argument(
            "--time-range",
            dest="time_range",
            type=int,
            default=60,
            help="Time range in minute for which we need to check progress updates in past",
        )

    def handle(self, *args, **options):
        if not settings.FEATURES['ENABLE_NOTIFICATIONS']:
            return

        # Increase time range so that users don't miss notification in case cron job is skipped or delayed.
        time_range = timezone.now() - timezone.timedelta(minutes=options['time_range'] * 2)
        leaderboard_size = getattr(settings, 'LEADERBOARD_SIZE', 3)
        courses = Aggregator.objects.filter(
            aggregation_name='course',
            last_modified__gte=time_range
        ).distinct().values_list('course_key', flat=True)
        for course_key in courses:
            all_progress = Aggregator.objects.filter(
                aggregation_name='course', course_key=course_key, percent__gt=0
            ).exclude(user__courseaccessrole__course_id=course_key,
                      user__courseaccessrole__role__in=['staff', 'observer', 'assistant']
            ).order_by('-percent', 'last_modified')[:leaderboard_size]

            all_leaders = LeaderBoard.objects.filter(course_key=course_key).all()
            leaders = {l.position: l for l in all_leaders}
            positions = {l.user_id: l.position for l in all_leaders}
            for idx, progress in enumerate(all_progress):
                position = idx + 1
                leader = leaders.get(position)
                if not leader:
                    leader = LeaderBoard(course_key=course_key, position=position)

                old_leader = leader.user_id
                old_position = positions.get(progress.user_id, sys.maxint)
                leader.user_id = progress.user_id
                leader.save()
                is_new = progress.modified >= time_range

                if old_leader != progress.user_id and position < old_position and is_new:
                    try:
                        notification_msg = NotificationMessage(
                            msg_type=get_notification_type(u'open-edx.lms.leaderboard.progress.rank-changed'),
                            namespace=unicode(course_key),
                            payload={
                                '_schema_version': '1',
                                'rank': position,
                                'leaderboard_name': 'Progress',
                            }
                        )

                        #
                        # add in all the context parameters we'll need to
                        # generate a URL back to the website that will
                        # present the new course announcement
                        #
                        # IMPORTANT: This can be changed to msg.add_click_link() if we
                        # have a particular URL that we wish to use. In the initial use case,
                        # we need to make the link point to a different front end website
                        # so we need to resolve these links at dispatch time
                        #
                        notification_msg.add_click_link_params({
                            'course_id': unicode(course_key),
                        })

                        publish_notification_to_user(int(leader.user_id), notification_msg)
                    except Exception, ex:  # pylint: disable=broad-except
                        # Notifications are never critical, so we don't want to disrupt any
                        # other logic processing. So log and continue.
                        log.exception(ex)
