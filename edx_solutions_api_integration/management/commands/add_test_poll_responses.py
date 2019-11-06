"""
Command to generate dummy problem responses xblock poll in a course
./manage.py lms add_test_poll_responses --settings=devstack --course-id=TestX/TS101/2019_T1
"""

import json
import logging
import random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from itertools import product
from optparse import make_option

from courseware.models import StudentModule  # pylint: disable=import-error
from edx_solutions_api_integration.models import APIUser as User
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore import ModuleStoreEnum
from xmodule.modulestore.django import modulestore


# Variables

logger = logging.getLogger(__name__)  # pylint: disable=locally-disabled, invalid-name

# Classes


class Command(BaseCommand):
    """
    Command to generate dummy problem responses xblock poll in a course
    """
    help = 'Generates dummy responses for users enrolled in a course'
    def add_arguments(self, parser):
        parser.add_argument(
            "--course-id",
            dest="course_id",
            help="Course ID",
        ),
        parser.add_argument(
            "--num-responses",
            dest="num_responses",
            default=10000,
            type="int",
            help="Limit generation of responses to this number. Defaults to "
                 "generating 10k responses. Setting this to 0 will generate"
                 "a dummy response for every user in every poll.",
        ),
        parser.add_argument(
            "--batch-size",
            dest="batch_size",
            default=500,
            type="int",
            help="Batch size to use when adding responses to the database.",
        ),
    state_summary = {}

    @staticmethod
    def get_course_blocks(  # pylint: disable=bad-continuation
        store,
        course_key,
        categories,
        revision=ModuleStoreEnum.RevisionOption.published_only
    ):
        """
        Retrieve all XBlocks in the course for a particular category.

        By default, only returns only XBlocks that are published.
        """
        return store.get_items(
            course_key,
            qualifiers={"category": categories},
            revision=revision,
        )

    @staticmethod
    def get_enrolled_students(course_key):
        """
        Given a course_id, returns a QuerySet of all the students
        in the course.
        """
        return User.objects.filter(courseenrollment__course_id=course_key)

    @staticmethod
    def generate_combinations(students, blocks, limit):
        """
        Generates all possible user X block combination that aren't
        already on the database
        """
        # User Cartesian multiplication to find all possible
        # student vs block combinations.
        combinations = product(students, blocks)

        # Remove combinations that already have entries on the answers table
        responses = StudentModule.objects.filter(
            student__in=students,
            module_state_key__in=[block.location for block in blocks]
        )
        # Compile list of combinations to be skipped and map course run info.
        # The casting to str() is needed to avoid the need to call
        # map_to_course on every location, which could lead to a query explosion
        skip_combinations = [
            (item.student, str(item.module_state_key)) for item in responses
        ]

        count = 0
        # Returns generator of combinations that aren't already stored
        for student, block in combinations:
            if count >= limit:
                break
            if (student, str(block.location)) not in skip_combinations:
                count += 1
                yield student, block

    @staticmethod
    def generate_dummy_submission(student, block, course_key):
        """
        Generates a random answers for a specified user and block

        The currently supported blocks are poll and survey
        """
        location = block.location
        answer = {}

        if block.category == 'poll':
            # Possible answers come in this format:
            # ('G', {'img': None, 'img_alt': None, 'label': 'Green'})
            # and we only need the 'G' bit for saving the answer
            possible_answers = [answer for answer, _ in block.answers]
            # Answer format for StudentModule model:
            # {"submissions_count": 1, "choice": "R"}
            choice = random.choice(possible_answers)
            answer = {
                "submissions_count": 1,
                "choice": choice,
            }
        elif block.category == 'survey':
            # Questions come in this format:
            # ('enjoy', {'img': None, 'img_alt': None, 'label': 'Are you enjoying the course?'})
            # and we only need the 'enjoy' part
            questions = [question for question, _ in block.questions]
            # This happens similarly with answers
            possible_answers = [answer for answer, _ in block.answers]
            # Answer format for StudentModule model:
            # {"submissions_count": 1, "choices": {"enjoy": "Y", "learn": "Y", "recommend": "N"}}
            choices = {key: random.choice(possible_answers) for key in questions}
            answer = {
                "submissions_count": 1,
                "choices": choices,
            }

        return StudentModule(
            module_type='problem',
            module_state_key=location,
            student=student,
            course_id=course_key,
            state=json.dumps(answer),
        )

    def handle(self, *args, **options):
        """
        Retrieves all XBlocks for a course and generates dummy reponses for
        all users limited to --limit.

        To use this command to generate answers for polls and suveys, you'll
        need to create a course, add polls/surveys and users to it.
        """
        if not options.get('course_id'):
            raise CommandError("add_test_poll_responses command requires the parameter --course-id")

        store = modulestore()
        course_key = CourseKey.from_string(options['course_id'])

        # Get data from store and models
        blocks = self.get_course_blocks(store, course_key, ['poll', 'survey'])
        # Filter users that and in course
        students = self.get_enrolled_students(course_key)
        logger.info("Found %i users on course %s.", len(students), course_key)

        submissions = []
        # iterate over every combination that doesn't already exists on db
        for [student, block] in self.generate_combinations(students, blocks, options['num_responses']):
            submissions.append(self.generate_dummy_submission(
                student,
                block,
                course_key
            ))

        if submissions:
            logger.info("Generated %i submissions...", len(submissions))
            # Generate submissions
            StudentModule.objects.bulk_create(submissions, batch_size=options['batch_size'])
            # TODO: Update state summary on XModuleUserStateSummaryField
            logger.info("Poll and survey response generation completed successfully.")
        else:
            logger.info("No new submissions were generated.")
