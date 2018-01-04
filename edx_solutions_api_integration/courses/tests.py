# pylint: disable=E1103
"""
Run these tests @ Devstack:
    paver test_system -s lms --fasttest
        --fail_fast --verbose --test_id=lms/djangoapps/edx_solutions_api_integration/courses
"""
from datetime import datetime, timedelta
import ddt
import json
import uuid
import pytz
from django.utils import timezone
import mock
from random import randint
from urllib import urlencode
from freezegun import freeze_time
from dateutil.relativedelta import relativedelta

from requests.exceptions import ConnectionError
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.urlresolvers import reverse
from django.core.exceptions import ObjectDoesNotExist
from django.test.utils import override_settings
from django.test.client import Client
from rest_framework import status

from courseware import module_render
from courseware.model_data import FieldDataCache
from django_comment_common.models import Role, FORUM_ROLE_MODERATOR
from gradebook.models import StudentGradebook
from progress.models import StudentProgress
from course_metadata.models import CourseAggregatedMetaData, CourseSetting
from social_engagement.models import StudentSocialEngagementScore
from instructor.access import allow_access
from edx_solutions_organizations.models import Organization
from edx_solutions_projects.models import Workgroup, Project
from student.tests.factories import UserFactory, CourseEnrollmentFactory, GroupFactory
from student.models import CourseEnrollment
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.tests.factories import CourseFactory, ItemFactory
from xmodule.modulestore.tests.django_utils import (
    SharedModuleStoreTestCase,
    TEST_DATA_SPLIT_MODULESTORE,
    mixed_store_config
)
from xmodule.modulestore import ModuleStoreEnum
from openedx.core.djangolib.testing.utils import CacheIsolationTestCase
from edx_solutions_api_integration.courseware_access import get_course_key, get_course_descriptor
from edx_solutions_api_integration.test_utils import (
    APIClientMixin,
    SignalDisconnectTestMixin,
    CourseGradingMixin,
    make_non_atomic,
)
from edx_solutions_api_integration.utils import strip_whitespaces_and_newlines
from .content import TEST_COURSE_OVERVIEW_CONTENT, TEST_COURSE_UPDATES_CONTENT, TEST_COURSE_UPDATES_CONTENT_LEGACY
from .content import TEST_STATIC_TAB1_CONTENT, TEST_STATIC_TAB2_CONTENT

MODULESTORE_CONFIG = mixed_store_config(settings.COMMON_TEST_DATA_ROOT, {})
USER_COUNT = 6


def _fake_get_course_social_stats(course_id, end_date=None):
    """ Fake get_course_social_stats method """
    if end_date:
        raise Exception("Expected None for end_date parameter")

    course_key = get_course_key(course_id)
    users = CourseEnrollment.objects.users_enrolled_in(course_key)
    return {str(user.id): {user.first_name: user.last_name} for user in users}


def _fake_get_course_social_stats_date_expected(course_id, end_date=None):  # pylint: disable=C0103,W0613
    """ Fake get_course_social_stats_date_expected method """
    if not end_date:
        raise Exception("Expected non-None end_date parameter")
    return {
        '2': {'two': 'two-two'},
        '3': {'three': 'three-three-three'}
    }


def _fake_get_course_thread_stats(course_id):  # pylint: disable=W0613
    """ Fake get_course_thread_stats method """
    return {
        'num_threads': 5,
        'num_active_threads': 3
    }


def _fake_get_course(request, user, course_id, depth=0, load_content=False):
    course_descriptor = None
    course_content = None
    course_key = get_course_key(course_id)
    if course_key:
        course_descriptor = get_course_descriptor(course_key, depth)
    return course_descriptor, course_key, course_content


def _fake_get_service_unavailability(course_id, end_date=None):
    raise ConnectionError


@mock.patch("edx_solutions_api_integration.courses.views.get_course_thread_stats", _fake_get_course_thread_stats)
@mock.patch.dict("django.conf.settings.FEATURES", {'ENFORCE_PASSWORD_POLICY': False,
                                                   'ADVANCED_SECURITY': False,
                                                   'PREVENT_CONCURRENT_LOGINS': False})
@ddt.ddt
class CoursesApiTests(
    SignalDisconnectTestMixin, SharedModuleStoreTestCase, CacheIsolationTestCase, APIClientMixin, CourseGradingMixin
):
    """ Test suite for Courses API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super(CoursesApiTests, cls).setUpClass()
        cls.test_server_prefix = 'https://testserver'
        cls.base_courses_uri = '/api/server/courses'
        cls.base_groups_uri = '/api/server/groups'
        cls.base_users_uri = '/api/server/users'
        cls.base_organizations_uri = '/api/server/organizations/'
        cls.base_projects_uri = '/api/server/projects/'
        cls.base_workgroups_uri = '/api/server/workgroups/'
        cls.test_group_name = 'Alpha Group'
        cls.attempts = 3

        cls.course_start_date = timezone.now() + relativedelta(days=-1)
        cls.course_end_date = timezone.now() + relativedelta(days=60)
        cls.course = CourseFactory.create(
            start=cls.course_start_date,
            end=cls.course_end_date,
        )
        cls.test_data = '<html>{}</html>'.format(str(uuid.uuid4()))

        cls.chapter = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            due=cls.course_end_date,
            display_name="Overview Chapter",
        )

        cls.course_project = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            display_name="Group Project",
        )

        cls.course_project2 = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            display_name="Group Project2",
        )

        cls.course_content2 = ItemFactory.create(
            category="sequential",
            parent_location=cls.chapter.location,
            display_name="Sequential",
        )

        cls.content_child2 = ItemFactory.create(
            category="vertical",
            parent_location=cls.course_content2.location,
            display_name="Vertical Sequence",
        )

        cls.course_content = ItemFactory.create(
            category="videosequence",
            parent_location=cls.content_child2.location,
            display_name="Video_Sequence",
        )

        cls.content_child = ItemFactory.create(
            category="video",
            parent_location=cls.course_content.location,
            data=cls.test_data,
            display_name="Video",
        )

        cls.content_subchild = ItemFactory.create(
            category="video",
            parent_location=cls.content_child2.location,
            data=cls.test_data,
            display_name="Child Video",
        )

        cls.overview = ItemFactory.create(
            category="about",
            parent_location=cls.course.location,
            data=TEST_COURSE_OVERVIEW_CONTENT,
            display_name="overview about",
        )

        cls.updates = ItemFactory.create(
            category="course_info",
            parent_location=cls.course.location,
            data=cls.add_course_updates(),
            display_name="updates",
        )

        cls.static_tab1 = ItemFactory.create(
            category="static_tab",
            parent_location=cls.course.location,
            data=TEST_STATIC_TAB1_CONTENT,
            display_name="syllabus",
            name="Static+Tab",
        )

        cls.static_tab2 = ItemFactory.create(
            category="static_tab",
            parent_location=cls.course.location,
            data=TEST_STATIC_TAB2_CONTENT,
            display_name="readings",
        )

        cls.sub_section = ItemFactory.create(
            parent_location=cls.chapter.location,
            category="sequential",
            display_name=u"test subsection",
        )

        cls.unit = ItemFactory.create(
            parent_location=cls.sub_section.location,
            category="vertical",
            metadata={'graded': True, 'format': 'Homework'},
            display_name=u"test unit",
        )

        cls.dash_unit = ItemFactory.create(
            parent_location=cls.sub_section.location,
            category="vertical",
            metadata={'graded': True, 'format': 'Homework'},
            display_name=u"test unit 2",
        )

        cls.empty_course = CourseFactory.create(
            start=cls.course_start_date,
            end=cls.course_end_date,
            org="MTD",
        )

        cls.users = [UserFactory.create(username="testuser" + str(__), profile='test') for __ in xrange(USER_COUNT)]

        for user in cls.users:
            CourseEnrollmentFactory.create(user=user, course_id=cls.course.id)
            user_profile = user.profile
            user_profile.title = 'Software Engineer {}'.format(user.id)
            user_profile.city = 'Cambridge'
            user_profile.save()

        cls.test_course_id = unicode(cls.course.id)
        cls.languages = ["it", "de-at", "es", "pt-br"]
        cls.test_bogus_course_id = 'foo/bar/baz'
        cls.test_course_name = cls.course.display_name
        cls.test_course_number = cls.course.number
        cls.test_course_org = cls.course.org
        cls.test_chapter_id = unicode(cls.chapter.scope_ids.usage_id)
        cls.test_course_content_id = unicode(cls.course_content.scope_ids.usage_id)
        cls.test_bogus_content_id = "i4x://foo/bar/baz/12345"
        cls.test_content_child_id = unicode(cls.content_child.scope_ids.usage_id)
        cls.base_course_content_uri = '{}/{}/content'.format(cls.base_courses_uri, cls.test_course_id)
        cls.base_chapters_uri = cls.base_course_content_uri + '?type=chapter'

        Role.objects.get_or_create(
            name=FORUM_ROLE_MODERATOR,
            course_id=cls.course.id)

    @classmethod
    def add_course_updates(cls):
        updates = {
            u'items': [
                {u'date': u'April 15, 2014',
                 u'content': u'<p>A perfectly</p><p>formatted piece</p><p>of HTML</p>',
                 u'status': u'visible',
                 u'id': 4},
                {u'date': u'April 16, 2014',
                 u'content': u'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag'
                             u'<p>one more</p>',
                 u'status': u'visible',
                 u'id': 3},
                {u'date': u'April 17, 2014',
                 u'content': u'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag',
                 u'status': u'visible',
                 u'id': 2},
                {u'date': u'April 18, 2014',
                 u'content': u'This does not have a paragraph tag around it',
                 u'status': u'visible',
                 u'id': 1},
            ],
            u'data': u''}
        return updates

    def login(self):
        self.user = UserFactory.create(username='test', email='test@edx.org', password='test_password')
        self.client = Client()
        self.client.login(username=self.user.username, password='test_password')

    def _find_item_by_class(self, items, class_name):
        """Helper method to match a single matching item"""
        for item in items:
            if item['class'] == class_name:
                return item
        return None

    def _setup_courses_completions_leaders(self):
        """Setup for courses completions leaders"""

        course = CourseFactory.create(
            number='4033',
            name='leaders_by_completions',
            start=datetime(2014, 9, 16, 14, 30, tzinfo=pytz.UTC),
            end=datetime(2015, 1, 16, 14, 30, tzinfo=pytz.UTC),
        )

        chapter = ItemFactory.create(
            category="chapter",
            parent_location=course.location,
            due=datetime(2014, 5, 16, 14, 30, tzinfo=pytz.UTC),
            display_name="Overview chapter 2",
        )

        sub_section = ItemFactory.create(
            parent_location=chapter.location,
            category="sequential",
            display_name=u"test subsection",
        )
        unit = ItemFactory.create(
            parent_location=sub_section.location,
            category="vertical",
            metadata={'graded': True, 'format': 'Homework'},
            display_name=u"test unit",
        )

        # create 5 users
        user_count = 5
        users = [UserFactory.create(username="testuser_cctest" + str(__), profile='test') for __ in xrange(user_count)]
        groups = GroupFactory.create_batch(2)

        for i, user in enumerate(users):
            user.groups.add(groups[i % 2])

        users[0].groups.add(groups[1])

        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=course.id)
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        test_course_id = unicode(course.id)
        completion_uri = '{}/{}/completions/'.format(self.base_courses_uri, test_course_id)
        leaders_uri = '{}/{}/metrics/completions/leaders/'.format(self.base_courses_uri, test_course_id)
        # Make last user as observer to make sure that data is being filtered out
        allow_access(course, users[user_count - 1], 'observer')

        contents = []
        for i in xrange(1, 26):
            local_content_name = 'Video_Sequence_setup{}'.format(i)
            local_content = ItemFactory.create(
                category="videosequence",
                parent_location=unit.location,
                display_name=local_content_name
            )
            contents.append(local_content)
            if i < 3:
                user_id = users[0].id
            elif i < 10:
                user_id = users[1].id
            elif i < 17:
                user_id = users[2].id
            else:
                user_id = users[3].id

            content_id = unicode(local_content.scope_ids.usage_id)
            completions_data = {'content_id': content_id, 'user_id': user_id}
            response = self.do_post(completion_uri, completions_data)
            self.assertEqual(response.status_code, 201)

            # observer should complete everything, so we can assert that it is filtered out
            response = self.do_post(completion_uri, {
                'content_id': content_id, 'user_id': users[user_count - 1].id
            })
            self.assertEqual(response.status_code, 201)
        return {
            'leaders_uri': leaders_uri,
            'users': users,
            'contents': contents,
            'completion_uri': completion_uri,
            'groups': groups,
            'course': course,
        }

    def test_courses_list_get(self):
        test_uri = self.base_courses_uri + '/'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['results']), 0)
        self.assertIsNotNone(response.data['count'])
        self.assertIsNotNone(response.data['num_pages'])
        matched_course = False
        for course in response.data['results']:
            if matched_course is False and course['id'] == self.test_course_id:
                self.assertEqual(course['name'], self.test_course_name)
                self.assertEqual(course['number'], self.test_course_number)
                self.assertEqual(course['org'], self.test_course_org)
                confirm_uri = self.test_server_prefix + test_uri + course['id']
                self.assertEqual(course['uri'], confirm_uri)
                matched_course = True
        self.assertTrue(matched_course)

    def test_courses_list_get_with_filter(self):
        test_uri = self.base_courses_uri
        courses = [self.test_course_id, unicode(self.empty_course.id)]
        params = {'course_id': ','.join(courses).encode('utf-8')}
        response = self.do_get('{}/?{}'.format(test_uri, urlencode(params)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertIsNotNone(response.data['count'])
        self.assertIsNotNone(response.data['num_pages'])
        courses_in_result = []
        for course in response.data['results']:
            courses_in_result.append(course['id'])
            if course['id'] == self.test_course_id:
                self.assertEqual(course['name'], self.test_course_name)
                self.assertEqual(course['number'], self.test_course_number)
                self.assertEqual(course['org'], self.test_course_org)
                confirm_uri = self.test_server_prefix + test_uri + '/' + course['id']
                self.assertEqual(course['uri'], confirm_uri)
                self.assertIsNotNone(course['course_image_url'])
        self.assertItemsEqual(courses, courses_in_result)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_course_detail_without_date_values(self, store):
        self.login()
        # create a course without any dates.
        with self.store.default_store(store):
            course = CourseFactory.create(default_store=store)
            ItemFactory.create(
                category="chapter",
                parent_location=course.location,
                display_name="Test Chapter",
            )

        test_uri = self.base_courses_uri + '/' + unicode(course.id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['start'], course.start)
        self.assertEqual(response.data['end'], course.end)

    def test_courses_detail_get(self):
        CourseSetting.objects.create(id=self.test_course_id, languages=self.languages)
        self.login()
        test_uri = self.base_courses_uri + '/' + self.test_course_id
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['id'], self.test_course_id)
        self.assertEqual(response.data['name'], self.test_course_name)
        self.assertEqual(
            datetime.strftime(response.data['start'], '%Y-%m-%d %H:%M:%S'),
            datetime.strftime(self.course.start, '%Y-%m-%d %H:%M:%S')
        )
        self.assertEqual(
            datetime.strftime(response.data['end'], '%Y-%m-%d %H:%M:%S'),
            datetime.strftime(self.course.end, '%Y-%m-%d %H:%M:%S')
        )
        self.assertEqual(response.data['number'], self.test_course_number)
        self.assertEqual(response.data['org'], self.test_course_org)
        confirm_uri = self.test_server_prefix + test_uri
        self.assertEqual(response.data['uri'], confirm_uri)

        self.assertIn('languages', response.data)
        for language in response.data['languages']:
            self.assertIn(language, self.languages)

    def test_courses_detail_get_with_child_content(self):
        self.login()
        test_uri = self.base_courses_uri + '/' + self.test_course_id
        response = self.do_get('{}?depth=100'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['id'], self.test_course_id)
        self.assertEqual(response.data['name'], self.test_course_name)
        self.assertEqual(response.data['number'], self.test_course_number)
        self.assertEqual(response.data['org'], self.test_course_org)
        confirm_uri = self.test_server_prefix + test_uri
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertGreater(len(response.data['content']), 0)
        for resource in response.data['resources']:
            response = self.do_get(resource['uri'])
            self.assertEqual(response.status_code, 200)

    def test_courses_detail_get_notfound(self):
        self.login()
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_tree_get(self):
        self.login()
        # query the course tree to quickly get naviation information
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '?depth=2'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['category'], 'course')
        self.assertEqual(response.data['name'], self.course.display_name)
        self.assertEqual(len(response.data['content']), 3)

        chapter = response.data['content'][0]
        self.assertEqual(chapter['category'], 'chapter')
        self.assertEqual(chapter['name'], 'Overview Chapter')
        # we should have 2 children of Overview chapter
        # 2 sequentials named Sequential and test subsection
        self.assertEqual(len(chapter['children']), 2)

        # Make sure both of the children should be a sequential
        sequential = [child for child in chapter['children'] if child['category'] == 'sequential']
        self.assertEqual(len(sequential), 2)

    def test_courses_tree_get_root(self):
        self.login()
        # query the course tree to quickly get naviation information
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '?depth=0'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['category'], 'course')
        self.assertEqual(response.data['name'], self.course.display_name)
        self.assertIn('content', response.data)

    def test_chapter_list_get(self):
        test_uri = self.base_chapters_uri
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        matched_chapter = False
        for chapter in response.data:
            if matched_chapter is False and chapter['id'] == self.test_chapter_id:
                self.assertIsNotNone(chapter['uri'])
                self.assertGreater(len(chapter['uri']), 0)
                confirm_uri = self.test_server_prefix + self.base_course_content_uri + '/' + chapter['id']
                self.assertEqual(chapter['uri'], confirm_uri)
                matched_chapter = True
        self.assertTrue(matched_chapter)

    def test_chapter_detail_get(self):
        test_uri = self.base_course_content_uri + '/' + self.test_chapter_id
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data['id']), 0)
        self.assertEqual(response.data['id'], self.test_chapter_id)
        confirm_uri = self.test_server_prefix + test_uri
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertGreater(len(response.data['children']), 0)

    def test_course_content_list_get(self):
        test_uri = '{}/{}/children'.format(self.base_course_content_uri, self.test_course_content_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        matched_child = False
        for child in response.data:
            if matched_child is False and child['id'] == self.test_content_child_id:
                self.assertIsNotNone(child['uri'])
                self.assertGreater(len(child['uri']), 0)
                confirm_uri = self.test_server_prefix + self.base_course_content_uri + '/' + child['id']
                self.assertEqual(child['uri'], confirm_uri)
                matched_child = True
        self.assertTrue(matched_child)

    def test_course_content_list_get_invalid_course(self):
        test_uri = '{}/{}/content/{}/children'.format(
            self.base_courses_uri,
            self.test_bogus_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_list_get_invalid_content(self):
        test_uri = '{}/{}/content/{}/children'.format(
            self.base_courses_uri, self.test_bogus_course_id, self.test_bogus_content_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_detail_get(self):
        test_uri = self.base_course_content_uri + '/' + self.test_course_content_id
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['id'], self.test_course_content_id)
        confirm_uri = self.test_server_prefix + test_uri
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertGreater(len(response.data['children']), 0)

    def test_course_content_detail_get_with_extra_fields(self):
        test_uri = self.base_course_content_uri + '/' + self.test_course_content_id
        response = self.do_get('{}?include_fields=course_edit_method,edited_by'.format(test_uri))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertIsNotNone(response.data['course_edit_method'])
        self.assertEqual(response.data['edited_by'], ModuleStoreEnum.UserID.test)

    def test_course_content_detail_get_dashed_id(self):
        test_content_id = unicode(self.dash_unit.scope_ids.usage_id)
        test_uri = self.base_course_content_uri + '/' + test_content_id
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['id'], test_content_id)
        confirm_uri = self.test_server_prefix + test_uri
        self.assertEqual(response.data['uri'], confirm_uri)

    def test_course_content_detail_get_course(self):
        test_course_usage_key = unicode(modulestore().make_course_usage_key(self.course.id))
        test_uri = self.base_course_content_uri + '/' + test_course_usage_key
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['id'], self.test_course_id)
        confirm_uri = self.test_server_prefix + self.base_courses_uri + '/' + self.test_course_id
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertGreater(len(response.data['content']), 0)

    def test_course_content_detail_get_notfound(self):
        test_uri = '{}/{}/content/{}'.format(
            self.base_courses_uri, self.test_bogus_course_id, self.test_bogus_content_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_list_get_filtered_children_for_child(self):
        test_uri = self.base_course_content_uri + '/' + self.test_course_content_id + '/children?type=video'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        matched_child = False
        for child in response.data:
            if matched_child is False and child['id'] == self.test_content_child_id:
                confirm_uri = '{}{}/{}'.format(self.test_server_prefix, self.base_course_content_uri, child['id'])
                self.assertEqual(child['uri'], confirm_uri)
                matched_child = True
        self.assertTrue(matched_child)

    def test_course_content_list_get_notfound(self):
        test_uri = '{}{}/children?type=video'.format(self.base_course_content_uri, self.test_bogus_content_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_list_post(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']

        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        confirm_uri = self.test_server_prefix + test_uri + '/' + str(group_id)
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['course_id'], str(self.test_course_id))
        self.assertEqual(response.data['group_id'], str(group_id))

    def test_courses_groups_list_get(self):
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        course_fail_uri = '{}/{}/groups'.format(self.base_courses_uri, 'ed/Open_DemoX/edx_demo_course')
        group_types = ['Programming', 'Programming', 'Calculus']
        for i in range(len(group_types)):
            data_dict = {
                'name': 'Alpha Group {}'.format(i), 'type': group_types[i],
            }
            response = self.do_post(self.base_groups_uri, data_dict)
            group_id = response.data['id']
            data = {'group_id': group_id}
            self.assertEqual(response.status_code, 201)
            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        courses_groups_uri = '{}?type={}'.format(test_uri, 'Programming')
        response = self.do_get(courses_groups_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

        group_type_uri = '{}?type={}'.format(test_uri, 'Calculus')
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        # filter by more than one group type
        group_type_uri = '{}?type={}'.format(test_uri, 'Calculus,Programming')
        response = self.do_get(group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        error_group_type_uri = '{}?type={}'.format(test_uri, 'error_type')
        response = self.do_get(error_group_type_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        response = self.do_get(course_fail_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_list_post_duplicate(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)

    def test_courses_groups_list_post_invalid_course(self):
        test_uri = self.base_courses_uri + '/1239/87/8976/groups'
        data = {'group_id': "98723896"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_list_post_invalid_group(self):
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        data = {'group_id': "98723896"}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_detail_get(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        data = {'group_id': response.data['id']}
        response = self.do_post(test_uri, data)
        test_uri = response.data['uri']
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['uri'], test_uri)
        self.assertEqual(response.data['course_id'], self.test_course_id)
        self.assertEqual(response.data['group_id'], str(group_id))

    def test_courses_groups_detail_get_invalid_resources(self):
        test_uri = '{}/{}/groups/123145'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        test_uri = '{}/{}/groups/123145'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = '{}/{}/groups/{}'.format(self.base_courses_uri, self.test_course_id, response.data['id'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_detail_delete(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        data = {'group_id': response.data['id']}
        response = self.do_post(test_uri, data)
        test_uri = response.data['uri']
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)  # Idempotent
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_groups_detail_delete_invalid_course(self):
        test_uri = '{}/{}/groups/123124'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_courses_groups_detail_delete_invalid_group(self):
        test_uri = '{}/{}/groups/123124'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_courses_groups_detail_get_undefined(self):
        data = {'name': self.test_group_name, 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups/{}'.format(self.base_courses_uri, self.test_course_id, group_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_overview_get_unparsed(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/overview'

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        asset_id = self.test_course_id.split(":")[1]
        self.assertEqual(response.data['overview_html'], self.overview.data.format(asset_id, asset_id)[1:])
        self.assertIn(self.course.course_image, response.data['course_image_url'])

    def test_courses_overview_get_parsed(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/overview?parse=true'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertIn(self.course.course_image, response.data['course_image_url'])
        sections = response.data['sections']
        self.assertEqual(len(sections), 4)
        self.assertIsNotNone(self._find_item_by_class(sections, 'about'))
        self.assertIsNotNone(self._find_item_by_class(sections, 'prerequisites'))
        self.assertIsNotNone(self._find_item_by_class(sections, 'course-staff'))
        self.assertIsNotNone(self._find_item_by_class(sections, 'faq'))
        course_staff = self._find_item_by_class(sections, 'course-staff')
        staff = course_staff['articles']
        self.assertEqual(len(staff), 2)
        self.assertEqual(staff[0]['class'], "teacher")
        self.assertEqual(staff[0]['name'], "Staff Member #1")
        self.assertIn("images_placeholder-faculty.png", staff[0]['image_src'])
        self.assertIn("<p>Biography of instructor/staff member #1</p>", staff[0]['bio'])
        self.assertEqual(staff[1]['class'], "teacher")
        self.assertEqual(staff[1]['name'], "Staff Member #2")
        self.assertIn("images_placeholder-faculty.png", staff[1]['image_src'])
        self.assertIn("<p>Biography of instructor/staff member #2</p>", staff[1]['bio'])
        about = self._find_item_by_class(sections, 'about')
        self.assertGreater(len(about['body']), 0)
        prerequisites = self._find_item_by_class(sections, 'prerequisites')
        self.assertGreater(len(prerequisites['body']), 0)
        faq = self._find_item_by_class(sections, 'faq')
        self.assertGreater(len(faq['body']), 0)
        invalid_tab = self._find_item_by_class(sections, 'invalid_tab')
        self.assertFalse(invalid_tab)

    def test_courses_overview_get_invalid_course(self):
        # try a bogus course_id to test failure case
        test_uri = '{}/{}/overview'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_overview_get_invalid_content(self):

        with modulestore().default_store(ModuleStoreEnum.Type.mongo):
            # try a bogus course_id to test failure case
            test_course = CourseFactory.create(org='overviewX', run='test00', number='899')

            test_uri = '{}/{}/overview'.format(self.base_courses_uri, unicode(test_course.id))

            ItemFactory.create(
                category="about",
                parent_location=test_course.location,
                data='',
                display_name="overview",
            )

            response = self.do_get(test_uri)
            self.assertEqual(response.status_code, 404)

    def test_courses_updates_get(self):
        # first try raw without any parsing
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/updates'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['content'], TEST_COURSE_UPDATES_CONTENT)

        # then try parsed
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/updates?parse=True'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

        postings = response.data['postings']
        self.assertEqual(len(postings), 4)
        self.assertEqual(postings[0]['date'], 'April 18, 2014')
        self.assertEqual(postings[0]['content'], 'This does not have a paragraph tag around it')
        self.assertEqual(postings[1]['date'], 'April 17, 2014')
        self.assertEqual(
            postings[1]['content'],
            'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag'
        )
        self.assertEqual(postings[2]['date'], 'April 16, 2014')
        self.assertEqual(
            postings[2]['content'],
            'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag<p>one more</p>'
        )
        self.assertEqual(postings[3]['date'], 'April 15, 2014')
        self.assertEqual(postings[3]['content'], '<p>A perfectly</p><p>formatted piece</p><p>of HTML</p>')

    def test_courses_updates_get_invalid_course(self):
        #try a bogus course_id to test failure case
        test_uri = '{}/{}/updates'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_updates_get_invalid_content(self, store):
        #try a bogus course_id to test failure case
        test_course = CourseFactory.create(default_store=store)
        test_uri = '{}/{}/updates'.format(self.base_courses_uri, unicode(test_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_updates_legacy(self):
        #try a bogus course_id to test failure case
        test_course = CourseFactory.create()
        ItemFactory.create(
            category="course_info",
            parent_location=test_course.location,
            data=TEST_COURSE_UPDATES_CONTENT_LEGACY,
            display_name="updates",
        )
        test_uri = self.base_courses_uri + '/' + unicode(test_course.id) + '/updates'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        self.assertEqual(response.data['content'], TEST_COURSE_UPDATES_CONTENT_LEGACY)

        # then try parsed
        test_uri = self.base_courses_uri + '/' + unicode(test_course.id) + '/updates?parse=True'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

        postings = response.data['postings']
        self.assertEqual(len(postings), 4)
        self.assertEqual(postings[0]['date'], 'April 18, 2014')
        self.assertEqual(strip_whitespaces_and_newlines(postings[0]['content']), 'This is some legacy content')
        self.assertEqual(postings[1]['date'], 'April 17, 2014')
        self.assertEqual(
            strip_whitespaces_and_newlines(postings[1]['content']),
            'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag'
        )
        self.assertEqual(postings[2]['date'], 'April 16, 2014')
        self.assertEqual(
            strip_whitespaces_and_newlines(postings[2]['content']),
            'Some text before paragraph tag<p>This is inside paragraph tag</p>Some text after tag<p>one more</p>'
        )
        self.assertEqual(postings[3]['date'], 'April 15, 2014')
        self.assertEqual(strip_whitespaces_and_newlines(postings[3]['content']), '<p>A perfectly</p><p>formatted piece</p><p>of HTML</p>')

    def test_static_tab_list_get(self):
        test_uri = '{}/{}/static_tabs'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

        tabs = response.data['tabs']
        self.assertEqual(len(tabs), 2)
        self.assertEqual(tabs[0]['id'], u'syllabus')
        self.assertEqual(tabs[1]['id'], u'readings')

        # now try when we get the details on the tabs
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs?detail=true&strip_wrapper_div=false'
        response = self.do_get(test_uri)

        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

        tabs = response.data['tabs']
        self.assertEqual(tabs[0]['id'], u'syllabus')
        self.assertIn(self.static_tab1.data, tabs[0]['content'])
        self.assertEqual(tabs[1]['id'], u'readings')
        self.assertIn(self.static_tab2.data, tabs[1]['content'])

        # get tabs without strip wrapper div
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs?detail=true'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        tabs = response.data['tabs']
        self.assertEqual(self.static_tab1.data, tabs[0]['content'])
        self.assertEqual(self.static_tab2.data, tabs[1]['content'])

        # get syllabus tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tabs[0]['id']
        )
        tab1_content = cache.get(cache_key)
        self.assertIsNotNone(tab1_content)
        self.assertIn(self.static_tab1.data, tab1_content)

        # get readings tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tabs[1]['id']
        )
        tab2_content = cache.get(cache_key)
        self.assertIsNotNone(tab2_content)
        self.assertIn(self.static_tab2.data, tab2_content)

    def test_static_tab_list_get_invalid_course(self):
        #try a bogus course_id to test failure case
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id + '/static_tabs'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_static_tab_detail_get_by_name(self):
        # get course static tab by tab name
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/Static+Tab'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        tab = response.data
        self.assertEqual(tab['id'], u'syllabus')
        self.assertEqual(tab['name'], u'Static+Tab')
        self.assertIn(self.static_tab1.data, tab['content'])

        # now try to get syllabus tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tab['id']
        )
        tab_contents = cache.get(cache_key)
        self.assertTrue(tab_contents is not None)
        self.assertIn(self.static_tab1.data, tab_contents)

    def test_static_tab_detail_get_by_url_slug(self):
        # get course static tab by url_slug
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/readings'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        tab = response.data
        self.assertEqual(tab['id'], u'readings')
        self.assertIn(self.static_tab2.data, tab['content'])

        # now try to get readings tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tab['id']
        )
        tab_contents = cache.get(cache_key)
        self.assertTrue(tab_contents is not None)
        self.assertIn(self.static_tab2.data, tab_contents)

    @override_settings(STATIC_TAB_CONTENTS_CACHE_MAX_SIZE_LIMIT=4000)
    def test_static_tab_content_cache_max_size_limit_hit(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/syllabus'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        tab = response.data
        self.assertEqual(tab['id'], u'syllabus')
        self.assertIn(self.static_tab1.data, tab['content'])
        # try to get syllabus tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tab['id']
        )
        tab_contents = cache.get(cache_key)
        self.assertIsNotNone(tab_contents)
        self.assertIn(self.static_tab1.data, tab_contents)

    @override_settings(STATIC_TAB_CONTENTS_CACHE_MAX_SIZE_LIMIT=200)
    def test_static_tab_content_cache_max_size_limit_miss(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/syllabus'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        tab = response.data
        self.assertEqual(tab['id'], u'syllabus')
        self.assertIn(self.static_tab1.data, tab['content'])
        # try to get syllabus tab contents from cache
        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tab['id']
        )
        tab_contents = cache.get(cache_key)
        # value of tab contents in cache should be None
        self.assertIsNone(tab_contents)

    @override_settings(STATIC_TAB_CONTENTS_CACHE_TTL=60)
    def test_static_tab_content_cache_time_to_live(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/syllabus'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)
        tab = response.data
        self.assertEqual(tab['id'], u'syllabus')
        self.assertIn(self.static_tab1.data, tab['content'])

        cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(
            course_id=self.test_course_id,
            url_slug=tab['id']
        )

        # try to get syllabus tab contents from cache
        tab_contents = cache.get(cache_key)
        self.assertIsNotNone(tab_contents)
        self.assertIn(self.static_tab1.data, tab_contents)

        # now reset the time to 1 minute and 5 seconds from now in future to expire cache
        reset_time = datetime.now(pytz.UTC) + timedelta(seconds=65)
        with freeze_time(reset_time):
            # try to get syllabus tab contents from cache again
            tab_contents = cache.get(cache_key)
            self.assertIsNone(tab_contents)

    def test_static_tab_detail_get_invalid_course(self):
        # try a bogus courseId
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id + '/static_tabs/syllabus'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_static_tab_detail_get_invalid_item(self):
        # try a not found item
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/static_tabs/bogus'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_users_list_get_no_students(self, store):
        course = CourseFactory.create(display_name="TEST COURSE", org='TESTORG', default_store=store)
        test_uri = self.base_courses_uri + '/' + unicode(course.id) + '/users'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

        # assert that there is no enrolled students
        enrollments = response.data['results']
        self.assertEqual(len(enrollments), 0)

    def test_courses_users_list_invalid_course(self):
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id + '/users'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_users_list_post_nonexisting_user_deny(self):
        # enroll a non-existing student
        # first, don't allow non-existing
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        post_data = {
            'email': 'test+pending@tester.com',
            'allow_pending': False,
        }
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 400)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_users_list_post_nonexisting_user_allow(self, store):
        course = CourseFactory.create(display_name="TEST COURSE", org='TESTORG2', default_store=store)
        test_uri = self.base_courses_uri + '/' + unicode(course.id) + '/users'
        post_data = {}
        post_data['email'] = 'test+pending@tester.com'
        post_data['allow_pending'] = True
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 0)

    def test_courses_users_list_post_existing_user(self):
        # create a new user (note, this calls into the /users/ subsystem)
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        test_user_uri = self.base_users_uri
        local_username = "some_test_user" + str(randint(11, 99))
        local_email = "test+notpending@tester.com"
        data = {
            'email': local_email,
            'username': local_username,
            'password': 'fooabr',
            'first_name': 'Joe',
            'last_name': 'Brown'
        }
        response = self.do_post(test_user_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        created_user_id = response.data['id']

        # now enroll this user in the course
        post_data = {}
        post_data['user_id'] = created_user_id
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 201)

        # now unenroll this user and enroll by email
        user_detail_uri = '{}/{}'.format(test_uri, created_user_id)
        response = self.do_delete(user_detail_uri)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        response = self.do_post(test_uri, {'email': local_email})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        response = self.do_get(test_uri)
        self.assertContains(response, local_email)

    def test_courses_users_list_post_invalid_course(self):
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id + '/users'
        post_data = {}
        post_data['email'] = 'test+pending@tester.com'
        post_data['allow_pending'] = True
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 404)

    def test_courses_users_list_post_invalid_user(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        post_data = {}
        post_data['user_id'] = '123123124'
        post_data['allow_pending'] = True
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 404)

    def test_courses_users_list_post_invalid_payload(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        post_data = {}
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 400)

    def test_courses_users_list_get(self):
        # create a new user (note, this calls into the /users/ subsystem)
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        test_user_uri = self.base_users_uri
        local_username = "some_test_user" + str(randint(11, 99))
        local_email = "test+notpending@tester.com"
        data = {
            'email': local_email,
            'username': local_username,
            'password': 'fooabr',
            'first_name': 'Joe',
            'last_name': 'Brown'
        }
        response = self.do_post(test_user_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        created_user_id = response.data['id']
        post_data = {}
        post_data['user_id'] = created_user_id
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    def test_courses_users_list_courses_enrolled(self):
        """ Test courses_enrolled value returned by courses users list api """
        course = CourseFactory.create()
        course2 = CourseFactory.create()
        test_uri = self.base_courses_uri + '/{course_id}/users?additional_fields=courses_enrolled'
        # create a 2 new users
        users = UserFactory.create_batch(2)

        # create course enrollments
        CourseEnrollmentFactory.create(user=users[0], course_id=course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=course2.id)

        # fetch course 1 users
        response = self.do_get(test_uri.format(course_id=unicode(course.id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 2)
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(course.id))

        # fetch user 2
        response = self.do_get(test_uri.format(course_id=unicode(course2.id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)
        self.assertEqual(response.data['results'][0]['courses_enrolled'][0], unicode(course.id))
        self.assertEqual(response.data['results'][0]['courses_enrolled'][1], unicode(course2.id))

    def test_courses_users_list_courses_passed(self):
        """ Test courses_passed value returned by courses users list api """
        course = self.setup_course_with_grading()
        test_uri = self.base_courses_uri + '/{course_id}/users/passed'

        users = UserFactory.create_batch(2)
        CourseEnrollmentFactory.create(user=users[0], course_id=course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=course.id)

        module = self.get_module_for_user(users[0], course, course.homework_assignment)
        grade_dict = {'value': 0.5, 'max_value': 1, 'user_id': users[0].id}
        module.system.publish(module, 'grade', grade_dict)

        response = self.do_get(test_uri.format(course_id=unicode(course.id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

        module = self.get_module_for_user(users[0], course, course.homework_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': users[0].id}
        module.system.publish(module, 'grade', grade_dict)

        module = self.get_module_for_user(users[1], course, course.homework_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': users[1].id}
        module.system.publish(module, 'grade', grade_dict)

        response = self.do_get(test_uri.format(course_id=unicode(course.id)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

        self.assertIn('id', response.data['results'][0])
        self.assertIn('email', response.data['results'][0])
        self.assertIn('username', response.data['results'][0])
        self.assertIn('first_name', response.data['results'][0])
        self.assertIn('last_name', response.data['results'][0])
        self.assertIn('created', response.data['results'][0])
        self.assertIn('is_active', response.data['results'][0])

        self.assertEqual(response.data["results"][0]["id"], users[0].id)
        self.assertEqual(response.data["results"][1]["id"], users[1].id)

    def test_courses_passed_users_list_complete_status(self):
        """ Test complete_status in users passed list of a course """
        course = self.setup_course_with_grading()
        test_uri = self.base_courses_uri + '/{course_id}/users/passed'

        users = UserFactory.create_batch(2)
        CourseEnrollmentFactory.create(user=users[0], course_id=course.id)
        CourseEnrollmentFactory.create(user=users[1], course_id=course.id)

        module = self.get_module_for_user(users[0], course, course.homework_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': users[0].id}
        module.system.publish(module, 'grade', grade_dict)
        module = self.get_module_for_user(users[0], course, course.midterm_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': users[0].id}
        module.system.publish(module, 'grade', grade_dict)
        module = self.get_module_for_user(users[1], course, course.homework_assignment)
        grade_dict = {'value': 1, 'max_value': 1, 'user_id': users[1].id}
        module.system.publish(module, 'grade', grade_dict)

        response = self.do_get(test_uri.format(course_id=unicode(course.id)))
        self.assertEqual(response.status_code, 200)
        self.assertIn('complete_status', response.data['results'][0])
        self.assertEqual(response.data["results"][0]["id"], users[0].id)
        self.assertEqual(response.data["results"][1]["id"], users[1].id)
        self.assertEqual(response.data["results"][0]["complete_status"], True)
        self.assertEqual(response.data["results"][1]["complete_status"], False)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_users_list_get_attributes(self, store):
        """ Test presence of newly added attributes to courses users list api """
        course = CourseFactory.create(
            number='3035',
            name='metrics_grades_leaders',
            start=self.course_start_date,
            end=self.course_end_date,
            default_store=store
        )
        test_uri = self.base_courses_uri + '/{course_id}/users?additional_fields=organizations,grades,roles,progress'
        user = UserFactory.create(username="testuserattributes", profile='test')

        user_grade, user_proforma_grade = 0.9, 0.91
        user_completions, course_total_assesments = 50, 100

        CourseEnrollmentFactory.create(user=user, course_id=course.id)
        CourseAggregatedMetaData.objects.update_or_create(
            id=course.id, defaults={'total_assessments': course_total_assesments}
        )
        section_breakdown = [
            {
                "category": "Homework",
                "percent": 1.0,
                "detail": "Homework 1 - New Subsection - 100% (1/1)",
                "label": "Grade 01"
            }
        ]
        grade_summary = {"section_breakdown": section_breakdown}
        StudentGradebook.objects.create(
            user=user, course_id=course.id,
            grade=user_grade, proforma_grade=user_proforma_grade,
            grade_summary=json.dumps(grade_summary)
        )

        StudentProgress.objects.update_or_create(
            user=user,
            course_id=course.id,
            defaults={
                'completions': user_completions,
            }
        )

        data = {
            'name': 'Test Organization Attributes',
            'display_name': 'Test Org Display Name Attributes',
            'users': [user.id]
        }
        response = self.do_post(self.base_organizations_uri, data)
        self.assertEqual(response.status_code, 201)

        allow_access(course, user, 'instructor')
        allow_access(course, user, 'observer')
        allow_access(self.course, user, 'staff')

        response = self.do_get(test_uri.format(course_id=unicode(course.id)))
        self.assertEqual(response.status_code, 200)
        self.assertIn('id', response.data['results'][0])
        self.assertIn('email', response.data['results'][0])
        self.assertIn('username', response.data['results'][0])
        self.assertIn('last_login', response.data['results'][0])
        self.assertIn('full_name', response.data['results'][0])
        self.assertIn('is_active', response.data['results'][0])
        self.assertIsNotNone(response.data['results'][0]['organizations'])
        self.assertIn('url', response.data['results'][0]['organizations'][0])
        self.assertIn('id', response.data['results'][0]['organizations'][0])
        self.assertIn('name', response.data['results'][0]['organizations'][0])
        self.assertIn('created', response.data['results'][0]['organizations'][0])
        self.assertIn('display_name', response.data['results'][0]['organizations'][0])
        self.assertIn('logo_url', response.data['results'][0]['organizations'][0])
        roles = response.data['results'][0]['roles']
        self.assertIsNotNone(roles)
        self.assertItemsEqual(['instructor', 'observer'], roles)
        self.assertIn('grades', response.data['results'][0])
        self.assertEqual(
            response.data['results'][0]['grades'], {
                'grade': user_grade,
                'proforma_grade': user_proforma_grade,
                'section_breakdown': section_breakdown
            }
        )
        self.assertEqual(response.data['results'][0]['progress'], 50.0)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_users_list_with_fields(self, store):
        """ Tests when fields param is given it should return only those fields """
        course = CourseFactory.create(default_store=store)
        users = UserFactory.create_batch(3)
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=course.id)
            user_grade, user_proforma_grade = 0.9, 0.91
            StudentGradebook.objects.create(user=user, course_id=course.id,
                                            grade=user_grade, proforma_grade=user_proforma_grade)
            allow_access(course, user, 'instructor')

        test_uri = self.base_courses_uri + '/' + unicode(course.id) + '/users?fields=first_name,last_name'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 3)
        self.assertItemsEqual(['first_name', 'last_name'], response.data['results'][0].keys())

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_users_list_pagination(self, store):
        """ Tests users list API has pagination enabled """
        course = CourseFactory.create(default_store=store)
        users = UserFactory.create_batch(3)
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=course.id)

        test_uri = self.base_courses_uri + '/' + unicode(course.id) + '/users'
        response = self.do_get(test_uri)
        self.assertIn('count', response.data)
        self.assertIn('next', response.data)
        self.assertIn('previous', response.data)
        self.assertIn('num_pages', response.data)
        self.assertEqual(response.data['count'], 3)

    def test_courses_users_list_get_filter_by_orgs(self):
        # create 5 users
        users = []
        for i in xrange(1, 6):
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': 'test_pass',
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i)
            }
            response = self.do_post(self.base_users_uri, data)
            self.assertEqual(response.status_code, 201)
            users.append(response.data['id'])

        # create 3 organizations each one having one user
        org_ids = []
        for i in xrange(1, 4):
            data = {
                'name': '{} {}'.format('Test Organization', i),
                'display_name': '{} {}'.format('Test Org Display Name', i),
                'users': [users[i]]
            }
            response = self.do_post(self.base_organizations_uri, data)
            self.assertEqual(response.status_code, 201)
            self.assertGreater(response.data['id'], 0)
            org_ids.append(response.data['id'])

        # enroll all users in course
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        for user in users:
            data = {'user_id': user}
            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)

        # retrieve all users enrolled in the course
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 5)

        # retrieve users by organization
        response = self.do_get('{}?organizations={}'.format(test_uri, org_ids[0]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 1)

        # retrieve all users enrolled in the course
        response = self.do_get('{}?organizations={},{},{}'.format(test_uri, org_ids[0], org_ids[1], org_ids[2]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 3)

    def test_courses_users_list_get_filter_by_groups(self):
        # create 2 groups
        group_ids = []
        for i in xrange(1, 3):  # pylint: disable=C7620
            data = {'name': '{} {}'.format(self.test_group_name, i), 'type': 'test'}
            response = self.do_post(self.base_groups_uri, data)
            self.assertEqual(response.status_code, 201)
            group_ids.append(response.data['id'])

        # create 5 users
        users = []
        for i in xrange(0, 5):  # pylint: disable=C7620
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': 'test_pass',
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i)
            }
            response = self.do_post(self.base_users_uri, data)
            self.assertEqual(response.status_code, 201)
            users.append(response.data['id'])
            if i < 2:
                data = {'user_id': response.data['id']}
                response = self.do_post('{}{}/users'.format(self.base_groups_uri, group_ids[i]), data)
                self.assertEqual(response.status_code, 201)

        # enroll all users in course
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        for user in users:
            data = {'user_id': user}
            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)

        # retrieve all users enrolled in the course and member of group 1
        response = self.do_get('{}?groups={}'.format(test_uri, group_ids[0]))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 1)

        # retrieve all users enrolled in the course and member of group 1 and group 2
        response = self.do_get('{}?groups={},{}'.format(test_uri, group_ids[0], group_ids[1]))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 2)

        # retrieve all users enrolled in the course and not member of group 1
        response = self.do_get('{}?exclude_groups={}'.format(test_uri, group_ids[0]))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 4)

    def test_courses_users_list_get_filter_by_workgroups(self):
        """ Test courses users list workgroup filter """
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        organization = Organization.objects.create(
            name="Test Organization",
            display_name='Test Org Display Name',
        )
        project = Project.objects.create(course_id=self.test_course_id,
                                         content_id=self.test_course_content_id,
                                         organization=organization)
        # create 2 work groups
        workgroups = []
        workgroups.append(Workgroup.objects.create(name="Group1", project_id=project.id))
        workgroups.append(Workgroup.objects.create(name="Group2", project_id=project.id))
        workgroup_ids = ','.join([str(workgroup.id) for workgroup in workgroups])

        # create 5 users
        users = UserFactory.create_batch(5)

        for i, user in enumerate(users):
            workgroups[i % 2].add_user(user)

        # enroll all users in course
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=self.test_course_id)

        # retrieve all users enrolled in the course and member of workgroup 1
        response = self.do_get('{}?workgroups={}'.format(test_uri, workgroups[0].id))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 3)

        # retrieve all users enrolled in the course and member of workgroup 1 and workgroup 2
        response = self.do_get('{}?workgroups={}'.format(test_uri, workgroup_ids))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data['results']), 5)

    def test_courses_users_detail_get(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        test_user_uri = self.base_users_uri
        local_username = "some_test_user" + str(randint(11, 99))
        local_email = "test+notpending@tester.com"
        data = {
            'email': local_email,
            'username': local_username,
            'password': 'fooabr',
            'first_name': 'Joe',
            'last_name': 'Brown'
        }
        response = self.do_post(test_user_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        created_user_id = response.data['id']

        # Submit the query when unenrolled
        confirm_uri = '{}/{}'.format(test_uri, created_user_id)
        response = self.do_get(confirm_uri)
        self.assertEqual(response.status_code, 404)

        # now enroll this user in the course
        post_data = {}
        post_data['user_id'] = created_user_id
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = '{}/{}'.format(test_uri, created_user_id)
        response = self.do_get(confirm_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.data), 0)

    def test_courses_users_detail_get_invalid_course(self):
        test_uri = '{}/{}/users/{}'.format(self.base_courses_uri, self.test_bogus_course_id, self.users[0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)
        self.assertGreater(len(response.data), 0)

    def test_courses_users_detail_get_invalid_user(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users/213432'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)
        self.assertGreater(len(response.data), 0)

    def test_courses_users_detail_delete(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'
        test_user_uri = self.base_users_uri
        local_username = "some_test_user" + str(randint(11, 99))
        local_email = "test+notpending@tester.com"
        data = {
            'email': local_email,
            'username': local_username,
            'password': 'fooabr',
            'first_name': 'Joe',
            'last_name': 'Brown'
        }
        response = self.do_post(test_user_uri, data)
        self.assertEqual(response.status_code, 201)
        self.assertGreater(response.data['id'], 0)
        created_user_id = response.data['id']

        # now enroll this user in the course
        post_data = {}
        post_data['user_id'] = created_user_id
        response = self.do_post(test_uri, post_data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = '{}/{}'.format(test_uri, created_user_id)
        response = self.do_get(confirm_uri)
        self.assertEqual(response.status_code, 200)
        response = self.do_delete(confirm_uri)
        self.assertEqual(response.status_code, 204)

    def test_courses_users_detail_delete_invalid_course(self):
        test_uri = self.base_courses_uri + '/' + self.test_bogus_course_id + '/users/1'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_users_detail_delete_invalid_user(self):
        test_uri = self.base_courses_uri + '/' + self.test_course_id + '/users/213432'
        response = self.do_delete(test_uri)
        self.assertEqual(response.status_code, 204)

    def test_course_content_groups_list_post(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups'.format(self.base_course_content_uri, unicode(self.course_project.scope_ids.usage_id))
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        confirm_uri = self.test_server_prefix + test_uri + '/' + str(group_id)
        self.assertEqual(response.data['uri'], confirm_uri)
        self.assertEqual(response.data['course_id'], str(self.test_course_id))
        self.assertEqual(response.data['content_id'], unicode(self.course_project.scope_ids.usage_id))
        self.assertEqual(response.data['group_id'], str(group_id))

    def test_course_content_groups_list_post_duplicate(self):
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups'.format(self.base_course_content_uri, unicode(self.course_project.scope_ids.usage_id))
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 409)

    def test_course_content_groups_list_post_invalid_course(self):
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_bogus_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_post_invalid_content(self):
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_course_id,
            self.test_bogus_content_id
        )
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_post_invalid_group(self):
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        data = {'group_id': '12398721'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_post_missing_group(self):
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_get(self):
        test_uri = '{}/{}/groups'.format(self.base_course_content_uri, unicode(self.course_project.scope_ids.usage_id))
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        alpha_group_id = response.data['id']
        data = {'group_id': alpha_group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        # Add a profile-less group to the system to offset the identifiers
        Group.objects.create(name='Offset Group')

        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)

        data = {'name': 'Delta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)

        data = {'name': 'Gamma Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        gamma_group_id = response.data['id']
        data = {'group_id': gamma_group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['group_id'], alpha_group_id)
        self.assertEqual(response.data[1]['group_id'], gamma_group_id)

        test_uri = test_uri + '?type=project'
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_course_content_groups_list_get_invalid_course(self):
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_bogus_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_get_invalid_content(self):
        test_uri = '{}/{}/content/{}/groups'.format(
            self.base_courses_uri,
            self.test_course_id,
            self.test_bogus_content_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_list_get_filter_by_type(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        test_uri = '{}/{}/groups'.format(self.base_course_content_uri, unicode(self.course_project.scope_ids.usage_id))
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['group_id'], 2)

    def test_course_content_groups_detail_get(self):
        test_uri = '{}/{}/groups'.format(self.base_course_content_uri, unicode(self.course_project.scope_ids.usage_id))
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(response.data['uri'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['group_id'], str(group_id))

    def test_course_content_groups_detail_get_invalid_relationship(self):
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        group_id = response.data['id']
        test_uri = '{}/{}/groups/{}'.format(
            self.base_course_content_uri,
            unicode(self.course_project.scope_ids.usage_id),
            group_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_detail_get_invalid_course(self):
        test_uri = '{}/{}/content/{}/groups/123456'.format(
            self.base_courses_uri,
            self.test_bogus_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_detail_get_invalid_content(self):
        test_uri = '{}/{}/content/{}/groups/123456'.format(
            self.base_courses_uri,
            self.test_course_id,
            self.test_bogus_content_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_groups_detail_get_invalid_group(self):
        test_uri = '{}/{}/content/{}/groups/123456'.format(
            self.base_courses_uri,
            self.test_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_content_users_list_get(self):
        test_uri = '{}/{}/groups'.format(
            self.base_course_content_uri,
            unicode(self.course_project.scope_ids.usage_id)
        )
        test_uri_users = '{}/{}/users'.format(
            self.base_course_content_uri,
            unicode(self.course_project.scope_ids.usage_id)
        )
        test_course_users_uri = self.base_courses_uri + '/' + self.test_course_id + '/users'

        # Create a group and add it to course module
        data = {'name': 'Alpha Group', 'type': 'test'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        group_id = response.data['id']
        data = {'group_id': group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        # Create another group and add it to course module
        data = {'name': 'Beta Group', 'type': 'project'}
        response = self.do_post(self.base_groups_uri, data)
        self.assertEqual(response.status_code, 201)
        another_group_id = response.data['id']
        data = {'group_id': another_group_id}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        # create a 5 new users
        for i in xrange(1, 6):
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': 'test_pass',
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i)
            }
            response = self.do_post(self.base_users_uri, data)
            self.assertEqual(response.status_code, 201)
            created_user_id = response.data['id']

            #add two users to Alpha Group and one to Beta Group and keep two without any group
            if i <= 3:
                add_to_group = group_id
                if i > 2:
                    add_to_group = another_group_id
                test_group_users_uri = '{}/{}/users'.format(self.base_groups_uri, add_to_group)

                data = {'user_id': created_user_id}
                response = self.do_post(test_group_users_uri, data)
                self.assertEqual(response.status_code, 201)
                #enroll one user in Alpha Group and one in Beta Group created user
                if i >= 2:
                    response = self.do_post(test_course_users_uri, data)
                    self.assertEqual(response.status_code, 201)
        response = self.do_get('{}?enrolled={}'.format(test_uri_users, 'True'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        response = self.do_get('{}?enrolled={}'.format(test_uri_users, 'False'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        #filter by group id
        response = self.do_get('{}?enrolled={}&group_id={}'.format(test_uri_users, 'true', group_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        response = self.do_get('{}?enrolled={}&group_id={}'.format(test_uri_users, 'false', group_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        #filter by group type
        response = self.do_get('{}?enrolled={}&type={}'.format(test_uri_users, 'true', 'project'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_course_content_users_list_get_invalid_course_and_content(self):
        invalid_course_uri = '{}/{}/content/{}/users'.format(
            self.base_courses_uri,
            self.test_bogus_course_id,
            unicode(self.course_project.scope_ids.usage_id)
        )
        response = self.do_get(invalid_course_uri)
        self.assertEqual(response.status_code, 404)

        invalid_content_uri = '{}/{}/content/{}/users'.format(
            self.base_courses_uri,
            self.test_course_id,
            self.test_bogus_content_id
        )
        response = self.do_get(invalid_content_uri)
        self.assertEqual(response.status_code, 404)

    def test_coursemodulecompletions_post(self):

        data = {
            'email': 'test@example.com',
            'username': 'test_user',
            'password': 'test_pass',
            'first_name': 'John',
            'last_name': 'Doe'
        }
        response = self.do_post(self.base_users_uri, data)
        self.assertEqual(response.status_code, 201)
        created_user_id = response.data['id']
        completions_uri = '{}/{}/completions/'.format(self.base_courses_uri, unicode(self.course.id))
        stage = 'First'
        completions_data = {
            'content_id': unicode(self.course_content.scope_ids.usage_id),
            'user_id': created_user_id,
            'stage': stage
        }
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 201)
        coursemodulecomp_id = response.data['id']
        self.assertGreater(coursemodulecomp_id, 0)
        self.assertEqual(response.data['user_id'], created_user_id)
        self.assertEqual(response.data['course_id'], unicode(self.course.id))
        self.assertEqual(response.data['content_id'], unicode(self.course_content.scope_ids.usage_id))
        self.assertEqual(response.data['stage'], stage)
        self.assertIsNotNone(response.data['created'])
        self.assertIsNotNone(response.data['modified'])

        # test to create course completion with same attributes
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 409)

        # test to create course completion with empty user_id
        completions_data['user_id'] = None
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 400)

        # test to create course completion with empty content_id
        completions_data['content_id'] = None
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 400)

        # test to create course completion with invalid content_id
        completions_data['content_id'] = self.test_bogus_content_id
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 400)

    def test_course_module_completions_post_invalid_course(self):
        completions_uri = '{}/{}/completions/'.format(self.base_courses_uri, self.test_bogus_course_id)
        completions_data = {
            'content_id': unicode(self.course_content.scope_ids.usage_id),
            'user_id': self.users[0].id
        }
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 404)

    def test_course_module_completions_post_invalid_content(self):
        completions_uri = '{}/{}/completions/'.format(self.base_courses_uri, self.test_course_id)
        completions_data = {'content_id': self.test_bogus_content_id, 'user_id': self.users[0].id}
        response = self.do_post(completions_uri, completions_data)
        self.assertEqual(response.status_code, 400)

    def test_coursemodulecompletions_filters(self):  # pylint: disable=R0915
        completion_uri = '{}/{}/completions/'.format(self.base_courses_uri, unicode(self.course.id))
        for i in xrange(1, 3):
            data = {
                'email': 'test{}@example.com'.format(i),
                'username': 'test_user{}'.format(i),
                'password': 'test_pass',
                'first_name': 'John{}'.format(i),
                'last_name': 'Doe{}'.format(i)
            }
            response = self.do_post(self.base_users_uri, data)
            self.assertEqual(response.status_code, 201)
            created_user_id = response.data['id']

        content_ids = []
        for i in xrange(1, 26):
            local_content_name = 'Video_Sequence_competions{}'.format(i)
            local_content = ItemFactory.create(
                category="videosequence",
                parent_location=self.content_child2.location,
                display_name=local_content_name
            )
            content_ids.append(local_content.scope_ids.usage_id)
            if i < 25:
                content_id = unicode(local_content.scope_ids.usage_id)
                stage = None
            else:
                content_id = unicode(self.course_content.scope_ids.usage_id)
                stage = 'Last'
            completions_data = {'content_id': content_id, 'user_id': created_user_id, 'stage': stage}
            response = self.do_post(completion_uri, completions_data)
            self.assertEqual(response.status_code, 201)

        #filter course module completion by user
        user_filter_uri = '{}?user_id={}&page_size=10&page=3'.format(completion_uri, created_user_id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 5)
        self.assertEqual(response.data['num_pages'], 3)

        #filter course module completion by multiple user ids
        user_filter_uri = '{}?user_id={}'.format(completion_uri, str(created_user_id) + ',10001,10003')
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 20)
        self.assertEqual(response.data['num_pages'], 2)

        #filter course module completion by user who has not completed any course module
        user_filter_uri = '{}?user_id={}'.format(completion_uri, 10001)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 0)

        #filter course module completion by course_id
        course_filter_uri = '{}?course_id={}&page_size=10'.format(completion_uri, unicode(self.course.id))
        response = self.do_get(course_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 10)

        #filter course module completion by content_id
        content_id = {'content_id': '{}'.format(unicode(content_ids[0]))}
        content_filter_uri = '{}?{}'.format(completion_uri, urlencode(content_id))
        response = self.do_get(content_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)

        #filter course module completion by invalid content_id
        content_id = {'content_id': '{}1'.format(self.test_bogus_content_id)}
        content_filter_uri = '{}?{}'.format(completion_uri, urlencode(content_id))
        response = self.do_get(content_filter_uri)
        self.assertEqual(response.status_code, 404)

        #filter course module completion by stage
        content_filter_uri = '{}?stage={}'.format(completion_uri, 'Last')
        response = self.do_get(content_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(len(response.data['results']), 1)

    def test_coursemodulecompletions_get_invalid_course(self):
        completion_uri = '{}/{}/completions/'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(completion_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_metrics_social_check_service_availability(self):
        test_uri = '{}/{}/metrics/social/'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    @mock.patch("edx_solutions_api_integration.courses.views.get_course_social_stats", _fake_get_service_unavailability)
    def test_courses_social_metrics_get_service_unavailability(self):
        test_uri = '{}/{}/metrics/social/'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)

    @mock.patch(
        "edx_solutions_api_integration.courses.views.get_course_social_stats",
        _fake_get_course_social_stats_date_expected
    )
    def test_courses_metrics_social_get(self):
        test_uri = '{}/{}/metrics/social/'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data.keys()), 2)
        users = response.data['users']

        self.assertIn('2', users)
        self.assertIn('3', users)

        # make the first user an observer to asset that its content is being filtered out from
        # the aggregates
        users = [user for user in self.users if user.id == 2]
        allow_access(self.course, users[0], 'observer')

        cache.clear()
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data.keys()), 2)
        users = response.data['users']
        self.assertNotIn('2', users)
        self.assertIn('3', users)

    @mock.patch("edx_solutions_api_integration.courses.views.get_course_social_stats", _fake_get_course_social_stats)
    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_courses_metrics_social_get_no_date(self,store):
        course = CourseFactory.create(
            start=datetime(2014, 6, 16, 14, 30),
            default_store=store
        )
        USER_COUNT = 2  # pylint: disable=C0103,W0621
        users = [
            UserFactory.create(username="coursesmetrics_user" + str(__), profile='test') for __ in xrange(USER_COUNT)
        ]
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=course.id)

        test_uri = '{}/{}/metrics/social/'.format(self.base_courses_uri, unicode(course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data.keys()), 2)
        result_users = response.data['users']
        # expect all users are in result set
        for user in users:
            self.assertTrue(result_users.get(str(user.id)))

    def test_courses_completions_leaders_list_get(self):
        setup_data = self._setup_courses_completions_leaders()
        expected_course_avg = '25.000'
        test_uri = '{}?count=6'.format(setup_data['leaders_uri'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 4)
        self.assertEqual('{0:.3f}'.format(response.data['course_avg']), expected_course_avg)

        # without count filter and user_id
        test_uri = '{}?user_id={}'.format(setup_data['leaders_uri'], setup_data['users'][1].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 4)
        self.assertEqual(response.data['position'], 2)
        self.assertEqual('{0:.3f}'.format(response.data['completions']), '28.000')

        # with skipleaders filter
        test_uri = '{}?user_id={}&skipleaders=true'.format(setup_data['leaders_uri'], setup_data['users'][1].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data.get('leaders', None))
        self.assertEqual('{0:.3f}'.format(response.data['course_avg']), expected_course_avg)
        self.assertEqual('{0:.3f}'.format(response.data['completions']), '28.000')

        # test with bogus course
        test_uri = '{}/{}/metrics/completions/leaders/'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

        #filter course module completion by organization
        data = {
            'name': 'Test Organization',
            'display_name': 'Test Org Display Name',
            'users': [setup_data['users'][1].id]
        }
        response = self.do_post(self.base_organizations_uri, data)
        self.assertEqual(response.status_code, 201)
        test_uri = '{}?organizations={}'.format(setup_data['leaders_uri'], response.data['id'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 1)
        self.assertEqual(response.data['leaders'][0]['id'], setup_data['users'][1].id)
        self.assertEqual('{0:.3f}'.format(response.data['leaders'][0]['completions']), '28.000')
        self.assertEqual('{0:.3f}'.format(response.data['course_avg']), '28.000')

        # test with unknown user
        test_uri = '{}?user_id={}&skipleaders=true'.format(setup_data['leaders_uri'], '909999')
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data.get('leaders', None))
        self.assertEqual(response.data['completions'], 0)

        # test a case where completions are greater than total course modules. it should not be more than 100
        setup_data['contents'].append(self.course_content)
        for content in setup_data['contents'][2:]:
            user_id = setup_data['users'][0].id
            content_id = unicode(content.scope_ids.usage_id)
            completions_data = {'content_id': content_id, 'user_id': user_id}
            response = self.do_post(setup_data['completion_uri'], completions_data)
            self.assertEqual(response.status_code, 201)

        test_uri = '{}?user_id={}'.format(setup_data['leaders_uri'], setup_data['users'][0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual('{0:.3f}'.format(response.data['completions']), '100.000')

    def test_courses_completions_leaders_list_get_filter_users_by_group(self):
        """
        Test courses completions leaders with group filter
        """
        setup_data = self._setup_courses_completions_leaders()
        expected_course_avg = '18.000'
        test_uri = '{}?groups={}'.format(setup_data['leaders_uri'], setup_data['groups'][0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 2)
        self.assertEqual('{0:.3f}'.format(response.data['course_avg']), expected_course_avg)

    def test_courses_completions_leaders_exclude_roles(self):
        """
        Tests courses completions leaders with `exclude_roles` filter
        """
        setup_data = self._setup_courses_completions_leaders()
        local_content = setup_data['contents'][0]
        completion_uri = setup_data['completion_uri']
        course = setup_data['course']
        content_id = unicode(local_content.scope_ids.usage_id)

        # create couple of users, assign them observer and assistant roles and add content completion for them
        users = UserFactory.create_batch(2)
        for idx, user in enumerate(users):
            CourseEnrollmentFactory.create(user=user, course_id=unicode(course.id))
            roles = ['observer', 'assistant']
            allow_access(course, user, roles[idx])
            completions_data = {'content_id': content_id, 'user_id': user.id}
            response = self.do_post(completion_uri, completions_data)
            self.assertEqual(response.status_code, 201)

        # test both users are excluded from progress calculations
        test_uri = '{}?exclude_roles=observer,assistant'.format(setup_data['leaders_uri'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 4)

        # test only observer is excluded from progress calculations
        test_uri = '{}?exclude_roles=observer,'.format(setup_data['leaders_uri'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 5)

        # test when none is passed
        test_uri = '{}?exclude_roles=none'.format(setup_data['leaders_uri'])
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 7)

    def test_courses_completions_leaders_list_get_filter_users_by_multiple_groups(self):
        """
        Test courses completions leaders with group filter for users in multiple groups
        """
        setup_data = self._setup_courses_completions_leaders()
        expected_course_avg = '25.000'
        group_ids = ','.join([str(group.id) for group in setup_data['groups']])
        test_uri = '{}?groups={}'.format(setup_data['leaders_uri'], group_ids)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 4)
        self.assertEqual('{0:.3f}'.format(response.data['course_avg']), expected_course_avg)

    def test_course_project_list(self):
        projects_uri = self.base_projects_uri

        for i in xrange(0, 25):  # pylint: disable=C7620
            local_content_name = 'Video_Sequence_cp{}'.format(i)
            local_content = ItemFactory.create(
                category="videosequence",
                parent_location=self.content_child2.location,
                display_name=local_content_name
            )
            data = {
                'content_id': unicode(local_content.scope_ids.usage_id),
                'course_id': self.test_course_id
            }
            response = self.do_post(projects_uri, data)
            self.assertEqual(response.status_code, 201)

        response = self.do_get('{}/{}/projects/?page_size=10'.format(self.base_courses_uri, self.test_course_id))
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['num_pages'], 3)

    def test_courses_data_metrics(self):
        self.login()
        users_to_add, user_grade, user_completions, total_assessments = 5, 0.6, 10, 20
        course = CourseFactory()
        CourseAggregatedMetaData.objects.update_or_create(
            id=course.id, defaults={'total_assessments': total_assessments}
        )
        for idx in xrange(0, users_to_add):
            user = UserFactory()
            created_user_id = user.id
            CourseEnrollmentFactory(user=user, course_id=course.id)

            # add grades for users
            StudentGradebook.objects.update_or_create(
                user=user,
                course_id=course.id,
                defaults={
                    'grade': user_grade,
                    'proforma_grade': user_grade if idx % 2 == 0 else 0.95,
                    'is_passed': True,
                }
            )

            # add progress for users
            StudentProgress.objects.update_or_create(
                user=user,
                course_id=course.id,
                defaults={
                    'completions': user_completions,
                }
            )

        # create an organization and add last created user in it
        data = {
            'name': 'Test Organization',
            'display_name': 'Test Org Display Name',
            'users': [created_user_id]
        }
        response = self.do_post(self.base_organizations_uri, data)
        self.assertEqual(response.status_code, 201)
        org_id = response.data['id']

        # get course metrics
        course_metrics_uri = reverse(
            'course-metrics', kwargs={'course_id': unicode(course.id)}
        )
        course_metrics_uri = '{}/?metrics_required={}'.format(
            course_metrics_uri,
            'users_started,modules_completed,users_completed,thread_stats,avg_grade,avg_progress',
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['users_enrolled'], users_to_add)
        self.assertGreaterEqual(response.data['users_started'], users_to_add)
        self.assertEqual(response.data['users_not_started'], 0)
        self.assertEqual(response.data['modules_completed'], user_completions * users_to_add)
        self.assertEqual(response.data['users_completed'], 3)
        self.assertEqual(round(response.data['avg_progress']), round(user_completions/float(total_assessments) * 100))
        self.assertEqual(response.data['avg_grade'], user_grade)
        self.assertIsNotNone(response.data['grade_cutoffs'])
        self.assertEqual(response.data['thread_stats']['num_threads'], 5)
        self.assertEqual(response.data['thread_stats']['num_active_threads'], 3)

        # get course metrics by valid organization
        course_metrics_uri = '{}/?metrics_required={}&organization={}'.format(
            course_metrics_uri,
            'users_started,avg_grade,avg_progress',
            org_id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['users_enrolled'], 1)
        self.assertGreaterEqual(response.data['users_started'], 1)
        self.assertEqual(round(response.data['avg_progress']), round(user_completions/float(total_assessments) * 100))
        self.assertEqual(response.data['avg_grade'], user_grade)

        # test with bogus course
        course_metrics_uri = '{}/{}/metrics/'.format(
            self.base_courses_uri,
            self.test_bogus_course_id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_data_metrics_user_group_filter_for_empty_group(self):
        group = GroupFactory.create()

        # get course metrics for users in group
        course_metrics_uri = '{}/{}/metrics/?metrics_required=users_started&groups={}'.format(
            self.base_courses_uri,
            self.test_course_id,
            group.id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['users_enrolled'], 0)
        self.assertGreaterEqual(response.data['users_started'], 0)

    def test_course_data_metrics_user_group_filter_for_group_having_members(self):
        group = GroupFactory.create()
        users = UserFactory.create_batch(3, groups=(group,))

        # enroll all users in course
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        # create course completions
        for i, user in enumerate(users):
            completions_uri = '{}/{}/completions/'.format(self.base_courses_uri, self.test_course_id)
            completions_data = {
                'content_id': unicode(self.course_content.scope_ids.usage_id),
                'user_id': user.id,
                'stage': 'First'
            }
            response = self.do_post(completions_uri, completions_data)
            self.assertEqual(response.status_code, 201)

            # mark two users a complete
            if i % 2 == 0:
                StudentGradebook.objects.get_or_create(
                    user=user,
                    course_id=self.course.id,
                    grade=0.9,
                    proforma_grade=0.91,
                    is_passed=i > 1,
                )

        course_metrics_uri = '{}/{}/metrics/?metrics_required={}&groups={}'.format(
            self.base_courses_uri,
            self.test_course_id,
            'users_started,modules_completed,users_completed,users_passed',
            group.id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['users_enrolled'], 3)
        self.assertGreaterEqual(response.data['users_started'], 3)
        self.assertEqual(response.data['users_not_started'], 0)
        self.assertEqual(response.data['modules_completed'], 3)
        self.assertEqual(response.data['users_completed'], 2)
        self.assertEqual(response.data['users_passed'], 1)

    def test_course_data_metrics_user_group_filter_for_multiple_groups_having_members(self):
        groups = GroupFactory.create_batch(2)
        users = UserFactory.create_batch(4, groups=(groups[0],))
        users.append(UserFactory.create(groups=groups))

        # enroll all users in course
        for user in users:
            CourseEnrollmentFactory.create(user=user, course_id=self.course.id)

        # create course completions
        for user in users:
            completions_uri = '{}/{}/completions/'.format(self.base_courses_uri, self.test_course_id)
            completions_data = {
                'content_id': unicode(self.course_content.scope_ids.usage_id),
                'user_id': user.id,
                'stage': 'First'
            }
            response = self.do_post(completions_uri, completions_data)
            self.assertEqual(response.status_code, 201)

        course_metrics_uri = '{}/{}/metrics/?metrics_required={}&groups={},{}'.format(
            self.base_courses_uri,
            self.test_course_id,
            'users_started,modules_completed,users_completed',
            groups[0].id,
            groups[1].id,
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['users_enrolled'], 5)
        self.assertGreaterEqual(response.data['users_started'], 5)
        self.assertEqual(response.data['users_not_started'], 0)
        self.assertEqual(response.data['modules_completed'], 5)
        self.assertEqual(response.data['users_completed'], 0)

    def test_course_workgroups_list(self):
        projects_uri = self.base_projects_uri
        data = {
            'course_id': self.test_course_id,
            'content_id': self.test_course_content_id
        }
        response = self.do_post(projects_uri, data)
        self.assertEqual(response.status_code, 201)
        project_id = response.data['id']

        test_workgroups_uri = self.base_workgroups_uri
        for i in xrange(1, 12):
            data = {
                'name': '{} {}'.format('Workgroup', i),
                'project': project_id
            }
            response = self.do_post(test_workgroups_uri, data)
            self.assertEqual(response.status_code, 201)

        # get workgroups associated to course
        test_uri = '{}/{}/workgroups/?page_size=10'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.data['count'], 11)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['num_pages'], 2)

        # test with bogus course
        test_uri = '{}/{}/workgroups/'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    @ddt.data(ModuleStoreEnum.Type.split, ModuleStoreEnum.Type.mongo)
    def test_course_users_count_by_city(self, store):
        test_uri = self.base_users_uri
        course = CourseFactory(default_store=store)
        test_course_id = unicode(course.id)
        # create a 25 new users
        for i in xrange(1, 26):
            if i < 10:
                city = 'San Francisco'
            elif i < 15:
                city = 'Denver'
            elif i < 20:
                city = 'Dallas'
            else:
                city = 'New York City'
            data = {
                'email': 'test{}@example.com'.format(i), 'username': 'test_user{}'.format(i),
                'password': 'test.me!',
                'first_name': '{} {}'.format('John', i), 'last_name': '{} {}'.format('Doe', i), 'city': city,
                'country': 'PK', 'level_of_education': 'b', 'year_of_birth': '2000', 'gender': 'male',
                'title': 'Software Engineer'
            }

            response = self.do_post(test_uri, data)
            self.assertEqual(response.status_code, 201)
            created_user_id = response.data['id']
            user_uri = response.data['uri']
            # now enroll this user in the course
            post_data = {'user_id': created_user_id}
            courses_test_uri = self.base_courses_uri + '/' + test_course_id + '/users'
            response = self.do_post(courses_test_uri, post_data)
            self.assertEqual(response.status_code, 201)

            response = self.do_get(user_uri)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data['city'], city)

        response = self.do_get('{}/{}/metrics/cities/'.format(self.base_courses_uri, test_course_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 4)
        self.assertEqual(response.data[0]['city'], 'San Francisco')
        self.assertEqual(response.data[0]['count'], 9)

        # filter counts by city
        sf_uri = '{}/{}/metrics/cities/?city=new york city, San Francisco'.format(self.base_courses_uri,
                                                                                  test_course_id)
        response = self.do_get(sf_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        self.assertEqual(response.data[0]['city'], 'San Francisco')
        self.assertEqual(response.data[0]['count'], 9)
        self.assertEqual(response.data[1]['city'], 'New York City')
        self.assertEqual(response.data[1]['count'], 6)

        # filter counts by city
        dnv_uri = '{}/{}/metrics/cities/?city=Denver'.format(self.base_courses_uri, test_course_id)
        response = self.do_get(dnv_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['city'], 'Denver')
        self.assertEqual(response.data[0]['count'], 5)

        # Do a get with a bogus course to hit the 404 case
        response = self.do_get('{}/{}/metrics/cities/'.format(self.base_courses_uri, self.test_bogus_course_id))
        self.assertEqual(response.status_code, 404)

    def test_courses_roles_list_get(self):
        allow_access(self.course, self.users[0], 'staff')
        allow_access(self.course, self.users[1], 'instructor')
        allow_access(self.course, self.users[2], 'observer')
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 3)

        # filter roleset by user
        user_id = {'user_id': '{}'.format(self.users[0].id)}
        user_filter_uri = '{}?{}'.format(test_uri, urlencode(user_id))
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        # filter roleset by role
        role = {'role': 'instructor'}
        role_filter_uri = '{}?{}'.format(test_uri, urlencode(role))
        response = self.do_get(role_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        role = {'role': 'invalid_role'}
        role_filter_uri = '{}?{}'.format(test_uri, urlencode(role))
        response = self.do_get(role_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_courses_roles_list_get_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_roles_list_post(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

        data = {'user_id': self.users[0].id, 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        # Confirm this user also has forum moderation permissions
        role = Role.objects.get(course_id=self.course.id, name=FORUM_ROLE_MODERATOR)
        has_role = role.users.get(id=self.users[0].id)
        self.assertTrue(has_role)

    def test_courses_roles_list_post_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, self.test_bogus_course_id)
        data = {'user_id': self.users[0].id, 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 404)

    def test_courses_roles_list_post_invalid_user(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        data = {'user_id': 23423, 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_courses_roles_list_post_invalid_role(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        data = {'user_id': self.users[0].id, 'role': 'invalid_role'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 400)

    def test_courses_roles_users_detail_delete(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        data = {'user_id': self.users[0].id, 'role': 'instructor'}
        response = self.do_post(test_uri, data)
        self.assertEqual(response.status_code, 201)

        response = self.do_get(test_uri)
        self.assertEqual(len(response.data), 1)

        delete_uri = '{}instructor/users/{}'.format(test_uri, self.users[0].id)
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 204)

        response = self.do_get(test_uri)
        self.assertEqual(len(response.data), 0)

        # Confirm this user no longer has forum moderation permissions
        role = Role.objects.get(course_id=self.course.id, name=FORUM_ROLE_MODERATOR)
        try:
            role.users.get(id=self.users[0].id)
            self.assertTrue(False)
        except ObjectDoesNotExist:
            pass

    def test_courses_roles_users_detail_delete_invalid_course(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, self.test_bogus_course_id)
        delete_uri = '{}instructor/users/{}'.format(test_uri, self.users[0].id)
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_roles_users_detail_delete_invalid_user(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        delete_uri = '{}instructor/users/291231'.format(test_uri)
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_courses_roles_users_detail_delete_invalid_role(self):
        test_uri = '{}/{}/roles/'.format(self.base_courses_uri, unicode(self.course.id))
        delete_uri = '{}invalid_role/users/{}'.format(test_uri, self.users[0].id)
        response = self.do_delete(delete_uri)
        self.assertEqual(response.status_code, 404)

    def test_course_navigation(self):
        test_uri = '{}/{}/navigation/{}'.format(
            self.base_courses_uri, unicode(self.course.id), self.content_subchild.location.block_id
        )
        response = self.do_get(test_uri)
        self.assertEqual(
            {
                'chapter': unicode(self.chapter.location),
                'vertical': unicode(self.content_child2.location),
                'section': unicode(self.course_content2.location),
                'course_key': unicode(self.course.id),
                'final_target_id': unicode(self.content_child.location),
                'position': '1_1',
            },
            response.data
        )

    def test_courses_users_list_valid_email_enroll_user(self):
        # Test with valid email in request data, it should return response status HTTP_201_CREATED
        test_uri = '{}/{}/users'.format(self.base_courses_uri, self.course.id)
        response = self.do_post(test_uri, {'email': self.users[0].email})
        self.assertEqual(response.status_code, 201)

    def test_courses_groups_list_missing_group_id(self):
        # Test with missing group_id in request data
        test_uri = '{}/{}/groups'.format(self.base_courses_uri, self.test_course_id)
        response = self.do_post(test_uri, {})
        self.assertEqual(response.status_code, 400)

    @mock.patch("edx_solutions_api_integration.courses.views.get_course", _fake_get_course)
    def test_courses_users_detail_get_undefined_course_content(self):
        # Get course user details when course_content is None
        test_uri = '{}/{}/users/{}'.format(self.base_courses_uri, self.course.id, self.users[0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['position'], None)


@mock.patch.dict("django.conf.settings.FEATURES", {'ENFORCE_PASSWORD_POLICY': False,
                                                   'ADVANCED_SECURITY': False,
                                                   'PREVENT_CONCURRENT_LOGINS': False,
                                                   'MARK_PROGRESS_ON_GRADING_EVENT': True,
                                                   'SIGNAL_ON_SCORE_CHANGED': True,
                                                   'STUDENT_GRADEBOOK': True,
                                                   'STUDENT_PROGRESS': True})
class CoursesTimeSeriesMetricsApiTests(SignalDisconnectTestMixin, SharedModuleStoreTestCase, APIClientMixin):
    """ Test suite for CoursesTimeSeriesMetrics API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

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

    @classmethod
    def setUpClass(cls):
        super(CoursesTimeSeriesMetricsApiTests, cls).setUpClass()
        cls.test_server_prefix = 'https://testserver'
        cls.base_courses_uri = '/api/server/courses'
        cls.base_groups_uri = '/api/server/groups'
        cls.base_organizations_uri = '/api/server/organizations/'
        cls.test_data = '<html>{}</html>'.format(str(uuid.uuid4()))

        cls.reference_date = datetime(2015, 8, 21, 0, 0, 0, 0, pytz.UTC)
        course_start_date = cls.reference_date + relativedelta(months=-2)
        course_end_date = cls.reference_date + relativedelta(years=5)

        # Set up two courses, complete with chapters, sections, units, and items
        cls.course = CourseFactory.create(
            number='3033',
            name='metrics_in_timeseries',
            start=course_start_date,
            end=course_end_date,
        )
        cls.second_course = CourseFactory.create(
            number='3034',
            name='metrics_in_timeseries2',
            start=course_start_date,
            end=course_end_date,
        )
        cls.chapter = ItemFactory.create(
            category="chapter",
            parent_location=cls.course.location,
            due=course_end_date,
            display_name=u"3033 Overview",
        )
        cls.sub_section = ItemFactory.create(
            parent_location=cls.chapter.location,
            category="sequential",
            display_name="3033 test subsection",
        )
        cls.unit = ItemFactory.create(
            parent_location=cls.sub_section.location,
            category="vertical",
            metadata={'graded': True, 'format': 'Homework'},
            display_name=u"3033 test unit",
        )
        cls.item = ItemFactory.create(
            parent_location=cls.unit.location,
            category='problem',
            display_name='Problem to test timeseries',
            metadata={'rerandomize': 'always', 'graded': True, 'format': 'Midterm Exam'}
        )

        cls.item2 = ItemFactory.create(
            parent_location=cls.unit.location,
            category='problem',
            display_name='Problem 2 for test timeseries',
            metadata={'rerandomize': 'always', 'graded': True, 'format': 'Final Exam'}
        )

        # Create the set of users that will enroll in these courses
        cls.user_count = 25
        cls.users = UserFactory.create_batch(cls.user_count)
        cls.groups = GroupFactory.create_batch(2)
        cls.user_ids = []
        for i, user in enumerate(cls.users):
            cls.user_ids.append(user.id)
            user.groups.add(cls.groups[i % 2])

        cls.users[0].groups.add(cls.groups[1])

        # Create a test organization that will be used for validation of org filtering
        cls.test_organization = Organization.objects.create(
            name="Test Organization",
            display_name='Test Org Display Name',
        )
        cls.test_organization.users.add(*cls.users)
        cls.org_id = cls.test_organization.id

        # Enroll the users in the courses using an old datestamp
        enrolled_time = cls.reference_date - timedelta(days=cls.user_count, minutes=-30)
        with freeze_time(enrolled_time):
            for user in cls.users:
                CourseEnrollmentFactory.create(user=user, course_id=cls.course.id)
                CourseEnrollmentFactory.create(user=user, course_id=cls.second_course.id)

        # Set up the basic score container that will be used for student submissions
        points_scored = .25
        points_possible = 1
        cls.grade_dict = {'value': points_scored, 'max_value': points_possible}
        cache.clear()

    @make_non_atomic
    def _submit_user_scores(self):
        """Submit user scores for modules in the course"""
        # Submit user scores for the first module in the course
        # The looping is a bit wacky here, but it actually does work out correctly
        for j, user in enumerate(self.users):
            # Ensure all database entries in this block record the same timestamps
            # We record each user on a different day across the series to test the aggregations
            submit_time = self.reference_date - timedelta(days=(self.user_count - j), minutes=-30)
            with freeze_time(submit_time):
                module = self.get_module_for_user(user, self.course, self.item)
                self.grade_dict['user_id'] = user.id
                module.system.publish(module, 'grade', self.grade_dict)

                # For the final two users, submit an score for the second module
                if j >= self.user_count - 2:
                    second_module = self.get_module_for_user(user, self.course, self.item2)
                    second_module.system.publish(second_module, 'grade', self.grade_dict)
                    # Add an entry to the gradebook in addition to the scoring -- this is for completions
                    try:
                        sg_entry = StudentGradebook.objects.get(user=user, course_id=self.course.id)
                        sg_entry.grade = 0.9
                        sg_entry.proforma_grade = 0.91
                        sg_entry.save()
                    except StudentGradebook.DoesNotExist:
                        StudentGradebook.objects.create(user=user, course_id=self.course.id, grade=0.9,
                                                        proforma_grade=0.91)

        # Submit scores for the second module for the first five users
        # Pretend the scores were submitted over the course of the final five days
        for j, user in enumerate(self.users[:5]):
            submit_time = self.reference_date - timedelta(days=(5 - j), minutes=-30)
            with freeze_time(submit_time):
                self.grade_dict['user_id'] = user.id
                second_module = self.get_module_for_user(user, self.course, self.item2)
                second_module.system.publish(second_module, 'grade', self.grade_dict)

    def test_courses_data_time_series_metrics_for_first_five_days(self):
        """
        Calculate time series metrics for users in a particular course for first five days.
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Generate the time series report for the first five days of the set, filtered by organization
        # There should be one time series entry per day for each category, each day having varying counts
        end_date = self.reference_date - timedelta(days=(self.user_count - 4))
        start_date = self.reference_date - timedelta(days=self.user_count)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&organization={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters),
            self.org_id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 5)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 110)  # Aggregate total in the first five days (24,23,22,21,20)
        self.assertEqual(len(response.data['users_started']), 5)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 5)  # Five users started in the first five days
        self.assertEqual(len(response.data['users_completed']), 5)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 0)  # Zero users completed in the first five days
        self.assertEqual(len(response.data['modules_completed']), 5)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 5)  # Five modules completed in the first five days
        self.assertEqual(len(response.data['active_users']), 5)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 4)  # Four active users in the first five days due to how 'active' is defined
        self.assertEqual(len(response.data['users_enrolled']), 5)
        self.assertEqual(response.data['users_enrolled'][0][1], 25)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 25)  # Remember, everyone was enrolled on the first day

    def test_courses_data_time_series_metrics_for_final_five_days(self):
        """
        Calculate time series metrics for users in a particular course for final five days.
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Generate the time series report for the final five days, filtered by organization
        end_date = self.reference_date
        start_date = end_date - relativedelta(days=4)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&organization={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters),
            self.org_id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 5)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 6)  # Ticking down the nonstarters -- 3, 2, 1, 0, 0
        self.assertEqual(len(response.data['users_started']), 5)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 4)  # Four users started in the final five days
        self.assertEqual(len(response.data['users_completed']), 5)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 2)  # Two users completed in the final five days (see setup above)
        self.assertEqual(len(response.data['modules_completed']), 5)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 10)  # Ten modules completed in the final five days
        self.assertEqual(len(response.data['active_users']), 5)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 10)  # Ten active users in the final five days
        self.assertEqual(len(response.data['users_enrolled']), 5)
        self.assertEqual(response.data['users_enrolled'][0][1], 0)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 0)  # Remember, everyone was enrolled on the first day, so zero is correct here

    def test_courses_data_time_series_metrics_with_three_weeks_interval(self):
        """
        Calculate time series metrics for users in a particular course with three weeks interval.
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Change the time interval to three weeks, so we should now see three entries per category
        end_date = self.reference_date
        start_date = end_date - relativedelta(weeks=2)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&interval=weeks'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters)
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 3)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 5)
        self.assertEqual(len(response.data['users_started']), 3)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 18)
        self.assertEqual(len(response.data['users_completed']), 3)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 2)
        self.assertEqual(len(response.data['modules_completed']), 3)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 25)
        self.assertEqual(len(response.data['active_users']), 3)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 23)  # Three weeks x one user per day
        self.assertEqual(len(response.data['users_enrolled']), 3)
        self.assertEqual(response.data['users_enrolled'][0][1], 0)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 0)  # No users enrolled in this series

    def test_courses_data_time_series_metrics_with_four_months_interval(self):
        """
        Calculate time series metrics for users in a particular course with four months interval.
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Change the time interval to four months, so we're back to four entries per category
        end_date = self.reference_date + relativedelta(months=1)
        start_date = end_date - relativedelta(months=3)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&interval=months'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters)
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 4)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 20)  # 5 users started in july from 27th to 31st
        self.assertEqual(len(response.data['users_started']), 4)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 25)  # All users have started
        self.assertEqual(len(response.data['users_completed']), 4)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 2)  # Two completions logged
        self.assertEqual(len(response.data['modules_completed']), 4)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 32)  # 25 for all + 5 for some + 2 for two
        self.assertEqual(len(response.data['active_users']), 4)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 30)  # All users active at some point in this timeframe
        self.assertEqual(response.data['users_enrolled'][1][1], 25)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 25)  # All users enrolled in third month of this series

    def test_courses_data_time_series_metrics_after_unenrolling_users(self):
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Unenroll five users from the course and run the time series report for the final eleven days
        test_uri = self.base_courses_uri + '/' + unicode(self.course.id) + '/users'
        for user in self.users[-5:]:
            unenroll_uri = '{}/{}'.format(test_uri, user.id)
            response = self.do_delete(unenroll_uri)
            self.assertEqual(response.status_code, 204)
        end_date = self.reference_date
        start_date = end_date - relativedelta(days=10)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&organization={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters),
            self.org_id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 11)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 10)  # 4,3,2,1, then all zeroes due to the unenrolling
        self.assertEqual(len(response.data['users_started']), 11)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 5)  # Five, then nothin
        self.assertEqual(len(response.data['users_completed']), 11)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 0)  # Only completions were on days 1 and 2
        self.assertEqual(len(response.data['modules_completed']), 11)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 10)  # We maintain the module completions after unenrolling
        self.assertEqual(len(response.data['active_users']), 11)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 11)

    def test_courses_data_time_series_metrics_user_group_filter(self):
        """
        Test time series metrics for users in a particular course and filter by organizations and group
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Generate the time series report for the first five days of the set, filtered by organization and group
        # There should be one time series entry per day for each category, each day having varying counts
        end_date = self.reference_date - timedelta(days=(self.user_count - 4))
        start_date = self.reference_date - timedelta(days=self.user_count)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&organization={}&groups={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters),
            self.org_id,
            self.groups[0].id
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 5)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 56)  # Aggregate total in the first five days in group 1 (12,12,11,11,10)
        self.assertEqual(len(response.data['users_started']), 5)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 3)  # Five users started in the first five days three in group 1
        self.assertEqual(len(response.data['users_completed']), 5)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 0)  # Zero users completed in the first five days
        self.assertEqual(len(response.data['modules_completed']), 5)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        # Five modules completed in the first five days, 3 users in group 1
        self.assertEqual(total_modules_completed, 3)
        self.assertEqual(len(response.data['active_users']), 5)
        total_active = sum([active[1] for active in response.data['active_users']])
        # Four active users in the first five days due to how 'active' is defined, 2 users in group 1
        self.assertEqual(total_active, 2)
        self.assertEqual(len(response.data['users_enrolled']), 5)
        self.assertEqual(response.data['users_enrolled'][0][1], 13)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 13)  # Everyone was enrolled on the first day, 13 users in group 1

    def test_courses_data_time_series_metrics_user_multiple_group_filter(self):
        """
        Calculate time series metrics for users in a particular course.
        """
        # Submit user scores for modules in the course
        self._submit_user_scores()

        # Generate the time series report for the first five days of the set, filtered by organization & multiple groups
        # There should be one time series entry per day for each category, each day having varying counts
        end_date = self.reference_date - timedelta(days=(self.user_count - 4))
        start_date = self.reference_date - timedelta(days=self.user_count)
        date_parameters = {
            'start_date': start_date,
            'end_date': end_date
        }
        group_ids = ','.join([str(group.id) for group in self.groups])
        course_metrics_uri = '{}/{}/time-series-metrics/?{}&organization={}&groups={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            urlencode(date_parameters),
            self.org_id,
            group_ids
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['users_not_started']), 5)
        total_not_started = sum([not_started[1] for not_started in response.data['users_not_started']])
        self.assertEqual(total_not_started, 110)  # Aggregate total in the first five days (24,23,22,21,20)
        self.assertEqual(len(response.data['users_started']), 5)
        total_started = sum([started[1] for started in response.data['users_started']])
        self.assertEqual(total_started, 5)  # Five users started in the first five days
        self.assertEqual(len(response.data['users_completed']), 5)
        total_completed = sum([completed[1] for completed in response.data['users_completed']])
        self.assertEqual(total_completed, 0)  # Zero users completed in the first five days
        self.assertEqual(len(response.data['modules_completed']), 5)
        total_modules_completed = sum([completed[1] for completed in response.data['modules_completed']])
        self.assertEqual(total_modules_completed, 5)  # Five modules completed in the first five days
        self.assertEqual(len(response.data['active_users']), 5)
        total_active = sum([active[1] for active in response.data['active_users']])
        self.assertEqual(total_active, 4)  # Four active users in the first five days due to how 'active' is defined
        self.assertEqual(len(response.data['users_enrolled']), 5)
        self.assertEqual(response.data['users_enrolled'][0][1], 25)
        total_enrolled = sum([enrolled[1] for enrolled in response.data['users_enrolled']])
        self.assertEqual(total_enrolled, 25)  # Remember, everyone was enrolled on the first day

    def test_courses_data_time_series_metrics_without_end_date(self):
        # Missing end date should raise an error
        start_date = self.reference_date - relativedelta(days=10)

        course_metrics_uri = '{}/{}/time-series-metrics/?start_date={}'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            start_date
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 400)

    def test_courses_data_time_series_metrics_with_invalid_interval(self):
        # Unsupported interval should raise an error
        end_date = self.reference_date
        start_date = end_date - relativedelta(days=10)

        course_metrics_uri = '{}/{}/time-series-metrics/?start_date={}&end_date={}&interval=hours'.format(
            self.base_courses_uri,
            unicode(self.course.id),
            start_date,
            end_date
        )
        response = self.do_get(course_metrics_uri)
        self.assertEqual(response.status_code, 400)

    def test_courses_time_series_invalid_start_date(self):
         # Test with an invalid format of start_date
         test_uri = '{}/{}/time-series-metrics/?start_date={}&end_date={}'.format(
             self.base_courses_uri,
             self.course.id,
             '21102016',
             self.reference_date
         )
         response = self.do_get(test_uri)
         self.assertEqual(response.status_code, 400)

    def test_courses_time_series_invalid_end_date(self):
        # Test with an invalid format of end_date
        start_date = self.reference_date - relativedelta(days=10)
        test_uri = '{}/{}/time-series-metrics/?start_date={}&end_date={}'.format(
         self.base_courses_uri,
         self.course.id,
         start_date,
         '21102016'
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 400)


class CoursesGradingMetricsTests(
    SignalDisconnectTestMixin,
    SharedModuleStoreTestCase,
    APIClientMixin,
    CourseGradingMixin,
):
    """ Test suite for courses grading metrics API views """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    @classmethod
    def setUpClass(cls):
        super(CoursesGradingMetricsTests, cls).setUpClass()

        cls.base_courses_uri = '/api/server/courses'
        cls.test_bogus_course_id = 'foo/bar/baz'
        cls.empty_course = CourseFactory.create(org='emptyX', run='empt1', display_name="Empty Course")
        # average = sum of grades / total users enrolled in course i.e excluding observers
        cls.expected_course_average = float("{0:.3f}".format((0.1 + 0.2 + 0.3) / 7.0))

    def _setup_courses_metrics_grades_leaders(self):
        """Setup for courses metrics grades leaders"""
        course = self.setup_course_with_grading()

        # Create 8 users that will enroll in course
        users = [UserFactory.create(username="testleaderuser" + str(__), profile='test') for __ in xrange(8)]
        groups = GroupFactory.create_batch(2)
        for i, user in enumerate(users):
            user.groups.add(groups[i % 2])
            CourseEnrollmentFactory.create(user=user, course_id=course.id)

        users[0].groups.add(groups[1])

        # record grades for 4 users in the users list
        for j, user in enumerate(users[-4:]):
            assignment = course.homework_assignment if j % 2 is 0 else course.midterm_assignment

            points_scored = (j + 1) * 20  # (20, 40, 60, 80) for all 4 users
            points_possible = 100
            module = self.get_module_for_user(user, course, assignment)
            grade_dict = {'value': points_scored, 'max_value': points_possible, 'user_id': user.id}
            module.system.publish(module, 'grade', grade_dict)

        # make the last user an observer to assert that its content is being filtered out from
        # the aggregates
        allow_access(course, users[-1], 'observer')
        return {
            'course': course,
            'users': users,
            'groups': groups
        }

    @make_non_atomic
    def test_courses_metrics_grades_leaders_list_get(self):  # pylint: disable=R0915
        # setup data for course metrics grades leaders
        data = self._setup_courses_metrics_grades_leaders()
        test_uri = '{}/{}/metrics/grades/leaders/'.format(self.base_courses_uri, unicode(data['course'].id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 3)
        self.assertIn('testleaderuser', response.data['leaders'][0]['username'])
        self.assertEqual(response.data['course_avg'], self.expected_course_average)

        count_filter_test_uri = '{}?count=2'.format(test_uri)
        response = self.do_get(count_filter_test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 2)

        # Filter by user_id, include a user with the exact same score
        user_filter_uri = '{}?user_id={}&count=10'.format(test_uri, data['users'][-2].id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 3)
        self.assertEqual(response.data['course_avg'], self.expected_course_average)
        self.assertEqual(response.data['user_position'], 1)
        self.assertEqual(response.data['user_grade'], 0.3)

        # Filter by user who has never accessed a course module
        test_user = UserFactory.create(username="testusernocoursemod")
        CourseEnrollmentFactory.create(user=test_user, course_id=data['course'].id)
        user_filter_uri = '{}?user_id={}'.format(test_uri, test_user.id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user_grade'], 0)
        self.assertEqual(response.data['user_position'], 4)

        expected_course_average = float("{0:.3f}".format((0.1 + 0.2 + 0.3 + 0.0) / 8.0))
        # Also, with this new user now added the course average should be different
        self.assertEqual(response.data['course_avg'], expected_course_average)

        # test with bogus course
        bogus_test_uri = '{}/{}/metrics/grades/leaders/'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(bogus_test_uri)
        self.assertEqual(response.status_code, 404)

    @make_non_atomic
    def test_courses_metrics_grades_leaders_list_get_filter_by_group(self):
        # setup data for course metrics grades leaders
        data = self._setup_courses_metrics_grades_leaders()
        test_uri = '{}/{}/metrics/grades/leaders/?groups={}'.format(self.base_courses_uri,
                                                                    unicode(data['course'].id),
                                                                    data['groups'][0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 2)
        self.assertIn('testleaderuser', response.data['leaders'][0]['username'])
        # filter on groups does not affect course average
        self.assertEqual(response.data['course_avg'], self.expected_course_average)

        count_filter_test_uri = '{}&count=10'.format(test_uri)
        response = self.do_get(count_filter_test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 2)

        user_filter_uri = '{}&user_id={}&count=10'.format(test_uri, data['users'][-2].id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 2)
        self.assertEqual(response.data['course_avg'], self.expected_course_average)
        self.assertEqual(response.data['user_position'], 1)
        self.assertEqual(response.data['user_grade'], 0.3)

    @make_non_atomic
    def test_courses_metrics_grades_leaders_list_get_filter_by_multiple_group(self):
        # setup data for course metrics grades leaders
        data = self._setup_courses_metrics_grades_leaders()
        group_ids = ','.join([str(group.id) for group in data['groups']])

        test_uri = '{}/{}/metrics/grades/leaders/?groups={}'.format(self.base_courses_uri,
                                                                    unicode(data['course'].id),
                                                                    group_ids)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 3)
        self.assertIn('testleaderuser', response.data['leaders'][0]['username'])
        self.assertEqual(response.data['course_avg'], self.expected_course_average)

        count_filter_test_uri = '{}&count=10'.format(test_uri)
        response = self.do_get(count_filter_test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 3)

        user_filter_uri = '{}&user_id={}&count=10'.format(test_uri, data['users'][-2].id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['leaders']), 3)
        self.assertEqual(response.data['course_avg'], self.expected_course_average)
        self.assertEqual(response.data['user_position'], 1)
        self.assertEqual(response.data['user_grade'], 0.3)

    def test_courses_metrics_grades_leaders_list_get_empty_course(self):
        test_uri = '{}/{}/metrics/grades/leaders/'.format(self.base_courses_uri, unicode(self.empty_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_avg'], 0)
        self.assertEqual(len(response.data['leaders']), 0)

    @make_non_atomic
    def test_courses_metrics_grades_leaders_exclude_roles(self):
        """
        Tests courses metrics grades leaders with `exclude_roles` filter
        """
        setup_data = self._setup_courses_metrics_grades_leaders()
        course = setup_data['course']

        # create a user, assign assistant role and add grade
        user = UserFactory.create()
        CourseEnrollmentFactory.create(user=user, course_id=unicode(course.id))
        allow_access(course, user, 'assistant')

        points_scored = 100
        points_possible = 100
        module = self.get_module_for_user(user, course, course.midterm_assignment)
        grade_dict = {'value': points_scored, 'max_value': points_possible, 'user_id': user.id}
        module.system.publish(module, 'grade', grade_dict)

        # test user with role assistant not excluded
        test_uri = '{}/{}/metrics/grades/leaders?count=6'.format(self.base_courses_uri, unicode(course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(filter(lambda leaders: leaders['username'] == user.username, response.data['leaders'])), 1)
        self.assertEqual(len(response.data['leaders']), 4)

        # test user with role assistant excluded
        test_uri = '{}&exclude_roles=assistant'.format(test_uri, unicode(course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(filter(lambda leaders: leaders['username'] == user.username, response.data['leaders'])), 0)
        self.assertEqual(len(response.data['leaders']), 4)

    @make_non_atomic
    def test_courses_metrics_grades_list_get(self):
        setup_data = self._setup_courses_metrics_grades_leaders()
        course = setup_data['course']

        test_uri = '{}/{}/metrics/grades'.format(self.base_courses_uri, unicode(course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['grade_average'], 0.2)
        self.assertEqual(response.data['grade_maximum'], 0.3)
        self.assertEqual(response.data['grade_minimum'], 0.1)
        self.assertEqual(response.data['grade_count'], 3)
        self.assertEqual(response.data['course_grade_average'], self.expected_course_average)
        self.assertEqual(response.data['course_grade_maximum'], 0.3)
        self.assertEqual(response.data['course_grade_minimum'], 0.1)
        self.assertEqual(response.data['course_grade_count'], 3)
        self.assertEqual(len(response.data['grades']), 3)

        # Filter by user_id
        user_filter_uri = '{}?user_id={},{}'.format(test_uri, setup_data['users'][-2].id, setup_data['users'][-3].id)
        response = self.do_get(user_filter_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['grade_average'], 0.2)
        self.assertEqual(response.data['grade_maximum'], 0.3)
        self.assertEqual(response.data['grade_minimum'], 0.2)
        self.assertEqual(response.data['grade_count'], 2)
        self.assertEqual(response.data['course_grade_average'], self.expected_course_average)
        self.assertEqual(response.data['course_grade_maximum'], 0.3)
        self.assertEqual(response.data['course_grade_minimum'], 0.1)
        self.assertEqual(response.data['course_grade_count'], 3)
        self.assertEqual(len(response.data['grades']), 2)

    @make_non_atomic
    def test_courses_metrics_grades_list_get_filter_users_by_group(self):
        # Retrieve the list of grades for course and filter by groups
        setup_data = self._setup_courses_metrics_grades_leaders()
        course = setup_data['course']
        test_uri = '{}/{}/metrics/grades?user_id={}&groups={}'.format(self.base_courses_uri,
                                                                      unicode(course.id),
                                                                      setup_data['users'][-2].id,
                                                                      setup_data['groups'][0].id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['grade_average'], 0.2)
        self.assertEqual(response.data['grade_maximum'], 0.3)
        self.assertEqual(response.data['grade_minimum'], 0.3)
        self.assertEqual(response.data['grade_count'], 1)
        self.assertEqual(response.data['course_grade_average'], self.expected_course_average)
        self.assertEqual(response.data['course_grade_maximum'], 0.3)
        self.assertEqual(response.data['course_grade_minimum'], 0.1)
        self.assertEqual(response.data['course_grade_count'], 3)
        self.assertEqual(len(response.data['grades']), 1)

    @make_non_atomic
    def test_courses_metrics_grades_list_get_filter_users_by_multiple_groups(self):
        # Retrieve the list of grades for course and filter by multiple groups and user_id
        setup_data = self._setup_courses_metrics_grades_leaders()
        course = setup_data['course']
        test_uri = '{}/{}/metrics/grades?groups={},{}'.format(
            self.base_courses_uri, unicode(course.id), setup_data['groups'][0].id, setup_data['groups'][1].id
        )
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.data['grade_average'], 0.2)
        self.assertEqual(response.data['grade_maximum'], 0.3)
        self.assertEqual(response.data['grade_minimum'], 0.1)
        self.assertEqual(response.data['grade_count'], 3)
        self.assertEqual(response.data['course_grade_average'], self.expected_course_average)
        self.assertEqual(response.data['course_grade_maximum'], 0.3)
        self.assertEqual(response.data['course_grade_minimum'], 0.1)
        self.assertEqual(response.data['course_grade_count'], 3)
        self.assertEqual(len(response.data['grades']), 3)

    def test_courses_metrics_grades_list_get_empty_course(self):
        # Retrieve the list of grades for this course
        # All the course/item/user scaffolding was handled in Setup
        test_uri = '{}/{}/metrics/grades'.format(self.base_courses_uri, unicode(self.empty_course.id))
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['grade_count'], 0)
        self.assertEqual(response.data['course_grade_maximum'], 0)

    def test_courses_grades_list_get_invalid_course(self):
        # Retrieve the list of grades for this course
        # All the course/item/user scaffolding was handled in Setup
        test_uri = '{}/{}/grades'.format(self.base_courses_uri, self.test_bogus_course_id)
        response = self.do_get(test_uri)
        self.assertEqual(response.status_code, 404)


class CoursesSocialMetricsApiTests(
    SignalDisconnectTestMixin, SharedModuleStoreTestCase, APIClientMixin
):
    """ Test suite for Courses social metrics API views """

    @classmethod
    def setUpClass(cls):
        super(CoursesSocialMetricsApiTests, cls).setUpClass()

        cls.course = CourseFactory.create()
        cls.social_leaders_api = reverse(
            'course-metrics-social-leaders', kwargs={'course_id': unicode(cls.course.id)}
        )
        cls.scores = [10, 20, 30, 40, 50, 60, 70]
        cls.course_avg = sum(cls.scores) / len(cls.scores)
        cls.users = []
        for score in cls.scores:
            user = UserFactory.create()
            cls.users.append(user)
            CourseEnrollmentFactory(user=user, course_id=cls.course.id)
            StudentSocialEngagementScore.objects.get_or_create(
                user=user, course_id=cls.course.id, defaults={'score': score}
            )

    def _create_org_with_users(self, users):
        data = {
            'name': 'Test Organization Attributes',
            'display_name': 'Test Org Display Name Attributes',
            'users': users
        }
        response = self.do_post('/api/server/organizations/', data)
        self.assertEqual(response.status_code, 201)
        return response.data['id']

    def test_social_metrics_leader_list(self):
        """
        Tests social metrics leader list API
        """
        response = self.do_get(self.social_leaders_api)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_avg'], self.course_avg)
        self.assertEqual(len(response.data['leaders']), 3)

    def test_social_metrics_leader_list_with_count(self):
        """
        Tests social metrics leader list API with count parameter
        """
        leaders_count = 5
        uri = "{}?count={}".format(self.social_leaders_api, leaders_count)
        response = self.do_get(uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_avg'], self.course_avg)
        self.assertEqual(len(response.data['leaders']), leaders_count)

    def test_social_metrics_leader_list_with_organizations(self):
        """
        Tests social metrics leader list API with organizations parameter
        """
        org_users = [self.users[0].id, self.users[1].id]
        org_id = self._create_org_with_users(org_users)
        uri = "{}?organizations={}".format(self.social_leaders_api, org_id)
        response = self.do_get(uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_avg'], 4)
        self.assertEqual(len(response.data['leaders']), len(org_users))

    def test_social_metrics_leader_list_with_user_position(self):
        """
        Tests social metrics leader list API with user_id parameter
        """
        uri = "{}?user_id={}".format(self.social_leaders_api, self.users[-1].id)
        response = self.do_get(uri)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['course_avg'], self.course_avg)
        self.assertEqual(len(response.data['leaders']), 3)
        self.assertEqual(response.data['position'], 1)
        self.assertEqual(response.data['score'], self.scores[-1])
