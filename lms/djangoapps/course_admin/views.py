"""Plain HTML course-run administration for global LMS staff."""

import logging
import re
from datetime import timezone
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpResponseForbidden, HttpResponseRedirect
from django.middleware.csrf import get_token
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from cms.djangoapps.contentstore import course_admin as service
from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.course_modes.models import CourseMode
from common.djangoapps.edxmako.shortcuts import render_to_response
from common.djangoapps.student.roles import GlobalStaff
from lms.djangoapps.certificates.api import get_self_generation_enabled_for_courses
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview


LOG = logging.getLogger(__name__)
RUN_PATTERN = re.compile(r'^(\d{4})_C([1-9]\d*)$', re.IGNORECASE)
DATE_FIELDS = ('start', 'end', 'enrollment_start', 'enrollment_end', 'certificate_available_date')
FORM_VALUE_FIELDS = DATE_FIELDS + (
    'catalog_visibility', 'mode', 'certificate_mode', 'self_paced', 'invitation_only',
)
FORM_CHECKBOX_FIELDS = (
    'clear_enrollment_start', 'clear_enrollment_end', 'clear_certificate_available_date',
    'shift_content_dates', 'hide_source',
)
FORM_SESSION_KEY = 'course_admin.form.v1'
FEEDBACK_SESSION_KEY = 'course_admin.feedback.v1'
FORM_DEFAULTS = {'mode': 'copy', 'certificate_mode': 'copy', 'shift_content_dates': '1'}


def admin_required(view):
    """Allow only active global platform staff."""
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        allowed = (
            request.user.is_authenticated
            and request.user.is_active
            and GlobalStaff().has_user(request.user)
        )
        if not allowed:
            return HttpResponseForbidden('Доступ разрешён только администраторам платформы.')
        return view(request, *args, **kwargs)
    return wrapped


def parse_filters(values):
    """Read the small set of supported list filters from GET or POST data."""
    result = {}
    for field in ('q', 'org', 'run', 'year'):
        value = values.get(field, '')
        if not isinstance(value, str) or len(value) > 255:
            raise ValueError('Некорректное значение фильтра.')
        result[field] = value.strip()
    if result['year'] and not re.fullmatch(r'[1-9]\d{3}', result['year']):
        raise ValueError('Год должен состоять из четырёх цифр.')
    latest = values.get('latest', '1')
    if latest not in ('0', '1', 'true', 'false', 'on', ''):
        raise ValueError('Некорректный фильтр последнего запуска.')
    result['latest'] = latest in ('1', 'true', 'on')
    return result


def _courses():
    """Return the LMS SQL course inventory used by the existing reports."""
    courses = []
    for overview in CourseOverview.objects.all():
        key = overview.id
        if type(key) is not CourseLocator or key.deprecated:  # pylint: disable=unidiomatic-typecheck
            continue
        courses.append({
            'key': key,
            'name': overview.display_name or str(key),
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


def _run_order(course):
    match = RUN_PATTERN.fullmatch(course['key'].run)
    start = course.get('start')
    year = int(match[1]) if match else (start.year if start else 0)
    sequence = int(match[2]) if match else 0
    return year, sequence, str(start or ''), course['key'].run


def filter_courses(courses, filters):
    """Apply literal filters and optionally keep the latest run per course family."""
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
    return sorted(selected, key=lambda row: (row['name'].casefold(), str(row['key'])))


def _format_date(value):
    if not value:
        return ''
    return value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')


def _display_courses(courses):
    keys = [course['key'] for course in courses]
    modes = {}
    for mode in CourseMode.objects.filter(course_id__in=keys).order_by('mode_slug', 'currency'):
        modes.setdefault(str(mode.course_id), []).append(mode.mode_slug)
    generation = get_self_generation_enabled_for_courses(keys)
    rows = []
    for course in courses:
        key = course['key']
        rows.append({
            **course,
            'id': str(key),
            'org': key.org,
            'run': key.run,
            'about_url': reverse('about_course', kwargs={'course_id': str(key)}),
            'mode_labels': ', '.join(modes.get(str(key), [])) or CourseMode.DEFAULT_MODE_SLUG,
            'certificate_generation': 'Включена' if generation.get(str(key)) else 'Выключена',
            'start': _format_date(course['start']),
            'end': _format_date(course['end']),
            'enrollment_start': _format_date(course['enrollment_start']),
            'enrollment_end': _format_date(course['enrollment_end']),
            'certificate_available_date': _format_date(course['certificate_available_date']),
        })
    return rows


def _selected_keys(request, filters, all_courses):
    if request.POST.get('all_matching') == '1':
        keys = [course['key'] for course in filter_courses(all_courses, filters)]
    else:
        identifiers = list(dict.fromkeys(request.POST.getlist('course_ids')))
        keys = []
        for identifier in identifiers:
            key = CourseKey.from_string(identifier)
            if (
                type(key) is not CourseLocator  # pylint: disable=unidiomatic-typecheck
                or key.deprecated or key.branch or key.version_guid
            ):
                raise ValueError('Поддерживаются только обычные курсы с идентификатором course-v1.')
            keys.append(key)
    limit = getattr(settings, 'COURSE_ADMIN_MAX_BATCH_SIZE', 1000)
    if not keys or len(keys) > limit:
        raise ValueError(f'Выберите от 1 до {limit} курсов.')
    return keys


def _settings_from_form(request, rerun):
    values = {}
    for field in DATE_FIELDS:
        raw_value = request.POST.get(field, '').strip()
        if request.POST.get(f'clear_{field}') == '1':
            values[field] = None
        elif raw_value:
            values[field] = raw_value
    for field in ('catalog_visibility', 'mode', 'certificate_mode'):
        value = request.POST.get(field, '').strip()
        if value:
            values[field] = value
    for field in ('self_paced', 'invitation_only'):
        value = request.POST.get(field, '')
        if value in ('true', 'false'):
            values[field] = value == 'true'
        elif value:
            raise ValueError(f'{field}: некорректное значение.')
    if rerun:
        values['shift_content_dates'] = request.POST.get('shift_content_dates') == '1'
        values['hide_source'] = request.POST.get('hide_source') == '1'
    return service.validate_settings(values, require_dates=rerun)


def _form_values(request):
    """Keep an administrator's settings across redirects and later page loads."""
    values = dict(FORM_DEFAULTS)
    saved = request.session.get(FORM_SESSION_KEY, {})
    if isinstance(saved, dict):
        values.update({key: value for key, value in saved.items() if isinstance(value, str)})
    if request.method == 'POST':
        for field in FORM_VALUE_FIELDS:
            value = request.POST.get(field, '')
            values[field] = value[:255] if isinstance(value, str) else ''
        for field in FORM_CHECKBOX_FIELDS:
            values[field] = '1' if request.POST.get(field) == '1' else ''
        request.session[FORM_SESSION_KEY] = values
    return values


def _redirect_after_post(filters, page):
    query = {key: value for key, value in filters.items() if value not in ('', None, False)}
    query['latest'] = '1' if filters.get('latest') else '0'
    if page:
        query['page'] = page
    return HttpResponseRedirect(f"{reverse('course_admin')}?{urlencode(query)}")


def _state_result(state):
    messages = {
        'succeeded': 'Перезапуск завершён.',
        'failed': 'Ошибка перезапуска. Проверьте журнал фонового обработчика.',
        'in_progress': 'Курс создаётся в фоновой задаче.',
    }
    return {
        'source_course_id': str(state.source_course_key),
        'course_id': str(state.course_key),
        'state': state.state,
        'message': messages.get(state.state, state.message or state.state),
    }


@never_cache
@admin_required
@csrf_protect
@require_http_methods(['GET', 'POST'])
def course_admin(request):
    """Render and process the complete admin form on one LMS URL."""
    source = request.POST if request.method == 'POST' else request.GET
    form_values = _form_values(request)
    feedback = request.session.pop(FEEDBACK_SESSION_KEY, {}) if request.method == 'GET' else {}
    results = feedback.get('results', []) if isinstance(feedback, dict) else []
    error = feedback.get('error', '') if isinstance(feedback, dict) else ''
    try:
        filters = parse_filters(source)
    except ValueError as exc:
        filters = parse_filters({})
        error = error or str(exc)

    all_courses = _courses()
    if request.method == 'POST':
        if not error:
            try:
                action = request.POST.get('action')
                if action not in ('rerun', 'save'):
                    raise ValueError('Неизвестное действие.')
                keys = _selected_keys(request, filters, all_courses)
                normalized = _settings_from_form(request, rerun=action == 'rerun')
                if action == 'rerun':
                    results = service.queue_course_reruns(request.user, keys, normalized)
                else:
                    if not normalized:
                        raise ValueError('Заполните хотя бы одну настройку для сохранения.')
                    results = service.save_course_settings(request.user, keys, normalized)
                LOG.info(
                    'LMS course admin action=%s user=%s courses=%s results=%s',
                    action, request.user.pk, [str(key) for key in keys],
                    [(row['course_id'], row['state']) for row in results],
                )
            except (ValueError, TypeError, InvalidKeyError, ValidationError) as exc:
                error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            except Exception:  # pylint: disable=broad-except
                LOG.exception('Unexpected LMS course admin failure')
                error = 'Операция не выполнена. Подробности записаны в журнал LMS.'
        request.session[FEEDBACK_SESSION_KEY] = {'results': results, 'error': error}
        return _redirect_after_post(filters, request.POST.get('page'))

    courses = filter_courses(all_courses, filters)
    page = Paginator(courses, 50).get_page(request.GET.get('page', 1))
    recent = CourseRerunState.objects.find_all(created_user=request.user).order_by('-created_time')[:30]
    return render_to_response('course-admin.html', {
        'courses': _display_courses(page.object_list),
        'course_filters': filters,
        'page': page.number,
        'num_pages': page.paginator.num_pages,
        'total': page.paginator.count,
        'results': results,
        'recent_results': [_state_result(state) for state in recent],
        'form_values': form_values,
        'error': error,
        'csrf_token': get_token(request),
    }, request=request)
