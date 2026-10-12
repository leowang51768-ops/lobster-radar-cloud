import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from collect import open_db
from merge_evidence import merge

class MergeTests(unittest.TestCase):
    def test_union_newer_evidence_and_success_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = [Path(tmp)/x for x in ('a.sqlite','b.sqlite')]
            for path, day, stamp in ((a,'2026-10-12','2026-10-12T23:00'),(b,'2026-10-08','2026-10-08T23:00')):
                db=open_db(path)
                db.execute('INSERT INTO history VALUES(?,?,?,?,?,?,?,?,?)',(day,'6531','1','1',10,0,10,10,stamp))
                db.execute('INSERT INTO queue VALUES(?,?,?,?,?,?,?,?,?,?)',('6531','1','1','name','url','2026-10-01',day,day,day,None))
                db.commit();db.close()
            merge(a,b);merge(a,b)
            db=open_db(b)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM history').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT last_success FROM queue').fetchone()[0],'2026-10-12')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            db.close()
