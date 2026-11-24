"""Async guided user-flow regression tests with mocked Telegram; zero external traffic."""
import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import core
import finance
import edit_wizard
import finance_wizard

class FakeBot:
    def __init__(self):self.sent=[]
    async def send_message(self,**kwargs):self.sent.append(kwargs)

class FakeMessage:
    def __init__(self,text):self.text=text;self.sent=[]
    async def reply_text(self,text,**kwargs):self.sent.append((text,kwargs))

class FakeUpdate:
    def __init__(self,callback_data=None,text=None):
        self.callback_query=SimpleNamespace(data=callback_data) if callback_data is not None else None
        self.message=FakeMessage(text) if text is not None else None
        self.effective_chat=SimpleNamespace(id=1234)

class FakeContext:
    def __init__(self):
        self.bot=FakeBot()
        self.user_data={}

class WizardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=core.connect(Path(self.temp.name)/"test.sqlite3")
        self.context=FakeContext()
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def test_correction_wizard_requires_confirmation(self):
        c=self.context
        await edit_wizard.callback(FakeUpdate("edit:new:yesterday"),c,self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],0)
        await edit_wizard.text_input(FakeUpdate(text="16:08"),c)
        await edit_wizard.text_input(FakeUpdate(text="02:10"),c)
        await edit_wizard.callback(FakeUpdate("edit:mins:30"),c,self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],0)
        result=await edit_wizard.callback(FakeUpdate("edit:save:"+c.user_data["correction"]["_confirmation_token"]),c,self.db)
        self.assertEqual(result,"saved")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],1)
        self.assertIsNone(c.user_data.get("correction"))

    async def test_payment_wizard_requires_confirmation(self):
        c=self.context
        await finance_wizard.callback(FakeUpdate("fin:new:payment"),c,self.db)
        await finance_wizard.text(FakeUpdate(text="2026-10"),c)
        await finance_wizard.callback(FakeUpdate("fin:period:1"),c,self.db)
        await finance_wizard.text(FakeUpdate(text="22.10"),c)
        await finance_wizard.text(FakeUpdate(text="1999.95"),c)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM received_payments").fetchone()[0],0)
        await finance_wizard.callback(FakeUpdate("fin:save:"+c.user_data["finance_flow"]["_confirmation_token"]),c,self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM received_payments").fetchone()[0],1)
        self.assertAlmostEqual(finance.payment_totals(self.db,"2026-10")[1],1999.95)

    async def test_rate_wizard_preserves_historical_pay(self):
        c=self.context
        core.corrected_shift(self.db,"2026-10-05","16:00","02:00",30)
        past=core.totals(self.db,"2026-10")["earnings"]
        await finance_wizard.callback(FakeUpdate("fin:new:rate"),c,self.db)
        await finance_wizard.text(FakeUpdate(text="350"),c)
        self.assertEqual(len(finance.planned_rates(self.db)),0)
        await finance_wizard.callback(FakeUpdate("fin:save:"+c.user_data["finance_flow"]["_confirmation_token"]),c,self.db)
        self.assertEqual(len(finance.planned_rates(self.db)),1)
        self.assertEqual(core.totals(self.db,"2026-10")["earnings"],past)

    async def test_cancel_does_not_write(self):
        c=self.context
        await edit_wizard.callback(FakeUpdate("edit:new:yesterday"),c,self.db)
        await edit_wizard.callback(FakeUpdate("edit:cancel"),c,self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM shifts").fetchone()[0],0)
        await finance_wizard.callback(FakeUpdate("fin:new:payment"),c,self.db)
        await finance_wizard.callback(FakeUpdate("fin:cancel"),c,self.db)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM received_payments").fetchone()[0],0)
