"""Regression for selection events arriving after a coverage filter redraw."""
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop_ui as ui


class SelectionEventTests(unittest.TestCase):
    def test_empty_filter_message_survives_queued_deselection(self):
        app = ui.AutoStoryDesktop('http://127.0.0.1:18741', ROOT)
        try:
            app._apply_coverage({'rows': [
                {'code': 'X-1', 'group': 'X', 'mode': 'normal',
                 'state': 'unseen', 'blockers': []}]})
            app.tree.selection_set('0')
            app.tk_root.update()
            app.search_var.set('no_matching_stage')
            app._render_coverage()
            # Deleting a selected Treeview row queues a second selection event.
            app.tk_root.update()
            self.assertEqual(app.tree.get_children(), ())
            self.assertIn('没有匹配', app.details_text.get('1.0', 'end-1c'))
            self.assertEqual(str(app.btn_copy_details.cget('state')), 'disabled')
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
