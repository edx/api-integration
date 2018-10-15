from completion_aggregator.models import Aggregator
from django.db.models import Q, Sum


def _get_filtered_aggregation_queryset(
        course_key,
        exclude_users,
        org_ids=None,
        group_ids=None,
        cohort_user_ids=None,
):
    queryset = Aggregator.objects.filter(
        course_key__exact=course_key,
        user__is_active=True,
        user__courseenrollment__is_active=True,
        user__courseenrollment__course_id__exact=course_key,
        aggregation_name='course',
    ).exclude(user_id__in=exclude_users)
    if org_ids:
        queryset = queryset.filter(user__organizations__in=org_ids)
    if group_ids:
        queryset = queryset.filter(user__groups__in=group_ids).distinct()
    if cohort_user_ids:
        queryset = queryset.filter(user_id__in=cohort_user_ids)
    return queryset


def generate_leaderboard(
        course_key,
        count=None,
        exclude_users=None,
        org_ids=None,
        group_ids=None,
        cohort_user_ids=None,
):
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
    queryset = _get_filtered_aggregation_queryset(course_key, exclude_users, org_ids, group_ids, cohort_user_ids)
    queryset = queryset.values(
        'user__id',
        'user__username',
        'user__profile__title',
        'user__profile__profile_image_uploaded_at',
        'earned',
        'percent',
    ).order_by('-percent', 'modified')[:count]

    return queryset


def get_total_completions(course_key, exclude_users, org_ids=None, group_ids=None, cohort_user_ids=None):
    queryset = _get_filtered_aggregation_queryset(course_key, exclude_users, org_ids, group_ids, cohort_user_ids)
    return queryset.aggregate(total_earned=Sum('earned')).get('total_earned')


def get_num_users_started(course_key, exclude_users, org_ids=None, group_ids=None, cohort_user_ids=None):
    queryset = _get_filtered_aggregation_queryset(course_key, exclude_users, org_ids, group_ids, cohort_user_ids)
    return queryset.distinct().count()


def get_user_position(course_key, user_id, exclude_users=None):
    """
    Returns user's progress position and completions for a given course.
    data = {"completions": 22, "position": 4}
    """
    data = {"completions": 0, "position": 0}
    try:
        queryset = Aggregator.objects.get(
            course_key=course_key,
            user__id=user_id,
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

        users_above = Aggregator.objects.filter(
            more_completions_than_user | same_completions_but_faster_than_user,
            course_key=course_key,
            user__is_active=True,
            aggregation_name='course',
        ).exclude(user__id__in=exclude_users).count()
        data['position'] = users_above + 1
        data['completions'] = user_completions * 100
    return data
