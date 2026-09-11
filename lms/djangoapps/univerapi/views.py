import jwt
import requests
from django.contrib.auth import login, authenticate, get_user_model
from django.http import HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from common.djangoapps.student.models import UserProfile

SECRET_KEY = "$ecRet@3#$2958GPIs!1"
User = get_user_model()


class UniverTestView(APIView):
    authentication_classes = []
    permission_classes = []

    @csrf_exempt
    def get(self, request):
        auth_token = request.GET.get('auth')
        if not auth_token:
            return Response({'error': 'Missing auth token'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = jwt.decode(auth_token, SECRET_KEY, algorithms=['HS256'])
            uname = decoded.get('uname')
            upwd = decoded.get('upwd')

            if not uname or not upwd:
                return Response({'error': 'Invalid token payload'}, status=status.HTTP_400_BAD_REQUEST)

            username = uname
            email_value = f"{username}@open.edu.kz"

            # Получаем данные профиля из Univer API
            surname, name, gender, stage, birth_year = decode_token_and_fetch_profile(auth_token)

            # Проверка пользователя
            user = User.objects.filter(username=username).first()

            if not user:
                # Создание нового пользователя
                user = User.objects.create(username=username, email=email_value)
                user.set_password(upwd)
                user.save()

                UserProfile.objects.create(
                    user=user,
                    name=f"{name} {surname}",
                    country='KZ',
                    gender=gender,
                    level_of_education=stage,
                    year_of_birth=birth_year,
                    mailing_address='Kaznu',
                    goals='Цель обучаться на платформе ОпенКазну'
                )
            else:
                # Обновляем пароль при изменении
                if not user.check_password(upwd):
                    user.set_password(upwd)
                    user.save()

                # Проверка и обновление профиля
                profile, created = UserProfile.objects.get_or_create(user=user)
                updated = False

                if profile.name != f"{name} {surname}":
                    profile.name = f"{name} {surname}"
                    updated = True
                if profile.gender != gender:
                    profile.gender = gender
                    updated = True
                if profile.level_of_education != stage:
                    profile.level_of_education = stage
                    updated = True
                if profile.year_of_birth != birth_year:
                    profile.year_of_birth = birth_year
                    updated = True
                if profile.country != 'KZ':
                    profile.country = 'KZ'
                    updated = True
                if profile.mailing_address != 'Kaznu':
                    profile.mailing_address = 'Kaznu'
                    updated = True

                if updated:
                    profile.save()

            # Авторизация
            auth_user = authenticate(username=username, password=upwd)
            if auth_user is None:
                return Response({'error': 'Authentication failed'}, status=status.HTTP_401_UNAUTHORIZED)

            login(request, auth_user)
            return HttpResponseRedirect('/dashboard')

        except jwt.ExpiredSignatureError:
            return Response({'error': 'Token expired'}, status=status.HTTP_401_UNAUTHORIZED)
        except jwt.InvalidTokenError:
            return Response({'error': 'Invalid token'}, status=status.HTTP_401_UNAUTHORIZED)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

def decode_token_and_fetch_profile(token, secret_key=SECRET_KEY):
    """Декодирование токена и получение профиля пользователя с Univer API"""
    try:
        decoded = jwt.decode(token, secret_key, algorithms=['HS256'])
    except jwt.InvalidSignatureError:
        raise SystemExit("Ошибка: неверная подпись JWT")
    except jwt.DecodeError as e:
        raise SystemExit(f"Ошибка при декодировании JWT: {e}")

    uname = decoded.get('uname')
    upwd = decoded.get('upwd')
    if not uname or not upwd:
        raise SystemExit("Ошибка: в токене нет 'uname' или 'upwd'")

    session = requests.Session()
    resp = session.post(
        "https://univerapi.kaznu.kz/user/loginMoodle",
        data={'login': uname, 'password': upwd},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get('code') != 0:
        raise SystemExit(f"loginMoodle вернул: {data.get('message', '')}")

    def find_value(lst, key):
        for d in lst:
            if key in d and ':' in d[key]:
                return d[key].split(':', 1)[1].strip()
        return None

    # 1. Пробуем студентский профиль
    try:
        student_resp = session.get("https://univerapi.kaznu.kz/student/profile")
        if student_resp.status_code == 200:
            student_data = student_resp.json()
            if student_data.get('code') == 0 and 'data' in student_data:
                info_list, personal_list = student_data['data']

                surname = find_value(personal_list, 'sname')
                name = find_value(personal_list, 'name')

                raw_sex = (find_value(personal_list, 'sex') or '').lower()
                if u'муж' in raw_sex:
                    gender = 'm'
                elif u'жен' in raw_sex:
                    gender = 'f'
                else:
                    gender = 'o'

                raw_stage = (find_value(info_list, 'stage') or '').lower()
                if u'бакалав' in raw_stage:
                    stage = 'b'
                elif u'магис' in raw_stage:
                    stage = 'm'
                elif u'доктор' in raw_stage:
                    stage = 'p'
                else:
                    stage = 'none'

                birth_year = 1995
                return surname, name, gender, stage, birth_year
    except:
        pass

    # 2. Пробуем профиль преподавателя
    try:
        teacher_resp = session.get("https://univerapi.kaznu.kz/teacher/profile")
        if teacher_resp.status_code == 200:
            teacher_data = teacher_resp.json()
            if teacher_data.get('code') == 0 and 'data' in teacher_data:
                teacher_profile = teacher_data['data'][0]
                surname = teacher_profile.get('sname', '')
                name = teacher_profile.get('name', '')
                gender = 'o'
                stage = 'none'
                birth_year = None
                if 'dateOfBirth' in teacher_profile:
                    try:
                        birth_year = int(teacher_profile['dateOfBirth'].split('.')[-1])
                    except (ValueError, IndexError):
                        pass
                return surname, name, gender, stage, birth_year
    except:
        pass

    
    return "Сотрудник", "Казну", "o", "none", 1995


    return surname, name, gender, stage, birth_year
