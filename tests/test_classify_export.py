import io
import unittest
import zipfile

from attested import export, ingest, parser


class ClassifyTests(unittest.TestCase):
    def test_dice_employment_type_wins(self):
        self.assertEqual(
            parser.classify("Contract", "Chicago, IL", "dice", "")[0],
            "contract")
        self.assertEqual(
            parser.classify("Full Time", "Chicago, IL", "dice", "")[0],
            "full_time")
        self.assertEqual(
            parser.classify("Part Time", "Chicago, IL", "dice", "")[0],
            "part_time")

    def test_remoteok_is_remote(self):
        self.assertEqual(parser.classify("", "", "remoteok", "")[1],
                         "remote")

    def test_location_signals(self):
        self.assertEqual(
            parser.classify("", "Remote - US", "dice", "")[1], "remote")
        self.assertEqual(
            parser.classify("", "Austin, TX (Hybrid)", "dice", "")[1],
            "hybrid")
        self.assertEqual(
            parser.classify("", "Austin, TX", "dice", "")[1], "onsite")

    def test_unknown_stays_unknown(self):
        t, w = parser.classify("", "", "arbeitnow", "")
        self.assertEqual(t, "unknown")


class ExportTests(unittest.TestCase):
    MD = "# Alex Rivera\n\n## Professional Summary\nData engineer.\n\n- Built pipelines on EMR\n- SQL models in Snowflake\n"

    def test_docx_is_a_real_word_file(self):
        data = export.to_docx(self.MD)
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn("word/document.xml", z.namelist())
        xml = z.read("word/document.xml").decode()
        self.assertIn("Alex Rivera", xml)
        self.assertIn("Built pipelines on EMR", xml)

    def test_pdf_is_a_real_pdf(self):
        data = export.to_pdf(self.MD)
        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", data)
        self.assertIn(b"Alex Rivera", data)


class IngestTests(unittest.TestCase):
    TEXT = ("Jordan Lee — Data Engineer. Python and PySpark pipelines on "
            "Amazon EMR for five years, SQL throughout, Airflow DAGs.")

    def test_docx_roundtrip(self):
        data = export.to_docx("# Resume\n\n" + self.TEXT + "\n")
        out = ingest.extract_text("resume.docx", data)
        self.assertIn("PySpark pipelines", out)

    def test_pdf_roundtrip(self):
        data = export.to_pdf("# Resume\n\n" + self.TEXT + "\n")
        out = ingest.extract_text("resume.pdf", data)
        self.assertIn("Jordan", out)

    def test_txt_passthrough(self):
        out = ingest.extract_text("resume.txt", self.TEXT.encode())
        self.assertIn("Airflow", out)

    def test_rejects_unknown_and_empty(self):
        with self.assertRaises(ValueError):
            ingest.extract_text("resume.exe", b"MZ" + b"x" * 100)
        with self.assertRaises(ValueError):
            ingest.extract_text("resume.txt", b"short")


if __name__ == "__main__":
    unittest.main()
