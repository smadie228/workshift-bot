"""Regression coverage for actual Telegram callback routes and owner authorization.

Mocked update classes: no network and no production data.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import bot
import core
import inspect

class FakeQuery:
    def __init__(self,data):
        self.data=data
        self.answered=0
        self.alerts=[]
        self.edits=[]
    async def answer(self,*args,**kwargs):
        self.answered+=1
        self.alerts.append((args,kwargs))
    async def edit_message_text(self,text,**kwargs):
        self.edits.append((text,kwargs))

class InlineRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=core.connect(Path(self.tmp.name)/"test.sqlite3")
        core.put_config(self.db,"owner_id","111")
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def mk(self,uid=111,action="ui:home",chat_type="private"):
        query=FakeQuery(action)
        update=SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=uid),
            effective_chat=SimpleNamespace(type=chat_type,id=uid),
            message=None,
        )
        ctx=SimpleNamespace(application=SimpleNamespace(bot_data={"db":self.db}),user_data={},bot=None)
        return update,ctx,query
    async def test_owner_can_open_home(self):
        u,c,q=self.mk()
        await bot.inline_action(u,c)
        self.assertEqual(q.answered,1)
        self.assertEqual(len(q.edits),1)
        self.assertIn("Мои смены",q.edits[0][0])
    async def test_month_navigation(self):
        u,c,q=self.mk(action="month:2024-07")
        await bot.inline_action(u,c)
        self.assertEqual(q.answered,1)
        self.assertIn("Июль 2024",q.edits[0][0])
    async def test_unknown_user_denied(self):
        u,c,q=self.mk(uid=999)
        await bot.inline_action(u,c)
        self.assertEqual(q.answered,1)
        self.assertEqual(q.edits,[])
        self.assertTrue(q.alerts[0][1]["show_alert"])
    async def test_group_chat_denied(self):
        u,c,q=self.mk(chat_type="group")
        await bot.inline_action(u,c)
        self.assertEqual(q.edits,[])
    async def test_month_history_callback(self):
        core.corrected_shift(self.db,"2024-07-08","09:00","18:00",30)
        u,c,q=self.mk(action="hist:2024-07:0")
        await bot.inline_action(u,c)
        self.assertEqual(q.answered,1)
        self.assertIn("Июль 2024",q.edits[0][0])
        self.assertIn("08.07",q.edits[0][0])
    async def test_calendar_navigation_and_day(self):
        core.corrected_shift(self.db,"2024-07-12","09:00","18:00",30)
        u,c,q=self.mk(action="cal:2024-07")
        await bot.inline_action(u,c)
        self.assertIn("Календарь",q.edits[0][0])
        u,c,q=self.mk(action="day:2024-07-12")
        await bot.inline_action(u,c)
        self.assertIn("09:00",q.edits[0][0])
    def test_polling_subscribes_to_buttons(self):
        source=inspect.getsource(bot.main)
        self.assertIn('"callback_query"',source)
        self.assertIn('"message"',source)
