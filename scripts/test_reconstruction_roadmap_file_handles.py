"""Roadmap publication must not leak open file handles while counting lines.

The final `[roadmap] wrote N files, ~M lines` summary reads every published
packet file. Counting with `sum(1 for _ in open(path))` never closed those
files, so each one was finalized later as a leaked descriptor and raised a
ResourceWarning. The sibling analyze_program.py counts lines through
`read_text().splitlines()`, which closes each file; this guards the same idiom
here so the regression cannot return.
"""
import contextlib
import gc
import io
import tempfile
import unittest
import warnings
from pathlib import Path

import reconstruction_roadmap as roadmap


class RoadmapFileHandleTests(unittest.TestCase):
    def test_line_count_closes_every_published_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "packet"
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with contextlib.redirect_stdout(io.StringIO()):
                    roadmap.generate_roadmap(output)
                gc.collect()
            leaked = [w for w in caught if issubclass(w.category, ResourceWarning)]
            self.assertEqual(
                leaked, [],
                "line counting leaked file handles: "
                + ", ".join(str(w.message) for w in leaked))
            # The packet still publishes, so the guard exercised real files.
            self.assertTrue((output / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
