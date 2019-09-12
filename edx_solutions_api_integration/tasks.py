import logging
import json

from celery.task import task
import urllib2

from django.conf import settings

from xmodule.modulestore.django import modulestore
from opaque_keys.edx.keys import CourseKey

PLAYBACK_API_ENDPOINT = 'https://edge.api.brightcove.com/playback/v1/accounts/{account_id}/videos/ref:{reference_id}'
BRIGHTCOVE_ACCOUNT_ID = '6057949416001'

logger = logging.getLogger('edx.celery.task')
store = modulestore()


@task(name=u'lms.djangoapps.api_integration.tasks.convert_ooyala_ids_to_bcove')
def convert_ooyala_ids_to_bcove(staff_user_id, course_ids):
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

            if content_id and not is_bcove_id(content_id):
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
        logger.warning('Brightcove ID retrieval failed against reference ID: `{}`'.format(reference_id))
    else:
        logger.info('Successful retrieval of Brightcove ID against reference ID: `{}`'.format(reference_id))
        bc_video_id = video_data.get('id')

    return bc_video_id
