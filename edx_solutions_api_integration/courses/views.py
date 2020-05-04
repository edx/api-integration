""" API implementation for course-oriented interactions. """

import itertools
import logging
import warnings
import sys
from StringIO import StringIO
from collections import OrderedDict
from datetime import timedelta, datetime

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.core.exceptions import FieldDoesNotExist, ObjectDoesNotExist
from django.db.models import Count, F, Max, Min, Prefetch, Q, Sum
from django.http import Http404
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

from pytz import UTC
from lxml import etree
from multiprocessing.pool import ThreadPool
from requests.exceptions import ConnectionError
from rest_framework import status
from rest_framework.response import Response

from completion.models import BlockCompletion
from completion_aggregator.models import Aggregator
from courseware.courses import (
    get_course_about_section,
    get_course_info_section,
    get_course_info_section_module,
)
from courseware.models import StudentModule
from courseware.views.views import get_static_tab_fragment
from django_comment_common.models import FORUM_ROLE_MODERATOR
from gradebook.models import StudentGradebook
from instructor.access import revoke_access, update_forum_role
from lms.djangoapps.course_api.blocks.api import get_blocks
from lms.lib.comment_client.thread import get_course_thread_stats
from lms.lib.comment_client.utils import CommentClientMaintenanceError, CommentClientRequestError
from mobile_api.course_info.views import apply_wrappers_to_content
from opaque_keys.edx.keys import UsageKey
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from openedx.core.djangoapps.content.course_structures.api.v0.errors import CourseStructureNotAvailableError
from openedx.core.djangoapps.content.course_structures.models import CourseStructure
from openedx.core.djangoapps.course_groups.cohorts import get_cohort_user_ids
from openedx.core.djangoapps.course_groups.models import CourseUserGroup
from openedx.core.lib.courses import course_image_url
from opaque_keys.edx.keys import CourseKey
from openedx.core.lib.xblock_utils import get_course_update_items
from social_engagement.models import StudentSocialEngagementScore
from student.models import CourseEnrollment, CourseEnrollmentAllowed
from student.roles import (
    CourseAccessRole,
    CourseAssistantRole,
    CourseInstructorRole,
    CourseObserverRole,
    CourseStaffRole,
    UserBasedRole,
)
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError
from xmodule.modulestore.search import path_to_location

from edx_solutions_api_integration.courses.serializers import (
    BlockCompletionSerializer,
    CourseCompletionsLeadersSerializer,
    CourseProficiencyLeadersSerializer,
    CourseSerializer,
    CourseSocialLeadersSerializer,
    GradeSerializer,
    UserGradebookSerializer,
)
from edx_solutions_api_integration.courses.utils import (
    generate_leaderboard,
    get_num_users_started,
    get_total_completions,
    get_user_position,
    get_filtered_aggregation_queryset,
)
from edx_solutions_api_integration.courseware_access import (
    course_exists,
    get_course,
    get_course_child,
    get_course_child_key,
    get_course_key,
)
from edx_solutions_api_integration.models import (
    CourseContentGroupRelationship,
    CourseGroupRelationship,
    GroupProfile,
)
from edx_solutions_api_integration.permissions import (
    IsStaffView,
    MobileAPIView,
    MobileListAPIView,
    SecureAPIView,
    SecureListAPIView,
)
from edx_solutions_api_integration.users.serializers import UserSerializer, UserCountByCitySerializer
from edx_solutions_api_integration.utils import (
    cache_course_data,
    cache_course_user_data,
    css_param_to_list,
    generate_base_uri,
    get_aggregate_exclusion_user_ids,
    get_cached_data,
    get_ids_from_list_param,
    get_time_series_data,
    get_user_from_request_params,
    parse_datetime,
    is_cohort_available,
    str2bool,
    strip_xblock_wrapper_div,
    get_non_actual_company_users,
    Round,
)
from edx_solutions_organizations.models import Organization
from edx_solutions_projects.models import Project, Workgroup
from edx_solutions_projects.serializers import (
    BasicWorkgroupSerializer,
    ProjectSerializer,
)
from edx_solutions_api_integration.tasks import (
    convert_ooyala_to_bcove,
    get_modules_with_video_embeds,
    get_assets_with_incorrect_urls,
)


BLOCK_DATA_FIELDS = ['children', 'display_name', 'type', 'due', 'start']
log = logging.getLogger(__name__)


def _inner_content(tag):
    """
    Helper method
    """
    inner_content = None
    if tag is not None:
        inner_content = tag.text if tag.text else u''
        inner_content += u''.join(etree.tostring(e) for e in tag)  # pylint: disable=E1101
        inner_content += tag.tail if tag.tail else u''

    return inner_content


def _parse_overview_html(html):
    """
    Helper method to break up the course about HTML into components
    Overview content is stored in MongoDB (aka, the module store) with the following naming convention

            {
                "_id.org":"i4x",
                "_id.course":<course_num>,
                "_id.category":"about",
                "_id.name":"overview"
            }
    """
    result = {}

    parser = etree.HTMLParser()  # pylint: disable=E1101
    tree = etree.parse(StringIO(html), parser)  # pylint: disable=E1101

    sections = tree.findall('/body/section')

    result = []
    for section in sections:
        section_class = section.get('class')
        if section_class:
            section_data = OrderedDict()
            section_data['class'] = section_class

            section_data['attributes'] = {}
            for attribute_key in section.keys():
                # don't return the class attribute as we are already using the class attribute
                # as a key name to the result set, so we don't want to end up duplicating it
                if attribute_key != 'class':
                    section_data['attributes'][attribute_key] = section.get(attribute_key)

            articles = section.findall('article')
            if articles:
                section_data['articles'] = []
                for article in articles:
                    article_class = article.get('class')
                    if article_class:
                        article_data = OrderedDict()
                        article_data['class'] = article_class

                        if article_class == "teacher":

                            name_element = article.find('h3')
                            if name_element is not None:
                                article_data['name'] = name_element.text

                            image_element = article.find("./div[@class='teacher-image']/img")
                            if image_element is not None:
                                article_data['image_src'] = image_element.get('src')

                            bios = article.findall('p')
                            bio_html = ''
                            for bio in bios:
                                bio_html += etree.tostring(bio)  # pylint: disable=E1101

                            if bio_html:
                                article_data['bio'] = bio_html
                        else:
                            article_data['body'] = _inner_content(article)

                        section_data['articles'].append(article_data)
            else:
                section_data['body'] = _inner_content(section)

            result.append(section_data)

    return result


def _manage_role(course_descriptor, user, role, action):
    """
    Helper method for managing course/forum roles
    """
    supported_roles = ('instructor', 'staff', 'observer', 'assistant')
    forum_moderator_roles = ('instructor', 'staff', 'assistant')
    if role not in supported_roles:
        raise ValueError
    if action is 'allow':
        existing_role = CourseAccessRole.objects.filter(
            user=user,
            role=role,
            course_id=course_descriptor.id,
            org=course_descriptor.org,
        )
        if not existing_role:
            new_role = CourseAccessRole(user=user, role=role, course_id=course_descriptor.id, org=course_descriptor.org)
            new_role.save()
        if role in forum_moderator_roles:
            update_forum_role(course_descriptor.id, user, FORUM_ROLE_MODERATOR, 'allow')
    elif action is 'revoke':
        revoke_access(course_descriptor, user, role)
        if role in forum_moderator_roles:
            # There's a possibilty that the user may play more than one role in a course
            # And that more than one of these roles allow for forum moderation
            # So we need to confirm the removed role was the only role for this user for this course
            # Before we can safely remove the corresponding forum moderator role
            user_instructor_courses = UserBasedRole(user, CourseInstructorRole.ROLE).courses_with_role()
            user_staff_courses = UserBasedRole(user, CourseStaffRole.ROLE).courses_with_role()
            user_assistant_courses = UserBasedRole(user, CourseAssistantRole.ROLE).courses_with_role()
            queryset = user_instructor_courses | user_staff_courses | user_assistant_courses
            queryset = queryset.filter(course_id=course_descriptor.id)
            if len(queryset) == 0:
                update_forum_role(course_descriptor.id, user, FORUM_ROLE_MODERATOR, 'revoke')


def _make_block_tree(request, blocks_data, course_key, course_block, block=None, depth=1, usage_key=None, content_block=None):    # pylint: disable=line-too-long
    """
    Its a nested method that will return a serialized details
    of a content block and its children depending on the depth.
    usage_key must be provided in case of no root/parent block.
    """
    data = {}
    children = []

    base_content_uri = '{}://{}/api/server/courses'.format(
        request.scheme,
        request.get_host()
    )

    if block:
        data['id'] = block.get('id', None)
        data['name'] = block.get('display_name', None)
        data['due'] = block.get('due', None)
        data['start'] = block.get('start', None)
        data['category'] = block.get('type', None)

        if 'children' in block and depth > 0:
            for child in block['children']:
                child_content = _make_block_tree(
                    request, blocks_data, course_key, course_block, blocks_data[child], depth-1
                )
                children.append(child_content)

        if data['category'] and data['category'] == 'course':
            content_id = unicode(course_block.id)
            content_uri = '{}/{}'.format(base_content_uri, content_id)
            data['content'] = children
            data['end'] = getattr(course_block, 'end', None)
            data['number'] = course_block.location.course
            data['org'] = course_block.location.org
            data['id'] = unicode(course_block.id)
        else:
            data['children'] = children
            content_uri = '{}/{}/content/{}'.format(base_content_uri, unicode(course_key), data['id'])

        data['uri'] = content_uri

        include_fields = request.query_params.get('include_fields', None)
        if include_fields:
            include_fields = include_fields.split(',')
            for field in include_fields:
                data[field] = getattr(content_block, field, None)
        return data
    else:
        # result from the course block method includes the parent block too.
        # usage_key is needed as we have to filter out that parent block.
        if usage_key is None:
            raise KeyError("Usage key must be provided")

        for block_key, block_value in blocks_data.items():
            if block_key != unicode(usage_key):
                children.append(
                    _make_block_tree(request, blocks_data, course_key, course_block, block_value, depth - 1)
                )
        return children


def _get_static_tab_contents(request, course, tab, strip_wrapper_div=True):
    """
    Wrapper around get_static_tab_contents to cache contents for the given static tab
    """
    cache_key = u'course.{course_id}.static.tab.{url_slug}.contents'.format(course_id=course.id, url_slug=tab.url_slug)
    contents = cache.get(cache_key)
    if contents is None:
        contents = get_static_tab_fragment(request, course, tab).content
        _cache_static_tab_contents(cache_key, contents)

    if strip_wrapper_div:
        contents = strip_xblock_wrapper_div(contents)
    return contents


def _cache_static_tab_contents(cache_key, contents):
    """
    Caches course static tab contents.
    """
    cache_expiration = getattr(settings, 'STATIC_TAB_CONTENTS_CACHE_TTL', 60 * 5)
    contents_max_size_limit = getattr(settings, 'STATIC_TAB_CONTENTS_CACHE_MAX_SIZE_LIMIT', 4000)

    if not sys.getsizeof(contents) > contents_max_size_limit:
        cache.set(cache_key, contents, cache_expiration)


def _get_course_progress_metrics(course_key, **kwargs):
    """
    returns a dict containing these course progress metrics
    `course_avg`: average progress in course
    `completions`: given user's progress percentage
    `position`: given user's position in progress leaderboard
    `total_users`: total user's enrolled
    `total_possible_completions`: total possible modules to be completed
    """
    course_avg = 0
    data = {'course_avg': course_avg}
    total_actual_or_percent_completions, total_possible_completions = get_total_completions(course_key, **kwargs)
    if kwargs.get('user_id'):
        data.update(get_user_position(course_key, **kwargs))
    total_users_qs = CourseEnrollment.objects.users_enrolled_in(course_key).exclude(id__in=kwargs.get('exclude_users'))
    if kwargs.get('org_ids'):
        total_users_qs = total_users_qs.filter(organizations__in=kwargs.get('org_ids'))
    if kwargs.get('group_ids'):
        total_users_qs = total_users_qs.filter(groups__in=kwargs.get('group_ids')).distinct()
    if kwargs.get('cohort_user_ids'):
        total_users_qs = total_users_qs.filter(id__in=kwargs.get('cohort_user_ids'))
    total_users = total_users_qs.count()
    if total_users and total_actual_or_percent_completions and total_possible_completions:
        course_avg = total_actual_or_percent_completions / float(total_users)
        if not kwargs.get('percent_completion'):
            course_avg = min(100 * (course_avg / total_possible_completions), 100)  # avg in percentage
    data['course_avg'] = course_avg
    data['total_users'] = total_users
    data['total_possible_completions'] = total_possible_completions
    return data


def _get_courses_metrics_grades_leaders_list(course_key, **kwargs):
    course_id = course_key.to_deprecated_string()
    user_id = kwargs.get('user_id')
    data = {}

    if kwargs.get('skipleaders') and user_id:
        cached_grade_data = get_cached_data('grade', course_id, user_id)
        if not cached_grade_data:
            data['course_avg'] = StudentGradebook.course_grade_avg(course_key, **kwargs)
            data['user_grade'] = StudentGradebook.get_user_grade(course_key, user_id)
            cache_course_data('grade', course_id, {'course_avg': data['course_avg']})
            cache_course_user_data('grade', course_id, user_id, {'user_grade': data['user_grade']})
        else:
            data.update(cached_grade_data)
    else:
        cached_leader_board_data = get_cached_data('grade_leaderboard', course_id)
        cached_grade_data = get_cached_data('grade', course_id, user_id)
        if cached_leader_board_data and cached_grade_data and 'user_position' in cached_grade_data and not \
                (kwargs.get('group_ids') or kwargs.get('exclude_roles')):
            data.update(cached_grade_data)
            data.update(cached_leader_board_data)
        else:
            data.update(StudentGradebook.generate_leaderboard(course_key, **kwargs))

            serializer = CourseProficiencyLeadersSerializer(data.pop('queryset'), many=True)
            data['leaders'] = serializer.data  # pylint: disable=E1101
            leader_boards_cache_cohort_size = getattr(settings, 'LEADER_BOARDS_CACHE_COHORT_SIZE', 5000)
            if kwargs.get('user_id'):
                data.update(StudentGradebook.get_user_position(course_key, **kwargs))

                if data.pop('enrollment_count') > leader_boards_cache_cohort_size:
                    cache_course_data('grade', course_id, {'course_avg': data['course_avg']})
                    cache_course_data('grade_leaderboard', course_id, {'leaders': data['leaders']})
                    cache_course_user_data('grade', course_id, user_id, {
                        'user_grade': data.get('user_grade', 0), 'user_position': data['user_position']
                    })
            else:
                data.pop('enrollment_count')

    return data


def _get_courses_metrics_completions_leaders_list(course_key, **kwargs):
    course_id = course_key.to_deprecated_string()
    user_id = kwargs.get('user_id')
    data = {}

    if user_id:  # for single user's progress fetch from cache if available
        cached_progress_data = get_cached_data('progress', course_id, user_id)
        if cached_progress_data:
            data.update(cached_progress_data)
            if kwargs.get('skipleaders'):
                return data

            cached_leader_board_data = get_cached_data('progress_leaderboard', course_id)
            if cached_leader_board_data and \
                    not kwargs.get(('org_ids') or kwargs.get('group_ids') or kwargs.get('exclude_roles')):
                data.update(cached_leader_board_data)
                return data

    data = _get_course_progress_metrics(course_key, **kwargs)
    total_users = data['total_users']

    if not kwargs.get('skipleaders') and 'leaders' not in data:
        queryset = generate_leaderboard(course_key, **kwargs)
        serializer = CourseCompletionsLeadersSerializer(queryset, many=True)
        data['leaders'] = serializer.data  # pylint: disable=E1101
        leader_boards_cache_cohort_size = getattr(settings, 'LEADER_BOARDS_CACHE_COHORT_SIZE', 5000)
        if total_users > leader_boards_cache_cohort_size:
            cache_course_data('progress_leaderboard', course_id, {'leaders': data['leaders']})
    else:
        cache_course_data('progress', course_id, {
            'course_avg': data['course_avg'],
            'total_users': data['total_users'],
            'total_possible_completions': data['total_possible_completions'],
        })

        # set user data in cache only if the user exists
        if user_id:
            cache_course_user_data('progress', course_id, user_id, {
                'completions': data['completions'], 'position': data['position']
            })

    return data


def _get_courses_metrics_social_leaders_list(course_key, **kwargs):
    course_id = course_key.to_deprecated_string()
    user_id = kwargs.get('user_id')
    data = {}

    cached_social_data = get_cached_data('social', course_id, user_id)
    cached_leader_board_data = get_cached_data('social_leaderboard', course_id)
    if cached_leader_board_data and cached_social_data and 'position' in cached_social_data and \
            not (kwargs.get('org_ids') or kwargs.get('exclude_users')):
        data.update(cached_social_data)
        data.update(cached_leader_board_data)
        return data

    data.update(StudentSocialEngagementScore.generate_leaderboard(course_key, **kwargs))

    serializer = CourseSocialLeadersSerializer(data.pop('queryset'), many=True)
    data['leaders'] = serializer.data  # pylint: disable=E1101

    if user_id:
        user_data = StudentSocialEngagementScore.get_user_leaderboard_position(course_key, **kwargs)
        data.update(user_data)
        leader_boards_cache_cohort_size = getattr(settings, 'LEADER_BOARDS_CACHE_COHORT_SIZE', 5000)
        if data.pop('total_user_count') > leader_boards_cache_cohort_size:
            cache_course_user_data('social', course_id, user_id, {"score": data['score'], "position": data['position']})
            cache_course_data('social', course_id, {'course_avg': data['course_avg']})
            cache_course_data('social_leaderboard', course_id, {'leaders': data['leaders']})
    else:
        data.pop('total_user_count')

    return data


class CourseContentList(SecureAPIView):
    """
    **Use Case**

        CourseContentList gets a collection of content for a given
        course. You can use the **uri** value in
        the response to get details for that content entity.

        CourseContentList has an optional type parameter that allows you to
        filter the response by content type. The value of the type parameter
        matches the category value in the response. Valid values for the type
        parameter are:

        * chapter
        * sequential
        * vertical
        * html
        * problem
        * discussion
        * video
        * [CONFIRM]

    **Example requests**:

        GET /api/courses/{course_id}/content

        GET /api/courses/{course_id}/content?type=video

        GET /api/courses/{course_id}/content/{content_id}/children

    **Response Values**

        * category: The type of content.

        * due: The due date.

        * uri: The URI to use to get details of the content entity.

        * id: The unique identifier for the content entity.

        * name: The name of the course.
    """

    def get(self, request, course_id, content_id=None):
        """
        GET /api/courses/{course_id}/content
        """
        content_type = request.query_params.get('type', None)
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        if content_id is None:
            content_id = course_id
        if course_id != content_id:
            usage_key = UsageKey.from_string(content_id)
        else:
            usage_key = modulestore().make_course_usage_key(course_key)
        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
        data_blocks = get_blocks(
            request,
            usage_key,
            depth=1,
            requested_fields=BLOCK_DATA_FIELDS,
            block_types_filter=content_type
        )
        response_data = _make_block_tree(
            request, data_blocks['blocks'], course_key, course_descriptor, usage_key=usage_key
        )
        return Response(response_data, status=status.HTTP_200_OK)


class CourseContentDetail(SecureAPIView):
    """
    **Use Case**

        CourseContentDetail returns a JSON collection for a specified
        CourseContent entity. If the specified CourseContent is the Course, the
        course representation is returned. You can use the uri values in the
        children collection in the JSON response to get details for that content
        entity.

        CourseContentDetail has an optional type parameter that allows you to
        filter the response by content type. The value of the type parameter
        matches the category value in the response. Valid values for the type
        parameter are:

        * chapter
        * sequential
        * vertical
        * html
        * problem
        * discussion
        * video
        * [CONFIRM]

    **Example Request**

          GET /api/courses/{course_id}/content/{content_id}

    **Response Values**

        * category: The type of content.

        * name: The name of the content entity.

        * due:  The due date.

        * uri: The URI of the content entity.

        * id: The unique identifier for the course.

        * children: Content entities that this conent entity contains.

        * resources: A list of URIs to available users and groups:
          * Related Users  /api/courses/{course_id}/content/{content_id}/users
          * Related Groups /api/courses/{course_id}/content/{content_id}/groups
    """

    def get(self, request, course_id, content_id):
        """
        GET /api/courses/{course_id}/content/{content_id}
        depth is 1 as we have to return only the children of the given node not more than that
        """
        response_data = {}
        child_descriptor = None
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        if course_id != content_id:
            if 'include_fields' in request.query_params:
                # Here we need to get some additional fields from child_descriptor.
                # Only needed when we have include_fields param in request
                child_descriptor, child_key, child_content = get_course_child(request, request.user, course_key, content_id, True)  # pylint: disable=line-too-long
            usage_key = UsageKey.from_string(content_id)
        else:
            usage_key = modulestore().make_course_usage_key(course_key)
            protocol = 'http'
            if request.is_secure():
                protocol = protocol + 's'
            response_data['uri'] = '{}://{}/api/server/courses/{}'.format(
                protocol,
                request.get_host(),
                unicode(course_key)
            )
        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
        data_blocks = get_blocks(
            request,
            usage_key,
            depth=1,
            requested_fields=BLOCK_DATA_FIELDS
        )
        root_block = data_blocks['blocks'][data_blocks['root']]
        response_data = _make_block_tree(
            request,
            data_blocks['blocks'],
            course_key,
            course_descriptor,
            root_block,
            content_block=child_descriptor
        )
        base_uri_without_qs = generate_base_uri(request, True)
        resource_uri = '{}/groups'.format(base_uri_without_qs)
        response_data['resources'] = []
        response_data['resources'].append({'uri': resource_uri})
        resource_uri = '{}/users'.format(base_uri_without_qs)
        response_data['resources'].append({'uri': resource_uri})
        return Response(response_data, status=status.HTTP_200_OK)


class CoursesList(SecureListAPIView):
    """
    **Use Case**

        CoursesList returns paginated list of courses in the edX Platform. You can
        use the uri value in the response to get details of the course. course list can be
        filtered by course_id

    **Example Request**

          GET /api/courses
          GET /api/courses/?course_id={course_id1},{course_id2}

    **Response Values**

        * category: The type of content. In this case, the value is always "course".

        * name: The name of the course.

        * uri: The URI to use to get details of the course.

        * number: The course number.

        * due:  The due date. For courses, the value is always null.

        * org: The organization specified for the course.

        * id: The unique identifier for the course.
    """
    serializer_class = CourseSerializer

    def get_queryset(self):
        course_ids = css_param_to_list(self.request, 'course_id')
        if course_ids:
            course_keys = [get_course_key(course_id) for course_id in course_ids]
            results = CourseOverview.get_select_courses(course_keys)
        else:
            results = CourseOverview.get_all_courses()
        return results


class CoursesDetail(MobileAPIView):
    """
    **Use Case**

        CoursesDetail returns details for a course. You can use the uri values
        in the resources collection in the response to get more course
        information for:

        * Users (/api/courses/{course_id}/users/)
        * Groups (/api/courses/{course_id}/groups/)
        * Course Overview (/api/courses/{course_id}/overview/)
        * Course Updates (/api/courses/{course_id}/updates/)
        * Course Pages (/api/courses/{course_id}/static_tabs/)

        CoursesDetail has an optional **depth** parameter that allows you to
        get course content children to the specified tree level.

    **Example requests**:

        GET /api/courses/{course_id}

        GET /api/courses/{course_id}?depth=2

    **Response Values**

        * category: The type of content.

        * name: The name of the course.

        * uri: The URI to use to get details of the course.

        * number: The course number.

        * content: When the depth parameter is used, a collection of child
          course content entities, such as chapters, sequentials, and
          components.

        * due:  The due date. For courses, the value is always null.

        * org: The organization specified for the course.

        * id: The unique identifier for the course.

        * resources: A collection of URIs to use to get more information about
          the course.
    """

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}
        """
        user = request.user
        query_username = request.query_params.get('username')
        # Staff users can make requests on behalf of other users.
        if user.is_staff and query_username is not None:
            try:
                user = User.objects.get(username=query_username)
            except User.DoesNotExist:
                raise Http404()

        course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        usage_key = modulestore().make_course_usage_key(course_key)
        usage_key = usage_key.replace(course_key=modulestore().fill_in_run(usage_key.course_key))
        depth = int(request.query_params.get('depth', 0))
        try:
            data_blocks = get_blocks(
                request,
                usage_key,
                user=user,
                depth=depth,
                requested_fields=BLOCK_DATA_FIELDS
            )
            root_block = data_blocks.get('blocks', {}).get(
                data_blocks['root'],
                {
                    'id': unicode(course_descriptor.id),
                    'display_name': course_descriptor.display_name,
                    'type': course_descriptor.category
                }
            )
            response_data = _make_block_tree(
                request,
                data_blocks['blocks'],
                course_key,
                course_descriptor,
                root_block,
                depth,
                content_block=course_descriptor
            )
            base_uri_without_qs = generate_base_uri(request, True)
            if unicode(course_descriptor.id) not in base_uri_without_qs:
                base_uri_without_qs = '{}/{}'.format(base_uri_without_qs, unicode(course_descriptor.id))
            image_url = ''
            if hasattr(course_descriptor, 'course_image') and course_descriptor.course_image:
                image_url = course_image_url(course_descriptor)
            response_data['language'] = course_descriptor.language
            response_data['course_image_url'] = image_url
            response_data['resources'] = []
            resource_uri = '{}/content/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            resource_uri = '{}/groups/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            resource_uri = '{}/overview/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            resource_uri = '{}/updates/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            resource_uri = '{}/static_tabs/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            resource_uri = '{}/users/'.format(base_uri_without_qs)
            response_data['resources'].append({'uri': resource_uri})
            return Response(response_data, status=status.HTTP_200_OK)
        except (ItemNotFoundError, CourseStructureNotAvailableError) as exception:
            raise Http404("Block not found: {}".format(exception.message))


class CoursesGroupsList(SecureAPIView):
    """
    **Use Case**

        CoursesGroupsList returns a collection of course group relationship
        entities(?) for a specified course entity.

        CoursesGroupsList has an optional **type** parameter that allows you to
        filter the groups returned. Valid values for the type parameter are:

        * [CONFIRM]

    **Example Request**

        GET /api/courses/{course_id}/groups?type=workgroup

        POST /api/courses/{course_id}/groups

    **Response Values**


    ### The CoursesGroupsList view allows clients to retrieve a list of Groups for a given Course entity
    - URI: ```/api/courses/{course_id}/groups/```
    - GET: Returns a JSON representation (array) of the set of CourseGroupRelationship entities
        * type: Set filtering parameter
    - POST: Creates a new relationship between the provided Course and Group
        * group_id: __required__, The identifier for the Group with which we're establishing a relationship
    - POST Example:

            {
                "group_id" : 12345,
            }
    ### Use Cases/Notes:
    * Example: Display all of the courses for a particular academic series/program
    * If a relationship already exists between a Course and a particular group, the system returns 409 Conflict
    * The 'type' parameter filters groups by their 'group_type' field ('workgroup', 'series', etc.)
    * The 'type' parameter can be a single value or comma separated list of values ('workgroup,series')
    """

    def post(self, request, course_id):
        """
        POST /api/courses/{course_id}/groups
        """
        response_data = {}
        group_id = request.data.get('group_id', None)
        if not group_id:
            return Response({'message': _('group_id is missing')}, status.HTTP_400_BAD_REQUEST)

        base_uri = generate_base_uri(request)
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            existing_group = None
        if existing_group:
            try:
                existing_relationship = CourseGroupRelationship.objects.get(course_id=course_key, group=existing_group)
            except ObjectDoesNotExist:
                existing_relationship = None
            if existing_relationship is None:
                CourseGroupRelationship.objects.create(course_id=course_key, group=existing_group)
                response_data['course_id'] = unicode(course_key)
                response_data['group_id'] = str(existing_group.id)
                response_data['uri'] = '{}/{}'.format(base_uri, existing_group.id)
                response_status = status.HTTP_201_CREATED
            else:
                response_data['message'] = "Relationship already exists."
                response_status = status.HTTP_409_CONFLICT
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}/groups?type=workgroup
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        group_type = css_param_to_list(request, 'type')
        course_key = get_course_key(course_id)
        course_groups = CourseGroupRelationship.objects.filter(course_id=course_key)
        if group_type:
            course_groups = course_groups.filter(group__groupprofile__group_type__in=group_type)
        response_data = []
        group_profiles = GroupProfile.objects.filter(
            group_id__in=[course_group.group_id for course_group in course_groups]
        )
        for group_profile in group_profiles:
            group_data = {'id': group_profile.group_id, 'name': group_profile.name}
            response_data.append(group_data)
        response_status = status.HTTP_200_OK
        return Response(response_data, status=response_status)


class CoursesGroupsDetail(SecureAPIView):
    """
    ### The CoursesGroupsDetail view allows clients to interact with a specific CourseGroupRelationship entity
    - URI: ```/api/courses/{course_id}/group/{group_id}```
    - GET: Returns a JSON representation of the specified CourseGroupRelationship entity
        * type: Set filtering parameter
    - DELETE: Removes an existing CourseGroupRelationship from the system
    ### Use Cases/Notes:
    * Use this operation to confirm the existence of a specific Course-Group entity relationship
    """

    def get(self, request, course_id, group_id):
        """
        GET /api/courses/{course_id}/groups/{group_id}
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        try:
            existing_group = Group.objects.get(id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        try:
            course_key = get_course_key(course_id)
            CourseGroupRelationship.objects.get(course_id=course_key, group=existing_group)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        response_data['course_id'] = course_id
        response_data['group_id'] = group_id
        return Response(response_data, status=status.HTTP_200_OK)

    def delete(self, request, course_id, group_id):
        """
        DELETE /api/courses/{course_id}/groups/{group_id}
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_204_NO_CONTENT)
        try:
            existing_group = Group.objects.get(id=group_id)
            course_key = get_course_key(course_id)
            CourseGroupRelationship.objects.get(course_id=course_key, group=existing_group).delete()
        except ObjectDoesNotExist:
            pass
        response_data = {}
        response_data['uri'] = generate_base_uri(request)
        return Response(response_data, status=status.HTTP_204_NO_CONTENT)


class CoursesEnrollmentCount(SecureAPIView):
    """
    **Use Case**

        CoursesEnrollmentCount returns count of users enrolled in course.

    **Example Request**

          GET /api/courses/{course_id}/enrollment_count


    **Response Values**

        * enrollment_count: Count of users enrolled in course.

    """

    def get(self, request, course_id):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/enrollment_count
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data = {}
        enrollment_count = CoursesMetrics.get_course_enrollment_count(course_id)
        response_data['enrollment_count'] =  enrollment_count
        return Response(response_data, status=status.HTTP_200_OK)


class CoursesOverview(SecureAPIView):
    """
    **Use Case**

        CoursesOverview returns an HTML representation of the overview for the
        specified course. CoursesOverview has an optional parse parameter that
        when true breaks the response into a collection named sections. By
        default, parse is false.

    **Example Request**

          GET /api/courses/{course_id}/overview

          GET /api/courses/{course_id}/overview?parse=true

    **Response Values**

        * overview_html: The HTML representation of the course overview.
          Sections of the overview are indicated by an HTML section element.

        * sections: When parse=true, a collection of JSON objects representing
          parts of the course overview.

    """

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}/overview
        """
        response_data = OrderedDict()
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        existing_content = get_course_about_section(request, course_descriptor, 'overview')
        if not existing_content:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        if request.GET.get('parse') and request.GET.get('parse') in ['True', 'true']:
            response_data['sections'] = _parse_overview_html(existing_content)
        else:
            response_data['overview_html'] = existing_content

        course_overview = CourseOverview.get_from_id(course_key)
        response_data['course_image_urls'] = course_overview.image_urls
        response_data['course_video'] = get_course_about_section(request, course_descriptor, 'video')
        return Response(response_data, status=status.HTTP_200_OK)


class CoursesUpdates(SecureAPIView):
    """
    **Use Case**

        CoursesUpdates returns an HTML representation of the overview for the
        specified course. CoursesUpdates has an optional parse parameter that
        when true breaks the response into a collection named postings. By
        default, parse is false.

    **Example Requests**

          GET /api/courses/{course_id}/updates

          GET /api/courses/{course_id}/updates?parse=true

    **Response Values**

        * content: The HTML representation of the course overview.
          Sections of the overview are indicated by an HTML section element.

        * postings: When parse=true, a collection of JSON objects representing
          parts of the course overview. Each element in postings contains a date
          and content key.
    """

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}/updates
        """
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data = OrderedDict()
        if request.GET.get('parse') and request.GET.get('parse') in ['True', 'true']:
            course_updates_module = get_course_info_section_module(request, request.user, course_descriptor, 'updates')
            update_items = get_course_update_items(course_updates_module)

            updates_to_show = [
                update for update in update_items
                if update.get("status") != "deleted"
            ]

            for item in updates_to_show:
                item['content'] = apply_wrappers_to_content(item['content'], course_updates_module, request)
            response_data['postings'] = updates_to_show
        else:
            content = get_course_info_section(request, request.user, course_descriptor, 'updates')
            if not content:
                return Response({}, status=status.HTTP_404_NOT_FOUND)
            response_data['content'] = content
        return Response(response_data)


class CoursesStaticTabsList(SecureAPIView):
    """
    **Use Case**

        CoursesStaticTabsList returns a collection of custom pages in the
        course. CoursesStaticTabsList has an optional detail parameter that when
        true includes the custom page content in the response.

    **Example Requests**

          GET /api/courses/{course_id}/static_tabs

          GET /api/courses/{course_id}/static_tabs?detail=true

    **Response Values**

        * tabs: The collection of custom pages in the course. Each object in the
          collection conains the following keys:

          * id: The ID of the custom page.

          * name: The Display Name of the custom page.

          * detail: When detail=true, the content of the custom page as HTML.
    """

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}/static_tabs
        """
        user = get_user_from_request_params(self.request, self.kwargs)
        course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        strip_wrapper_div = str2bool(self.request.query_params.get('strip_wrapper_div', 'true'))
        response_data = OrderedDict()
        tabs = []
        for tab in course_descriptor.tabs:
            if tab.type == 'static_tab':
                tab_data = OrderedDict()
                tab_data['id'] = tab.url_slug
                tab_data['name'] = tab.name
                if request.GET.get('detail') and request.GET.get('detail') in ['True', 'true']:
                    tab_data['content'] = _get_static_tab_contents(
                        request,
                        course_descriptor,
                        tab,
                        strip_wrapper_div
                    )
                tabs.append(tab_data)
        response_data['tabs'] = tabs
        return Response(response_data)


class CoursesStaticTabsDetail(SecureAPIView):
    """
    **Use Case**

        CoursesStaticTabsDetail returns a custom page in the course,
        including the page content.

    **Example Requests**

          GET /api/courses/{course_id}/static_tabs/{tab_url_slug}
          GET /api/courses/{course_id}/static_tabs/{tab_name}

    **Response Values**

        * tab: A custom page in the course. containing following keys:

          * id: The url_slug of the custom page.

          * name: The Display Name of the custom page.

          * detail: The content of the custom page as HTML.
    """

    def get(self, request, course_id, tab_id):
        """
        GET /api/courses/{course_id}/static_tabs/{tab_id}
        """
        user = get_user_from_request_params(self.request, self.kwargs)
        course_descriptor, course_key, course_content = get_course(request, user, course_id)  # pylint: disable=W0612
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        strip_wrapper_div = str2bool(self.request.query_params.get('strip_wrapper_div', 'true'))
        response_data = OrderedDict()
        for tab in course_descriptor.tabs:
            if tab.type == 'static_tab' and (tab.url_slug == tab_id or tab.name == tab_id):
                response_data['id'] = tab.url_slug
                response_data['name'] = tab.name
                response_data['content'] = _get_static_tab_contents(
                    request,
                    course_descriptor,
                    tab,
                    strip_wrapper_div
                )
                return Response(response_data, status=status.HTTP_200_OK)

        return Response({}, status=status.HTTP_404_NOT_FOUND)


class CoursesUsersList(MobileListAPIView):
    """
    **Use Case**

        CoursesUsersList returns a collection of users enrolled or pre-enrolled
        in the course.

        You also use CoursesUsersList to enroll a new user in the course.

    **Example Requests**

          GET /api/courses/{course_id}/users

          POST /api/courses/{course_id}/users

    **GET Response Values**

        * results: The collection of users in the course. Each object in the
          collection has set of user related field.
        * GET supports dynamic fields which means we can pass list of fields we want.
        for example if we want to get only user's first_name and last_name
        ```/api/courses/{course_id}/users?fields=first_name,last_name```
        * GET supports direct filtering of users by user_id
        ```/api/courses/{course_id}/users?users={user_id1},{user_id2}
        * GET supports filtering of users by organizations, groups
         * To get users enrolled in a course and are also member of organization
         ```/api/courses/{course_id}/users?organizations={organization_id}```
         * organizations filter can be a single id or multiple ids separated by comma
         ```/api/courses/{course_id}/users?organizations={organization_id1},{organization_id2}```
         * To get users enrolled in a course and also member of specific groups
         ```/api/courses/{course_id}/users?groups={group_id1},{group_id2}```
        * GET supports exclude filtering of user by groups
         * To get users enrolled in a course and also not member of specific groups
         ```/api/courses/{course_id}/users?exclude_groups={group_id1},{group_id2}```
        * GET supports additional fields to extract organizations,grades,roles,progress of the user
         * To get users with organizations,grades,roles and progress
         ```/api/courses/{course_id}/users?additional_fields=organizations,grades,roles,progress```
        * GET supports ordering the returned users by a specific field, passed into `order_by`.
         * To get users ordered by email
         ```/api/courses/{course_id}/users?order_by=email```


    **Post Values**

        To create a new user through POST /api/courses/{course_id}/users, you
        must include either a user_id or email key in the JSON object.
    """
    serializer_class = UserSerializer
    course_key = None
    user_organizations = []

    def post(self, request, course_id):
        """
        POST /api/courses/{course_id}/users
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        if 'user_id' in request.data:
            user_id = request.data['user_id']
            try:
                existing_user = User.objects.get(id=user_id)
            except ObjectDoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)
        elif 'email' in request.data:
            try:
                email = request.data['email']
                existing_user = User.objects.get(email=email)
            except ObjectDoesNotExist:
                if request.data.get('allow_pending'):
                    # If the email doesn't exist we assume the student does not exist
                    # and the instructor is pre-enrolling them
                    # Store the pre-enrollment data in the CourseEnrollmentAllowed table
                    # NOTE: This logic really should live in CourseEnrollment.....
                    cea, created = CourseEnrollmentAllowed.objects.get_or_create(course_id=course_key, email=email)  # pylint: disable=W0612
                    cea.auto_enroll = True
                    cea.save()
                    return Response({}, status.HTTP_201_CREATED)
                else:
                    return Response({}, status.HTTP_400_BAD_REQUEST)
        else:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)

        # enroll user in the course
        CourseEnrollment.enroll(existing_user, course_key)
        return Response({}, status=status.HTTP_201_CREATED)

    def get(self, request, course_id):  # pylint: disable=W0221
        """
        GET /api/courses/{course_id}/users
        """
        if not request.user.is_staff:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        self.course_key = get_course_key(course_id)
        return super(CoursesUsersList, self).list(request)

    def get_serializer_context(self):
        """
        Extra context provided to the serializer class.
        """
        serializer_context = super(CoursesUsersList, self).get_serializer_context()
        default_fields = [
            "id",
            "email",
            "username",
            "first_name",
            "last_name",
            "created",
            "is_active",
            "profile_image",
            "city",
            "title",
            "country",
            "full_name",
            "is_staff",
            "last_login",
            "attributes",
        ]

        active_attributes = []
        for organization in self.user_organizations:
            active_attributes = active_attributes + organization.get_all_attributes()

        serializer_context.update({
            'course_id': self.course_key,
            'default_fields': default_fields,
            'active_attributes': active_attributes,
        })
        return serializer_context

    def get_queryset(self):
        """
        :return: queryset for course users list.
        """
        user_qs = CourseEnrollment.objects.users_enrolled_in(self.course_key)
        attribute_keys = css_param_to_list(self.request, 'attribute_keys')
        attribute_values = css_param_to_list(self.request, 'attribute_values')
        orgs = get_ids_from_list_param(self.request, 'organizations')
        groups = get_ids_from_list_param(self.request, 'groups')
        users = get_ids_from_list_param(self.request, 'users')
        workgroups = get_ids_from_list_param(self.request, 'workgroups')
        exclude_groups = get_ids_from_list_param(self.request, 'exclude_groups')
        additional_fields = self.request.query_params.get('additional_fields', [])
        order_by_field = self.request.query_params.get('order_by', 'id')
        exclude_type = self.request.query_params.get('exclude_type')
        if orgs:
            user_qs = user_qs.filter(organizations__in=orgs)
            if exclude_type:
                non_company_users = get_non_actual_company_users(exclude_type, orgs[0])
                user_qs.exclude(id__in=non_company_users)
        if groups:
            user_qs = user_qs.filter(groups__in=groups).distinct()
        if users:
            user_qs = user_qs.filter(id__in=users).distinct()
        if workgroups:
            user_qs = user_qs.filter(workgroups__in=workgroups).distinct()
        if exclude_groups:
            user_qs = user_qs.exclude(groups__in=exclude_groups)
        if 'organizations' in additional_fields:
            user_qs = user_qs.prefetch_related('organizations')
        if 'roles' in additional_fields:
            user_qs = user_qs.prefetch_related('courseaccessrole_set')
        if 'grades' in additional_fields:
            user_qs = user_qs.prefetch_related('studentgradebook_set')
        if 'courses_enrolled' in additional_fields:
            user_qs = user_qs.prefetch_related('courseenrollment_set')
        if 'course_groups' in additional_fields:
            user_qs = user_qs.prefetch_related(
                Prefetch('course_groups', queryset=CourseUserGroup.objects.filter(course_id=self.course_key))
            )

        self.user_organizations = Organization.objects.filter(users__in=user_qs).distinct()
        if orgs:
            self.user_organizations.filter(id__in=orgs).distinct()

        user_qs = Organization.get_all_users_by_organization_attribute_filter(
            user_qs, self.user_organizations, attribute_keys, attribute_values
        )
        user_qs = user_qs.select_related('profile')
        user_qs = user_qs.prefetch_related('user_attributes')
        try:
            User._meta.get_field(order_by_field)
            return user_qs.order_by(order_by_field)
        except FieldDoesNotExist:
            return user_qs


class CoursesEngagementSummary(MobileListAPIView):
    """
    ### The CoursesEngagementSummary view allows clients to fetch course engagement summary

    **Example Request**

        * GET /api/courses/{course_id}/engagement-summary
        * GET supports filtering of course engagement summary by organizations, groups
        * To get course engagement summary for an organization
        ```/api/courses/{course_id}/engagement-summary?organizations={organization_id}```

    **GET Response Values**

        * {
            "engaged_users": 8,
            "total_users": 21,
            "active_users": 12,
            "last_week_login_users": 1,
            "total_course_progress": 0.5024027959807782,
            "active_users_progress": 0.8792048929663618,
            "active_users_percentage": 57.14285714285714,
            "engaged_users_progress": 1.3188073394495428,
            "engaged_users_percentage": 38.095238095238095,
            "last_week_login_users_progress": 0.9174311926605511,
            "last_week_login_users_percentage": 4.761904761904762,
        }
    """
    def get(self, request, course_id):  # pylint: disable=W0221
        """
        GET /api/courses/{course_id}/engagement-summary
        """
        if not request.user.is_staff:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        self.course_key = get_course_key(course_id)

        user_id = self.request.query_params.get('user_id', None)
        params = {
            'user_id': user_id,
            'org_ids': get_ids_from_list_param(self.request, 'organizations'),
            'group_ids': get_ids_from_list_param(self.request, 'groups'),
            'exclude_users': get_aggregate_exclusion_user_ids(self.course_key),
            'cohort_user_ids': _get_users_in_cohort(user_id, self.course_key, ignore_groupwork=True),
        }
        user_qs = CourseEnrollment.objects.users_enrolled_in(self.course_key).exclude(
            id__in=params.get('exclude_users') or []
        )
        if params.get('org_ids'):
            user_qs = user_qs.filter(organizations__in=params.get('org_ids') or [])

        total_users = user_qs.count()

        active_users = user_qs.filter(is_active=True).count()
        last_week = timezone.now() - timezone.timedelta(days=7)
        users_logged_in_last_week = user_qs.filter(last_login__range=(last_week, timezone.now()))
        users_logged_in_last_week_count = users_logged_in_last_week.count()

        progress_qs = get_filtered_aggregation_queryset(self.course_key, **params)
        progress_sum = progress_qs.aggregate(percent=Sum(Round(F('percent')*100))).get('percent') or 0
        last_week_progress = progress_qs.filter(user_id__in=users_logged_in_last_week)
        last_week_progress_sum = (last_week_progress.aggregate(
            percent=Sum('percent')
        ).get('percent') or 0) * 100
        users_with_progress = progress_qs.count()

        def safe_division(dividend, divisor):
            return 0 if divisor == 0 else dividend / divisor

        data = {}
        data['total_users'] = total_users
        data['total_course_progress'] = safe_division(float(progress_sum), total_users)

        data['active_users'] = active_users
        data['active_users_percentage'] = safe_division(float(active_users), total_users) * 100
        data['active_users_progress'] = safe_division(float(progress_sum), active_users)

        data['engaged_users'] = users_with_progress
        data['engaged_users_percentage'] = safe_division(float(users_with_progress), total_users) * 100
        data['engaged_users_progress'] = safe_division(float(progress_sum), users_with_progress)

        data['last_week_login_users'] = users_logged_in_last_week_count
        data['last_week_login_users_percentage'] = safe_division(
            float(users_logged_in_last_week_count), total_users
        ) * 100
        data['last_week_login_users_progress'] = safe_division(
            float(last_week_progress_sum), users_logged_in_last_week_count
        )

        return Response(data, status=status.HTTP_200_OK)


class CoursesUsersPassedList(SecureListAPIView):
    """
    **Use Case**

        CoursesUsersPassedList returns a list of users passed in the course.

    **Example Requests**

          GET /api/courses/{course_id}/users/passed

    **GET Response Values**

        * results: The list of users passed in the course.
        * GET supports filtering of user by organization(s), groups
         * To get a list of users passed in a course and are also member of organization
         ```/api/courses/{course_id}/users/passed?organizations={organization_id}```
         * organizations filter can be a single id or multiple ids separated by comma
         ```/api/courses/{course_id}/users/passed?organizations={organization_id1},{organization_id2}```
         * To get a list of users passed in a course and also member of specific groups
         ```/api/courses/{course_id}/users/passed?groups={group_id1},{group_id2}```
    """
    serializer_class = UserGradebookSerializer

    def get_queryset(self):
        """
        GET /api/courses/{course_id}/users/passed
        """
        course_id = self.kwargs['course_id']
        if not course_exists(course_id):
            raise Http404

        org_ids = get_ids_from_list_param(self.request, 'organization')
        group_ids = get_ids_from_list_param(self.request, 'groups')

        course_key = get_course_key(course_id)
        exclude_users = get_aggregate_exclusion_user_ids(course_key)

        queryset = StudentGradebook.get_passed_users_gradebook(
            course_key, exclude_users=exclude_users, org_ids=org_ids, group_ids=group_ids
        )
        return queryset


class CoursesUsersDetail(SecureAPIView):
    """
    **Use Case**

        CoursesUsersDetail returns a details about a specified user of a course.

        You also use CoursesUsersDetail to unenroll a user from the course.

    **Example Requests**

          GET /api/courses/{course_id}/users/{user_id}

          DELETE /api/courses/{course_id}/users/{user_id}

    **GET Response Values**

        * course_id: The ID of the course the user is enrolled in.

        * position: The last known position in the course. (??? in outline?)

        * user_id: The ID of the user.

        * uri: The URI to use to get details of the user.
    """
    def get(self, request, course_id, user_id):
        """
        GET /api/courses/{course_id}/users/{user_id}
        """
        base_uri = generate_base_uri(request)
        response_data = {
            'course_id': course_id,
            'user_id': user_id,
            'uri': base_uri,
        }
        try:
            user = User.objects.get(id=user_id, is_active=True)
        except ObjectDoesNotExist:
            return Response(response_data, status=status.HTTP_404_NOT_FOUND)
        course_descriptor, course_key, course_content = get_course(request, user, course_id, load_content=True)
        if not course_descriptor:
            return Response(response_data, status=status.HTTP_404_NOT_FOUND)
        if CourseEnrollment.is_enrolled(user, course_key):
            response_data['position'] = getattr(course_content, 'position', None)
            response_status = status.HTTP_200_OK
        else:
            response_status = status.HTTP_404_NOT_FOUND
        return Response(response_data, status=response_status)

    def delete(self, request, course_id, user_id):
        """
        DELETE /api/courses/{course_id}/users/{user_id}
        """
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_204_NO_CONTENT)
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        CourseEnrollment.unenroll(user, course_key)
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri
        return Response(response_data, status=status.HTTP_204_NO_CONTENT)


class CourseContentGroupsList(SecureAPIView):
    """
    ### The CourseContentGroupsList view allows clients to retrieve a list of Content-Group relationships
    - URI: ```/api/courses/{course_id}/content/{content_id}/groups```
    - GET: Returns a JSON representation (array) of the set of Content-Group relationships
        * type: Set filtering parameter
    - POST: Creates a new CourseContentGroupRelationship entity using the provided Content and Group
        * group_id: __required__, The identifier for the Group being related to the Content
    - POST Example:

            {
                "group_id" : 12345
            }
    ### Use Cases/Notes:
    * Example: Link a specific piece of course content to a group, such as a student workgroup
    * Note: The specified Group must have a corresponding GroupProfile record for this operation to succeed
    * Providing a 'type' parameter will attempt to filter the related Group set by the specified value
    """

    def post(self, request, course_id, content_id):
        """
        POST /api/courses/{course_id}/content/{content_id}/groups
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        content_descriptor, content_key, existing_content = get_course_child(request, request.user, course_key, content_id)  # pylint: disable=W0612,C0301
        if not content_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        group_id = request.data.get('group_id')
        if group_id is None:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        try:
            existing_profile = GroupProfile.objects.get(group_id=group_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = '{}/{}'.format(base_uri, existing_profile.group_id)
        response_data['course_id'] = unicode(course_key)
        response_data['content_id'] = unicode(content_key)
        response_data['group_id'] = str(existing_profile.group_id)
        try:
            CourseContentGroupRelationship.objects.get(
                course_id=course_key,
                content_id=content_key,
                group_profile=existing_profile
            )
            response_data['message'] = "Relationship already exists."
            return Response(response_data, status=status.HTTP_409_CONFLICT)
        except ObjectDoesNotExist:
            CourseContentGroupRelationship.objects.create(
                course_id=course_key,
                content_id=content_key,
                group_profile=existing_profile
            )
            return Response(response_data, status=status.HTTP_201_CREATED)

    def get(self, request, course_id, content_id):
        """
        GET /api/courses/{course_id}/content/{content_id}/groups?type=workgroup
        """
        response_data = []
        group_type = request.query_params.get('type')
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        content_descriptor, content_key, existing_content = get_course_child(request, request.user, course_key, content_id)  # pylint: disable=W0612,C0301
        if not content_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        relationships = CourseContentGroupRelationship.objects.filter(
            course_id=course_key,
            content_id=content_key,
        ).select_related("group_profile")
        if group_type:
            relationships = relationships.filter(group_profile__group_type=group_type)
        response_data = [
            {'course_id': course_id, 'content_id': content_id, 'group_id': relationship.group_profile.group_id}
            for relationship in relationships
        ]
        return Response(response_data, status=status.HTTP_200_OK)


class CourseContentGroupsDetail(SecureAPIView):
    """
    ### The CourseContentGroupsDetail view allows clients to interact with a specific Content-Group relationship
    - URI: ```/api/courses/{course_id}/content/{content_id}/groups/{group_id}```
    - GET: Returns a JSON representation of the specified Content-Group relationship
    ### Use Cases/Notes:
    * Use the GET operation to verify the existence of a particular Content-Group relationship
    * If the User is enrolled in the course, we provide their last-known position to the client
    """
    def get(self, request, course_id, content_id, group_id):
        """
        GET /api/courses/{course_id}/content/{content_id}/groups/{group_id}
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        content_descriptor, content_key, existing_content = get_course_child(request, request.user, course_key, content_id)  # pylint: disable=W0612,C0301
        if not content_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        try:
            CourseContentGroupRelationship.objects.get(
                course_id=course_key,
                content_id=content_key,
                group_profile__group_id=group_id
            )
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        response_data = {
            'course_id': course_id,
            'content_id': content_id,
            'group_id': group_id,
        }
        return Response(response_data, status=status.HTTP_200_OK)


class CourseContentUsersList(SecureAPIView):
    """
    ### The CourseContentUsersList view allows clients to users enrolled and
    users not enrolled for course within all groups of course
    - URI: ```/api/courses/{course_id}/content/{content_id}/users
        * enrolled: boolean, filters user set by enrollment status
        * group_id: numeric, filters user set by membership in a specific group
        * type: string, filters user set by membership in groups matching the specified type
    - GET: Returns a JSON representation of users enrolled or not enrolled
    ### Use Cases/Notes:
    * Filtering related Users by enrollement status should be self-explanatory
    * An example of specific group filtering is to get the set of users who are members of a particular workgroup
        related to the content
    * An example of group type filtering is to get all users who are members of an organization group
        related to the content
    """

    def get(self, request, course_id, content_id):
        """
        GET /api/courses/{course_id}/content/{content_id}/users
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        content_descriptor, content_key, existing_content = get_course_child(request, request.user, course_key, content_id)  # pylint: disable=W0612,C0301
        if not content_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        enrolled = self.request.query_params.get('enrolled', 'True')
        group_type = self.request.query_params.get('type', None)
        group_id = self.request.query_params.get('group_id', None)
        relationships = CourseContentGroupRelationship.objects.filter(
            course_id=course_key, content_id=content_key).select_related("group_profile")

        if group_id:
            relationships = relationships.filter(group_profile__group__id=group_id)

        if group_type:
            relationships = relationships.filter(group_profile__group_type=group_type)

        lookup_group_ids = relationships.values_list('group_profile', flat=True)
        users = User.objects.filter(groups__id__in=lookup_group_ids)
        enrolled_users = CourseEnrollment.objects.users_enrolled_in(course_key).filter(groups__id__in=lookup_group_ids)
        if enrolled in ['True', 'true']:
            queryset = enrolled_users
        else:
            queryset = list(itertools.ifilterfalse(lambda x: x in enrolled_users, users))

        serializer = UserSerializer(queryset, many=True)
        return Response(serializer.data)  # pylint: disable=E1101



class CoursesMetricsGradesList(SecureListAPIView):
    """
    ### The CoursesMetricsGradesList view allows clients to retrieve a list of grades for the specified Course
    - URI: ```/api/courses/{course_id}/grades/```
    - GET: Returns a JSON representation (array) of the set of grade objects
    ### Use Cases/Notes:
    * Example: Display a graph of all of the grades awarded for a given course
    """

    def get(self, request, course_id):  # pylint: disable=W0221
        """
        GET /api/courses/{course_id}/metrics/grades?user_ids=1,2
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        exclude_users = get_aggregate_exclusion_user_ids(course_key)
        queryset = StudentGradebook.objects.filter(course_id__exact=course_key,
                                                   user__is_active=True,
                                                   user__courseenrollment__is_active=True,
                                                   user__courseenrollment__course_id__exact=course_key)\
            .exclude(user__in=exclude_users)
        user_ids = get_ids_from_list_param(self.request, 'user_id')
        if user_ids:
            queryset = queryset.filter(user__in=user_ids)

        group_ids = get_ids_from_list_param(self.request, 'groups')
        if group_ids:
            queryset = queryset.filter(user__groups__in=group_ids).distinct()

        sum_of_grades = sum([gradebook.grade for gradebook in queryset])
        queryset_grade_avg = sum_of_grades / len(queryset) if len(queryset) > 0 else 0
        queryset_grade_count = len(queryset)
        queryset_grade_max = queryset.aggregate(Max('grade'))
        queryset_grade_min = queryset.aggregate(Min('grade'))

        course_metrics = StudentGradebook.generate_leaderboard(course_key,
                                                               group_ids=group_ids,
                                                               exclude_users=exclude_users)

        response_data = {}
        base_uri = generate_base_uri(request)
        response_data['uri'] = base_uri

        response_data['grade_average'] = queryset_grade_avg
        response_data['grade_count'] = queryset_grade_count
        response_data['grade_maximum'] = queryset_grade_max['grade__max']
        response_data['grade_minimum'] = queryset_grade_min['grade__min']

        response_data['course_grade_average'] = course_metrics['course_avg']
        response_data['course_grade_maximum'] = course_metrics['course_max']
        response_data['course_grade_minimum'] = course_metrics['course_min']
        response_data['course_grade_count'] = course_metrics['course_count']

        response_data['grades'] = []
        for row in queryset:
            serializer = GradeSerializer(row)
            response_data['grades'].append(serializer.data)  # pylint: disable=E1101
        return Response(response_data, status=status.HTTP_200_OK)


class CoursesProjectList(SecureListAPIView):
    """
    ### The CoursesProjectList view allows clients to retrieve paginated list of projects by course
    - URI: ```/api/courses/{course_id}/projects/```
    - GET: Provides paginated list of projects for a course
    """

    serializer_class = ProjectSerializer

    def get_queryset(self):
        course_id = self.kwargs['course_id']
        course_key = get_course_key(course_id)
        return Project.objects.filter(course_id=course_key)


def _get_users_in_cohort(user_id, course_key, ignore_groupwork):
    """
    Get users in the same cohort, possibly ignoring if Group Work is enabled
    or Waffle flag disabled
    """
    if not is_cohort_available() or (ignore_groupwork and Project.objects.filter(course_id=course_key)):
        return None
    return get_cohort_user_ids(user_id, course_key)


class CoursesMetrics(SecureAPIView):
    """
    ### The CoursesMetrics view allows clients to retrieve a list of Metrics for the specified Course
    - URI: ```/api/courses/{course_id}/metrics/?organization={organization_id}&user_id={user_id}```
    - GET: Returns a JSON representation (array) of the set of course metrics
    - metrics can be filtered by organization by adding organization parameter to GET request
    - metrics can be filtered by cohort when `user_id` param is provided
    - metrics_required param should be comma separated list of metrics required
    - possible values for metrics_required param are
    - ``` users_started,modules_completed,users_completed,thread_stats,users_passed,avg_grade,avg_progress ```
    ### Use Cases/Notes:
    * Example: Display number of users enrolled in a given course
    """
    @staticmethod
    def get_course_enrollment_count(course_id, org_id=None, exclude_org_admins=False):
        """
        Get enrollment count of a course
        if org_id is passed then count is limited to that org's users
        """
        cache_category = 'course_enrollments'
        if org_id:
            cache_category = '{}_{}'.format(cache_category, org_id)
            if exclude_org_admins:
                cache_category = '{}_exclude_admins'.format(cache_category)

        enrollment_count = get_cached_data(cache_category, course_id)
        if enrollment_count is not None:
            return enrollment_count.get('enrollment_count')

        course_key = get_course_key(course_id)
        exclude_user_ids = get_aggregate_exclusion_user_ids(course_key)
        users_enrolled_qs = CourseEnrollment.objects.users_enrolled_in(course_key).exclude(id__in=exclude_user_ids)

        if org_id:
            users_enrolled_qs = users_enrolled_qs.filter(organizations=org_id).distinct()
            if exclude_org_admins:
                non_company_users = get_non_actual_company_users('mcka_role_company_admin', org_id)
                users_enrolled_qs.exclude(id__in=non_company_users)

        enrollment_count = users_enrolled_qs.count()
        cache_course_data(cache_category, course_id, {'enrollment_count': enrollment_count})

        return enrollment_count

    @staticmethod
    def get_course_avg_grade(course_id, org_id=None):
        """
        Get average grade socre of a course
        If org_id is passed then score is limited to that org's users
        """
        cache_category = 'grade'
        if org_id:
            cache_category = '{}_{}'.format(cache_category, org_id)

        data = get_cached_data(cache_category, course_id)
        if data is not None:
            return data.get('course_avg')

        course_key = get_course_key(course_id)
        exclude_user_ids = get_aggregate_exclusion_user_ids(course_key)

        avg_grade = StudentGradebook.course_grade_avg(
            course_key,
            exclude_users=exclude_user_ids,
            org_ids=[org_id] if org_id else None
        )
        cache_course_data(cache_category, course_id, {'course_avg': avg_grade})

        return avg_grade

    @staticmethod
    def get_course_avg_progress(course_id, org_id=None, percent_completion=True):
        """
        Get average progress score of a course
        If org_id is passed then score is limited to that org's users
        """
        cache_category = 'progress'
        if org_id:
            cache_category = '{}_{}'.format(cache_category, org_id)

        data = get_cached_data(cache_category, course_id)
        if data is not None:
            return data.get('course_avg')

        course_key = get_course_key(course_id)
        exclude_user_ids = get_aggregate_exclusion_user_ids(course_key)

        data = _get_course_progress_metrics(
            course_key,
            exclude_users=exclude_user_ids,
            org_ids=[org_id] if org_id else None,
            percent_completion=percent_completion,
        )
        cache_course_data(cache_category, course_id, data)

        return data.get('course_avg')

    @staticmethod
    def get_course_passed_users_count(course_id, org_id=None):
        """
        Get passed users count of a course
        if org_id is passed then count is limited to that org's users
        """
        cache_category = 'passed_count'
        if org_id:
            cache_category = '{}_{}'.format(cache_category, org_id)

        passed_count = get_cached_data(cache_category, course_id)
        if passed_count is not None:
            return passed_count

        course_key = get_course_key(course_id)
        exclude_user_ids = get_aggregate_exclusion_user_ids(course_key)

        passed_count = StudentGradebook.get_passed_users_gradebook(
            course_key,
            exclude_users=exclude_user_ids,
            org_ids=[org_id] if org_id else None
        ).count()
        cache_course_data(cache_category, course_id, passed_count)

        return passed_count

    @staticmethod
    def get_course_completed_users_count(course_id, org_id=None):
        """
        Get completed users count of a course
        if org_id is passed then count is limited to that org's users
        """
        cache_category = 'completed_count'
        if org_id:
            cache_category = '{}_{}'.format(cache_category, org_id)

        completed_count = get_cached_data(cache_category, course_id)
        if completed_count is not None:
            return completed_count

        course_key = get_course_key(course_id)
        exclude_user_ids = get_aggregate_exclusion_user_ids(course_key)

        completed_count = StudentGradebook.get_num_users_completed(
            course_key,
            exclude_users=exclude_user_ids,
            org_ids=[org_id] if org_id else None
        )
        cache_course_data(cache_category, course_id, completed_count)

        return completed_count

    def get(self, request, course_id):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/metrics/
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_descriptor, course_key, course_content = get_course(request, request.user, course_id)  # pylint: disable=W0612
        slash_course_id = get_course_key(course_id, slashseparated=True)
        exclude_type = request.query_params.get('exclude_type', None)
        organization = request.query_params.get('organization', None)
        org_ids = [organization] if organization else None
        group_ids = get_ids_from_list_param(self.request, 'groups')
        metrics_required = css_param_to_list(request, 'metrics_required')
        exclude_users = get_aggregate_exclusion_user_ids(course_key)
        user_id = request.query_params.get('user_id', None)
        cohort_user_ids = _get_users_in_cohort(user_id, course_key, ignore_groupwork=True)

        if not any([group_ids, cohort_user_ids]):
            enrollment_count = CoursesMetrics.get_course_enrollment_count(
                course_id=course_id,
                org_id=organization,
                exclude_org_admins=bool(exclude_type),
            )
        else:
            users_enrolled_qs = CourseEnrollment.objects.users_enrolled_in(course_key).exclude(id__in=exclude_users)

            if organization:
                users_enrolled_qs = users_enrolled_qs.filter(organizations=organization).distinct()
                if exclude_type:
                    non_company_users = get_non_actual_company_users(exclude_type, organization)
                    users_enrolled_qs.exclude(id__in=non_company_users)

            if group_ids:
                users_enrolled_qs = users_enrolled_qs.filter(groups__in=group_ids).distinct()

            if cohort_user_ids:
                users_enrolled_qs = users_enrolled_qs.filter(id__in=cohort_user_ids)

            enrollment_count = users_enrolled_qs.count()

        data = {
            'grade_cutoffs': course_descriptor.grading_policy['GRADE_CUTOFFS'],
            'users_enrolled': enrollment_count
        }

        if 'users_started' in metrics_required:
            users_started = get_num_users_started(
                course_key,
                exclude_users=exclude_users,
                org_ids=org_ids,
                group_ids=group_ids,
                cohort_user_ids=cohort_user_ids,
            )
            data['users_started'] = users_started
            data['users_not_started'] = data['users_enrolled'] - users_started

        if 'modules_completed' in metrics_required:
            modules_completed, _ = get_total_completions(
                course_key,
                exclude_users=exclude_users,
                org_ids=org_ids,
                group_ids=group_ids,
                cohort_user_ids=cohort_user_ids,
            )
            data['modules_completed'] = modules_completed

        if 'users_completed' in metrics_required:
            if not any([group_ids, cohort_user_ids]):
                users_completed = CoursesMetrics.get_course_completed_users_count(
                    course_id=course_id,
                    org_id=organization
                )
            else:
                users_completed = StudentGradebook.get_num_users_completed(
                    course_key,
                    exclude_users=exclude_users,
                    org_ids=org_ids,
                    group_ids=group_ids,
                    cohort_user_ids=cohort_user_ids,
                )
            data['users_completed'] = users_completed

        if 'users_passed' in metrics_required:
            if not any([group_ids, cohort_user_ids]):
                users_passed = CoursesMetrics.get_course_passed_users_count(
                    course_id=course_id,
                    org_id=organization
                )
            else:
                users_passed = StudentGradebook.get_passed_users_gradebook(
                    course_key,
                    exclude_users=exclude_users,
                    org_ids=org_ids,
                    group_ids=group_ids,
                    cohort_user_ids=cohort_user_ids,
                ).count()

            data['users_passed'] = users_passed

        if 'avg_progress' in metrics_required:
            if not any([group_ids, cohort_user_ids]):
                avg_progress = CoursesMetrics.get_course_avg_progress(
                    course_id=course_id,
                    org_id=organization,
                    percent_completion=True
                )
            else:
                progress_metrics = _get_course_progress_metrics(
                    course_key,
                    exclude_users=exclude_users,
                    org_ids=org_ids,
                    group_ids=group_ids,
                    cohort_user_ids=cohort_user_ids,
                    percent_completion=True,
                )
                avg_progress = progress_metrics.get('course_avg')

            data['avg_progress'] = avg_progress

        if 'avg_grade' in metrics_required:
            if not any([group_ids, cohort_user_ids]):
                avg_grade = CoursesMetrics.get_course_avg_grade(
                    course_id=course_id,
                    org_id=organization
                )
            else:
                avg_grade = StudentGradebook.course_grade_avg(
                    course_key,
                    exclude_users=exclude_users,
                    org_ids=org_ids,
                    group_ids=group_ids,
                    cohort_user_ids=cohort_user_ids,
                )

            data['avg_grade'] = avg_grade

        if 'thread_stats' in metrics_required:
            try:
                data['thread_stats'] = get_course_thread_stats(slash_course_id)
            except (CommentClientMaintenanceError, CommentClientRequestError, ConnectionError), e:
                logging.error("Forum service returned an error: %s", str(e))

                data = {
                    "err_msg": str(e)
                }
                return Response(data, status=status.HTTP_200_OK)
        return Response(data, status=status.HTTP_200_OK)


class CoursesTimeSeriesMetrics(SecureAPIView):
    """
    ### The CoursesTimeSeriesMetrics view allows clients to retrieve a list of Metrics for the specified Course
    in time series format.
    - URI: ```/api/courses/{course_id}/time-series-metrics/?start_date={date}&end_date={date}
        &interval={interval}&organization={organization_id}```
    - interval can be `days`, `weeks` or `months`
    - GET: Returns a JSON representation with three metrics
    {
        "users_not_started": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]],
        "users_started": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]],
        "users_completed": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]],
        "modules_completed": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]]
        "users_enrolled": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]]
        "active_users": [[datetime-1, count-1], [datetime-2, count-2], ........ [datetime-n, count-n]]
    }
    - metrics can be filtered by organization by adding organization parameter to GET request
    ### Use Cases/Notes:
    * Example: Display number of users completed, started or not started in a given course for a given time period
    """

    def get(self, request, course_id):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/time-series-metrics/
        """
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        start = request.query_params.get('start_date', None)
        end = request.query_params.get('end_date', None)
        interval = request.query_params.get('interval', 'days')
        if not start or not end:
            return Response({"message": _("Both start_date and end_date parameters are required")},
                            status=status.HTTP_400_BAD_REQUEST)
        if interval not in ['days', 'weeks', 'months']:
            return Response({"message": _("Interval parameter is not valid. It should be one of these "
                                          "'days', 'weeks', 'months'")}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start_dt = parse_datetime(start)
            end_dt = parse_datetime(end)
        except ValueError:
            return Response({'message': _('date format is invalid')}, status=status.HTTP_400_BAD_REQUEST)

        course_key = get_course_key(course_id)
        exclude_users = get_aggregate_exclusion_user_ids(course_key)
        grade_complete_match_range = getattr(settings, 'GRADEBOOK_GRADE_COMPLETE_PROFORMA_MATCH_RANGE', 0.01)
        grades_qs = StudentGradebook.objects.filter(
            course_id__exact=course_key,
            user__is_active=True,
            user__courseenrollment__is_active=True,
            user__courseenrollment__course_id__exact=course_key
        ).exclude(user_id__in=exclude_users)
        grades_complete_qs = grades_qs.filter(proforma_grade__lte=F('grade') + grade_complete_match_range,
                                              proforma_grade__gt=0)
        enrolled_qs = CourseEnrollment.objects.filter(course_id__exact=course_key, user__is_active=True,
                                                      is_active=True).exclude(user_id__in=exclude_users)
        users_started_qs = Aggregator.objects.filter(
            course_key__exact=course_key,
            user__is_active=True,
            user__courseenrollment__is_active=True,
            user__courseenrollment__course_id__exact=course_key,
            aggregation_name='course',
            earned__gt=0.0,
        ).exclude(user_id__in=exclude_users)
        modules_completed_qs = BlockCompletion.objects.filter(
            course_key__exact=course_key,
            user__courseenrollment__is_active=True,
            user__courseenrollment__course_id__exact=course_key,
            user__is_active=True,
            completion__gt=0.0,
        ).exclude(user_id__in=exclude_users)
        active_users_qs = StudentModule.objects\
            .filter(course_id__exact=course_key, student__is_active=True,
                    student__courseenrollment__is_active=True,
                    student__courseenrollment__course_id__exact=course_key)\
            .exclude(student_id__in=exclude_users)

        organization = request.query_params.get('organization', None)
        if organization:
            enrolled_qs = enrolled_qs.filter(user__organizations=organization)
            grades_complete_qs = grades_complete_qs.filter(user__organizations=organization)
            users_started_qs = users_started_qs.filter(user__organizations=organization)
            modules_completed_qs = modules_completed_qs.filter(user__organizations=organization)
            active_users_qs = active_users_qs.filter(student__organizations=organization)

        group_ids = get_ids_from_list_param(self.request, 'groups')
        if group_ids:
            enrolled_qs = enrolled_qs.filter(user__groups__in=group_ids).distinct()
            grades_complete_qs = grades_complete_qs.filter(user__groups__in=group_ids).distinct()
            users_started_qs = users_started_qs.filter(user__groups__in=group_ids).distinct()
            modules_completed_qs = modules_completed_qs.filter(user__groups__in=group_ids).distinct()
            active_users_qs = active_users_qs.filter(student__groups__in=group_ids).distinct()

        total_enrolled = enrolled_qs.filter(created__lt=start_dt).count()
        total_started_count = users_started_qs.filter(created__lt=start_dt).aggregate(Count('user', distinct=True))
        total_started = total_started_count['user__count'] or 0
        enrolled_series = get_time_series_data(
            enrolled_qs, start_dt, end_dt, interval=interval,
            date_field='created', date_field_model=CourseEnrollment,
            aggregate=Count('id', distinct=True)
        )
        started_series = get_time_series_data(
            users_started_qs, start_dt, end_dt, interval=interval,
            date_field='created', date_field_model=Aggregator,
            aggregate=Count('user', distinct=True)
        )
        completed_series = get_time_series_data(
            grades_complete_qs, start_dt, end_dt, interval=interval,
            date_field='modified', date_field_model=StudentGradebook,
            aggregate=Count('id', distinct=True)
        )
        modules_completed_series = get_time_series_data(
            modules_completed_qs, start_dt, end_dt, interval=interval,
            date_field='created', date_field_model=BlockCompletion,
            aggregate=Count('id', distinct=True)
        )

        # active users are those who accessed course in last 24 hours
        start_dt = start_dt - timedelta(hours=24)
        end_dt = end_dt - timedelta(hours=24)
        active_users_series = get_time_series_data(
            active_users_qs, start_dt, end_dt, interval=interval,
            date_field='modified', date_field_model=StudentModule,
            aggregate=Count('student', distinct=True)
        )

        not_started_series = []
        for enrolled, started in zip(enrolled_series, started_series):
            not_started_series.append((started[0], (total_enrolled + enrolled[1]) - (total_started + started[1])))
            total_started += started[1]
            total_enrolled += enrolled[1]

        data = {
            'users_not_started': not_started_series,
            'users_started': started_series,
            'users_completed': completed_series,
            'modules_completed': modules_completed_series,
            'users_enrolled': enrolled_series,
            'active_users': active_users_series
        }

        return Response(data, status=status.HTTP_200_OK)


class CoursesMetricsGradesLeadersList(SecureListAPIView):
    """
    ### The CoursesMetricsGradesLeadersList view allows clients to retrieve top 3 users who are leading
    in terms of grade and course average for the specified Course. If user_id parameter is given
    it would return user's position
    - URI: ```/api/courses/{course_id}/metrics/grades/leaders/?user_id={user_id}```
    - GET: Returns a JSON representation (array) of the users with grades
    To get more than 3 users use count parameter
    ``` /api/courses/{course_id}/metrics/grades/leaders/?count=3```
    To exclude users with certain roles from leaders
    ```/api/courses/{course_id}/metrics/grades/leaders/?exclude_roles=observer,assistant```
    To get only grade of a user and course average skipleaders parameter can be used
    ```/api/courses/{course_id}/metrics/grades/leaders/?user_id={user_id}&skipleaders=true```
    ### Use Cases/Notes:
    * Example: Display grades leaderboard of a given course
    * Example: Display position of a users in a course in terms of grade and course avg
    """

    def get(self, request, course_id):  # pylint: disable=W0613,W0221
        """
        GET /api/courses/{course_id}/grades/leaders/
        """
        course_key = get_course_key(course_id)
        exclude_roles = css_param_to_list(self.request, 'exclude_roles')
        user_id = self.request.query_params.get('user_id', None)
        params = {
            'user_id': user_id,
            'group_ids': get_ids_from_list_param(self.request, 'groups'),
            'count': self.request.query_params.get('count', 3),
            'skipleaders': str2bool(self.request.query_params.get('skipleaders', 'false')),
            # Users having certain roles (such as an Observer) are excluded from aggregations
            'exclude_users': get_aggregate_exclusion_user_ids(course_key, roles=exclude_roles),
            'cohort_user_ids': _get_users_in_cohort(user_id, course_key, ignore_groupwork=True),
        }

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        data = _get_courses_metrics_grades_leaders_list(course_key, **params)

        return Response(data, status=status.HTTP_200_OK)


class CompletionList(SecureListAPIView):  # pylint: disable=too-many-ancestors
    """
    ### The CompletionList allows clients to view user's course module completion entities
    to monitor a user's progression throughout the duration of a course,
    -   URI: ```/api/courses/{course_id}/completions```
    -   GET: Returns a JSON representation of the course, content and user and timestamps.
        Note that it contains duplicate fields to support new and old naming schemes.
    -   GET Example:
            {
                "count":"1",
                "num_pages": "1",
                "previous": null
                "next": null
                "results": [
                    {
                        "id": 18,
                        "block_key": "block-v1:edX+DemoX+Demo_Course+type@poll+block@44cc060f582743899d70b850097cf029",
                        "block_type": "poll",
                        "completion": 1,
                        "content_id": "block-v1:edX+DemoX+Demo_Course+type@poll+block@44cc060f582743899d70b850097cf029",
                        "course_id": "course-v1:edX+DemoX+Demo_Course",
                        "course_key": "course-v1:edX+DemoX+Demo_Course",
                        "created": "2018-10-26T18:22:59.416943Z",
                        "modified": "2018-10-26T18:22:59.417711Z",
                        "stage": null,
                        "user": 5
                        "user_id": 5,
                    }
                ]
            }

        Filters can also be applied:

        `/api/courses/{course_id}/completions/?user_id={user_id}`
        `/api/courses/{course_id}/completions/?content_id={content_id}`
        `/api/courses/{course_id}/completions/?user_id={user_id}&content_id={content_id}`

    - POST: Creates a Course-Module completion entity
    - POST Example:
        {
            "content_id":"i4x://the/content/location",
            "user_id":4,
        }
    ### Use Cases/Notes:
    * Use GET operation to retrieve list of course completions by user
    * Use GET operation to verify user has completed specific course module
    """
    serializer_class = BlockCompletionSerializer

    def get_queryset(self):
        """
        GET /api/courses/{course_id}/completions/
        """
        content_id = self.request.query_params.get('content_id', None)
        course_id = self.kwargs['course_id']
        if not course_exists(course_id):
            raise Http404
        course_key = get_course_key(course_id)
        queryset = BlockCompletion.objects.filter(course_key=course_key).select_related('user')
        user_ids = get_ids_from_list_param(self.request, 'user_id')
        if user_ids:
            queryset = queryset.filter(user__in=user_ids)

        if content_id:
            content_descriptor, content_key, _existing_content = get_course_child(
                self.request,
                self.request.user,
                course_key,
                content_id
            )
            if not content_descriptor:
                raise Http404
            queryset = queryset.filter(block_key=content_key)

        return queryset

    def post(self, request, course_id):
        """
        POST /api/courses/{course_id}/completions/
        """
        warnings.warn(
            "BlockCompletions should not be created this way.  Use the suggested APIs instead",
            DeprecationWarning,
        )
        content_id = request.data.get('content_id', None)
        user_id = request.data.get('user_id', None)
        if not content_id:
            return Response({'message': _('content_id is missing')}, status.HTTP_400_BAD_REQUEST)
        if not user_id:
            return Response({'message': _('user_id is missing')}, status.HTTP_400_BAD_REQUEST)
        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)
        course_key = get_course_key(course_id)
        content_descriptor, content_key, _existing_content = get_course_child(request, request.user, course_key, content_id)  # pylint: disable=W0612,C0301
        if not content_descriptor:
            return Response({'message': _('content_id is invalid')}, status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'message': _('user_id is invalid')}, status.HTTP_400_BAD_REQUEST)
        completion, created = BlockCompletion.objects.submit_completion(
            user=user,
            course_key=course_key,
            block_key=content_key,
            completion=1.0,
        )
        serializer = BlockCompletionSerializer(completion)
        if created:
            return Response(serializer.data, status=status.HTTP_201_CREATED)  # pylint: disable=E1101
        else:
            return Response({}, status=status.HTTP_204_NO_CONTENT)


class CoursesMetricsCompletionsLeadersList(SecureAPIView):
    """
    ### The CoursesCompletionsLeadersList view allows clients to retrieve top 3 users who are leading
    in terms of course module completions and course average for the specified Course, if user_id parameter is given
    position of user is returned. When cohorting is enabled, users will be filtered by cohort.
    - URI: ```/api/courses/{course_id}/metrics/completions/leaders/```
    - GET: Returns a JSON representation (array) of the users with points scored
    Filters can also be applied
    ```/api/courses/{course_id}/metrics/completions/leaders/?content_id={content_id}```
    To get more than 3 users use count parameter
    ```/api/courses/{course_id}/metrics/completions/leaders/?count=6```
    To get only percentage of a user and course average skipleaders parameter can be used
    ```/api/courses/{course_id}/metrics/completions/leaders/?user_id={user_id}&skipleaders=true```
    To get data for one or more orgnaizations organizations filter can be applied
    * organizations filter can be a single id or multiple ids separated by comma
    ```/api/courses/{course_id}/metrics/completions/leaders/?organizations={organization_id1},{organization_id2}```
    To exclude users with certain roles from progress/completions calculations
    ```/api/courses/{course_id}/metrics/completions/leaders/?exclude_roles=observer,assistant```

    ### Use Cases/Notes:
    * Example: Display leaders in terms of completions in a given course
    * Example: Display top 3 users leading in terms of completions in a given course
    """

    def get(self, request, course_id):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/metrics/completions/leaders/
        """
        course_key = get_course_key(course_id)
        exclude_roles = css_param_to_list(self.request, 'exclude_roles')
        user_id = self.request.query_params.get('user_id', None)
        params = {
            'user_id': user_id,
            'count': self.request.query_params.get('count', None),
            'org_ids': get_ids_from_list_param(self.request, 'organizations'),
            'group_ids': get_ids_from_list_param(self.request, 'groups'),
            'skipleaders': str2bool(self.request.query_params.get('skipleaders', 'false')),
            # Users having certain roles (such as an Observer) are excluded from aggregations
            'exclude_users': get_aggregate_exclusion_user_ids(course_key, roles=exclude_roles),
            'cohort_user_ids': _get_users_in_cohort(user_id, course_key, ignore_groupwork=True),
        }

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        data = _get_courses_metrics_completions_leaders_list(course_key, **params)

        return Response(data, status=status.HTTP_200_OK)


class CoursesMetricsSocialLeadersList(SecureListAPIView):
    """
    ### The CoursesMetricsSocialLeadersList view allows clients to retrieve top n users who are leading
    in terms of social engagement and course social score average for the specified Course.
    If user_id parameter is given it would return user's position/rank in social engagement leaderboard
    - URI: ```/api/courses/{course_id}/metrics/social/leaders/?user_id={user_id}```
    - GET: Returns a JSON representation (array) of the users with social scores
    By default leaderboard has top 3 user scores to get more than 3 users use count parameter
    ``` /api/courses/{course_id}/metrics/social/leaders/?count=10```
    To exclude users with certain roles from leaderboard
    ```/api/courses/{course_id}/metrics/social/leaders/?exclude_roles=observer,assistant```
    ### Use Cases/Notes:
    * Example: Display social engagement leaderboard of a given course
    * Example: Display position of a users in a course in terms of social engagement and course avg
    """

    def get(self, request, course_id):
        """
        GET /api/courses/{course_id}/metrics/social/leaders/
        """
        course_key = get_course_key(course_id)
        exclude_roles = css_param_to_list(self.request, 'exclude_roles')
        user_id = self.request.query_params.get('user_id', None)
        params = {
            'user_id': user_id,
            'org_ids': get_ids_from_list_param(self.request, 'organizations'),
            'count': self.request.query_params.get('count', 3),
            'exclude_users': get_aggregate_exclusion_user_ids(course_key, roles=exclude_roles),
            'cohort_user_ids': _get_users_in_cohort(user_id, course_key, ignore_groupwork=True),
        }

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        data = _get_courses_metrics_social_leaders_list(course_key, **params)

        return Response(data, status=status.HTTP_200_OK)


class CourseMetricsLeaders(SecureAPIView):
    def get(self, request, course_id):
        """
        ### The `CourseMetricsLeaders` view contains combined functionality of:
        - `CoursesMetricsGradesLeadersList`,
        - `CoursesMetricsCompletionsLeadersList`,
        - `CoursesMetricsCompletionsLeadersList`.

        All params valid for the views above are applicable here as well.
        Returned data is a dict with the following structure:
        ```
        {
            'grades': data from `CoursesMetricsGradesLeadersList`
            'completions': data from `CoursesMetricsCompletionsLeadersList`
            'social': data from `CoursesMetricsCompletionsLeadersList`
        }
        ```
        Usage: `GET /api/courses/{course_id}/metrics/leaders/`
        """
        course_key = get_course_key(course_id)
        exclude_roles = css_param_to_list(self.request, 'exclude_roles')
        user_id = self.request.query_params.get('user_id', None)
        params = {
            'user_id': user_id,
            'group_ids': get_ids_from_list_param(self.request, 'groups'),
            'org_ids': get_ids_from_list_param(self.request, 'organizations'),
            'count': self.request.query_params.get('count', 3),
            'exclude_roles': css_param_to_list(self.request, 'exclude_roles'),
            'exclude_users': get_aggregate_exclusion_user_ids(course_key, roles=exclude_roles),
            'skipleaders': str2bool(self.request.query_params.get('skipleaders', 'false')),
            'cohort_user_ids': _get_users_in_cohort(user_id, course_key, ignore_groupwork=True),
        }

        if not course_exists(course_id):
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        pool = ThreadPool(3)
        data = {
            'grades': pool.apply_async(_get_courses_metrics_grades_leaders_list, (course_key,), params),
            'completions': pool.apply_async(_get_courses_metrics_completions_leaders_list, (course_key,), params),
            'social': pool.apply_async(_get_courses_metrics_social_leaders_list, (course_key,), params),
        }
        for key in data.keys():
            data[key] = data[key].get()

        return Response(data, status=status.HTTP_200_OK)


class CoursesWorkgroupsList(SecureListAPIView):
    """
    ### The CoursesWorkgroupsList view allows clients to retrieve a list of workgroups
    associated to a course
    - URI: ```/api/courses/{course_id}/workgroups/```
    - GET: Provides paginated list of workgroups associated to a course
    """

    serializer_class = BasicWorkgroupSerializer

    def get_queryset(self):
        course_id = self.kwargs['course_id']
        if not course_exists(course_id):
            raise Http404

        queryset = Workgroup.objects.filter(project__course_id=course_id)
        return queryset


class CoursesMetricsSocial(MobileListAPIView):
    """
    ### The CoursesMetricsSocial view allows clients to query about the activity of all users in the
    forums
    - URI: ```/api/courses/{course_id}/metrics/social/?organization={org_id}&score={bool}```
    - GET: Returns a list of social metrics for users in the specified course.
        * use `organization` query param to filter users by organization
        * use `scores` query param to get only scores of the users
    """

    def get(self, request, course_id):  # pylint: disable=arguments-differ
        if not request.user.is_staff:
            return Response({}, status=status.HTTP_401_UNAUTHORIZED)

        if not course_exists(course_id):
            raise Http404

        organization = request.query_params.get('organization', None)
        scores = request.query_params.get('scores', False)
        course_key = get_course_key(course_id)
        # remove any excluded users from the aggregate
        exclude_users = get_aggregate_exclusion_user_ids(course_key)

        if scores:
            data = StudentSocialEngagementScore.get_course_engagement_scores(course_key, organization, exclude_users)

        else:
            data = StudentSocialEngagementScore.get_course_engagement_stats(course_key, organization, exclude_users)

            enrollment_qs = CourseEnrollment.objects.users_enrolled_in(course_key) \
                .filter(is_active=True) \
                .exclude(id__in=exclude_users)

            if organization:
                enrollment_qs = enrollment_qs.filter(organizations=organization)

            data = {
                'total_enrollments': enrollment_qs.count(),
                'users': data,
            }

        return Response(data, status.HTTP_200_OK)


class CoursesMetricsCities(SecureListAPIView):
    """
    ### The CoursesMetricsCities view allows clients to retrieve ordered list of user
    count by city in a particular course. You can filter cities by cohort by providing `user_id` param.
    - URI: ```/api/courses/{course_id}/metrics/cities/?user_id={user_id}```
    - GET: Provides paginated list of user count by cities
    list can be filtered by city
    GET ```/api/courses/{course_id}/metrics/cities/?city={city1},{city2}```
    """

    serializer_class = UserCountByCitySerializer
    pagination_class = None

    def get_queryset(self):
        course_id = self.kwargs['course_id']
        user_id = self.request.query_params.get('user_id', None)
        city = css_param_to_list(self.request, 'city')
        if not course_exists(course_id):
            raise Http404
        course_key = get_course_key(course_id)
        exclude_users = get_aggregate_exclusion_user_ids(course_key)
        cohort_user_ids = _get_users_in_cohort(user_id, course_key, ignore_groupwork=True)
        cached_cities_data = get_cached_data('cities_count', course_id)
        if cached_cities_data and not len(self.request.query_params):
            queryset = cached_cities_data
        else:
            queryset = CourseEnrollment.objects.users_enrolled_in(course_key)\
                .exclude(id__in=exclude_users).exclude(profile__city__isnull=True).exclude(profile__city__iexact='')

            if cohort_user_ids:
                queryset = queryset.filter(id__in=cohort_user_ids)

            if city:
                q_list = [Q(profile__city__iexact=item.strip()) for item in city]
                q_list = reduce(lambda a, b: a | b, q_list)
                queryset = queryset.filter(q_list)

            queryset = queryset.values('profile__city').annotate(count=Count('profile__city')).order_by('-count')
            if not len(self.request.query_params):
                cache_course_data('cities_count', course_id, queryset)
        return queryset


class CoursesRolesList(SecureAPIView):
    """
    ### The CoursesRolesList view allows clients to interact with the Course's roleset
    - URI: ```/api/courses/{course_id}/roles```
    - GET: Returns a JSON representation of the specified Course roleset

    ### Use Cases/Notes:
    * Use the CoursesRolesList view to manage a User's TA status
    * Use GET to retrieve the set of roles configured for a particular course
    """

    def get(self, request, course_id):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/roles/
        """
        course_id = self.kwargs['course_id']
        if not course_exists(course_id):
            raise Http404

        user_id = self.request.query_params.get('user_id', None)
        role = self.request.query_params.get('role', None)
        course_key = get_course_key(course_id)
        instructors = CourseInstructorRole(course_key).users_with_role()
        staff = CourseStaffRole(course_key).users_with_role()
        observers = CourseObserverRole(course_key).users_with_role()
        assistants = CourseAssistantRole(course_key).users_with_role()
        all_users = (instructors | staff | observers | assistants).annotate(role=F("courseaccessrole__role"))
        if user_id:
            all_users = all_users.filter(id=int(user_id))
        if role:
            all_users = all_users.filter(role=role)
        return Response(
            all_users.distinct().values("id", "role"),
            status=status.HTTP_200_OK
        )

    def post(self, request, course_id):
        """
        POST /api/courses/{course_id}/roles/
        """
        course_id = self.kwargs['course_id']
        course_descriptor, course_key, course_content = get_course(self.request, self.request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            raise Http404

        user_id = request.data.get('user_id', None)
        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)

        role = request.data.get('role', None)
        try:
            _manage_role(course_descriptor, user, role, 'allow')
        except ValueError:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        return Response(request.data, status=status.HTTP_201_CREATED)


class CoursesRolesUsersDetail(SecureAPIView):
    """
    ### The CoursesUsersRolesDetail view allows clients to interact with a specific Course Role
    - URI: ```/api/courses/{course_id}/roles/{role}/users/{user_id}```
    - DELETE: Removes an existing Course Role specification
    ### Use Cases/Notes:
    * Use the DELETE operation to revoke a particular role for the specified user
    """
    def delete(self, request, course_id, role, user_id):  # pylint: disable=W0613
        """
        DELETE /api/courses/{course_id}/roles/{role}/users/{user_id}
        """
        course_descriptor, course_key, course_content = get_course(self.request, self.request.user, course_id)  # pylint: disable=W0612,C0301
        if not course_descriptor:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        try:
            user = User.objects.get(id=user_id)
        except ObjectDoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        try:
            _manage_role(course_descriptor, user, role, 'revoke')
        except ValueError:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

        return Response({}, status=status.HTTP_204_NO_CONTENT)


class CourseNavView(SecureAPIView):
    """
    ### The CourseNavView view exposes navigation information for particular usage id: course, chapter, section and
    vertical keys, position in innermost container and last addressable block/module on the path (usually the same
    usage id that was passed as an argument)
    - URI: ```/api/courses/{course_id}/navigation/{module_id}```
    - GET: Gets navigation information
    """

    def _get_full_location_key_by_module_id(self, request, course_key, module_id):
        """
        Gets full location id by module id
        """
        items = modulestore().get_items(course_key, qualifiers={'name': module_id})
        if len(items) == 0:
            usage_key = get_course_child_key(module_id)
            if usage_key:
                try:
                    item = modulestore().get_item(usage_key)
                    return item.location
                except ItemNotFoundError:
                    raise Http404()
            else:
                raise Http404()
        return items[0].location

    def get(self, request, course_id, usage_key_string):  # pylint: disable=W0613
        """
        GET /api/courses/{course_id}/navigation/{module_id}
        """
        try:
            _, course_key, __ = get_course(request, request.user, course_id)
            usage_key = self._get_full_location_key_by_module_id(request, course_key, usage_key_string)
        except InvalidKeyError:
            raise Http404(u"Invalid course_key or usage_key")

        (course_key, chapter, section, vertical, position, final_target_id) = path_to_location(modulestore(), usage_key)
        chapter_key = course_key.make_usage_key('chapter', chapter)
        section_key = course_key.make_usage_key('sequential', section)
        vertical_key = course_key.make_usage_key('vertical', vertical)

        result = {
            'course_key': unicode(course_key),
            'chapter': unicode(chapter_key),
            'section': unicode(section_key),
            'vertical': unicode(vertical_key),
            'position': unicode(position),
            'final_target_id': unicode(final_target_id)
        }

        return Response(result, status=status.HTTP_200_OK)


class CoursesTree(MobileListAPIView):
    """
    **Use Case**

        CoursesTree returns a course tree for a list of comma seperated course ids.
        Response will be a list of courses with content and id. content is actual course tree.


    **Example Request**

        POST /api/courses/tree
        {
            course_ids: ['CA/CS102/2018', 'CA/CS104/2019']
        }

    **Example Response**
    [
        {
            "id": **,
            "content": {
            }
        },
        {
            "id": **,
            "content": {
            }
        },
    ]
    """
    def post(self, request):
        course_ids = request.data.get('course_ids')
        course_ids = [get_course_key(c) for c in course_ids]
        course_structures = CourseStructure.objects.filter(course_id__in=course_ids)
        response_data = []
        for course_structure in course_structures:
            blocks = course_structure.structure.get('blocks', {})

            course = None
            for block in blocks.values():
                block['name'] = block.pop('display_name')
                block['category'] = block.pop('block_type')
                if course is None and block['category'] == 'course':
                    course = block

            self._update_blocks(course, blocks)
            course_data = {
                "id": str(course_structure.course_id),
                "content": course['children']
            }
            response_data.append(course_data)
        return Response(response_data, status=status.HTTP_200_OK)

    def _update_blocks(self, _block, _blocks):
        # add actual blocks from _blocks instead of block ids in the _block

        children = []
        for block_id in _block['children']:
            _blocks[block_id]['id'] = block_id
            children.append(_blocks[block_id])

        _block['children'] = children
        for b in _block['children']:
            self._update_blocks(b, _blocks)


class OoyalaToBcoveConversion(MobileAPIView, IsStaffView):
    """
    Controls a background task to convert the Ooyala Xblock's
    instances in given courses to Brightcove
    """
    batch_size = 100

    def post(self, request):
        course_ids = request.data.get('course_ids')
        staff_user_id = request.data.get('staff_user_id')
        company_name = request.data.get('company_name')
        revert = str2bool(request.data.get('revert'))
        run_on_all = str2bool(request.data.get('run_on_all'))
        exclude_course_ids = request.data.get('exclude_course_ids', [])
        email_ids = request.data.get('email_ids')
        task_ids = []

        if None in (course_ids or run_on_all, staff_user_id) \
                or not isinstance(exclude_course_ids, list):
            return Response(status.HTTP_400_BAD_REQUEST)

        try:
            User.objects.get(id=staff_user_id)
        except User.DoesNotExist:
            return Response(status.HTTP_400_BAD_REQUEST)

        # run on all open courses
        if run_on_all:
            try:
                exclude_course_ids = [CourseKey.from_string(course_id) for course_id in exclude_course_ids]
            except Exception as e:
                return Response(status.HTTP_400_BAD_REQUEST)

            course_ids = CourseOverview.objects.filter(
                Q(end__gte=datetime.today().replace(tzinfo=UTC)) |
                Q(end__isnull=True)
            ).exclude(id__in=exclude_course_ids).values_list('id', flat=True)

        for course_ids in self.chunks(course_ids, self.batch_size):
            task = convert_ooyala_to_bcove.delay(
                staff_user_id=staff_user_id,
                course_ids=course_ids,
                revert=revert,
                company_name=company_name,
                email_ids=email_ids,
                callback="conversion_script_success_callback",
            )

            task_ids.append(task.task_id)

        return Response({'task_ids': task_ids}, status=status.HTTP_200_OK)

    @staticmethod
    def chunks(l, n):
        """Yield successive n-sized chunks from l."""
        for i in range(0, len(l), n):
            yield l[i:i + n]

    def get(self, request):
        course_ids = request.data.get('course_ids')
        email_ids = request.data.get('email_ids')
        report = request.data.get('report')

        if not email_ids:
            return Response(status.HTTP_400_BAD_REQUEST)

        if report not in ('non_ie_html_videos', 'all_videos'):
            return Response(status.HTTP_400_BAD_REQUEST)

        task = get_modules_with_video_embeds.delay(
            course_ids=course_ids,
            email_ids=email_ids,
            report=report,
            callback="module_list_success_callback",
        )

        return Response({'result': task.task_id}, status=status.HTTP_200_OK)


class AssetURLs(MobileAPIView, IsStaffView):
    def get(self, request):
        course_ids = request.data.get('course_ids')
        email_ids = request.data.get('email_ids')
        environment = request.data.get('env')
        studio_url = request.data.get('studio_url')
        course_type = request.data.get('course_type', 'open')
        staff_user_id = request.data.get('staff_user_id')
        update = request.data.get('update', False)

        if None in (email_ids, environment, staff_user_id):
            return Response(status.HTTP_400_BAD_REQUEST)

        task = get_assets_with_incorrect_urls.delay(
            course_ids=course_ids,
            course_type=course_type,
            email_ids=email_ids,
            environment=environment,
            studio_url=studio_url,
            staff_user_id=staff_user_id,
            update=update,
        )

        return Response({'task_id': task.task_id}, status=status.HTTP_200_OK)
