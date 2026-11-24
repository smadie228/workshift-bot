"""Security regression coverage for XLSX export escaping."""
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook,load_workbook
from report_catalog import addrow

class SecurityTests(unittest.TestCase):
    def test_untrusted_cell_never_becomes_excel_formula(self):
        values=['=HYPERLINK("https://example.invalid","click")',
                '+SUM(A1:A5)','@SUM(A1:A5)','-CMD','   =1+1','regular text',500]
        book=Workbook()
        ws=book.active
        addrow(ws,7,values)
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/"safe.xlsx"
            book.save(file)
            reopened=load_workbook(file)
            for col in range(1,6):
                cell=reopened.active.cell(7,col)
                self.assertEqual(cell.data_type,"s")
                self.assertTrue(cell.value.startswith("'"))
            self.assertEqual(reopened.active.cell(7,6).value,"regular text")
            self.assertEqual(reopened.active.cell(7,7).value,500)
            reopened.close()
