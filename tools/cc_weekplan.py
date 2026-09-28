"""Persistent weekly decisions over the original study rules; no network or model calls."""
import copy
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from cc_radar import clashes, first_step_for
from cc_study import (ADMIN_RE, FIRST, current_week, item_kind, minutes_for,
                      pick_top, schedule_days, week_items)
from cc_time import monday_of, parse_ts


def merge_analysis(saved, analysis, allowed):
    """Attach trusted source versions, retain other announcements and user decisions."""
    sources = {a['id']: a for a in allowed or []}
    records = saved.setdefault('announcement_analysis', {})
    for old in saved.get('latest_analysis', {}).get('announcements', []):
        records.setdefault(old['id'], old)
    actions = saved.setdefault('announcement_actions', {})
    for result in analysis['announcements']:
        source = sources[result['id']]
        record = {**result, 'version': source['version'], 'course_id': source['course_id'],
                  'generated_at': analysis['generated_at'], 'timezone': analysis['timezone']}
        records[result['id']] = record
        for index, candidate in enumerate(result['actions']):
            key = f"announcement:{result['id']}:{source['version']}:{index}"
            actions.setdefault(key, {**candidate, 'id': key, 'announcement_id': source['id'],
                'version': source['version'], 'reviewed_version': source['version'],
                'course_id': source['course_id'], 'course': source['course'],
                'source': source['source'], 'extracted_at': analysis['generated_at'],
                'status': 'pending', 'revision': 0})
    saved['latest_analysis'] = {**analysis, 'announcements': list(records.values())}


def action_views(saved, active):
    current = {a['id']: a for a in saved.get('snapshot', {}).get('announcements', [])}
    views = []
    for action in saved.get('announcement_actions', {}).values():
        if action['course_id'] not in active:
            continue
        source = current.get(action['announcement_id'])
        source_version = source['version'] if source else action['reviewed_version']
        views.append({**action, 'needs_review': source_version != action['reviewed_version'],
            'source_version': source_version,
            'action_version': [action['revision'], source_version]})
    return views


def reconcile(ctx, saved, active):
    """Merge local facts into this week's stable frame, returning a browser projection."""
    zone = ZoneInfo(ctx.clock.user_name)
    now = ctx.clock.now_utc()
    today = now.astimezone(zone).date()
    monday = monday_of(today)
    end = monday + dt.timedelta(days=6)
    week_key = monday.isoformat()
    weeks = saved.setdefault('study_weeks', {})
    week = weeks.setdefault(week_key, {'entries': {}, 'revision': 0, 'created_at': now.isoformat()})
    decisions = saved.setdefault('study_decisions', {})
    snapshot = saved.get('snapshot', {})
    # Original CLI rules use course_date. The website deliberately uses account time.
    clock = SimpleNamespace(course_date=lambda value: value.astimezone(zone).date(),
                            now_utc=ctx.clock.now_utc, week_no=ctx.clock.week_no)
    rules_ctx = SimpleNamespace(cfg=ctx.cfg, clock=clock)
    courses = {str(c['id']): {'code': c['code'], 'name': c.get('name'),
        'weekday': c.get('weekday'), 'before_class': [], 'todo': [], 'deadline_related': []}
        for c in ctx.cfg.get('courses', []) if str(c['id']) in active}
    all_items, rows, gaps = {}, [], []
    completed = saved.get('completed_tasks', {})
    reminders = saved.get('reminders', {})
    facts = snapshot.get('assignments', {})
    def add(item, bucket):
        all_items[item['id']] = item
        if not item['completed'] and not item.get('stopped'):
            courses[item['course_id']][bucket].append(item)
    for aid, fact in facts.items():
        cid = str(fact.get('course_id'))
        if cid not in courses:
            continue
        due = parse_ts(fact.get('due_at'))
        local_date = due.astimezone(zone).date() if due else None
        done = fact.get('sub_state') in ('submitted', 'excused') or bool(fact.get('excused')) or (
            aid in completed and not completed[aid].get('needs_review'))
        key = 'task:' + aid
        is_quiz = bool(fact.get('is_quiz'))
        row = {'course': fact['course'], 'item': fact.get('name') or aid,
               'url': fact.get('html_url'), 'submission_types': fact.get('submission_types', []),
               'kind': 'exam' if is_quiz else 'assignment', 't': due,
               'overdue': bool(due and due <= now), 'undated': due is None}
        item = {'id': key, 'origin_id': key, 'task_id': aid, 'course_id': cid,
            'course': fact['course'], 'title': row['item'], 'url': row['url'],
            'source_label': 'Canvas 作业', 'kind': 'Overdue' if row['overdue'] else 'Deadline',
            'first_step': first_step_for(row), 'due_at': fact.get('due_at'),
            'when': due.astimezone(zone).strftime('%Y-%m-%d %H:%M') if due else '日期待确认',
            'days_left': (local_date - today).days if local_date else None,
            'weight': fact.get('weight') or '—', 'weight_source': '作业标题' if fact.get('weight') not in (None, '—') else None,
            'exam': is_quiz, 'minutes': None, 'minutes_src': None, 'verb': '准备',
            'completed': done, 'stopped': aid in reminders and not completed.get(aid, {}).get('needs_review'),
            'needs_review': bool(completed.get(aid, {}).get('needs_review')),
            'fact_version': [fact.get('due_at'), fact.get('sub_state'), fact.get('name')]}
        add(item, 'deadline_related')
        if due and not done and not item['stopped'] and today <= local_date <= end + dt.timedelta(days=2):
            rows.append(row)
    metadata = snapshot.get('study_metadata', {})
    modules = {c['code']: metadata.get(cid, {}).get('modules', []) for cid, c in courses.items()}
    term_week, week_source = current_week(rules_ctx, today, modules)
    for cid, course in courses.items():
        meta = metadata.get(cid, {})
        if not meta or not meta.get('complete'):
            gaps.append({'course': course['code'], 'text': '模块元数据未更新，课件安排可能不完整',
                         'collected_at': meta.get('collected_at'), 'source': f"{ctx.cfg['canvas_host']}/courses/{cid}/modules"})
        selected, method = week_items(rules_ctx, course['code'], meta.get('modules', []), term_week, monday)
        if method == 'none':
            gaps.append({'course': course['code'], 'text': '无法从模块确认本周课件；请核对课程模块',
                         'source': f"{ctx.cfg['canvas_host']}/courses/{cid}/modules"})
        for module, source in selected:
            kind = item_kind(source)
            if kind in ('Assignment', 'Quiz', 'Discussion') or ADMIN_RE.search(source.get('title') or ''):
                continue
            details = source.get('content_details') or {}
            if details.get('locked_for_user') or module.get('state') == 'locked':
                continue
            unlock = parse_ts(details.get('unlock_at') or module.get('unlock_at'))
            if unlock and unlock > now:
                continue
            key = f"material:{cid}:{source['id']}"
            item = {'id': key, 'origin_id': key, 'course_id': cid, 'course': course['code'],
                'title': source.get('title') or '未命名模块条目', 'kind': kind,
                'url': source.get('html_url') or f"{ctx.cfg['canvas_host']}/courses/{cid}/modules/items/{source['id']}",
                'source_label': '模块：' + (module.get('name') or ''), 'first_step': FIRST[kind],
                'minutes': minutes_for(rules_ctx, kind, source), 'minutes_src': '估算',
                'due_at': None, 'verb': '学习', 'completed': decisions.get(key, {}).get('completed', False),
                'fact_version': [source.get('title'), module.get('id')]}
            add(item, 'before_class')
    actions = action_views(saved, active)
    for action in actions:
        if action['status'] not in ('confirmed', 'completed') or action['needs_review']:
            continue
        due = parse_ts(action.get('due_at'))
        local_date = due.astimezone(zone).date() if due else None
        item = {'id': action['id'], 'origin_id': action['id'], 'course_id': action['course_id'],
            'course': action['course'], 'title': action['title'], 'url': action['source'],
            'source_label': '公告行动（本人已确认）', 'first_step': action['first_step'],
            'due_at': action.get('due_at'), 'when': due.astimezone(zone).strftime('%Y-%m-%d %H:%M') if due else '日期待确认',
            'days_left': (local_date - today).days if local_date else None,
            'kind': 'Overdue' if due and due <= now else 'Deadline' if due else 'Other',
            'weight': '—', 'minutes': None, 'minutes_src': None, 'verb': '处理',
            'completed': action['status'] == 'completed', 'uncertainty': action['uncertainty'],
            'fact_version': [action['version'], action['reviewed_version'], action.get('due_at')]}
        add(item, 'deadline_related' if due else 'todo')
    course_values = list(courses.values())
    remaining = copy.deepcopy(course_values)
    priorities = []
    for _ in range(3):
        top = pick_top(remaining)
        if not top:
            break
        priorities.append(top['id'])
        for course in remaining:
            for bucket in ('deadline_related', 'before_class', 'todo'):
                course[bucket] = [i for i in course[bucket] if i['id'] != top['id']]
    scheduled = schedule_days(rules_ctx, today, monday, course_values, rows, structured=True)
    proposals = {}
    for day in scheduled['days']:
        for entry in day['entries']:
            proposals[entry['id']] = {**entry, 'date': day['date']}
    for item in scheduled['parking']:
        proposals.setdefault(item['id'], {**item, 'date': None, 'slot': 'should', 'activity': item.get('activity', 'work')})
    # Undated facts remain visible for confirmation, not silently assigned a deadline.
    pending = [item for item in all_items.values() if not item.get('due_at')
               and item['kind'] == 'Deadline' and not item['completed'] and not item.get('stopped')]
    before = copy.deepcopy(week['entries'])
    for key, old in before.items():
        base = all_items.get(old.get('origin_id', key))
        decision = decisions.get(key, {})
        if base and not base.get('stopped') and decision.get('pinned') and week_key <= (decision.get('date') or '') <= end.isoformat():
            proposals.setdefault(key, {**old, **base, 'id': key, 'origin_id': old.get('origin_id', key)})
    entries = {}
    for key, proposal in proposals.items():
        origin = proposal.get('origin_id', key)
        base = all_items.get(origin)
        if not base or base.get('stopped'):
            continue
        decision = decisions.get(key, {})
        if decision.get('completed') and proposal.get('activity') == 'review':
            proposal['completed'] = True
        old = before.get(key, {})
        same_fact = old.get('fact_version') == base.get('fact_version')
        date = proposal.get('date')
        if old and same_fact and old.get('date') and old['date'] >= today.isoformat():
            date = old['date']
        pinned = bool(decision.get('pinned') and week_key <= (decision.get('date') or '') <= end.isoformat())
        if pinned:
            date = decision['date']
        step = decision.get('first_step', proposal.get('first_step'))
        needs_review = bool(base.get('needs_review') or (old and old.get('needs_review')) or (pinned and old and not same_fact))
        entry = {**base, **proposal, 'origin_id': origin, 'date': date, 'first_step': step,
                 'pinned': pinned, 'completed': bool(base['completed'] or proposal.get('completed')),
                 'fact_version': base.get('fact_version'), 'needs_review': needs_review}
        if pinned and old and not same_fact:
            for k in ('title', 'when', 'due_at', 'days_left', 'weight', 'exam', 'uncertainty'):
                if k in base:
                    entry[k] = base[k]
        entries[key] = entry
    # Retain checked items in their original frame instead of recreating them tomorrow.
    for key, old in before.items():
        origin = old.get('origin_id', key)
        base = all_items.get(origin)
        decision = decisions.get(key, {})
        if base and (base['completed'] or decision.get('completed')):
            entries.setdefault(key, {**old, **base, 'id': key, 'origin_id': origin, 'completed': True})
    if entries != before or week.get('timezone') != ctx.clock.user_name:
        week['revision'] += 1
    week.update(entries=entries, timezone=ctx.clock.user_name)
    # Bound obsolete frames, while durable decisions and announcement evidence survive.
    saved['study_weeks'] = {k: v for k, v in weeks.items() if k >= (monday - dt.timedelta(weeks=8)).isoformat()}
    days = [{'date': (monday + dt.timedelta(days=i)).isoformat(), 'must': None, 'should': [], 'completed': []} for i in range(7)]
    by_date = {day['date']: day for day in days}
    parking, overload = [], []
    for entry in sorted(entries.values(), key=lambda e: (not e['pinned'], e.get('slot') != 'must', e.get('date') or '9999', e['id'])):
        view = {**entry, 'version': [week_key, week['revision']]}
        date = view.get('date')
        day = by_date.get(date)
        if view['completed']:
            if day:
                day['completed'].append(view)
            continue
        if not day:
            parking.append(view)
        elif day['must'] is None:
            day['must'] = view
        elif len(day['should']) < 2:
            day['should'].append(view)
        else:
            parking.append(view)
            overload.append(date)
    public_entries = {key: {**entry, 'version': [week_key, week['revision']]} for key, entry in entries.items()}
    return {'week': week_key, 'end': end.isoformat(), 'today': today.isoformat(), 'timezone': ctx.clock.user_name,
        'term_week': term_week, 'week_source': week_source, 'collected_at': saved.get('collected_at'),
        'revision': week['revision'], 'days': days, 'top': [public_entries.get(key, all_items[key]) for key in priorities],
        'pending': pending, 'parking': parking, 'gaps': gaps, 'entries': public_entries,
        'clashes': [[{'title': row['item'], 'course': row['course'], 'due_at': row['t'].isoformat(), 'source': row['url']} for row in group] for group in clashes(rows)],
        'overloaded_dates': sorted(set(overload)), 'announcement_actions': actions}
