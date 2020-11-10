from completion_aggregator.models import Aggregator
from django.db.models import Avg, F, Q, Sum
from edx_solutions_api_integration.courseware_access import get_course_key
from edx_solutions_api_integration.utils import (
    Round, cache_course_data, get_aggregate_exclusion_user_ids,
    get_cached_data, get_non_actual_company_users)
from student.models import CourseEnrollment


def get_filtered_aggregation_queryset(course_key, **kwargs):
    queryset = Aggregator.objects.filter(
        course_key__exact=course_key,
        user__is_active=True,
        user__courseenrollment__is_active=True,
        user__courseenrollment__course_id__exact=course_key,
        aggregation_name='course',
    ).exclude(user_id__in=kwargs.get('exclude_users') or [])

    if kwargs.get('org_ids'):
        queryset = queryset.filter(user__organizations__in=kwargs.get('org_ids'))

    if kwargs.get('group_ids'):
        queryset = queryset.filter(user__groups__in=kwargs.get('group_ids')).distinct()

    if kwargs.get('cohort_user_ids'):
        queryset = queryset.filter(user_id__in=kwargs.get('cohort_user_ids'))

    return queryset


def generate_leaderboard(course_key, **kwargs):
    """
    Assembles a data set representing the Top N users, by progress, for a given course.

    data = [
            {
                'id': 123,
                'username': 'testuser1',
                'title': 'Engineer',
                'profile_image_uploaded_at': '2014-01-15 06:27:54',
                'completions': 0.92
            },
            {
                'id': 983,
                'username': 'testuser2',
                'title': 'Analyst',
                'profile_image_uploaded_at': '2014-01-15 06:27:54',
                'completions': 0.91
            },
            {
                'id': 246,
                'username': 'testuser3',
                'title': 'Product Owner',
                'profile_image_uploaded_at': '2014-01-15 06:27:54',
                'completions': 0.90
            },
            {
                'id': 357,
                'username': 'testuser4',
                'title': 'Director',
                'profile_image_uploaded_at': '2014-01-15 06:27:54',
                'completions': 0.89
            },
    ]

    """
    count = kwargs.get('count')
    queryset = get_filtered_aggregation_queryset(course_key, **kwargs).filter(percent__gt=0)
    queryset = queryset.values(
        'user__id',
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__profile__title',
        'user__profile__profile_image_uploaded_at',
        'earned',
        'percent',
    ).order_by('-percent', 'modified')
    if count:
        queryset = queryset [:int(count)]

    return queryset


def get_total_completions(course_key, **kwargs):
    queryset = get_filtered_aggregation_queryset(course_key, **kwargs)
    if kwargs.get('percent_completion'):
        aggregate = queryset.aggregate(percent=Sum(Round((F('earned')/F('possible'))*100)), possible=Avg('possible'))
        return aggregate.get('percent'), aggregate.get('possible')
    else:
        aggregate = queryset.aggregate(earned=Sum('earned'), possible=Avg('possible'))
        return aggregate.get('earned'), aggregate.get('possible')


def get_num_users_started(course_key, **kwargs):
    queryset = get_filtered_aggregation_queryset(course_key, **kwargs)
    return queryset.distinct().count()


def get_user_position(course_key, **kwargs):
    """
    Returns user's progress position and completions for a given course.
    data = {"completions": 22, "position": 4}
    """
    data = {"completions": 0, "position": 0}
    try:
        queryset = Aggregator.objects.get(
            course_key=course_key,
            user__id=kwargs.get('user_id'),
            aggregation_name='course',
        )
    except Aggregator.DoesNotExist:
        return data

    if queryset:
        user_completions = queryset.percent
        user_time_completed = queryset.modified

        more_completions_than_user = Q(percent__gt=user_completions)
        same_completions_but_faster_than_user = Q(
            percent=user_completions,
            modified__lt=user_time_completed,
        )

        users_above_qs = Aggregator.objects.filter(
            more_completions_than_user | same_completions_but_faster_than_user,
            course_key=course_key,
            user__is_active=True,
            aggregation_name='course',
        ).exclude(user__id__in=kwargs.get('exclude_users') or [])

        if kwargs.get('cohort_user_ids'):
            users_above_qs = users_above_qs.filter(user__id__in=kwargs.get('cohort_user_ids'))

        users_above = users_above_qs.count()

        data['position'] = users_above + 1
        data['completions'] = user_completions * 100
    return data


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
