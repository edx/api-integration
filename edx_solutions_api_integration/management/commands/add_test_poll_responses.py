"""
Management command to migrate profile images from s3 bucket to open edx profile images storage
./manage.py lms migrate_profile_images  --settings=aws --aws-access-key="x" --aws-access-secret="x" --bucket-name="b"
"""

import logging
import datetime
import urllib2 as urllib
import io

from django.db import connection
import random
from itertools import product
from collections import namedtuple

from optparse import make_option
from django.core.management.base import BaseCommand, CommandError

from edx_solutions_api_integration.models import APIUser as User
from course_blocks.api import get_course_blocks
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore
from xmodule.modulestore import ModuleStoreEnum

from django.contrib.auth.models import User
from student.models import UserProfile
from courseware.models import StudentModule

from django.db import transaction


# Variables

log = logging.getLogger(__name__)

# Classes


class Command(BaseCommand):
    """
    Command to generate dummy problem responses xblock poll in a course
    """
    help = 'Generates dummy responses for users enrolled in a course'
    option_list = BaseCommand.option_list + (
        make_option(
            "--course-id",
            dest="course_id",
            help="Course ID",
        ),
        make_option(
            "--num-responses",
            dest="num_responses",
            default=10000,
            type="int",
            help="Limit generation of responses to this number. Defaults to "
                 "generating 10k responses. Setting this to 0 will generate"
                 "a dummy response for every user in every poll.",
        ),
    )

    @staticmethod
    def get_course_blocks(
        store,
        course_key,
        categories,
        revision=ModuleStoreEnum.RevisionOption.published_only
    ):
        """
        Retrieve all XBlocks in the course for a particular category.

        By default, only returns only XBlocks that are published.
        """
        return [
            block for block in store.get_items(
                course_key,
                qualifiers={"category": categories},
                revision=revision,
            )
        ]

    @staticmethod
    def get_enrolled_students(course_key):
        """
        Given a course_id, returns a QuerySet of all the active students
        in the course.
        """
        return User.objects.filter(
            courseenrollment__course_id=course_key,
            courseenrollment__is_active=True,
        )

    @staticmethod
    def generate_combinations(students, blocks, course_key):
        # User Cartesian multiplication to find all possible
        # student vs block combinations. This needs to be a list!
        combinations = [item for item in product(students, blocks)]

        # Remove combinations that already have entries on the answers table
        responses = StudentModule.objects.filter(
            student__in=students,
            module_state_key__in=[block.location for block in blocks]
        )
        # Compile list of combinations to be skipped and map course run info.
        # The casting to str() is needed to avoid the need to call
        # map_to_course on every location, which could lead to a query explosion
        skip_combinations = [
            [item.student, str(item.module_state_key)] for item in responses
        ]

        # Returns combinations that aren't already stored
        return [c for c in combinations if [c[0], str(c[1].location)] not in skip_combinations]

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
            possible_answers = [a[0] for a in block.answers]
            # Answer format for StudentModule model:
            # {"submissions_count": 1, "choice": "R"}
            answer = {
                "submissions_count": 1,
                "choice": random.choice(possible_answers),
            }
        elif block.category == 'survey':
            # Questions come in this format:
            # ('enjoy', {'img': None, 'img_alt': None, 'label': 'Are you enjoying the course?'})
            # and we only need the 'enjoy' part
            questions = [q[0] for q in block.questions]
            # This happens similarly with answers
            possible_answers = [a[0] for a in block.answers]

            # Answer format for StudentModule model:
            # {"submissions_count": 1, "choices": {"enjoy": "Y", "learn": "Y", "recommend": "N"}}
            answer = {
                "submissions_count": 1,
                "choices": {key: random.choice(possible_answers) for key in questions},
            }

        return StudentModule(
            module_type='problem',
            module_state_key=location,
            student=student,
            course_id=course_key,
            state=answer,
        )

    def handle(self, *args, **options):
        """
        Retrieves all XBlocks for a course and generates dummy reponses for
        all users limited to --limit.

        To use this command to generate answers for polls and suveys, you'll
        need to create a course, add polls/surveys and users to it.
        """
        base = len(connection.queries)
        if not options.get('course_id'):
            raise CommandError("add_test_poll_responses command requires the parameter --course-id")

        store = modulestore()
        course_key = CourseKey.from_string(options['course_id'])

        # Get data from store and models
        courses = store.get_course(course_key)
        blocks = self.get_course_blocks(store, course_key, ['poll', 'survey'])
        students = self.get_enrolled_students(course_key)

        # Create product between users and polls
        combinations = self.generate_combinations(students, blocks, course_key)

        submissions = []
        # Zip through items, limiting range with num_responses
        for index, [student, block] in zip(range(options['num_responses']), combinations):
            submissions.append(self.generate_dummy_submission(student, block, course_key))

        if submissions:
            StudentModule.objects.bulk_create(submissions)
        print(len(connection.queries)-base)
