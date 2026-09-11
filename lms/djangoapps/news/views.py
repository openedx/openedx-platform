from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from common.djangoapps.edxmako.shortcuts import render_to_response
from .models import News
from .forms import NewsForm
from django.contrib.auth.decorators import user_passes_test
from common.djangoapps.student.models import CourseEnrollment, UserProfile
from lms.djangoapps.certificates.data import CertificateStatuses
from lms.djangoapps.certificates.models import GeneratedCertificate
from lms.djangoapps.grades.models import PersistentCourseGrade
from opaque_keys import InvalidKeyError
from opaque_keys.edx.keys import CourseKey

import json
from django.utils import timezone
from collections import Counter
from openedx.core.djangoapps.content.course_overviews.models import CourseOverview
# from ..commerce.api.v1.models import Course
import logging
import jwt
import urllib.parse
import random
from datetime import datetime, timedelta

from django.http import HttpResponseRedirect

from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings


from django.db.models.functions import ExtractYear, TruncMonth
from django.db.models import Avg, Count, Exists, Min, OuterRef, Q

logger = logging.getLogger(__name__)

def news_list(request):
    news = News.objects.all()
    context = {
        'news_list': news,
        'create_url': reverse('news_create'),
    }
    return render_to_response('news/list.html', context, request=request)

def news_detail(request, news_id):  # используем news_id
    news = get_object_or_404(News, pk=news_id)  # все равно используем pk для поиска
    context = {
        'news': news,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/detail.html', context, request=request)


@user_passes_test(lambda u: u.is_staff)
def news_create(request):
    if request.method == 'POST':
        form = NewsForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            return redirect('news_list')
    else:
        form = NewsForm()

    context = {
        'form': form,
        'list_url': reverse('news_list'),
    }
    return render_to_response('news/form.html', context, request=request)

#
from django.utils.translation import get_language, gettext as _

FACULTY_TRANSLATIONS = {
    "Биология и биотехнология": {
        "kk": "Биология және биотехнология",
        "en": "Biology and Biotechnology",
    },
    "Востоковедение": {
        "kk": "Шығыстану",
        "en": "Oriental Studies",
    },
    "Высшая школа экономики и бизнеса": {
        "kk": "Экономика және бизнес жоғары мектебі",
        "en": "Higher School of Economics and Business",
    },
    "Довузовское образование": {
        "kk": "Жоғары оқу орнына дейінгі білім беру",
        "en": "Pre-university Education",
    },
    "Журналистика": {
        "kk": "Журналистика",
        "en": "Journalism",
    },
    "Офис академических и цифровых инноваций": {
        "kk": "Академиялық және цифрлық инновациялар кеңсесі",
        "en": "Office of Academic and Digital Innovations",
    },
    "Информационные технологии и искусственный интеллект": {
        "kk": "Ақпараттық технологиялар және жасанды интеллект",
        "en": "Information Technology and Artificial Intelligence",
    },
    "История": {
        "kk": "Тарих",
        "en": "History",
    },
    "Кластер инжиниринга и наукоемких технологий": {
        "kk": "Инжиниринг және жоғары технологиялар кластері",
        "en": "Cluster of Engineering and High Technologies",
    },
    "Механико-математический": {
        "kk": "Механика-математика",
        "en": "Mechanics and Mathematics",
    },
    "Физико-технический": {
        "kk": "Физика-техникалық",
        "en": "Physics and Technology",
    },
    "Филологический": {
        "kk": "Филология",
        "en": "Philology",
    },
    "Философии и политологии": {
        "kk": "Философия және саясаттану",
        "en": "Philosophy and Political Science",
    },
    "Химии и химической технологии": {
        "kk": "Химия және химиялық технологиялар",
        "en": "Chemistry and Chemical Technology",
    },
    "Юридический": {
        "kk": "Заң",
        "en": "Law",
    },
    "Международных отношений": {
        "kk": "Халықаралық қатынастар",
        "en": "International Relations",
    },
    "Медицины и здравоохранения": {
        "kk": "Медицина және денсаулық сақтау",
        "en": "Medicine and Healthcare",
    },
}

DIRECTION_TRANSLATIONS = {
    "Социальные науки, журналистика и информация": {
        "kk": "Әлеуметтік ғылымдар, журналистика және ақпарат",
        "en": "Social sciences, Journalism and Information",
    },
    "Естественные науки, математика и статистика": {
        "kk": "Жаратылыстану ғылымдары, математика және статистика",
        "en": "Natural Sciences, Mathematics and Statistics",
    },
    "Искусство и гуманитарные науки": {
        "kk": "Өнер және гуманитарлық ғылымдар",
        "en": "Arts and Humanities",
    },
    "Бизнес, управление и право": {
        "kk": "Бизнес, басқару және құқық",
        "en": "Business, Management and Law",
    },
    "Педагогические науки": {
        "kk": "Педагогикалық ғылымдар",
        "en": "Pedagogical sciences",
    },
    "Информационно-коммуникационные технологии": {
        "kk": "Ақпараттық-коммуникациялық технологиялар",
        "en": "Information and communication technologies",
    },
    "Инженерные, обрабатывающие и строительные отрасли": {
        "kk": "Инженерлік, өңдеу және құрылыс салалары",
        "en": "Engineering, manufacturing and construction branches",
    },
    "Здравоохранение": {
        "kk": "Денсаулық сақтау",
        "en": "Healthcare",
    },
}


def normalize_language_code():
    language = get_language() or "ru"
    return language.split("-")[0].split("_")[0]


def translate_faculty(value):
    if not value:
        return ""

    language = normalize_language_code()

    if language == "ru":
        return value

    return FACULTY_TRANSLATIONS.get(value, {}).get(language, value)


def translate_direction(value):
    if not value:
        return ""

    language = normalize_language_code()

    if language == "ru":
        return value

    return DIRECTION_TRANSLATIONS.get(value, {}).get(language, value)
#
@user_passes_test(lambda u: u.is_staff)
def analyze(request):
    course_org_filter = ["Test_kaznu", "rty", "123", "Demo"]
    course_name_filter = ["AI Tools in Action: Boosting Productivity with Modern Workflows"]

    now = timezone.now()
    today = now.date()
    current_year = today.year

    year_start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    next_year_start = year_start.replace(year=current_year + 1)
    all_courses_qs = (
        CourseOverview.objects
        .exclude(org__in=course_org_filter)
        .exclude(display_name__in=course_name_filter)
    )
    # Count each nonempty title once, even across different course codes and runs.
    unique_course_names = {
        name.strip()
        for name in all_courses_qs.values_list("display_name", flat=True)
        if name and name.strip()
    }

    # Include every run starting this year, regardless of its end date or title.
    current_year_courses = list(all_courses_qs.filter(
        start__gte=year_start,
        start__lt=next_year_start,
    ))
    max_year = current_year + 1

    courses_by_year_qs = (
        all_courses_qs
        .exclude(start__isnull=True)
        .filter(start__year__lte=max_year)
        .annotate(year=ExtractYear("start"))
        .values("year")
        .annotate(total=Count("id"))
        .order_by("year")
    )
    courses_by_year = [
        {"year": row["year"], "total": row["total"]}
        for row in courses_by_year_qs
    ]

    #
    faculty_counter = Counter(
        course.faculty for course in current_year_courses
        if course.faculty
    )

    directions_counter = Counter(
        course.directions for course in current_year_courses
        if course.directions
    )

    language_counter = Counter(
        course.language for course in current_year_courses
        if course.language
    )

    courses_by_faculty = [
        {"faculty": faculty, "total": total}
        for faculty, total in faculty_counter.most_common(12)
    ]

    courses_by_directions = [
        {"directions": directions, "total": total}
        for directions, total in directions_counter.most_common(12)
    ]

    courses_by_lang = [
        {"language": language, "total": total}
        for language, total in language_counter.most_common()
    ]

    top_courses = sorted(
        current_year_courses,
        key=lambda course: course.start,
        reverse=True
    )

    # Group runs across years by course identity, not by their editable titles.
    course_run_counts = Counter(
        (course_key.org, course_key.course)
        for course_key in all_courses_qs.values_list("id", flat=True)
    )

    courses_json = [
        {
            "id": str(course.id),
            "display_name": course.display_name or str(course.id),
            "faculty": translate_faculty(course.faculty),
            "directions": translate_direction(course.directions),
            "language": course.language or "",
            "start": course.start.strftime("%d.%m.%Y") if course.start else "",
            "run_count": course_run_counts.get((course.id.org, course.id.course), 1),
            "url": reverse("course_details", kwargs={"course_id": str(course.id)}),
        }
        for course in top_courses
    ]
    context = {
        "courses_count": len(unique_course_names),
        "current_year": current_year,
        "current_year_courses_count": len(current_year_courses),
        "faculty_count": len(faculty_counter),
        "directions_count": len(directions_counter),
        "generated_at": timezone.localtime().strftime("%d.%m.%Y %H:%M"),

        "language_summary": [
            {"label": row["language"], "total": row["total"]}
            for row in courses_by_lang
        ],

        "faculty_labels": json.dumps(
            [translate_faculty(row["faculty"]) for row in courses_by_faculty],
            ensure_ascii=False
        ),
        "faculty_data": json.dumps([row["total"] for row in courses_by_faculty]),

        "directions_labels": json.dumps(
            [translate_direction(row["directions"]) for row in courses_by_directions],
            ensure_ascii=False
        ),
        "directions_data": json.dumps([row["total"] for row in courses_by_directions]),

        "year_labels": json.dumps([row["year"] for row in courses_by_year]),
        "year_data": json.dumps([row["total"] for row in courses_by_year]),

        "courses_json": json.dumps(courses_json, ensure_ascii=False),
    }

    return render_to_response("news/analyze.html", context, request=request)


@user_passes_test(lambda u: u.is_staff)
def course_details(request, course_id):
    """Aggregate selected runs of the same course, deduplicating learner totals."""
    try:
        course_key = CourseKey.from_string(course_id)
    except InvalidKeyError as exc:
        raise Http404("Invalid course ID") from exc

    course = get_object_or_404(CourseOverview, id=course_key)
    # The run component and editable display name are not part of course identity.
    available_runs = [
        run for run in CourseOverview.objects.filter(org=course_key.org).order_by("-start", "id")
        if (run.id.org, run.id.course) == (course_key.org, course_key.course)
    ]
    available_ids = {str(run.id) for run in available_runs}
    if request.GET.get("scope") == "all":
        selected_ids = available_ids
    elif "selection" in request.GET or "run" in request.GET:
        selected_ids = set(request.GET.getlist("run"))
        if not selected_ids or not selected_ids.issubset(available_ids):
            return HttpResponseBadRequest(_("Select at least one valid run of this course."))
    else:
        selected_ids = {str(course_key)}
    selected_runs = [run for run in available_runs if str(run.id) in selected_ids]
    selected_keys = [run.id for run in selected_runs]
    # A single selection displays that run's metadata and course link.
    if len(selected_runs) == 1:
        course = selected_runs[0]
    now = timezone.now()
    enrollments = CourseEnrollment.objects.filter(course_id__in=selected_keys).order_by()
    enrollment_stats = enrollments.aggregate(
        total=Count("user_id", distinct=True),
        enrollment_records=Count("id"),
        first_enrollment=Min("created"),
        active=Count("user_id", filter=Q(is_active=True), distinct=True),
        recent=Count("user_id", filter=Q(created__gte=now - timedelta(days=30), created__lte=now), distinct=True),
    )
    # Use the same enrollment population for outcomes and their denominators.
    # Match BOTH the learner and run, not just enrollment in any selected run.
    matching_enrollment = CourseEnrollment.objects.filter(
        course_id=OuterRef("course_id"), user_id=OuterRef("user_id"),
    )
    certificate_stats = GeneratedCertificate.objects.filter(
        Exists(matching_enrollment),
        course_id__in=selected_keys,
        status=CertificateStatuses.downloadable,
    ).aggregate(recipients=Count("user_id", distinct=True), issued=Count("id"))
    certificate_count = certificate_stats["recipients"]
    grade_stats = PersistentCourseGrade.objects.filter(
        Exists(matching_enrollment),
        course_id__in=selected_keys,
    ).aggregate(
        recorded=Count("user_id", distinct=True),
        passed=Count("user_id", filter=Q(passed_timestamp__isnull=False), distinct=True),
        average=Avg("percent_grade"),
    )
    first_enrollment = enrollment_stats.pop("first_enrollment")
    total = enrollment_stats["total"]
    stats = {
        **enrollment_stats,
        "inactive": total - enrollment_stats["active"],
        "certificates": certificate_count,
        "issued_certificates": certificate_stats["issued"],
        "certificate_rate": round(certificate_count * 100 / total, 1) if total else 0,
        "grades_recorded": grade_stats["recorded"],
        "passed": grade_stats["passed"],
        "average_grade": round(grade_stats["average"] * 100, 1) if grade_stats["average"] is not None else None,
    }

    # Include the complete enrollment history, with empty calendar months filled in.
    current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month = min(first_enrollment or now, now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    months = []
    while month <= current_month:
        months.append(month)
        month = (month + timedelta(days=32)).replace(day=1)
    monthly_counts = {
        (row["month"].year, row["month"].month): row["total"]
        for row in enrollments.filter(created__gte=months[0], created__lte=now)
        .annotate(month=TruncMonth("created", tzinfo=now.tzinfo))
        .values("month")
        .annotate(total=Count("id"))
        .order_by("month")
    }
    peak = max(monthly_counts.values(), default=0)
    enrollment_history = []
    for month in months:
        count = monthly_counts.get((month.year, month.month), 0)
        enrollment_history.append({
            "label": month.strftime("%m.%Y"),
            "total": count,
            "height": round(count * 100 / peak, 1) if peak else 0,
        })

    context = {
        "course": course,
        "available_runs": available_runs,
        "selected_ids": selected_ids,
        "selected_runs": selected_runs,
        "selection_url": reverse("course_details", kwargs={"course_id": str(course_key)}),
        "stats": stats,
        "faculty": translate_faculty(course.faculty),
        "directions": translate_direction(course.directions),
        "enrollment_history": enrollment_history,
        "history_total": sum(row["total"] for row in enrollment_history),
        "analysis_url": reverse("analyze"),
        "course_url": reverse("about_course", kwargs={"course_id": str(course.id)}),
        "generated_at": timezone.localtime(now).strftime("%d.%m.%Y %H:%M"),
    }
    return render_to_response("news/course_details.html", context, request=request)



PROCTORING_URL = "https://farabi-proctoring.kaznu.kz/integration/simple/kaznu_open/start/"

def go_to_exam(request):
    SECRET_KEY = str(settings.PROCTORING_API_KEY)
    # ✅ 1. Берем данные из frontend
    user_id = request.user.id
    username = request.user.username
    unit_url = request.GET.get("unit_url", "/")
    course_name = request.GET.get("course_name", "empty")
    section_name = request.GET.get("section_name", "empty")


    # можно взять реальные данные если нужно
    try:
        name = request.user.profile.name.strip()
        parts = name.split(maxsplit=1)

        firstname = parts[0] if len(parts) > 0 else ""
        lastname = parts[1] if len(parts) > 1 else ""
    except:
        firstname, lastname = "None", "None"

    exam_id = random.randint(10**7, 10**8 - 1)
    session_id = random.randint(10**10, 10**11 - 1)
    exam_name = f"Экзамен по {course_name}"
    request.session["proctoring_session_id"] = session_id
    logger.info(f"my-log: exam start {session_id}")


    # 2. Время
    now = datetime.utcnow()
    start_iso = now.isoformat() + "Z"
    end_iso = (now + timedelta(hours=2)).isoformat() + "Z"

    # ✅ 3. Payload с динамическим возвратом
    payload = {
        "userId": user_id,
        "lastName": lastname,
        "firstName": firstname,
        "thirdName": username,
        "language": "ru",
        "accountName": "kaznu_open",
        "examId": exam_id,
        "examName": exam_name,
        "duration": 30,
        "schedule": False,
        "proctoring": "online",
        "examDesc": f"<b>Курс:</b> {course_name}<br><b>Модуль:</b> {section_name}<br><br>Ссылка на задание</b> {unit_url}",
        "rules": {
            "websites": True,
            "look_away": True,
            "move_away": False,
            "voices": True,
        },
        "startDate": start_iso,
        "endDate": end_iso,
        "sessionId": session_id,

        # 🔥 ВАЖНО: возвращаем туда откуда пришли
        "sessionUrl": unit_url,
        "redirectUrl": unit_url,
    }

    # 4. JWT
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")

    # 5. URL
    encoded_token = urllib.parse.quote(token)
    final_url = f"{PROCTORING_URL}?token={encoded_token}"

    logger.info(f"my-log: request get {request.GET}")

    return HttpResponseRedirect(final_url)


def finish_exam(request):
    session_id = request.session.get("proctoring_session_id")
    logger.info(f"my-log: exam finished {session_id}")

    redirect_url = request.GET.get("redirectUrl", "/")
    logger.info(f"my-log: exam finished {redirect_url}")

    url = f"https://farabi-proctoring.kaznu.kz/integration/simple/kaznu_open/finish/{session_id}/?redirectUrl={redirect_url}"

    return HttpResponseRedirect(url)
