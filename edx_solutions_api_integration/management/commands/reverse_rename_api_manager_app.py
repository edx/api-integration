"""
Management command to revert renaming of api_manager app
"""
import logging
from south.db import db
from django.core.management.base import BaseCommand
from django.db import transaction

log = logging.getLogger(__name__)


def get_old_appname():
    """
    Returns old app names
    """
    return 'edx_solutions_api_integration'


def get_new_appname():
    """
    Returns new app names
    """
    return 'api_manager'


def get_table_name_mappings():
    """
    Returns dictionary containing mappings of old table names to new table names
    """
    return {
        'edx_solutions_api_integration_linkedgrouprelationship': 'api_manager_linkedgrouprelationship',
        'edx_solutions_api_integration_grouprelationship': 'api_manager_grouprelationship',
        'edx_solutions_api_integration_coursegrouprelationship': 'api_manager_coursegrouprelationship',
        'edx_solutions_api_integration_coursecontentgrouprelationship': 'api_manager_coursecontentgrouprelationship'
    }


class Command(BaseCommand):
    """
    Renames api_manager app to edx_solutions_api_integration and updates database accordingly
    """
    help = 'Reverts renaming of api_manager app'

    def handle(self, *args, **options):
        log.info('reverting renaming api_manager app')

        with transaction.commit_on_success():
            db.execute("UPDATE south_migrationhistory SET app_name = %s WHERE app_name = %s", [get_new_appname(), get_old_appname()])  # pylint: disable=line-too-long
            db.execute("UPDATE django_content_type SET app_label = %s WHERE app_label = %s", [get_new_appname(), get_old_appname()])  # pylint: disable=line-too-long

            for old_table_name, new_table_name in get_table_name_mappings().items():
                db.rename_table(old_table_name, new_table_name)

            log.info('renaming of api_manager app successfully reverted')
