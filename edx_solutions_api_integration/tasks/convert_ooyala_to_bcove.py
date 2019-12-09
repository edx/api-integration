import logging
import json
import re
from pytz import UTC
import datetime
from collections import defaultdict

from celery.task import task, Task
import urllib2
from bs4 import BeautifulSoup

from django.conf import settings
from django.template.loader import render_to_string
from django.core.cache import cache
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db.models import Q
from xmodule.modulestore.django import modulestore
from xmodule.modulestore import ModuleStoreEnum
from opaque_keys.edx.keys import CourseKey

from openedx.core.djangoapps.content.block_structure.api import clear_course_from_cache
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


PLAYBACK_API_ENDPOINT = 'https://edge.api.brightcove.com/playback/v1/accounts/{account_id}/videos/ref:{reference_id}'
BRIGHTCOVE_ACCOUNT_ID = '6057949416001'
RESULTS_CACHE_KEY = 'bcove-task-{}'

logger = logging.getLogger('edx.celery.task')
store = modulestore()


def conversion_script_success_callback(errors, kwargs):
    user_id = kwargs.get('staff_user_id')
    course_ids = kwargs.get('course_ids')
    company_name = kwargs.get('company_name')

    if not errors:
        errors = 'No errors.'
    else:
        errors = '\n'.join(errors)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.warning('Brightcove conversion task:: User `{}` does not exist. Could not send success email.'
                       .format(user_id))
    else:
        subject = 'Ooyala to Brightcove conversion task completed'
        if company_name:
            subject += ' for {}'.format(company_name)

        text = '''Ooyala to Brightcove conversion task has been completed for following courses: \n {} \n\n\n
                Errors:\n
                {}'''.format('\n'.join(course_ids), errors)

        send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email])


class ConversionScriptTask(Task):
    def on_success(self, result, task_id, args, kwargs):
        callback = kwargs.get('callback')
        if callback and globals().get(callback):
            globals()[callback](result, kwargs)


@task(name=u'lms.djangoapps.api_integration.tasks.convert_ooyala_to_bcove', bind=True, base=ConversionScriptTask)
def convert_ooyala_to_bcove(
        self, staff_user_id, course_ids,
        company_name=None, callback=None,
        revert=False
    ):
    xblock_settings = settings.XBLOCK_SETTINGS if hasattr(settings, "XBLOCK_SETTINGS") else {}
    bcove_policy = xblock_settings.get('OoyalaPlayerBlock', {}).get('BCOVE_POLICY')
    cache_key = RESULTS_CACHE_KEY.format(self.request.id)

    cache.set(cache_key, [])

    if not bcove_policy:
        error = 'BCOVE POLICY value not found in settings. Exiting.'
        logger.error(error)
        return [error]

    convert_ooyala_ids_to_bcove(staff_user_id, course_ids, self.request.id, bcove_policy, revert)
    convert_ooyala_embeds(staff_user_id, course_ids, self.request.id, bcove_policy)

    flush_courses_cache(course_ids)

    result = cache.get(cache_key)

    return result

def convert_ooyala_ids_to_bcove(staff_user_id, course_ids, task_id, bcove_policy, revert=False):
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        oo_blocks = store.get_items(
            course_key,
            qualifiers={"category": 'ooyala-player'},
            revision=ModuleStoreEnum.RevisionOption.published_only
        )

        for block in oo_blocks:
            content_id = block.content_id

            if content_id and revert:
                # write reference id back to content_id and empty out reference id
                if is_bcove_id(content_id) and block.reference_id:
                    block.content_id = block.reference_id
                    block.reference_id = ''

                    store.update_item(xblock=block, user_id=staff_user_id)

                    logger.info('Successfully reverted Brightcove ID for block `{}` in course: `{}`'
                                .format(block.parent.block_id, course_id))
            elif content_id and not is_bcove_id(content_id):
                bcove_video_id = get_brightcove_video_id(
                    content_id, block.parent.block_id,
                    course_id, task_id, bcove_policy
                )

                if bcove_video_id:
                    block.content_id = bcove_video_id
                    block.reference_id = content_id

                    store.update_item(xblock=block, user_id=staff_user_id)

                    logger.info('Successfully Updated Ooyala ID for block `{}` in course: `{}`'
                                .format(block.parent.block_id, course_id))


def flush_courses_cache(course_ids):
    """
    Clears course cache so API returns updated data
    """
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        clear_course_from_cache(course_key)


def is_bcove_id(video_id):
    """
    Checks if video_id belongs to Brightcove
    Brightcove IDs are all numeric
    """
    try:
        int(video_id)
    except (ValueError, TypeError):
        return False
    else:
        return True


def get_brightcove_video_id(reference_id, block_id, course_id, task_id, bcove_policy):
    """
    Get a Brightcove video id against reference id
    using Brightcove Playback API
    """
    bc_video_id = None
    cache_key = RESULTS_CACHE_KEY.format(task_id)
    api_endpoint = PLAYBACK_API_ENDPOINT.format(
        account_id=BRIGHTCOVE_ACCOUNT_ID,
        reference_id=reference_id
    )
    request = urllib2.Request(api_endpoint, headers={"BCOV-Policy": bcove_policy})

    try:
        response = urllib2.urlopen(request).read()
        video_data = json.loads(response)
    except Exception as e:
        logger.warning('Brightcove ID retrieval failed against reference ID: `{}` with exception: {}'
                       .format(reference_id, e.message))
        errors = cache.get(cache_key)
        if errors is not None:
            errors.append('Video `{}` not found on Video Cloud. '
                      'Could not convert block `{}` in course `{}`'.format(reference_id, block_id, course_id))
            cache.set(cache_key, errors)
    else:
        logger.info('Successful retrieval of Brightcove ID against reference ID: `{}`'.format(reference_id))
        bc_video_id = video_data.get('id')

    return bc_video_id


def convert_ooyala_embeds(staff_user_id, course_ids, task_id, bcove_policy):
    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)

        for blocks in blocks_to_clean(course_key):
            for block in blocks:
                transform_ooyala_embeds(block, staff_user_id, course_id, task_id, bcove_policy)


def blocks_to_clean(course_key):
    categories = [
        'html',
        'image-explorer',
        'adventure',
        'pb-mcq',
        'pb-mrq',
        'pb-tip',
        'pb-answer',
        'poll',
        'survey',
        'gp-v2-video-resource',
        'static_tab',
    ]
    for category in categories:
        yield store.get_items(
            course_key,
            qualifiers={"category": category},
            revision=ModuleStoreEnum.RevisionOption.published_only
        )


def transform_ooyala_embeds(block, user_id, course_id, task_id, bcove_policy):
    """
    Transforms ooyala embeds in the given block
    """
    if hasattr(block.parent, 'block_id'):
        block_loc = block.parent.block_id
    else:
        block_loc = block.location

    # adventure has different format for ooyala tags
    if block.category == 'adventure':
        updated = False
        soup = BeautifulSoup(block.xml_content, 'html.parser')

        for oo_tag in soup.find_all('ooyala-player'):
            oo_id = oo_tag.attrs.get('content_id', '')

            if oo_id and not is_bcove_id(oo_id):
                bcove_id = get_brightcove_video_id(oo_id, block_loc, course_id, task_id, bcove_policy)
                if is_bcove_id(bcove_id):
                    updated = True
                    oo_tag.attrs['content_id'] = bcove_id

        if updated:
            block.xml_content = str(soup)
            store.update_item(xblock=block, user_id=user_id)
            logger.info('Successfully transformed Ooyala embeds for block `{}` in course: `{}`'
                        .format(block_loc, course_id))
    elif block.category == 'gp-v2-video-resource':
        updated = False
        oo_id = block.video_id
        if oo_id and not is_bcove_id(oo_id):
            bcove_id = get_brightcove_video_id(oo_id, block_loc, course_id, task_id, bcove_policy)
            if is_bcove_id(bcove_id):
                updated = True
                block.video_id = bcove_id

        if updated:
            store.update_item(xblock=block, user_id=user_id)
            logger.info('Successfully transformed Ooyala embeds for block `{}` in course: `{}`'
                        .format(block_loc, course_id))
    else:
        if block.category in ('pb-mcq', 'poll', 'pb-mrq', 'pb-answer'):
            soup = BeautifulSoup(block.question, 'html.parser')
        elif block.category == 'pb-tip':
            soup = BeautifulSoup(block.content, 'html.parser')
        elif block.category == 'survey':
            soup = BeautifulSoup(block.feedback, 'html.parser')
        else:
            soup = BeautifulSoup(block.data, 'html.parser')

        soup, bcove_ids, updated = cleanup_ooyala_tags(soup, block_loc, course_id, task_id, bcove_policy)

        # insert new embeds in the block
        if bcove_ids:
            soup = insert_bcove_embed(block.category, soup, bcove_ids)

        # update back block's data
        if updated:
            if block.category in ('pb-mcq', 'poll', 'pb-mrq', 'pb-answer'):
                block.question = str(soup)
            elif block.category == 'pb-tip':
                block.content = str(soup)
            elif block.category == 'survey':
                block.feedback = str(soup)
            else:
                block.data = str(soup)

            store.update_item(xblock=block, user_id=user_id)

            logger.info('Successfully transformed Ooyala embeds for block `{}` in course: `{}`'
                        .format(block_loc, course_id))


def cleanup_ooyala_tags(soup, block_loc, course_id, task_id, bcove_policy):
    """
    Remove any ooyala related scripts from given BeautifulSoup instance
    extract out associated bcove ids
    """
    oo_reg = r"OO.Player.create\(['\"]\w+['\"],['\"][\w+-]+['\"]"
    bcove_ids = []
    updated = False

    for script in soup.find_all('script'):
        # remove any spaces for regex to work properly
        script_text = script.get_text().strip().replace(' ', '')
        decompose = False

        if 'OO.Player.create' in script_text:
            match = re.search(oo_reg, script_text)
            if match:
                parts = match.group().split(',')
                if len(parts) > 1:
                    oo_id = parts[1].strip("'")

                    if not is_bcove_id(oo_id):
                        bcove_id = get_brightcove_video_id(oo_id, block_loc, course_id, task_id, bcove_policy)
                        if is_bcove_id(bcove_id):
                            bcove_ids.append(bcove_id)
                            decompose = True

        if 'player.ooyala.com' in script.attrs.get('src', ''):
            decompose = True

        if decompose:
            updated = True
            script.decompose()

    return soup, bcove_ids, updated


def insert_bcove_embed(block_type, soup, bcove_ids):
    # any div with id starting with 'ooyala'
    oo_regex = re.compile('^ooyala')

    if block_type in ('html', 'pb-mcq', 'pb-tip', 'poll', 'survey', 'pb-mrq', 'pb-answer', 'static_tab',):
        template = 'bcove_html_embed.html'
    elif block_type == 'image-explorer':
        template = 'bcove_ie_embed.html'
    else:
        logger.warning('Unrecognized block type `{}`. Not updating embed'.format(block_type))
        return

    for index, oo_div in enumerate(soup.find_all('div', {'id': oo_regex})):
        try:
            bcove_id = bcove_ids[index]
        except IndexError:
            continue

        div_id = oo_div.attrs.get('id', 'bcove-player')

        bcove_embed_code = render_to_string(
            template, {
            'dom_id': div_id,
            'account_id': BRIGHTCOVE_ACCOUNT_ID,
            'video_id': bcove_id
        })
        bcove_embed_code = BeautifulSoup(bcove_embed_code, 'html.parser')

        # embed new code after oo div
        oo_div.insert_after(bcove_embed_code)
        oo_div.decompose()

    return soup


def non_html_ie_blocks(course_key):
    categories = [
        'adventure',
        'pb-mcq',
        'pb-mrq',
        'pb-tip',
        'pb-answer',
        'poll',
        'survey',
        'gp-v2-video-resource',
    ]
    for category in categories:
        yield store.get_items(
            course_key,
            qualifiers={"category": category},
            revision=ModuleStoreEnum.RevisionOption.published_only
        )


def module_list_success_callback(result, kwargs):
    email_ids = kwargs.get('email_ids')

    results = ''
    if result:
        for category, modules in result.items():
            results += '''\n\n{}: {} module \n {}'''.format(category, len(modules), '\n'.join(modules))

    subject = 'Brightcove get module list task completed'

    text = '''Following is the list of modules where Video embeds exist: \n\n\n{}'''.format(results)

    send_mail(subject, text, settings.DEFAULT_FROM_EMAIL, email_ids)


@task(name=u'lms.djangoapps.api_integration.tasks.get_modules_with_video_embeds',bind=True, base=ConversionScriptTask)
def get_modules_with_video_embeds(self, email_ids, callback=None):
    course_ids = CourseOverview.objects.filter(
        Q(end__gte=datetime.datetime.today().replace(tzinfo=UTC)) |
        Q(end__isnull=True)
    ).values_list('id', flat=True)

    # create studio url of module
    block_url = '/container/{}'
    block_locs = defaultdict(list)

    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        for blocks in non_html_ie_blocks(course_key):
            for block in blocks:
                if block.category == 'adventure':
                    soup = BeautifulSoup(block.xml_content, 'html.parser')
                    if soup.find_all('ooyala-player'):
                        module_url = block_url.format(block.location)
                        block_locs[block.category].append(module_url)
                elif block.category == 'gp-v2-video-resource':
                    if block.video_id:
                        module_url = block_url.format(block.location)
                        block_locs[block.category].append(module_url)
                else:
                    if block.category in ('pb-mcq', 'poll', 'pb-mrq', 'pb-answer'):
                        soup = BeautifulSoup(block.question, 'html.parser')
                    elif block.category == 'pb-tip':
                        soup = BeautifulSoup(block.content, 'html.parser')
                    elif block.category == 'survey':
                        soup = BeautifulSoup(block.feedback, 'html.parser')
                    else:
                        soup = BeautifulSoup(block.data, 'html.parser')

                    for script in soup.find_all('script'):
                        script_text = script.get_text().strip().replace(' ', '')
                        if 'OO.Player.create' in script_text:
                            module_url = block_url.format(block.location)
                            block_locs[block.category].append(module_url)

    return block_locs
