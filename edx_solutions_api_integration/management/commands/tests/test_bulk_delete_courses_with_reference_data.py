"""
Tests to support bulk_delete_courses_with_reference_data django management command
"""
from datetime import datetime, timedelta

import mock
import pytz
from completion.waffle import ENABLE_COMPLETION_TRACKING, WAFFLE_NAMESPACE
from completion_aggregator.models import Aggregator
from course_metadata.models import CourseAggregatedMetaData
from lms.djangoapps.courseware.models import StudentModule
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from edx_solutions_api_integration.models import CourseGroupRelationship
from freezegun import freeze_time
from gradebook.models import StudentGradebook
from mock import PropertyMock
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_structures.models import CourseStructure
from student.models import CourseAccessRole, CourseEnrollment
from student.tests.factories import GroupFactory, UserFactory
from waffle.testutils import override_switch
from xmodule.course_module import CourseDescriptor
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.django_utils import (ModuleStoreTestCase,
                                                    mixed_store_config)
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory

MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {})


@override_switch(
    '{}.{}'.format(WAFFLE_NAMESPACE, ENABLE_COMPLETION_TRACKING),
    active=True,
)
@override_settings(MODULESTORE=MODULESTORE_CONFIG)
class BulkCourseDeleteTests(ModuleStoreTestCase):
    """
    Test suite for bulk course delete script
    """
    YESNO_PATCH_LOCATION = 'edx_solutions_api_integration.management.commands.bulk_delete_courses_with_reference_data.query_yes_no'  # pylint: disable=C0301

    def setUp(self):
        super().setUp()

    @staticmethod
    def create_course():
        """
        Creates a course with just one chapter inside it
        """
        course = CourseFactory.create()
        ItemFactory.create(
            category="chapter",
            parent_location=course.location,
            display_name="Overview"
        )
        return course

    @staticmethod
    def create_course_reference_data(course):
        """
        Populates DB with test data
        """
        course_key = course.id
        user = UserFactory()
        group = GroupFactory()
        CourseGroupRelationship(course_id=course_key, group=group).save()
        StudentGradebook(
            user=user,
            course_id=course_key,
            grade=0.9,
            proforma_grade=0.91,
            progress_summary='test',
            grade_summary='test',
            grading_policy='test',
        ).save()
        CourseEnrollment.get_or_create_enrollment(user, course_key)
        CourseAccessRole(user=user, course_id=course_key, org='test', role='TA').save()
        handouts_usage_key = course_key.make_usage_key('course_info', 'handouts')
        StudentModule(student=user, course_id=course_key, module_state_key=handouts_usage_key).save()
        CourseAggregatedMetaData(id=course_key, total_assessments=10, total_modules=20).save()

        structure_json = '{"test": true}'
        course_structure, created = CourseStructure.objects.get_or_create(
            course_id=course_key,
            defaults={'structure_json': structure_json}
        )
        if not created:
            course_structure.structure_json = structure_json
            course_structure.save()

        CourseOverview.get_from_id(course_key)

    def assert_reference_data_exists(self, course_id):
        """
        Asserts course reference data exists in DB
        """
        self.assertEqual(1, CourseGroupRelationship.objects.filter(course_id=course_id).count())
        self.assertEqual(1, StudentGradebook.objects.filter(course_id=course_id).count())
        self.assertEqual(1, CourseEnrollment.objects.filter(course_id=course_id).count())
        self.assertEqual(1, CourseAccessRole.objects.filter(course_id=course_id).count())
        self.assertEqual(1, StudentModule.objects.filter(course_id=course_id).count())
        self.assertEqual(1, CourseAggregatedMetaData.objects.filter(id=course_id).count())
        self.assertEqual(1, CourseOverview.objects.filter(id=course_id).count())
        self.assertEqual(1, CourseStructure.objects.filter(course_id=course_id).count())

        course = modulestore().get_course(course_id)
        self.assertIsNotNone(course)
        self.assertEqual(str(course_id), str(course.id))

    def assert_reference_data_deleted(self, course_id):
        """
        Asserts course reference data deleted in DB
        """
        self.assertEqual(0, CourseGroupRelationship.objects.filter(course_id=course_id).count())
        self.assertEqual(0, StudentGradebook.objects.filter(course_id=course_id).count())
        self.assertEqual(0, Aggregator.objects.filter(course_key=course_id).count())
        self.assertEqual(0, CourseEnrollment.objects.filter(course_id=course_id).count())
        self.assertEqual(0, CourseAccessRole.objects.filter(course_id=course_id).count())
        self.assertEqual(0, StudentModule.objects.filter(course_id=course_id).count())
        self.assertEqual(0, CourseAggregatedMetaData.objects.filter(id=course_id).count())
        self.assertEqual(0, CourseOverview.objects.filter(id=course_id).count())
        self.assertEqual(0, CourseStructure.objects.filter(course_id=course_id).count())

        course = modulestore().get_course(course_id)
        self.assertIsNone(course)

    def setup_course_data(self, number_of_courses=1, days_ago=60):
        """
        Creates courses and reference data to test and return list of course ids created
        """
        course_ids = []
        past_datetime = datetime.now(pytz.UTC) + timedelta(days=-days_ago)
        with freeze_time(past_datetime):
            while len(course_ids) < number_of_courses:
                course = BulkCourseDeleteTests.create_course()
                BulkCourseDeleteTests.create_course_reference_data(course)
                course_ids.append(course.id)
        return course_ids

    def test_course_bulk_delete(self):
        """
        Test bulk course deletion
        """
        # Set up courses and data to be deleted
        course_ids = self.setup_course_data(number_of_courses=4)

        # assert data exists
        for course_id in course_ids:
            self.assert_reference_data_exists(course_id)

        with mock.patch(self.YESNO_PATCH_LOCATION) as patched_yes_no:
            patched_yes_no.return_value = True
            call_command('bulk_delete_courses_with_reference_data', age=60)

        # assert data deleted
        for course_id in course_ids:
            self.assert_reference_data_deleted(course_id)

    def test_course_bulk_delete_with_no_prompt(self):
        """
        Test bulk course deletion when user opt to type `No` when prompted
        """
        # Set up courses and data to be deleted
        course_ids = self.setup_course_data()

        with mock.patch(self.YESNO_PATCH_LOCATION) as patched_yes_no:
            patched_yes_no.return_value = False
            call_command('bulk_delete_courses_with_reference_data', age=60)

        # assert data still exists
        for course_id in course_ids:
            self.assert_reference_data_exists(course_id)

    def test_course_bulk_delete_without_age(self):
        """
        Test bulk course deletion when age option is not given
        """
        # Set up courses and data to be deleted
        course_ids = self.setup_course_data()

        with self.assertRaises(CommandError):
            call_command('bulk_delete_courses_with_reference_data')

        # assert data still exists
        for course_id in course_ids:
            self.assert_reference_data_exists(course_id)

    def test_course_bulk_delete_with_non_int_age(self):
        """
        Test bulk course deletion when age option is not an integer
        """
        # Set up courses and data to be deleted
        course_ids = self.setup_course_data()

        with self.assertRaises(ValueError):
            call_command('bulk_delete_courses_with_reference_data', age='junk')

        # assert data still exists
        for course_id in course_ids:
            self.assert_reference_data_exists(course_id)

    def test_course_bulk_delete_with_no_edited_on(self):
        """
        Test bulk course deletion when course has no edited_on attribute
        """
        # Set up courses and data to be deleted
        course_ids = self.setup_course_data(number_of_courses=2, days_ago=90)

        with mock.patch(self.YESNO_PATCH_LOCATION) as patched_yes_no:
            patched_yes_no.return_value = True
            with mock.patch.object(
                CourseDescriptor, 'edited_on', create=True, new_callable=PropertyMock
            ) as mocked_edited_on:
                mocked_edited_on.return_value = None
                call_command('bulk_delete_courses_with_reference_data', age=60)

        # assert data still exists
        for course_id in course_ids:
            self.assert_reference_data_exists(course_id)
