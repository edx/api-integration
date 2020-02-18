import logging
import pymongo
import re
import datetime
from urlparse import urlparse
from pytz import UTC

from bson.son import SON
from celery.task import task, Task

from django.db.models import Q
from django.core.mail import send_mail
from django.conf import settings
from opaque_keys.edx.keys import CourseKey
from xmodule.modulestore.django import modulestore
from xmodule.contentstore.content import StaticContent

from openedx.core.djangoapps.content.course_overviews.models import CourseOverview

log = logging.getLogger(__name__)
store = modulestore()

URL_RE = 'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'


class AssetURLsTask(Task):
    def on_success(self, result, task_id, args, kwargs):
        email_ids = kwargs.get('email_ids')

        results = ''
        if result:
            for course, blocks in result.items():
                if blocks:
                    results += '''\n\n {}:'''.format(course)
                    results += '''\n\n{}'''.format('\n'.join(blocks))

        subject = 'Get assets with incorrect urls task completed'
        text = '''Following is the list of modules where incorrect asset links exist:{}'''.format(results)

        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, email_ids)


@task(
    name=u'lms.djangoapps.api_integration.tasks.get_assets_with_incorrect_urls',
    bind=True,
    base=AssetURLsTask
)
def get_assets_with_incorrect_urls(self, course_ids, email_ids, environment):
    if not course_ids:
        course_ids = CourseOverview.objects.filter(
            Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
            Q(end__isnull=True)
        ).values_list('id', flat=True)

    course_asset_blocks = dict()
    for course_id in course_ids:
        asset_blocks = find_asset_urls_in_course(course_id, environment)
        if asset_blocks:
            course_asset_blocks[course_id] = asset_blocks

    return course_asset_blocks


def find_asset_urls_in_course(course_id, environment):
    block_url = '/courses/{}/lessons/jump_to_page/{}'
    course_key = CourseKey.from_string(course_id)
    query = SON([
        ('_id.tag', 'i4x'),
        ('_id.org', course_key.org),
        ('_id.course', course_key.course),
    ])

    _store = store._get_modulestore_for_courselike(course_key)
    blocks = list(_store.collection.find(
        query,
        sort=[('_id.revision', pymongo.DESCENDING)],
    ))

    asset_blocks = []
    for block in blocks:
        asset_urls = []
        _find_asset_urls_in_block(block, asset_urls, course_key, environment)
        if asset_urls:
            loc = block.get('_id', {}).get('name')
            asset_blocks.append(block_url.format(course_id, loc))

    return asset_blocks


def _find_asset_urls_in_block(block, asset_urls, course_key, environment):
    for key, value in block.items():
        if type(value) == dict:
            _find_asset_urls_in_block(value, asset_urls, course_key, environment)
        if type(value) == str or type(value) == unicode:
            urls = re.findall(URL_RE, value)
            for url in urls:
                parsed_url = urlparse(url)
                asset_url = StaticContent.ASSET_URL_RE.match(parsed_url.path)

                if asset_url is not None:
                    # check if asset URL belongs to some other server or course
                    if parsed_url.hostname != environment or \
                                    asset_url.groupdict().get('course') != course_key.course:
                        asset_urls.append(url)
