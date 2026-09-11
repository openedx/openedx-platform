"""Private Studio console for global administrators managing course runs."""

import json
import logging
import re
from datetime import timezone
from functools import wraps

from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore import course_admin as service
from cms.djangoapps.contentstore.utils import get_schedule_details_url
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.certificates.api import get_self_generation_enabled_for_courses
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore.django import modulestore


LOG = logging.getLogger(__name__)
PREVIEW_SALT = 'studio.course-admin.preview.v1'
RUN_PATTERN = re.compile(r'^(\d{4})_C([1-9]\d*)$', re.IGNORECASE)


def admin_required(view):
    """Check every endpoint, including reads, independently of navigation."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not (request.user.is_authenticated and request.user.is_active and GlobalStaff().has_user(request.user)):
            return JsonResponse({'error': 'Доступ разрешён только администраторам платформы.'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def parse_filters(values):
    """Accept bounded, literal filters from either a query string or JSON."""
    if not isinstance(values, dict) and not hasattr(values, 'getlist'):
        raise ValueError('Некорректные фильтры.')
    result = {}
    for field in ('q', 'org', 'run', 'year'):
        value = values.get(field, '')
        if not isinstance(value, str) or len(value) > 255:
            raise ValueError('Некорректное значение фильтра.')
        result[field] = value.strip()
    if result['year'] and not re.fullmatch(r'[1-9]\d{3}', result['year']):
        raise ValueError('Год должен состоять из четырёх цифр.')
    latest = values.get('latest', True)
    if latest not in (True, False, '1', '0', 'true', 'false', 'on', ''):
        raise ValueError('Некорректный фильтр последнего запуска.')
    result['latest'] = latest in (True, '1', 'true', 'on')
    return result


def filter_courses(courses, filters):
    """Filter arbitrary legacy runs, then select the latest matching family run."""
    selected = []
    for course in courses:
        key = course['key']
        if filters['org'] and filters['org'].casefold() != key.org.casefold():
            continue
        if filters['run'] and filters['run'].casefold() != key.run.casefold():
            continue
        if filters['year'] and not key.run.startswith(filters['year']):
            continue
        searchable = f"{key} {course['name']} {course.get('faculty') or ''}".casefold()
        if filters['q'] and filters['q'].casefold() not in searchable:
            continue
        selected.append(course)
    if filters['latest']:
        families = {}
        for course in selected:
            key = course['key']
            family = (key.org.casefold(), key.course.casefold())
            previous = families.get(family)
            if previous is None or _run_order(course) > _run_order(previous):
                families[family] = course
        selected = list(families.values())
    return sorted(selected, key=lambda course: (course['name'].casefold(), str(course['key'])))


def _run_order(course):
    """Compare C10 numerically; legacy runs fall back to their course start."""
    match = RUN_PATTERN.fullmatch(course['key'].run)
    start = course.get('start')
    year = int(match[1]) if match else (start.year if start else 0)
    sequence = int(match[2]) if match else 0
    return year, sequence, str(start or ''), course['key'].run


def _courses():
    """Use Studio's inventory, including drafts absent from the SQL overview cache."""
    overviews = {str(course.id): course for course in CourseOverview.objects.all()}
    courses = []
    for summary in modulestore().get_course_summaries():
        key = summary.id
        # Native reruns require split course keys; libraries/CCX are not courses to clone here.
        if type(key) is not CourseLocator or key.deprecated:  # pylint: disable=unidiomatic-typecheck
            continue
        overview = overviews.get(str(key))
        courses.append({
            'key': key,
            'name': summary.display_name or str(key),
            'start': getattr(overview, 'start', None),
            'end': getattr(overview, 'end', None),
            'enrollment_start': getattr(overview, 'enrollment_start', None),
            'enrollment_end': getattr(overview, 'enrollment_end', None),
            'visibility': getattr(overview, 'catalog_visibility', None),
            'faculty': getattr(overview, 'faculty', ''),
            'self_paced': getattr(overview, 'self_paced', False),
            'invitation_only': getattr(overview, 'invitation_only', False),
            'certificate_available_date': getattr(overview, 'certificate_available_date', None),
        })
    return courses


def _display_course(course, modes, generation):
    row = dict(course)
    key = row.pop('key')
    row.update(id=str(key), org=key.org, run=key.run)
    row['mode_labels'] = ', '.join(modes.get(str(key), [])) or f'{CourseMode.DEFAULT_MODE_SLUG} (по умолчанию)'
    row['certificate_generation'] = 'Включена' if generation.get(str(key)) else 'Выключена'
    row['self_paced_label'] = 'В своём темпе' if row.pop('self_paced') else 'По расписанию преподавателя'
    row['invitation_only_label'] = 'Запись только по приглашению' if row.pop('invitation_only') else 'Открытая запись'
    for field in ('start', 'end', 'enrollment_start', 'enrollment_end', 'certificate_available_date'):
        value = row[field]
        row[field] = value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M') if value else ''
    row['details_url'] = get_schedule_details_url(key) or reverse(
        'settings_handler', kwargs={'course_key_string': str(key)},
    )
    return row


def _state_result(state):
    return {
        'source_course_id': str(state.source_course_key),
        'course_id': str(state.course_key),
        'state': state.state,
        'message': ('Перезапуск завершён.' if state.state == 'succeeded' else
                    'Ошибка перезапуска. Подробности доступны в журнале CMS worker.' if state.state == 'failed' else
                    'Курс создаётся в фоновой задаче.'),
    }


@never_cache
@admin_required
@ensure_csrf_cookie
@require_GET
def course_admin_page(request):
    """Render a direct-link-only management console, without adding any menu entry."""
    try:
        filters = parse_filters(request.GET)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    courses = filter_courses(_courses(), filters)
    page = Paginator(courses, 50).get_page(request.GET.get('page', 1))
    page_keys = [course['key'] for course in page.object_list]
    modes = {}
    for mode in CourseMode.objects.filter(course_id__in=page_keys).order_by('mode_slug', 'currency'):
        modes.setdefault(str(mode.course_id), []).append(f'{mode.mode_slug} ({mode.currency})')
    generation = get_self_generation_enabled_for_courses(page_keys)
    recent = CourseRerunState.objects.find_all(created_user=request.user).order_by('-created_time')[:50]
    return render_to_response('course-admin.html', {
        'courses': [_display_course(course, modes, generation) for course in page.object_list],
        'course_filters': filters,
        'page': page.number,
        'num_pages': page.paginator.num_pages,
        'total': page.paginator.count,
        'endpoint_url': reverse('course_admin_action'),
        'status_url': reverse('course_admin_status'),
        'initial_results': [_state_result(state) for state in recent],
        'timezone_label': 'UTC (Казахстан: UTC+5)',
        'csrf_token': get_token(request),
    })


def _selected_keys(payload):
    all_matching = payload.get('all_matching', False)
    if not isinstance(all_matching, bool):
        raise ValueError('Некорректный способ выбора курсов.')
    if all_matching:
        keys = [course['key'] for course in filter_courses(_courses(), parse_filters(payload.get('filters', {})))]
    else:
        identifiers = payload.get('course_ids', [])
        if not isinstance(identifiers, list) or not all(isinstance(value, str) for value in identifiers):
            raise ValueError('Передайте список идентификаторов курсов.')
        keys = []
        for identifier in dict.fromkeys(identifiers):
            key = CourseKey.from_string(identifier)
            if (type(key) is not CourseLocator or key.deprecated or key.branch  # pylint: disable=unidiomatic-typecheck
                    or key.version_guid):
                raise ValueError('Поддерживаются только обычные курсы Studio с идентификатором course-v1.')
            keys.append(key)
    limit = getattr(settings, 'COURSE_ADMIN_MAX_BATCH_SIZE', 1000)
    if not keys or len(keys) > limit:
        raise ValueError(f'Выберите от 1 до {limit} курсов. Уточните фильтры для большой выборки.')
    return keys


@never_cache
@admin_required
@require_POST
@csrf_protect
def course_admin_action(request):
    """Validate a complete batch before dispatch; report operational failures per course."""
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValueError('Ожидается JSON-объект.')
        action = payload.get('action')
        if action not in ('preview', 'rerun', 'save'):
            raise ValueError('Неизвестное действие.')
        raw_settings = payload.get('settings', {})
        if not isinstance(raw_settings, dict):
            raise ValueError('Некорректные настройки.')
        normalized = service.validate_settings(raw_settings, require_dates=action != 'save')
        keys = _selected_keys(payload)
        if action == 'preview':
            results = service.preview_course_reruns(request.user, keys, normalized)
            targets = {row['source_course_id']: row['course_id'] for row in results if row['state'] == 'preview'}
            token = signing.dumps({
                'user_id': request.user.pk, 'course_ids': [str(key) for key in keys],
                'settings': raw_settings, 'targets': targets,
            }, salt=PREVIEW_SALT, compress=True)
            return JsonResponse({'results': results, 'preview_token': token})
        if action == 'rerun':
            token = payload.get('preview_token')
            if not isinstance(token, str):
                raise ValueError('Сначала выполните предварительный просмотр перезапуска.')
            preview = signing.loads(token, salt=PREVIEW_SALT, max_age=3600)
            if (preview['user_id'] != request.user.pk or preview['settings'] != raw_settings
                    or set(preview['course_ids']) != {str(key) for key in keys}
                    or set(preview['targets']) != {str(key) for key in keys}):
                raise ValueError('Выбор курсов или настройки изменились. Повторите предварительный просмотр.')
            results = service.queue_course_reruns(request.user, keys, normalized,
                                                  expected_course_ids=preview['targets'])
        else:
            if not normalized:
                raise ValueError('Выберите хотя бы одну настройку для сохранения.')
            results = service.save_course_settings(request.user, keys, normalized)
        LOG.info('Course admin action=%s user=%s courses=%s settings=%s results=%s', action, request.user.pk,
                 [str(key) for key in keys], normalized,
                 [(row['course_id'], row['state']) for row in results])
        return JsonResponse({'results': results})
    except (signing.BadSignature, KeyError):
        return JsonResponse({
            'error': 'Предварительный просмотр истёк или недействителен. Выполните его снова.',
        }, status=400)
    except (ValueError, TypeError, InvalidKeyError, ValidationError) as exc:
        message = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        return JsonResponse({'error': message}, status=400)


@never_cache
@admin_required
@require_GET
def course_admin_status(request):
    """Fetch durable rerun results without relying on the Celery result backend."""
    try:
        identifiers = request.GET.get('course_ids', '').split(',')
        if not 1 <= len(identifiers) <= 50:
            raise ValueError('Запрашивайте от 1 до 50 статусов за один запрос.')
        keys = [CourseKey.from_string(identifier) for identifier in identifiers]
    except (ValueError, InvalidKeyError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    states = {str(state.course_key): state for state in CourseRerunState.objects.find_all(course_key__in=keys)}
    return JsonResponse({'results': [
        _state_result(states[str(key)]) if str(key) in states else {
            'source_course_id': '', 'course_id': str(key), 'state': 'unknown',
            'message': 'Запись о перезапуске не найдена. Обновите страницу.',
        } for key in keys
    ]})
