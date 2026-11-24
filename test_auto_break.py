"""Automatic break on new shifts: isolated SQLite, no production credentials."""
import json
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
import core
import actual_shift
import miniapp_actions
import bot
import friendly_ui
import workflows

class AutoBreakTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=core.connect(Path(self.tmp.name)/'test.sqlite3')
        self.time=datetime(2024,7,20,18,0,tzinfo=core.TZ)
        self.now=patch('core.now',return_value=self.time)
        self.now.start()
    def tearDown(self):
        self.now.stop()
        self.db.close()
        self.tmp.cleanup()
    def _open(self,minutes=30):
        core.put_config(self.db,'default_break_minutes',minutes)
        mode,shift=core.toggle(self.db,1001,self.time)
        self.assertEqual(mode,'in')
        self.assertEqual(shift['break_minutes'],minutes)
        self.assertEqual(json.loads(shift['note'])['break_policy'],'automatic_default')
        self.assertFalse(self.db.execute('SELECT 1 FROM break_sessions WHERE shift_id=?',(shift['id'],)).fetchone())
        return shift['id']
    def test_normal_duration_deducts_default_once(self):
        sid=self._open()
        mode,shift=core.toggle(self.db,1002,self.time+timedelta(hours=8))
        self.assertEqual(mode,'out')
        self.assertEqual(shift['break_minutes'],30)
        self.assertAlmostEqual(core.paid_hours(shift),7.5)
        self.assertFalse(self.db.execute("SELECT 1 FROM events WHERE kind='automatic_break_skipped'").fetchone())
    def test_short_shift_never_loses_all_work_to_unfitting_default(self):
        sid=self._open()
        _,shift=core.toggle(self.db,1002,self.time+timedelta(minutes=20))
        self.assertEqual(shift['break_minutes'],0)
        self.assertAlmostEqual(core.paid_hours(shift),20/60)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='automatic_break_skipped'").fetchone()[0],1)
    def test_short_manual_time_from_miniapp_is_safe(self):
        before=self.time-timedelta(minutes=20)
        payload={'action':'clock_in_at','nonce':'a'*32,'shift_id':None,'mode':'chosen',
                 'at':before.strftime('%Y-%m-%dT%H:%M'),'expected_revision':actual_shift.attendance_revision(self.db)}
        result=miniapp_actions.execute_action(self.db,77,payload)
        shift=self.db.execute('SELECT * FROM shifts WHERE id=?',(result['id'],)).fetchone()
        self.assertEqual(shift['break_minutes'],30)
        out={'action':'clock_out_at','nonce':'b'*32,'shift_id':shift['id'],'mode':'now',
             'at':None,'expected_revision':actual_shift.shift_revision(self.db,shift['id'])}
        self.now.stop()
        self.now=patch('core.now',return_value=self.time)
        self.now.start()
        miniapp_actions.execute_action(self.db,77,out)
        saved=self.db.execute('SELECT * FROM shifts WHERE id=?',(shift['id'],)).fetchone()
        self.assertEqual(saved['break_minutes'],0)
        self.assertAlmostEqual(core.paid_hours(saved),20/60)
    def test_tracked_break_is_preserved(self):
        sid=self._open()
        workflows.start_break(self.db,at=self.time+timedelta(minutes=5))
        workflows.stop_break(self.db,at=self.time+timedelta(minutes=15))
        _,shift=core.toggle(self.db,1002,self.time+timedelta(minutes=20))
        self.assertEqual(shift['break_minutes'],10)
        self.assertAlmostEqual(core.paid_hours(shift),10/60)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='automatic_break_skipped'").fetchone()[0],0)
    def test_bot_keyboard_has_no_new_break_start(self):
        self._open()
        labels=[button.text for row in bot.kb(self.db).keyboard for button in row]
        self.assertNotIn(bot.BTN_BREAK_START,labels)
        self.assertNotIn(bot.BTN_BREAK_END,labels)
        text,inline=friendly_ui.home(self.db)
        callbacks=[b.callback_data for row in inline.inline_keyboard for b in row]
        self.assertNotIn('pause:start',callbacks)
        self.assertIn('За часы',text)
    def test_old_running_break_has_recovery_control(self):
        self._open()
        workflows.start_break(self.db,at=self.time+timedelta(minutes=1))
        labels=[button.text for row in bot.kb(self.db).keyboard for button in row]
        self.assertIn(bot.BTN_BREAK_END,labels)
        _,inline=friendly_ui.home(self.db)
        callbacks=[b.callback_data for row in inline.inline_keyboard for b in row]
        self.assertIn('pause:end',callbacks)
    def test_later_manual_break_correction_is_still_possible(self):
        sid=self._open()
        _,shift=core.toggle(self.db,1002,self.time+timedelta(hours=8))
        self.db.execute('UPDATE shifts SET break_minutes=? WHERE id=?',(45,sid))
        corrected=self.db.execute('SELECT * FROM shifts WHERE id=?',(sid,)).fetchone()
        self.assertAlmostEqual(core.paid_hours(corrected),7.25)

if __name__=='__main__':
    unittest.main()
