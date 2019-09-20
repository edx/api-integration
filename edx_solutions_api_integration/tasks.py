import logging
import json
import re

from celery.task import task
import urllib2
from bs4 import BeautifulSoup

from django.conf import settings
from django.template.loader import render_to_string

from xmodule.modulestore.django import modulestore
from opaque_keys.edx.keys import CourseKey

PLAYBACK_API_ENDPOINT = 'https://edge.api.brightcove.com/playback/v1/accounts/{account_id}/videos/ref:{reference_id}'
BRIGHTCOVE_ACCOUNT_ID = '6057949416001'


logger = logging.getLogger('edx.celery.task')
store = modulestore()


@task(name=u'lms.djangoapps.api_integration.tasks.convert_ooyala_ids_to_bcove')
def convert_ooyala_ids_to_bcove(staff_user_id, course_ids, revert=False):
    xblock_settings = settings.XBLOCK_SETTINGS if hasattr(settings, "XBLOCK_SETTINGS") else {}
    bcove_policy = xblock_settings.get('OoyalaPlayerBlock', {}).get('BCOVE_POLICY')

    if not bcove_policy:
        logger.error('BCOVE POLICY value not found in settings. Exiting.')
        return True

    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)
        oo_blocks = store.get_items(
            course_key,
            qualifiers={"category": 'ooyala-player'}
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
                bcove_video_id = get_brightcove_video_id(content_id, bcove_policy)

                if bcove_video_id:
                    block.content_id = bcove_video_id
                    block.reference_id = content_id

                    store.update_item(xblock=block, user_id=staff_user_id)

                    logger.info('Successfully Updated Ooyala ID for block `{}` in course: `{}`'
                                .format(block.parent.block_id, course_id))


def is_bcove_id(video_id):
    """
    Checks if video_id belongs to Brightcove
    Brightcove IDs are all numeric
    """
    try:
        int(video_id)
    except ValueError:
        return False
    else:
        return True


def get_brightcove_video_id(reference_id, bcove_policy):
    """
    Get a Brightcove video id against reference id
    using Brightcove Playback API
    """
    bc_video_id = None
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
    else:
        logger.info('Successful retrieval of Brightcove ID against reference ID: `{}`'.format(reference_id))
        bc_video_id = video_data.get('id')

    return bc_video_id


@task(name=u'lms.djangoapps.api_integration.tasks.convert_ooyala_embeds')
def convert_ooyala_embeds(staff_user_id, course_ids, revert=False):
    xblock_settings = settings.XBLOCK_SETTINGS if hasattr(settings, "XBLOCK_SETTINGS") else {}
    bcove_policy = xblock_settings.get('OoyalaPlayerBlock', {}).get('BCOVE_POLICY')

    if not bcove_policy:
        logger.error('BCOVE POLICY value not found in settings. Exiting.')
        return True

    for course_id in course_ids:
        course_key = CourseKey.from_string(course_id)

        for blocks in blocks_to_clean(course_key):
            for block in blocks:
                transform_ooyala_embeds(block, staff_user_id, course_id, bcove_policy)


def blocks_to_clean(course_key):
    categories = ['html']
    for category in categories:
        yield store.get_items(course_key, qualifiers={"category": category})


def transform_ooyala_embeds(block, user_id, course_id, bcove_policy):
    """
    Transforms ooyala embeds in the given block
    """
    soup = BeautifulSoup(block.data, 'html.parser')
    soup, bcove_ids, updated = cleanup_ooyala_tags(soup, bcove_policy)

    # insert new embeds in the block
    if bcove_ids:
        soup = insert_bcove_embed(block.category, soup, bcove_ids)

    # update back block's data
    if updated:
        block.data = str(soup)
        store.update_item(xblock=block, user_id=user_id)
        logger.info('Successfully transformed Ooyala embeds for block `{}` in course: `{}`'
                    .format(block.parent.block_id, course_id))


def cleanup_ooyala_tags(soup, bcove_policy):
    """
    Remove any ooyala related scripts from given BeautifulSoup instance
    extract out associated bcove ids
    """
    oo_reg = r"OO.Player.create\(['\"]\w+['\"],['\"]\w+['\"]"
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
                        bcove_id = get_brightcove_video_id(oo_id, bcove_policy)
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

    if block_type == 'html':
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
