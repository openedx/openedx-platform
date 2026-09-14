"""Services for the private course administration page.

Reruns use Studio's existing cloning task. SQL stores only the reservation,
enrollment modes and certificate configuration; learner records are never copied.
"""

import json
import logging
import re
from copy import deepcopy
from datetime import datetime, timezone

from celery import current_app
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey
from opaque_keys.edx.locator import CourseLocator

from common.djangoapps.course_action_state.models import CourseRerunState
from common.djangoapps.course_modes.models import CourseMode
from lms.djangoapps.certificates.api import (
    create_course_certificate_generation_settings,
    get_course_certificate_generation_settings,
)
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
from xmodule.modulestore import EdxJSONEncoder, ModuleStoreEnum
from xmodule.modulestore.django import modulestore
from xmodule.modulestore.exceptions import ItemNotFoundError


RERUN_COURSE_TASK_NAME = 'cms.djangoapps.contentstore.tasks.rerun_course'
CMS_CELERY_QUEUE = 'edx.cms.core.default'
CMS_CELERY_EXCHANGE = 'edx.cms.core'

LOGGER = logging.getLogger(__name__)
RUN_PATTERN = re.compile(r"^(\d{4})_C([1-9]\d*)$", re.IGNORECASE)
DATE_FIELDS = frozenset(('start', 'end', 'enrollment_start', 'enrollment_end', 'certificate_available_date'))
BOOLEAN_FIELDS = frozenset(('self_paced', 'invitation_only'))
COURSE_FIELDS = DATE_FIELDS | BOOLEAN_FIELDS | {'catalog_visibility'}
WORKFLOW_BOOLEAN_FIELDS = frozenset(('shift_content_dates', 'hide_source'))
SETTINGS_FIELDS = COURSE_FIELDS | {'mode', 'certificate_mode'} | WORKFLOW_BOOLEAN_FIELDS


def _require_admin(user):
    """Keep service calls as restricted as the HTTP entry points."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        raise PermissionDenied()


def validate_settings(payload, require_dates=False):
    """Validate the small, explicit settings contract; omitted values stay unchanged.

    Date-only and timezone-less inputs use UTC. The page sends offsets explicitly.
    Empty optional dates clear their field; start and end cannot be cleared.
    """
    if not isinstance(payload, dict):
        raise ValidationError('Настройки должны быть объектом JSON.')
    unknown = set(payload) - SETTINGS_FIELDS
    if unknown:
        raise ValidationError(f'Неизвестные настройки: {", ".join(sorted(unknown))}.')
    result = dict(payload)
    for name in DATE_FIELDS & result.keys():
        value = result[name]
        if value in (None, ''):
            result[name] = None
        else:
            try:
                parsed = value if isinstance(value, datetime) else parse_datetime(value)
            except (TypeError, ValueError) as exc:
                raise ValidationError(f'Некорректная дата: {name}.') from exc
            if parsed is None:
                raise ValidationError(f'Некорректная дата: {name}.')
            result[name] = (
                parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            )
    for name in ('start', 'end'):
        if (require_dates or name in result) and result.get(name) is None:
            raise ValidationError(f'Укажите дату {name}.')
    for name in (BOOLEAN_FIELDS | WORKFLOW_BOOLEAN_FIELDS) & result.keys():
        if not isinstance(result[name], bool):
            raise ValidationError(f'{name}: требуется true или false.')
    choices = {
        'catalog_visibility': ('both', 'about', 'none'),
        'mode': ('copy', 'honor', 'audit'),
        'certificate_mode': ('copy', 'enabled', 'disabled'),
    }
    for name, allowed in choices.items():
        if name in result and result[name] not in allowed:
            raise ValidationError(f'Недопустимое значение {name}.')
    _validate_dates(result)
    return result


def _validate_dates(values):
    """Validate complete or partial course/enrollment calendars."""
    for start_name, end_name in (('start', 'end'), ('enrollment_start', 'enrollment_end')):
        start, end = values.get(start_name), values.get(end_name)
        if start and end and start >= end:
            raise ValidationError(f'{end_name} должна быть позже {start_name}.')
    if values.get('enrollment_end') and values.get('end') and values['enrollment_end'] > values['end']:
        raise ValidationError('Окончание записи не может быть позже окончания курса.')
    if values.get('enrollment_start') and values.get('end') and values['enrollment_start'] > values['end']:
        raise ValidationError('Начало записи не может быть позже окончания курса.')


def _source_course(raw_key):
    """Require a real, unversioned split-store course."""
    try:
        key = raw_key if isinstance(raw_key, CourseKey) else CourseKey.from_string(raw_key)
    except (InvalidKeyError, TypeError) as exc:
        raise ValidationError('Некорректный идентификатор курса.') from exc
    if (type(key) is not CourseLocator or key.deprecated  # pylint: disable=unidiomatic-typecheck
            or key.branch or key.version_guid):
        raise ValidationError('Поддерживаются только курсы course-v1 без версии или ветки.')
    course = modulestore().get_course(key, depth=0)
    if course is None:
        raise ValidationError('Курс не найден.')
    return course


def _known_course_keys():
    """Include Mongo courses, SQL overviews and all outstanding run reservations."""
    keys = {course.id for course in modulestore().get_course_summaries()}
    keys.update(CourseOverview.objects.values_list('id', flat=True))
    keys.update(CourseRerunState.objects.filter(action='rerun').values_list('course_key', flat=True))
    return keys


def next_course_key(source_key, year, known_keys):
    """Choose the next numeric YYYY_Cn within this organization/course/year."""
    maximum = 0
    for key in known_keys:
        if not isinstance(key, CourseKey):
            key = CourseKey.from_string(key)
        if key.org.casefold() != source_key.org.casefold() or key.course.casefold() != source_key.course.casefold():
            continue
        match = RUN_PATTERN.fullmatch(key.run or '')
        if match and int(match.group(1)) == year:
            maximum = max(maximum, int(match.group(2)))
    return CourseLocator(org=source_key.org, course=source_key.course, run=f'{year}_C{maximum + 1}')


def _generation_values(course, settings):
    """Resolve the latest certificate settings, requiring an active template to enable."""
    original = get_course_certificate_generation_settings(course.id)
    values = {
        'self_generation_enabled': original['self_generation_enabled'] if original else False,
        'language_specific_templates_enabled': original['language_specific_templates_enabled'] if original else False,
        'include_hours_of_effort': original['include_hours_of_effort'] if original else None,
    }
    policy = settings.get('certificate_mode', 'copy')
    if policy != 'copy':
        values['self_generation_enabled'] = policy == 'enabled'
    if values['self_generation_enabled']:
        certificates = (course.certificates or {}).get('certificates', [])
        if not any(certificate.get('is_active') for certificate in certificates):
            raise ValidationError('Сначала настройте и активируйте сертификат курса в Studio.')
    return values


def _settings_for_course(course, settings, rerun=False):
    """Validate resulting dates, including unchanged values on an existing course."""
    values = {name: getattr(course, name) for name in DATE_FIELDS}
    if rerun:
        values.update(enrollment_start=None, enrollment_end=None, certificate_available_date=None)
    values.update(settings)
    _validate_dates(values)
    if rerun or settings.get('certificate_mode') == 'enabled':
        _generation_values(course, settings)


def _result(source_key, course_key=None, state='failed', message='', state_id=None):
    return {
        'source_course_id': str(source_key),
        'course_id': str(course_key) if course_key else None,
        'state': state,
        'message': message,
        'state_id': state_id,
    }


def _error_message(exc):
    if isinstance(exc, ValidationError):
        return ' '.join(exc.messages)
    return 'Операция не выполнена. Подробности записаны в журнал Studio.'


def _prepare_course(raw_key, settings, seen):
    course = _source_course(raw_key)
    family = (course.id.org.casefold(), course.id.course.casefold())
    if family in seen:
        raise ValidationError('Выберите только один исходный запуск для одного курса.')
    seen.add(family)
    _settings_for_course(course, settings, rerun=True)
    return course


def preview_course_reruns(user, course_keys, settings):
    """Preflight each selection without reserving a key or starting a task."""
    _require_admin(user)
    settings = validate_settings(settings, require_dates=True)
    known, seen, results = _known_course_keys(), set(), []
    for raw_key in course_keys:
        try:
            course = _prepare_course(raw_key, settings, seen)
            destination = next_course_key(course.id, settings['start'].year, known)
            known.add(destination)
            results.append(_result(course.id, destination, 'preview', 'Готов к перезапуску.'))
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception('Course admin preflight failed for %s', raw_key)
            results.append(_result(raw_key, message=_error_message(exc)))
    return results


def _reserve_rerun(course, user, year, known, expected=None):
    """The SQL unique constraint arbitrates simultaneous requests for the next run.

Never use the existing manager's initiated()/get_or_create(): it overwrites a
competing worker's reservation. Each collision gets a separate savepoint.
"""
    if expected:
        try:
            expected = CourseKey.from_string(expected)
        except (InvalidKeyError, TypeError) as exc:
            raise ValidationError('Некорректный идентификатор из предварительного просмотра.') from exc
        match = RUN_PATTERN.fullmatch(expected.run or '')
        if (type(expected) is not CourseLocator  # pylint: disable=unidiomatic-typecheck
                or expected.branch or expected.version_guid
                or expected.org != course.id.org or expected.course != course.id.course
                or not match or int(match.group(1)) != year):
            raise ValidationError('Идентификатор запуска не соответствует выбранному курсу и году.')
        previous = CourseRerunState.objects.filter(course_key=expected, action='rerun').first()
        if previous:
            if previous.source_course_key == course.id and previous.created_user_id == user.id:
                return previous, False
            raise ValidationError('Этот запуск уже зарезервирован. Обновите предварительный просмотр.')
        if expected != next_course_key(course.id, year, known):
            raise ValidationError('Список запусков изменился. Обновите предварительный просмотр.')
    for _ in range(1000):
        destination = expected or next_course_key(course.id, year, known)
        known.add(destination)
        if modulestore().has_course(destination, ignore_case=True):
            if expected:
                raise ValidationError('Курс уже существует. Обновите предварительный просмотр.')
            continue
        try:
            with transaction.atomic():
                reservation = CourseRerunState.objects.create(
                    course_key=destination,
                    action='rerun',
                    state='in_progress',
                    source_course_key=course.id,
                    created_user=user,
                    updated_user=user,
                    display_name=course.display_name or str(course.id),
                    should_display=True,
                    message='',
                )
                return reservation, True
        except IntegrityError:
            # Another request reserved this run between the read and insert.
            if expected:
                previous = CourseRerunState.objects.filter(course_key=expected, action='rerun').first()
                if previous and previous.source_course_key == course.id and previous.created_user_id == user.id:
                    return previous, False
                raise ValidationError('Этот запуск уже зарезервирован. Обновите предварительный просмотр.') from None
            continue
    raise ValidationError('Не удалось зарезервировать запуск. Повторите запрос.')


def _send_rerun_task(source, destination, user_id, fields, settings):
    """Send the CMS-only cloning task without importing CMS Django apps in LMS."""
    current_app.send_task(
        RERUN_COURSE_TASK_NAME,
        args=(
            str(source),
            str(destination),
            user_id,
            json.dumps(fields, cls=EdxJSONEncoder),
        ),
        kwargs={
            'admin_settings': json.loads(json.dumps(settings, cls=EdxJSONEncoder)),
        },
        queue=CMS_CELERY_QUEUE,
        exchange=CMS_CELERY_EXCHANGE,
        routing_key=CMS_CELERY_QUEUE,
    )


def queue_course_reruns(user, course_keys, settings, expected_course_ids=None):
    """Reserve distinct destinations and dispatch one existing Studio task per course."""
    _require_admin(user)
    settings = validate_settings(settings, require_dates=True)
    if expected_course_ids is not None:
        if isinstance(expected_course_ids, list) and len(expected_course_ids) == len(course_keys):
            expected_course_ids = dict(zip(map(str, course_keys), expected_course_ids))
        if not isinstance(expected_course_ids, dict) or set(expected_course_ids) != set(map(str, course_keys)):
            raise ValidationError('Предварительный просмотр должен включать все выбранные курсы.')
        if not all(isinstance(value, str) and value for value in expected_course_ids.values()):
            raise ValidationError('Предварительный просмотр содержит некорректные идентификаторы.')
    known, seen, results = _known_course_keys(), set(), []
    for raw_key in course_keys:
        reservation = None
        try:
            course = _prepare_course(raw_key, settings, seen)
            reservation, created = _reserve_rerun(
                course, user, settings['start'].year, known,
                expected=expected_course_ids[str(raw_key)] if expected_course_ids is not None else None,
            )
            destination = reservation.course_key
            if not created:
                results.append(_result(
                    course.id, destination, reservation.state,
                    'Этот запрос уже принят; новый запуск не создан.', reservation.pk,
                ))
                continue
            fields = {name: value for name, value in settings.items() if name in COURSE_FIELDS}
            fields.update(
                display_name=course.display_name,
                wiki_slug=f'{destination.org}.{destination.course}.{destination.run}',
                advertised_start=None,
                video_upload_pipeline={},
            )
            for name in ('enrollment_start', 'enrollment_end', 'certificate_available_date'):
                fields.setdefault(name, None)
            if settings.get('certificate_mode') == 'enabled':
                fields['cert_html_view_enabled'] = True
            result = _result(course.id, destination, 'in_progress', 'Задача поставлена в очередь.', reservation.pk)
            # Enqueue only once the unique reservation is committed. With autocommit
            # this executes immediately; it also remains safe under ATOMIC_REQUESTS.

            def dispatch(state=reservation, source=course.id, overrides=fields, row=result):
                try:
                    _send_rerun_task(source, state.course_key, user.id, overrides, settings)
                except Exception:  # pylint: disable=broad-except
                    LOGGER.exception('Could not enqueue admin rerun for %s', state.course_key)
                    CourseRerunState.objects.failed(course_key=state.course_key)
                    row.update(state='failed', message='Не удалось отправить задачу в очередь. Проверьте Celery.')
            transaction.on_commit(dispatch)
            results.append(result)
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception('Course admin rerun failed for %s', raw_key)
            if reservation:
                CourseRerunState.objects.failed(course_key=reservation.course_key)
            results.append(_result(
                raw_key, reservation.course_key if reservation else None,
                message=_error_message(exc), state_id=reservation.pk if reservation else None,
            ))
    return results


def _ensure_mode(course_key, slug):
    """Add a free mode without removing or modifying existing paid tracks."""
    CourseMode.objects.get_or_create(
        course_id=course_key, mode_slug=slug, currency='usd',
        defaults={'mode_display_name': 'Honor' if slug == 'honor' else 'Audit', 'min_price': 0},
    )


def configure_rerun(source_key, destination_key, settings):
    """Copy only course-level SQL settings after the Studio clone has completed.

Caller wraps this and its final success state in the same SQL transaction.
The clone already carries grading, content, active certificate design and other
Studio course fields. Ecommerce identifiers belong to the source product and
must be provisioned separately for a new course run.
"""
    settings = validate_settings(settings, require_dates=True)
    source = _source_course(source_key)
    generation_values = _generation_values(source, settings)
    CourseOverview.get_from_id(destination_key)
    policy = settings.get('mode', 'copy')
    if policy == 'copy':
        modes = list(CourseMode.objects.filter(course_id=source_key))
        for mode in modes:
            values = {
                field.name: getattr(mode, field.name)
                for field in CourseMode._meta.concrete_fields
                if field.name not in ('id', 'course', 'mode_slug', 'currency')
            }
            delta = settings['start'] - source.start if source.start else None
            if delta and values['_expiration_datetime']:
                values['_expiration_datetime'] += delta
            if delta and values['expiration_date']:
                values['expiration_date'] += delta
            for name in ('sku', 'android_sku', 'ios_sku', 'bulk_sku'):
                values[name] = None
            CourseMode.objects.update_or_create(
                course_id=destination_key, mode_slug=mode.mode_slug, currency=mode.currency, defaults=values,
            )
        if not modes:
            default = CourseMode.DEFAULT_MODE
            CourseMode.objects.get_or_create(
                course_id=destination_key, mode_slug=default.slug, currency=default.currency,
                defaults={
                    'mode_display_name': default.name, 'min_price': default.min_price,
                    'suggested_prices': default.suggested_prices, 'description': default.description,
                },
            )
    else:
        _ensure_mode(destination_key, policy)
    create_course_certificate_generation_settings(destination_key, generation_values)


def hide_source_course(source_key, user_id):
    """Hide a successfully replaced source run from course discovery."""
    course = _source_course(source_key)
    if course.catalog_visibility == 'none':
        return
    previous_visibility = course.catalog_visibility
    try:
        course.catalog_visibility = 'none'
        modulestore().update_item(course, user_id)
        CourseOverview.load_from_module_store(course.id)
    except Exception:
        LOGGER.exception('Could not hide source course %s; restoring its visibility', source_key)
        restored = _source_course(source_key)
        restored.catalog_visibility = previous_visibility
        modulestore().update_item(restored, user_id)
        CourseOverview.load_from_module_store(restored.id)
        raise


def shift_rerun_content_dates(source_key, destination_key, user_id, settings):
    """Shift explicit section/component dates in each branch without publishing drafts.

Use the split store's branch-local update primitive intentionally: the normal
Studio update auto-publishes chapters/sequentials, replacing published content
with draft content. The two existing branches must remain independent here.
Advanced schedules embedded in third-party XBlock JSON are not rewritten.
"""
    if not settings.get('shift_content_dates', True):
        return
    from xmodule.modulestore.split_mongo.split import SplitMongoModuleStore  # pylint: disable=import-outside-toplevel

    settings = validate_settings(settings, require_dates=True)
    source = _source_course(source_key)
    if not source.start:
        return
    delta = settings['start'] - source.start
    if not delta:
        return
    store = modulestore()._get_modulestore_for_courselike(destination_key)  # pylint: disable=protected-access
    with store.bulk_operations(destination_key):
        published_changed = False
        for branch in (ModuleStoreEnum.BranchName.published, ModuleStoreEnum.BranchName.draft):
            try:
                blocks = store.get_items(destination_key.for_branch(branch))
            except ItemNotFoundError:
                # A draft-only source may have no published branch.
                continue
            for block in blocks:
                if block.category == 'course':
                    continue
                changed = False
                for name in ('start', 'due'):
                    field = block.fields.get(name)
                    if field and field.is_set_on(block) and getattr(block, name) is not None:
                        setattr(block, name, getattr(block, name) + delta)
                        changed = True
                if changed:
                    # Drop the snapshot version so multiple updates advance this
                    # branch's current head instead of writing detached versions.
                    block.location = block.location.version_agnostic()
                    SplitMongoModuleStore.update_item(store, block, user_id)
                    published_changed = published_changed or branch == ModuleStoreEnum.BranchName.published
        if published_changed:
            store._flag_publish_event(destination_key)  # pylint: disable=protected-access


def save_course_settings(user, course_keys, settings):
    """Apply selected fields to each existing course; preserve unselected settings."""
    _require_admin(user)
    settings = validate_settings(settings)
    results, seen = [], set()
    for raw_key in course_keys:
        previous_fields = None
        try:
            course = _source_course(raw_key)
            if course.id in seen:
                raise ValidationError('Курс указан повторно.')
            seen.add(course.id)
            _settings_for_course(course, settings)
            with transaction.atomic():
                CourseOverview.get_from_id(course.id)
                # Serialize SQL configuration updates for this existing course.
                CourseOverview.objects.select_for_update().get(id=course.id)
                course = _source_course(course.id)
                _settings_for_course(course, settings)
                if settings.get('mode', 'copy') != 'copy':
                    _ensure_mode(course.id, settings['mode'])
                generation_values = None
                if settings.get('certificate_mode', 'copy') != 'copy':
                    generation_values = _generation_values(course, settings)
                touched = set(settings) & COURSE_FIELDS
                if settings.get('certificate_mode') == 'enabled':
                    touched.add('cert_html_view_enabled')
                previous_fields = {
                    name: (course.fields[name].is_set_on(course), deepcopy(getattr(course, name)))
                    for name in touched
                }
                for name, value in settings.items():
                    if name in COURSE_FIELDS:
                        setattr(course, name, value)
                if settings.get('certificate_mode') == 'enabled':
                    course.cert_html_view_enabled = True
                modulestore().update_item(course, user.id)
                CourseOverview.load_from_module_store(course.id)
                if generation_values is not None:
                    create_course_certificate_generation_settings(course.id, generation_values)
            results.append(_result(course.id, course.id, 'saved', 'Настройки сохранены.'))
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.exception('Course admin settings failed for %s', raw_key)
            message = _error_message(exc)
            if previous_fields:
                # SQL rolled back above, but Mongo does not participate in its
                # transaction. Restore precisely the touched fields on failure.
                try:
                    with transaction.atomic():
                        CourseOverview.objects.select_for_update().get(id=course.id)
                        restored = _source_course(course.id)
                        for name, (was_set, value) in previous_fields.items():
                            if was_set:
                                setattr(restored, name, value)
                            else:
                                delattr(restored, name)
                        modulestore().update_item(restored, user.id)
                        CourseOverview.load_from_module_store(restored.id)
                except Exception:  # pylint: disable=broad-except
                    LOGGER.exception('Could not restore course settings for %s', raw_key)
                    message += ' Часть настроек могла сохраниться; проверьте курс в Studio.'
            results.append(_result(raw_key, raw_key, message=message))
    return results
