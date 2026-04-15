# Notification Digest Email: Cron → Celery Migration

---

## Overview

This change replaces the cron-job-driven digest email system with a self-scheduling Celery task approach. Instead of a periodic job that sweeps all users at a fixed interval, digest emails are now scheduled automatically — per user, at the exact configured delivery time — the moment a qualifying notification is created.

---

## Before: Cron-based Batch Delivery

### How it worked

```
[External cron schedule]
        │
        ▼
python manage.py send_email_digest daily
        │
        ▼
send_digest_email_to_all_users.delay(cadence_type)   ← single shared_task
        │
        ▼
get_audience_for_cadence_email(cadence_type)          ← fetch ALL eligible users
        │
        ▼
for user in users:                                    ← serial loop
    send_digest_email_to_user(user, ...)              ← send email
    notifications.update(email_sent_on=datetime.now())
```

### Key characteristics

| Aspect | Detail |
|---|---|
| **Trigger** | External cron job (e.g. Kubernetes CronJob, Celery Beat) |
| **Entry point** | `send_email_digest` management command |
| **Task** | `send_digest_email_to_all_users` — one task processes all users |
| **Delivery time control** | Set entirely by when the cron job fires |
| **Deduplication** | None — running the command twice would send duplicate emails |
| **Failure handling** | No retries; a single task failure could silently skip users |
| **Scalability** | All users processed serially in one task |
| **Timezone handling** | `datetime.datetime.now()` (naive) + manual `utc.localize()` |

### Files involved (before)

- `notifications/management/commands/send_email_digest.py` — command entry point
- `notifications/email/tasks.py` — `send_digest_email_to_all_users`
- `notifications/email/utils.py` — `get_start_end_date` (using naive datetime)

---

## After: Celery Delayed Task Scheduling

### How it works

```
send_notifications(user_ids, ...)                     ← called when a notification is created
        │
        ├── [immediate cadence users] ──────────────► send_immediate_cadence_email(...)
        │
        └── [daily/weekly cadence users]
                │
                ▼
        schedule_bulk_digest_emails({user_id: cadence_type, ...})
                │
                ├── get_next_digest_delivery_time(cadence_type)   ← compute ETA
                ├── SELECT existing DigestSchedule records         ← 1 query, skip already-scheduled
                ├── bulk_create new DigestSchedule records         ← 1 query
                ├── Notification.objects.update(email_scheduled=True) ← 1 query
                │
                └── on transaction.commit():
                        send_user_digest_email_task.apply_async(
                            kwargs={user_id, cadence_type},
                            eta=delivery_time,
                            task_id=<dedupe_key>,       ← Celery-level dedup
                        )

────────────── At configured delivery time ──────────────

send_user_digest_email_task(user_id, cadence_type)
        │
        ├── _claim_digest_schedule(...)      ← atomic DB delete (prevents double-send)
        │       └── returns False if row already gone → skip
        │
        ├── check: was digest already sent in this window?  ← cron co-existence guard
        │
        ├── send_digest_email_to_user(user, start_date, end_date, ...)
        │       └── Notification.filter(email_sent_on__isnull=True)  ← skip already-sent rows
        │
        └── Notification.update(email_scheduled=False)      ← clean up flags
```

### Key characteristics

| Aspect | Detail |
|---|---|
| **Trigger** | Automatically when `send_notifications()` creates a qualifying notification |
| **Entry point** | `schedule_bulk_digest_emails()` inside `tasks.send_notifications` |
| **Task** | `send_user_digest_email_task` — one task per user, ETA-scheduled |
| **Delivery time control** | Settings: `NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR/MINUTE`, `NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY/HOUR/MINUTE` |
| **Deduplication** | Three layers: `DigestSchedule` DB record, Celery task ID, `_claim_digest_schedule` atomic delete |
| **Failure handling** | Auto-retry up to 3×, exponential backoff (5 min → 10 min → 20 min) |
| **Scalability** | ~3 DB queries per cadence group regardless of user count; tasks run in parallel |
| **Timezone handling** | `django.utils.timezone.now()` (timezone-aware) throughout |



## New Settings (`openedx/envs/common.py`)

```python
NOTIFICATION_DAILY_DIGEST_DELIVERY_HOUR   = 17   # 5 PM UTC
NOTIFICATION_DAILY_DIGEST_DELIVERY_MINUTE = 0

NOTIFICATION_WEEKLY_DIGEST_DELIVERY_DAY   = 0    # Monday (0=Mon … 6=Sun)
NOTIFICATION_WEEKLY_DIGEST_DELIVERY_HOUR  = 17   # 5 PM UTC
NOTIFICATION_WEEKLY_DIGEST_DELIVERY_MINUTE = 0
```

Override these in your deployment settings to change when digests are delivered.

---

## Deprecated: `send_email_digest` Management Command

The management command still exists but is now a no-op with a deprecation warning. **Remove any cron jobs that call it.**

```
WARNING: This command is deprecated. Digest emails are now scheduled
automatically. Please remove cron jobs using this command.
```

---
