""" This module has utility methods to be used in tests. """
import json
import uuid
from datetime import datetime, timedelta
from functools import wraps
from io import BytesIO

import mock
from lms.djangoapps.courseware import module_render
from lms.djangoapps.courseware.model_data import FieldDataCache
from django.conf import settings
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import Client
from django.utils.http import urlencode
from gradebook.signals import on_course_grade_changed
from lms.djangoapps.grades.signals.signals import PROBLEM_WEIGHTED_SCORE_CHANGED
from oauth2_provider import models as dot_models
from PIL import Image
from student.tests.factories import UserFactory
from util.db import OuterAtomic
from xmodule.modulestore.django import SignalHandler
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory


def get_temporary_image():
    io = BytesIO()
    size = (200, 200)
    color = (255, 0, 0, 0)
    image = Image.new("RGBA", size, color)
    image.save(io, format='PNG')
    image_file = InMemoryUploadedFile(io, None, 'temp.png', 'image/png', io.getbuffer().nbytes  , None)
    image_file.seek(0)
    return image_file


def get_non_atomic_database_settings(db_alias='default'):
    """
    returns non atomic database settings
    """
    databases = settings.DATABASES.copy()
    databases[db_alias]['ATOMIC_REQUESTS'] = False
    return databases


def make_non_atomic(*args):
    """
    Disables outer atomic restriction in testcase
    """

    def _wrap(func):
        @wraps(func)
        def _wrapped_func(*args, **kwargs):
            OuterAtomic.atomic_for_testcase_calls = number_of_calls
            func(*args, **kwargs)

        return _wrapped_func

    if len(args) == 1 and callable(args[0]):
        number_of_calls = 100
        return _wrap(args[0])
    else:
        number_of_calls = args[0]
        return _wrap


class CourseGradingMixin:
    """
    Mixin class to setup a course with grading to be used in tests
    """

    def setup_course_with_grading(self, start=None, end=None):
        grading_course = CourseFactory.create(
            start=start,
            end=end,
            org='gradeX',
            run='GRAD1',
            display_name="Test Grading Course",
            grading_policy={
                "GRADER": [
                    {
                        "type": "Homework",
                        "min_count": 1,
                        "drop_count": 0,
                        "short_label": "HW",
                        "weight": 0.5
                    },
                    {
                        "type": "Midterm Exam",
                        "min_count": 1,
                        "drop_count": 0,
                        "short_label": "ME",
                        "weight": 0.5
                    },
                ],
                "GRADE_CUTOFFS": {
                    'A': .9,
                    'B': .33
                }
            },
        )

        test_data = '<html>{}</html>'.format(str(uuid.uuid4()))
        chapter1 = ItemFactory.create(
            category="chapter",
            parent_location=grading_course.location,
            display_name="Chapter 1",
        )
        chapter2 = ItemFactory.create(
            category="chapter",
            parent_location=grading_course.location,
            display_name="Chapter 2",
        )
        ItemFactory.create(
            category="sequential",
            parent_location=chapter1.location,
            display_name="Sequence 1",
        )
        sequential2 = ItemFactory.create(
            category="sequential",
            parent_location=chapter2.location,
            display_name="Sequence 2",
            graded=True,
            metadata={'rerandomize': 'always', 'graded': True, 'format': "Homework"},
        )
        vertical1 = ItemFactory.create(
            category="vertical",
            parent_location=sequential2.location,
            display_name="Vertical 1",
        )
        sequential3 = ItemFactory.create(
            category="sequential",
            parent_location=chapter2.location,
            display_name="Sequence 3",
            graded=True,
            metadata={'rerandomize': 'always', 'graded': True, 'format': "Midterm Exam"},
        )
        vertical2 = ItemFactory.create(
            category="vertical",
            parent_location=sequential3.location,
            display_name="Vertical 2",
        )
        item = ItemFactory.create(
            parent_location=vertical1.location,
            category='mentoring',
            display_name="test mentoring homework",

        )
        item2 = ItemFactory.create(
            parent_location=vertical2.location,
            category='mentoring',
            display_name="test mentoring midterm",
        )

        grading_course = self.store.get_course(grading_course.id)
        setattr(grading_course, 'homework_assignment', item)
        setattr(grading_course, 'midterm_assignment', item2)
        return grading_course

    def get_module_for_user(self, user, course, problem):
        """Helper function to get useful module at self.location in self.course_id for user"""
        mock_request = mock.MagicMock()
        mock_request.user = user
        field_data_cache = FieldDataCache.cache_for_descriptor_descendents(
            course.id, user, course, depth=2)
        module = module_render.get_module(  # pylint: disable=protected-access
            user,
            mock_request,
            problem.location,
            field_data_cache,
        )
        return module


class APIClientMixin(Client):
    """
    Customized client having edx api key prepended with each request
    """
    TEST_API_KEY = settings.EDX_API_KEY

    def do_post_multipart(self, uri, data, secure=True):
        """Submit an HTTP POST Multipart request"""
        headers = {
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        extra = {}
        if secure:
            extra['wsgi.url_scheme'] = 'https'
            extra['SERVER_PORT'] = 443

        return self.client.post(
            uri, headers=headers, data=data, **extra
        )

    def do_post(self, uri, data, secure=True):
        """Submit an HTTP POST request"""
        headers = {
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        json_data = json.dumps(data)
        extra = {}
        if secure:
            extra['wsgi.url_scheme'] = 'https'
            extra['SERVER_PORT'] = 443

        return self.client.post(
            uri, headers=headers, content_type='application/json', data=json_data, **extra
        )

    def do_put(self, uri, data):
        """Submit an HTTP PUT request"""
        headers = {
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        json_data = json.dumps(data)

        return self.client.put(
            uri, headers=headers, content_type='application/json', data=json_data)

    def do_patch(self, uri, data):
        """Submit an HTTP PATCH request"""
        headers = {
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        json_data = json.dumps(data)

        return self.client.patch(
            uri, headers=headers, content_type='application/json', data=json_data)

    def do_get(self, uri, secure=True, query_parameters=None):
        """Submit an HTTP GET request"""
        headers = {
            'Content-Type': 'application/json',
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        extra = {}
        if secure:
            extra['wsgi.url_scheme'] = 'https'
            extra['SERVER_PORT'] = 443

        if query_parameters:
            uri += "?" + urlencode(query_parameters)
        return self.client.get(uri, headers=headers, **extra)

    def do_delete(self, uri, data=''):
        """Submit an HTTP DELETE request"""
        headers = {
            'X-Edx-Api-Key': str(self.TEST_API_KEY),
        }
        json_data = json.dumps(data)
        return self.client.delete(uri, content_type='application/json', data=json_data,
                                  headers=headers)


class SignalDisconnectTestMixin:
    """
    Mixin for tests to disable calls to signals.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.connect_signals()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.disconnect_signals()

    @staticmethod
    def connect_signals():
        """
        connects signals defined in solutions apps
        """
        PROBLEM_WEIGHTED_SCORE_CHANGED.connect(on_course_grade_changed)

    @staticmethod
    def disconnect_signals():
        """
        Disconnects signals defined in solutions apps
        """
        PROBLEM_WEIGHTED_SCORE_CHANGED.disconnect(on_course_grade_changed)


class OAuth2TokenMixin:
    """
       Mixin for tests to create bearer token.
    """

    def create_oauth2_token(self, user):
        """
        Create an OAuth2 Access Token for the specified user,
        to test OAuth2-based API authentication
        Returns the token as a string.
        """
        # Use django-oauth-toolkit (DOT) models to create the app and token:
        dot_app = dot_models.Application.objects.create(
            name='test app',
            user=UserFactory.create(),
            client_type='confidential',
            authorization_grant_type='authorization-code',
            redirect_uris='http://none.none'
        )
        dot_access_token = dot_models.AccessToken.objects.create(
            user=user,
            application=dot_app,
            expires=datetime.utcnow() + timedelta(weeks=1),
            scope='read',
            token='s3cur3t0k3n12345678901234567890'
        )
        return dot_access_token.token
