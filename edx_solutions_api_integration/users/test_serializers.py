import ddt
from datetime import datetime
from django.test import TestCase
from student.models import CourseEnrollment
from edx_solutions_api_integration.users.serializers import CourseProgressSerializer


@ddt.ddt
class CourseProgressSerializerTests(TestCase):

    @ddt.data(
        (
            # Values from the Newer API
            'test_course_id',
            {
                'completion': {
                    'earned': 3,
                    'possible': 4
                }
            },
            75.0
        ),
        (
            # No Value from the Newer API
            '', {}, 50.0
        ),
    )
    @ddt.unpack
    def test_get_progress(self, new_api_course_id, new_api_data, expected_progress):
        course_id = 'test_course_id'
        enrollment = {
            'course_id': course_id,
            'created': datetime.now(),
            'is_active': True,
            'progress': 0,
        }
        context = {
            'new_api_data': {new_api_course_id: new_api_data},
            'course_overview': [],
            'student_progress': [
                {
                    'course_id': course_id,
                    'completions': 1,
                },
            ],
            'course_metadata': [
                {
                    'id': course_id,
                    'total_assessments': 2
                }
            ]
        }

        serializer = CourseProgressSerializer(enrollment, context=context)
        data = serializer.data
        self.assertEqual(data['progress'], expected_progress)
