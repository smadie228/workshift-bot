"""Simulated full Telegram dialog flows for break buttons, plan and adjustments."""
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import core, bot, workflows, extra_wizard

class FakeQuery:
    def __init__(self,data):
        self.data=data;self.edits=[];self.answers=[]
    async def answer(self,*a,**kw):self.answers.append((a,kw))
    async def edit_message_text(self,text,**kw):self.edits.append(text)

class FakeBot:
    def __init__(self):self.sent=[]
    async def send_message(self,**kw):self.sent.append(kw)

class FakeMessage:
    def __init__(self,text):self.text=text;self.sent=[]
    async def reply_text(self,text,**kwargs):self.sent.append(text)

class FlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory()
        self.db=core.connect(Path(self.tmp.name)/"test.sqlite3")
        core.put_config(self.db,"owner_id","77")
        self.ctx=SimpleNamespace(
           application=SimpleNamespace(bot_data={"db":self.db}),
           user_data={},bot=FakeBot())
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def upd(self,cmd):
        q=FakeQuery(cmd)
        u=SimpleNamespace(callback_query=q,
            effective_user=SimpleNamespace(id=77),
            effective_chat=SimpleNamespace(id=77,type="private"),message=None)
        return u,q
    def msg(self,text):
        m=FakeMessage(text)
        u=SimpleNamespace(message=m,
            effective_user=SimpleNamespace(id=77),
            effective_chat=SimpleNamespace(id=77,type="private"),
            callback_query=None)
        return u,m

    async def test_break_buttons_follow_actual_time(self):
        base=datetime.now(timezone.utc)-timedelta(minutes=40)
        core.toggle(self.db,1001,base)
        u,q=self.upd("pause:start")
        await bot.inline_action(u,self.ctx)
        self.assertEqual(len(q.edits),1)
        self.assertIn("Перерыв",q.edits[0])
        self.assertTrue(workflows.active_break(self.db))
        self.assertEqual(core.open_shift(self.db)["break_minutes"],0)
        u,q=self.upd("pause:end")
        await bot.inline_action(u,self.ctx)
        self.assertEqual(len(q.edits),1)
        self.assertFalse(workflows.active_break(self.db))
        self.assertTrue(workflows.break_status(self.db)["tracked"])

    async def test_plan_requires_approval(self):
        future=(datetime.now(core.TZ).date()+timedelta(days=1)).isoformat()
        u,q=self.upd(f"extra:plan:quick:{future}")
        await bot.inline_action(u,self.ctx)
        self.assertEqual(workflows.plan_day(self.db,future),None)
        u,q=self.upd("extra:save")
        await bot.inline_action(u,self.ctx)
        self.assertEqual(workflows.plan_day(self.db,future)["status"],"shift")
        self.assertEqual(workflows.plan_day(self.db,future)["start"],"16:00")

    async def test_cancel_plan_without_side_effect(self):
        future=(datetime.now(core.TZ).date()+timedelta(days=1)).isoformat()
        u,q=self.upd(f"extra:plan:off:{future}")
        await bot.inline_action(u,self.ctx)
        u,q=self.upd("extra:cancel")
        await bot.inline_action(u,self.ctx)
        self.assertIsNone(workflows.plan_day(self.db,future))

    async def test_adjustment_requires_date_and_approval(self):
        u,q=self.upd("extra:adjust:bonus")
        await bot.inline_action(u,self.ctx)
        u,m=self.msg("2500.50")
        await extra_wizard.text(u,self.ctx)
        u,m=self.msg("сегодня")
        await extra_wizard.text(u,self.ctx)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM pay_adjustments").fetchone()[0],0)
        u,q=self.upd("extra:save")
        await bot.inline_action(u,self.ctx)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM pay_adjustments").fetchone()[0],1)

    async def test_workflow_rejects_unknown_user(self):
        u,q=self.upd("extra:adjust:bonus")
        u.effective_user.id=9999
        await bot.inline_action(u,self.ctx)
        self.assertEqual(len(self.ctx.bot.sent),0)
        self.assertEqual(self.ctx.user_data,{})
        self.assertTrue(q.answers[0][1].get("show_alert"))
