"""Backup recovery and retention tests with temporary databases only."""
import tempfile
import unittest
from pathlib import Path
import os
import core
import backup

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.directory=Path(self.temporary.name)
        self.dbpath=self.directory/"main.sqlite3"
        self.db=core.connect(self.dbpath)
    def tearDown(self):
        self.db.close()
        self.temporary.cleanup()

    def test_snapshot_integrity_and_permissions(self):
        core.corrected_shift(self.db,"2024-07-08","09:00","18:00",30)
        folder=self.directory/"backups"
        first=backup.create_backup(self.dbpath,folder)
        self.assertTrue(first.exists())
        self.assertEqual(backup.verify_backup(first),1)
        if os.name!="nt":
            self.assertEqual(first.stat().st_mode & 0o777,0o600)
            self.assertEqual(folder.stat().st_mode & 0o777,0o700)
        core.corrected_shift(self.db,"2024-07-09","09:00","18:00",30)
        self.assertEqual(backup.verify_backup(first),1)
        second=backup.create_backup(self.dbpath,folder)
        self.assertEqual(backup.verify_backup(second),2)

    def test_backup_rotation(self):
        core.corrected_shift(self.db,"2024-07-10","09:00","18:00",30)
        folder=self.directory/"backups"
        for _ in range(17):
            backup.create_backup(self.dbpath,folder)
        files=list(folder.glob("*.sqlite3"))
        self.assertEqual(len(files),backup.KEEP)
        self.assertTrue(all(backup.verify_backup(f)==1 for f in files))
        self.assertFalse(list(folder.glob("*.partial")))
