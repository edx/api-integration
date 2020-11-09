"""
Management command deletes old courses and relevant data from mongo db and mysql db
"""
import logging
from datetime import datetime, timedelta
from optparse import make_option

import pytz
from completion.models import BlockCompletion
from completion_aggregator.models import Aggregator
from course_metadata.models import CourseAggregatedMetaData
from lms.djangoapps.courseware.models import StudentModule
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from edx_solutions_api_integration.models import (
    CourseContentGroupRelationship, CourseGroupRelationship)
from gradebook.models import StudentGradebook, StudentGradebookHistory
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_structures.models import CourseStructure
from openedx.core.djangoapps.course_groups.models import (
    CourseCohortsSettings, CourseUserGroup)
from student.models import CourseAccessRole, CourseEnrollment
from util.prompt import query_yes_no
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore

log = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Deletes courses based on their age and relevant data of each course
    """
    help = 'Deletes courses based on their age and relevant data of each course'
    def add_arguments(self, parser):
        parser.add_argument(
            "-c",
            "--age",
            dest="age",
            help="Age in days",
            metavar="60"
        ),
    module_store = modulestore()
    total_courses = 0
    total_deleted = 0

    @staticmethod
    def delete_reference_data(course_key):
        """
        Deletes reference data for course
        """
        log.info('removing reference data for course %s', str(course_key))
        CourseGroupRelationship.objects.filter(course_id=course_key).delete()
        CourseContentGroupRelationship.objects.filter(course_id=course_key).delete()
        CourseCohortsSettings.objects.filter(course_id=course_key).delete()
        CourseUserGroup.objects.filter(course_id=course_key).delete()
        CourseAggregatedMetaData.objects.filter(id=course_key).delete()
        CourseStructure.objects.filter(course_id=course_key).delete()
        CourseOverview.objects.filter(id=course_key).delete()
        StudentModule.objects.filter(course_id=course_key).delete()
        BlockCompletion.objects.filter(context_key=course_key).delete()
        Aggregator.objects.filter(course_key=course_key).delete()
        StudentGradebookHistory.objects.filter(course_id=course_key).delete()
        StudentGradebook.objects.filter(course_id=course_key).delete()
        CourseAccessRole.objects.filter(course_id=course_key).delete()
        CourseEnrollment.objects.filter(course_id=course_key).delete()

    def delete_course_from_modulestore(self, course_key):
        """
        Deletes course from modulestore
        """
        user_id = ModuleStoreEnum.UserID.mgmt_command
        with self.module_store.bulk_operations(course_key):
            log.info('Removing course %s from modulestore', str(course_key))
            self.module_store.delete_course(course_key, user_id)

    @transaction.atomic
    def delete_course_and_data(self, course_key):
        """
        Delete course from modulestore and it reference data
        """
        Command.delete_reference_data(course_key)
        self.delete_course_from_modulestore(course_key)

    def handle(self, *args, **options):
        if not options.get('age'):
            raise CommandError("bulk_delete_courses_with_reference_data command requires one integer argument: --age")

        age_in_days = int(options.get('age'))
        if query_yes_no(
                "Are you sure you want to delete all courses and their reference data having no activity "
                "in last %s days. This action cannot be undone!" % age_in_days, default="no"
        ):
            created_time = datetime.now(pytz.UTC) + timedelta(days=-age_in_days)
            courses = self.module_store.get_courses()

            for course in courses:
                last_edited_on = getattr(course, 'edited_on')
                if last_edited_on and last_edited_on < created_time:
                    self.total_courses += 1
                    try:
                        self.delete_course_and_data(course.id)
                        self.total_deleted += 1
                    except Exception as ex:   # pylint: disable=broad-except
                        log.exception("Exception while deleting course %s", ex.message)

            completion_message = "command completed. Total {} courses deleted out of {}".format(
                self.total_deleted, self.total_courses
            )
            log.info(completion_message)
