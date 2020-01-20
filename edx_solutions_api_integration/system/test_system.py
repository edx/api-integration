# pylint: disable=E1103

"""
Tests for systems module
"""
import uuid

from django.core.cache import cache
from django.test import TestCase
from edx_solutions_api_integration.test_utils import APIClientMixin


class SystemApiTests(TestCase, APIClientMixin):
    """ Test suite for base API views """

    def setUp(self):  # pylint: disable=E7601
        self.base_system_uri = '/api/server/system'
        self.test_username = str(uuid.uuid4())
        self.test_password = str(uuid.uuid4())
        self.test_email = str(uuid.uuid4()) + '@test.org'
        self.test_group_name = str(uuid.uuid4())
        cache.clear()

    def test_system_detail_get(self):
        """ Ensure the system returns base data about the system """
        response = self.do_get(self.base_system_uri)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['uri'])
        self.assertIn(self.base_system_uri, response.data['uri'])
        self.assertIsNotNone(response.data['documentation'])
        self.assertGreater(len(response.data['documentation']), 0)
        self.assertIsNotNone(response.data['name'])
        self.assertGreater(len(response.data['name']), 0)
        self.assertIsNotNone(response.data['description'])
        self.assertGreater(len(response.data['description']), 0)

    def test_system_detail_api_get(self):
        """ Ensure the system returns base data about the API """
        test_uri = '/api/server/'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.data['uri'])
        self.assertIn(test_uri, response.data['uri'])
        self.assertGreater(len(response.data['csrf_token']), 0)
        self.assertIsNotNone(response.data['documentation'])
        self.assertGreater(len(response.data['documentation']), 0)
        self.assertIsNotNone(response.data['name'])
        self.assertGreater(len(response.data['name']), 0)
        self.assertIsNotNone(response.data['description'])
        self.assertGreater(len(response.data['description']), 0)
        self.assertIsNotNone(response.data['resources'])
