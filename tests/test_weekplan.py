"""Weekly frame and announcement lifecycle against the real isolated Canvas adapter."""
import datetime as dt
import os
import shutil
import unittest
from unittest.mock import patch
from pathlib import Path

import test_web_service as webfixtures
from cc_account import AccountService, StateConflict
from cc_store import jload, jsave
from cc_time import pin_now


@unittest.skipUnless(shutil.which("openssl") and os.name == "posix", "requires OpenSSL and POSIX service fixtures")
class WeeklyPlan(unittest.TestCase):
    setUpClass = classmethod(webfixtures.Portal.setUpClass.__func__)
    tearDownClass = classmethod(webfixtures.Portal.tearDownClass.__func__)
    setUp = webfixtures.Portal.setUp
    command = webfixtures.Portal.command
    ai_endpoint = webfixtures.Portal.ai_endpoint
    toggle_ai = webfixtures.Portal.toggle_ai
    setting = webfixtures.Portal.setting

    def state(self):
        return self.service.portal_state()

    def operate(self, item, operation, **extra):
        return self.service.portal_action({'action': 'study', 'id': item['id'],
            'version': item['version'], 'value': {'operation': operation, **extra}})

    def test_rules_metadata_and_pinned_frame_survive_refresh_and_model(self):
        self.ai_endpoint(limit=3)
        self.command('refresh')
        plan = self.state()['weekly_plan']
        self.assertEqual(7, len(plan['days']))
        self.assertTrue(any(i['id'].startswith('material:') for i in plan['entries'].values()))
        self.assertTrue(all(len(day['should']) <= 2 for day in plan['days']))
        requests = self.server.requests()
        self.assertTrue(any('/modules' in r['path'] for r in requests))
        self.assertFalse(any('/files/' in r['path'] or '/pages/' in r['path'] for r in requests))
        item = next(i for i in plan['entries'].values() if i.get('task_id') and not i['completed'])
        self.operate(item, 'schedule', date=plan['end'], first_step='先核对我的提纲')
        self.command('refresh')
        self.toggle_ai('AI 主开关')
        self.command('ai')
        updated = self.state()['weekly_plan']['entries'][item['id']]
        self.assertEqual(plan['end'], updated['date'])
        self.assertTrue(updated['pinned'])
        self.assertEqual('先核对我的提纲', updated['first_step'])
        with self.assertRaises(StateConflict):
            self.operate(item, 'schedule', date=plan['week'], first_step='陈旧页面不可覆盖')
        self.service = AccountService(self.home.archive, self.secrets_path)
        self.assertEqual(updated, self.state()['weekly_plan']['entries'][item['id']])

    def test_completion_cross_day_week_and_resubmission(self):
        self.command('refresh')
        plan = self.state()['weekly_plan']
        item = next(i for i in plan['entries'].values() if i.get('task_id') and not i['completed'] and i.get('activity') == 'work')
        self.operate(item, 'complete')
        pin_now('2026-03-25T23:00:00Z')
        active = lambda plan: [i['id'] for d in plan['days'] for i in ([d['must']] if d['must'] else []) + d['should']] + [i['id'] for i in plan['parking']]
        self.assertNotIn(item['id'], active(self.state()['weekly_plan']))
        pin_now('2026-03-30T23:00:00Z')
        self.assertEqual('2026-03-30', self.state()['weekly_plan']['week'])
        self.assertNotIn(item['id'], active(self.state()['weekly_plan']))
        task = next(a for c in self.sc.course.values() for a in c['assignments'] if str(a['id']) == item['task_id'])
        task['due_at'] = '2026-04-01T23:00:00Z'
        task['submission'] = {'workflow_state': 'unsubmitted', 'redo_request': True}
        self.command('refresh')
        self.assertIn(item['id'], active(self.state()['weekly_plan']))
        self.assertTrue(self.state()['weekly_plan']['entries'][item['id']]['needs_review'])

    def test_account_midnight_and_expired_week_pin(self):
        cfg_path = Path(self.home.archive, 'config.json')
        cfg = jload(cfg_path)
        cfg['user_tz'] = 'America/New_York'
        jsave(cfg_path, cfg)
        pin_now('2026-03-30T03:59:00Z')
        self.command('refresh')
        plan = self.state()['weekly_plan']
        self.assertEqual('2026-03-29', plan['today'])
        self.assertEqual('2026-03-23', plan['week'])
        item = next(i for i in plan['entries'].values() if i.get('task_id') and not i['completed'])
        self.operate(item, 'schedule', date=plan['end'], first_step='跨周仍保留我的步骤')
        pin_now('2026-03-30T04:01:00Z')
        next_week = self.state()['weekly_plan']
        self.assertEqual('2026-03-30', next_week['today'])
        self.assertEqual('2026-03-30', next_week['week'])
        self.assertFalse(next_week['entries'][item['id']]['pinned'])
        self.assertEqual('跨周仍保留我的步骤', next_week['entries'][item['id']]['first_step'])

    def test_read_analysis_confirmation_change_and_course_lifecycle(self):
        ann = self.sc.announcements[0]
        ann['message'] = '<p>Bring a calculator to class.</p>'
        aid = ann['context_code'].removeprefix('course_') + ':' + str(ann['id'])
        def candidates(ident, body):
            return [{'title': '准备计算器', 'first_step': '检查计算器电量', 'due_at': None,
                     'uncertainty': '具体课次待确认', 'evidence': 'Bring a calculator to class.'}] if ident == aid else []
        received = self.ai_endpoint(limit=8, action_factory=candidates)
        self.toggle_ai('AI 主开关')
        self.setting('announcement_collection', True)
        self.command('refresh')
        selected = next(a for a in self.state()['announcements'] if a['id'] == aid)
        self.service.portal_action({'action': 'announcement', 'id': aid, 'version': selected['version'], 'value': True})
        self.command('ai')
        self.assertNotIn('Bring a calculator', received[-1][2]['messages'][1]['content'])
        selected = next(a for a in self.state()['announcements'] if a['id'] == aid)
        self.service.portal_action({'action': 'ai', 'id': aid, 'version': selected['version'], 'value': 'analyze_once'})
        self.assertIn('Bring a calculator', received[-1][2]['messages'][1]['content'])
        self.assertEqual(1, received[-1][2]['messages'][1]['content'].count('- ID: '))
        plan = self.state()['weekly_plan']
        candidate = next(a for a in plan['announcement_actions'] if a['announcement_id'] == aid)
        self.assertEqual('pending', candidate['status'])
        self.assertNotIn(candidate['id'], plan['entries'])
        self.service.portal_action({'action': 'announcement_action', 'id': candidate['id'], 'version': candidate['action_version'], 'value': 'confirm'})
        self.assertIn(candidate['id'], self.state()['weekly_plan']['entries'])
        self.toggle_ai('公告正文授权')
        self.command('ai')
        self.assertNotIn('Bring a calculator', received[-1][2]['messages'][1]['content'])
        self.assertTrue(any(a['id'] == aid for a in self.state()['analysis']['announcements']))
        archived = next(a for a in self.state()['analysis']['announcements'] if a['id'] == aid)
        self.assertTrue(archived['effective_read'])
        self.assertTrue(archived['in_current_snapshot'])
        self.assertFalse(archived['analysis_stale'])
        self.assertTrue(archived['generated_at'])
        ann['message'] = '<p>Bring a calculator to class. Room changed.</p>'
        self.command('refresh')
        archived = next(a for a in self.state()['analysis']['announcements'] if a['id'] == aid)
        self.assertTrue(archived['analysis_stale'])
        self.assertFalse(archived['effective_read'])
        saved = jload(self.service.report_path)
        saved['snapshot']['announcements'] = [a for a in saved['snapshot']['announcements'] if a['id'] != aid]
        jsave(self.service.report_path, saved)
        missing = next(a for a in self.state()['analysis']['announcements'] if a['id'] == aid)
        self.assertIsNone(missing['effective_read'])
        self.assertFalse(missing['in_current_snapshot'])
        self.assertTrue(missing['analysis_stale'])
        with self.assertRaises(StateConflict):
            self.service.portal_action({'action': 'announcement_action', 'id': candidate['id'],
                                        'version': next(a for a in self.state()['weekly_plan']['announcement_actions']
                                                        if a['id'] == candidate['id'])['action_version'],
                                        'value': 'complete_reviewed'})
        self.command('refresh')
        plan = self.state()['weekly_plan']
        changed = next(a for a in plan['announcement_actions'] if a['id'] == candidate['id'])
        self.assertTrue(changed['needs_review'])
        self.assertNotIn(candidate['id'], plan['entries'])
        with self.assertRaises(StateConflict):
            self.service.portal_action({'action': 'announcement_action', 'id': candidate['id'], 'version': candidate['action_version'], 'value': 'complete'})
        self.service.portal_action({'action': 'announcement_action', 'id': changed['id'], 'version': changed['action_version'], 'value': 'confirm'})
        item = self.state()['weekly_plan']['entries'][candidate['id']]
        self.operate(item, 'complete')
        self.command('refresh')
        done = next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == candidate['id'])
        self.assertEqual('completed', done['status'])
        course = next(c for c in self.state()['courses'] if c['id'] == candidate['course_id'])
        self.service.portal_action({'action': 'course', 'id': course['id'], 'version': course['version'], 'value': False})
        self.assertFalse(any(a['id'] == candidate['id'] for a in self.state()['weekly_plan']['announcement_actions']))
        self.assertIn(candidate['id'], jload(self.service.report_path)['announcement_actions'])

    def test_reviewed_completion_from_pending_and_changed_source(self):
        ann = self.sc.announcements[0]
        ann['message'] = '<p>Check both workshop materials.</p>'
        aid = ann['context_code'].removeprefix('course_') + ':' + str(ann['id'])
        def candidates(ident, body):
            return [{'title': title, 'first_step': '核对材料', 'due_at': None,
                     'uncertainty': '时间待确认', 'evidence': 'Check both workshop materials.'}
                    for title in ('材料一', '材料二')] if ident == aid else []
        self.ai_endpoint(limit=4, action_factory=candidates)
        self.toggle_ai('AI 主开关')
        self.setting('announcement_collection', True)
        self.setting('ai_announcements', True)
        self.command('ai')
        original = self.state()
        actions = [a for a in original['weekly_plan']['announcement_actions'] if a['announcement_id'] == aid]
        self.assertEqual(2, len(actions))
        first, other = actions
        self.assertEqual('pending', first['status'])
        before_read = next(a for a in original['announcements'] if a['id'] == aid)['effective_read']
        request = {'action': 'announcement_action', 'id': first['id'],
                   'version': first['action_version'], 'value': 'complete_reviewed'}
        self.service.portal_action(request)
        completed = self.state()['weekly_plan']
        done = next(a for a in completed['announcement_actions'] if a['id'] == first['id'])
        self.assertEqual('completed', done['status'])
        self.assertEqual(first['source_version'], done['reviewed_version'])
        self.assertEqual(first['revision'] + 1, done['revision'])
        self.assertEqual('pending', next(a for a in completed['announcement_actions'] if a['id'] == other['id'])['status'])
        self.assertNotIn(first['id'], [a['id'] for day in completed['days'] for a in
                          ([day['must']] if day['must'] else []) + day['should']])
        self.assertNotIn(first['id'], [a['id'] for a in completed['parking']])
        self.assertNotIn(first['id'], [a['id'] for a in completed['top'] + completed['pending']])
        self.assertEqual(before_read, next(a for a in self.state()['announcements'] if a['id'] == aid)['effective_read'])
        with self.assertRaises(StateConflict):
            self.service.portal_action(request)
        self.service = AccountService(self.home.archive, self.secrets_path)
        persisted = next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == first['id'])
        self.assertEqual('completed', persisted['status'])
        self.service.portal_action({**request, 'version': persisted['action_version'], 'value': 'reopen'})
        reopened = next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == first['id'])
        self.assertEqual('confirmed', reopened['status'])
        ann['message'] = '<p>Check both workshop materials. New room.</p>'
        self.command('refresh')
        changed = next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == first['id'])
        self.assertTrue(changed['needs_review'])
        with self.assertRaises(StateConflict):
            self.service.portal_action({**request, 'version': reopened['action_version']})
        with self.assertRaises(ValueError):
            self.service.portal_action({**request, 'version': changed['action_version'], 'value': 'complete'})
        self.service.portal_action({**request, 'version': changed['action_version']})
        reviewed = next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == first['id'])
        self.assertEqual('completed', reviewed['status'])
        self.assertFalse(reviewed['needs_review'])
        self.assertEqual(changed['source_version'], reviewed['reviewed_version'])
        self.assertEqual('pending', next(a for a in self.state()['weekly_plan']['announcement_actions'] if a['id'] == other['id'])['status'])
        self.assertEqual(before_read, next(a for a in self.state()['announcements'] if a['id'] == aid)['effective_read'])

    def test_daily_summary_skips_unchanged_inputs_without_spending_quota(self):
        received = self.ai_endpoint(limit=5)
        self.toggle_ai('AI 主开关')
        self.toggle_ai('每日自动摘要')
        self.command('ai')
        used = self.state()['quota']['used']
        saved = jload(self.service.report_path)
        saved.setdefault('daily_plans', {})['synthetic-day'] = {'state': 'formed', 'ai_state': 'pending', 'ai_snapshot': saved['snapshot']}
        jsave(self.service.report_path, saved)
        self.service._auto_summary('synthetic-day', self.service.generation())
        self.assertEqual(1, len(received))
        self.assertEqual(used, self.state()['quota']['used'])
        self.assertEqual('done', jload(self.service.report_path)['daily_plans']['synthetic-day']['ai_state'])

    def test_old_announcement_consent_rejected_after_collection_gap(self):
        received = self.ai_endpoint(limit=3)
        self.toggle_ai('AI 主开关')
        self.setting('announcement_collection', True)
        self.command('refresh')
        selected = self.state()['announcements'][0]
        old_request = {'action': 'ai', 'id': selected['id'],
                       'version': selected['version'], 'value': 'analyze_once'}
        self.setting('announcement_collection', False)
        self.command('refresh')
        source = next(a for a in self.sc.announcements
                      if a['context_code'].removeprefix('course_') + ':' + str(a['id']) == selected['id'])
        source['message'] = '<p>NEW_PRIVATE_BODY_NEVER_PREVIEWED</p>'
        self.setting('announcement_collection', True)
        self.service = AccountService(self.home.archive, self.secrets_path)
        self.command('refresh')
        with self.assertRaises(StateConflict):
            self.service.portal_action(old_request)
        self.assertEqual([], received)
        current = next(a for a in self.state()['announcements'] if a['id'] == selected['id'])
        self.assertNotEqual(selected['version'], current['version'])
        self.service.portal_action({**old_request, 'version': current['version']})
        self.assertIn('NEW_PRIVATE_BODY_NEVER_PREVIEWED', received[0][2]['messages'][1]['content'])

    def test_course_stop_before_ai_send_revokes_pending_payload(self):
        received = self.ai_endpoint(limit=3)
        self.toggle_ai('AI 主开关')
        self.command('refresh')
        stop = self.service.course_list(1234)['actions'][0][0]['data']
        reserve = self.service._reserve_ai
        def stop_after_reservation(ctx, generation):
            result = reserve(ctx, generation)
            self.service.course_action(1234, stop)
            return result
        with patch.object(self.service, '_reserve_ai', side_effect=stop_after_reservation):
            result = self.command('ai')
        self.assertTrue(result['stale'])
        self.assertEqual([], received)
        self.assertIsNone(self.state()['analysis'])

    def test_course_stop_during_response_discards_analysis(self):
        stop = []
        received = self.ai_endpoint(limit=3, handler_hook=lambda: self.service.course_action(1234, stop[0]))
        self.toggle_ai('AI 主开关')
        self.command('refresh')
        stop.append(self.service.course_list(1234)['actions'][0][0]['data'])
        result = self.command('ai')
        self.assertEqual(1, len(received))
        self.assertTrue(result['stale'])
        self.assertIsNone(self.state()['analysis'])


if __name__ == '__main__':
    unittest.main()
