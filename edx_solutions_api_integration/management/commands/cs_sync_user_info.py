from django.core.management.base import BaseCommand

from edx_solutions_api_integration.tasks import cs_sync_user_info


class Command(BaseCommand):
    """
    Management command for adding all users to the discussion service.
    """
    help = 'Sync users info between platform and cs-comment-service'

    def handle(self, *args, **options):
        try:
            task_id = cs_sync_user_info.delay()
        except Exception as e:
            self.stderr.write('Task failed to trigger with exception: "%s"' % e.message)
        else:
            self.stdout.write('Successfully triggered cs_sync_user_info task: "%s"'
                                                 % task_id)
