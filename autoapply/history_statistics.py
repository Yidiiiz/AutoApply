"""Statistics derived exclusively from canonical application records; times are UTC."""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from statistics import mean, median

from .models import now


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError, AttributeError):
        return None


def calculate_statistics(records, reference=None):
    from .archive import STATES
    reference = reference or datetime.now(timezone.utc)
    counts = dict.fromkeys(STATES, 0)
    ats, companies, locations, sources = {}, Counter(), Counter(), Counter()
    security, providers = Counter(), Counter()
    daily = defaultdict(lambda: dict(discovered=0, started=0, submitted=0, manual_required=0, failed=0))
    durations, filling_durations, manual_durations = [], [], []
    discovered, started, submitted, unresolved = [], [], [], []
    attempts = manual = failed_attempts = 0
    for record in records:
        state = record['application_state']
        counts[state] = counts.get(state, 0) + 1
        provider = record.get('ats_type')
        if provider in {None, '', 'UNKNOWN'}:
            provider = record.get('ats') or 'UNKNOWN'
        group = ats.setdefault(provider, dict(total=0))
        group['total'] += 1
        group[state.lower()] = group.get(state.lower(), 0) + 1
        companies[record.get('company') or 'UNKNOWN'] += 1
        locations[record.get('location') or 'UNKNOWN'] += 1
        source_names = {s.get('source_name', 'UNKNOWN') for s in record.get('sources', []) if isinstance(s, dict)}
        sources.update(source_names or {record.get('source') or 'UNKNOWN'})
        history = [h for h in record.get('status_history', []) if isinstance(h, dict)]
        previous_security, previous_manual = ('NONE', 'UNKNOWN'), False
        security_count = 0
        entered_manual = None
        for event in history:
            current = event.get('security_state', 'NONE'), event.get('security_provider') or 'UNKNOWN'
            if current[0] != 'NONE' and current != previous_security:
                security[current[0]] += 1
                providers[current[1]] += 1
                security_count += 1
            previous_security = current
            is_manual = bool(event.get('manual_action_required')) or event.get('status') == 'MANUAL_REQUIRED'
            when = timestamp(event.get('timestamp'))
            if is_manual and not previous_manual:
                manual += 1
                entered_manual = when
                if when:
                    daily[when.date().isoformat()]['manual_required'] += 1
            elif not is_manual and previous_manual and when and entered_manual and when >= entered_manual:
                manual_durations.append((when - entered_manual).total_seconds())
                entered_manual = None
            previous_manual = is_manual
        if security_count == 0 and record.get('security_state', 'NONE') != 'NONE':
            security[record['security_state']] += 1
            providers[record.get('security_provider') or 'UNKNOWN'] += 1
        if not any(h.get('status') == 'MANUAL_REQUIRED' or h.get('manual_action_required') for h in history) and record.get('manual_action_required'):
            manual += 1
        submit_events = {h.get('timestamp') for h in history if h.get('status') == 'SUBMITTING'}
        attempts += max(len(submit_events), int(bool(record.get('submit_intent_at'))), int(state == 'SUBMITTED'))
        if state == 'FAILED' and (submit_events or record.get('submit_intent_at')):
            failed_attempts += 1
        discovery = timestamp(record.get('discovered_at'))
        start = timestamp(record.get('started_at'))
        submission = timestamp(record.get('submitted_at')) if state == 'SUBMITTED' else None
        for value, collection, key in [(discovery, discovered, 'discovered'), (start, started, 'started'), (submission, submitted, 'submitted')]:
            if value:
                collection.append((value, record['application_id']))
                daily[value.date().isoformat()][key] += 1
        if state not in {'SUBMITTED', 'CLOSED', 'INVALID', 'INELIGIBLE', 'DUPLICATE', 'ALREADY_APPLIED'} and (discovery or start):
            unresolved.append((discovery or start, record['application_id']))
        if submission and discovery and submission >= discovery:
            durations.append((submission - discovery).total_seconds())
        filling = [timestamp(h.get('timestamp')) for h in history if h.get('status') == 'FILLING']
        filling = [t for t in filling if t]
        if submission and filling and submission >= min(filling):
            filling_durations.append((submission - min(filling)).total_seconds())
        if state == 'FAILED':
            failure = next((timestamp(h.get('timestamp')) for h in reversed(history) if h.get('status') == 'FAILED'), None)
            if failure:
                daily[failure.date().isoformat()]['failed'] += 1
    total = len(records)
    ratio = lambda count, denominator=total: count / denominator if denominator else 0.0
    completed = counts['SUBMITTED'] + counts['FAILED']
    result = dict(generated_at=now(), timezone='UTC', total_applications=total, status_counts=counts,
        submission_rate=ratio(counts['SUBMITTED']), failure_rate=ratio(counts['FAILED']),
        manual_intervention_rate=ratio(counts['MANUAL_REQUIRED']), unknown_rate=ratio(counts['UNKNOWN']),
        total_submission_attempts=attempts, total_confirmed_submissions=counts['SUBMITTED'],
        total_failed_submissions=failed_attempts, total_manual_interventions=manual,
        completed_submission_attempts=completed, completed_attempt_success_rate=ratio(counts['SUBMITTED'], completed),
        by_ats=ats, security_events=dict(total=sum(security.values()), by_type=dict(security)),
        security_providers=dict(providers), applications_by_company=dict(companies),
        applications_by_location=dict(locations), applications_by_source=dict(sources))
    for prefix, field in [('successful_submission', 'submission_rate'), ('manual_intervention', 'manual_intervention_rate'), ('failure', 'failure_rate'), ('unknown', 'unknown_rate')]:
        result[prefix + '_percentage'] = result[field] * 100
    result['activity'] = {}
    today = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    for name, boundary in [('today', today), ('this_week', today - timedelta(days=today.weekday())), ('this_month', today.replace(day=1))]:
        result['activity'][name] = {key: sum(boundary <= t <= reference for t, _ in entries)
            for key, entries in [('discovered', discovered), ('started', started), ('submitted', submitted)]}
        for key in ('started', 'submitted'):
            result[f'applications_{key}_{name}'] = result['activity'][name][key]
    cutoff = (today - timedelta(days=365)).date().isoformat()
    result['daily'] = {key: daily[key] for key in sorted(daily) if cutoff <= key <= today.date().isoformat()}
    result['daily_retention_days'] = 366
    def endpoint(entries, newest=False):
        if not entries:
            return None
        time, ident = (max if newest else min)(entries)
        return dict(application_id=ident, timestamp=time.isoformat())
    result.update(average_time_discovered_to_submitted=mean(durations) if durations else None,
        median_time_discovered_to_submitted=median(durations) if durations else None,
        average_time_filling_to_submitted=mean(filling_durations) if filling_durations else None,
        average_time_in_manual_required=mean(manual_durations) if manual_durations else None,
        duration_unit='seconds', oldest_unresolved_application=endpoint(unresolved),
        most_recent_application=endpoint(discovered, True), most_recent_submission=endpoint(submitted, True))
    return result
