"""Read-only UI visual audit; never starts a worker or submits a command."""
import argparse
import ctypes
import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop_ui as ui


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='ui-050')
    parser.add_argument('--size', default=ui.WINDOW_SIZE)
    parser.add_argument('--tab', choices=['coverage', 'logs'], default='coverage')
    parser.add_argument('--page', type=int, default=1)
    args = parser.parse_args()
    app = ui.AutoStoryDesktop('http://127.0.0.1:18741', ROOT)
    try:
        state, coverage, errors = app.client.fetch_dashboard()
        if errors:
            raise RuntimeError(errors)
        app._apply_dashboard({'state': state, 'coverage': coverage, 'errors': []})
        app.tk_root.geometry(args.size + '+80+60')
        app.tk_root.deiconify()
        # This short-lived preview must remain visible during the screen crop.
        # The shipping application does not use a topmost window.
        app.tk_root.attributes('-topmost', True)
        app.tk_root.lift()
        if args.tab == 'logs':
            app.notebook.select(app.logs_tab)
        else:
            for _ in range(max(0, args.page - 1)):
                app._next_page()
            children = app.tree.get_children()
            if children:
                app.tree.selection_set(children[0])
                app.tree.event_generate('<<TreeviewSelect>>')
        app.tk_root.update()
        def measure(widget):
            return {'x': widget.winfo_rootx()-app.tk_root.winfo_rootx(),
                    'y': widget.winfo_rooty()-app.tk_root.winfo_rooty(),
                    'w': widget.winfo_width(), 'h': widget.winfo_height(),
                    'mapped': bool(widget.winfo_ismapped())}
        report = {'size': args.size, 'requested': [app.tk_root.winfo_reqwidth(), app.tk_root.winfo_reqheight()],
                  'widgets': {name: measure(getattr(app, name)) for name in
                              ('btn_stop', 'btn_run_all', 'btn_run', 'chapter_combo', 'tree', 'notebook')},
                  'row_height': int(app.tk_root.tk.call('ttk::style', 'lookup', 'Treeview', '-rowheight')),
                  'total_records': len(coverage.get('rows', [])), 'source': 'live GET-only dashboard'}
        for name in ('details_text', 'btn_copy_details', 'btn_prev_page', 'btn_next_page', 'budget_label'):
            if hasattr(app, name):
                report['widgets'][name] = measure(getattr(app, name))
        if args.tab == 'coverage':
            width, height = map(int, args.size.split('x'))
            for name, bounds in report['widgets'].items():
                assert bounds['mapped'] and 0 <= bounds['x'] < width and 0 <= bounds['y'] < height, (name, bounds)
                assert bounds['x']+bounds['w'] <= width and bounds['y']+bounds['h'] <= height, (name, bounds)
            report['page'] = app.page_info_var.get()
        # Check the full ancestor chain, not just the root: a control can fit
        # the window yet be clipped by a sidebar or toolbar container.
        clipped = []
        controls = []
        def check_children(parent):
            for widget in parent.winfo_children():
                if widget.winfo_ismapped() and widget.winfo_class() in ('Button', 'TButton', 'TEntry', 'TCombobox', 'TCheckbutton'):
                    bounds = measure(widget)
                    controls.append({'text': str(widget.cget('text')) if 'text' in widget.keys() else widget.winfo_class(), **bounds})
                    ancestor = widget.master
                    while ancestor is not None:
                        x, y = widget.winfo_rootx(), widget.winfo_rooty()
                        ax, ay = ancestor.winfo_rootx(), ancestor.winfo_rooty()
                        if x < ax or y < ay or x+widget.winfo_width() > ax+ancestor.winfo_width()+1 or y+widget.winfo_height() > ay+ancestor.winfo_height()+1:
                            clipped.append(str(widget))
                            break
                        ancestor = ancestor.master
                check_children(widget)
        check_children(app.tk_root)
        report['controls'] = controls
        report['clipped_controls'] = clipped
        assert not clipped, clipped
        screenshot = pathlib.Path(tempfile.gettempdir()) / (args.label + '.png')
        # Tk uses logical coordinates on a scaled desktop. Capture by HWND so
        # the screenshot helper resolves physical pixels in its own DPI context.
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = user32.GetAncestor(app.tk_root.winfo_id(), 2)
        helper = ROOT/'packaging/capture_window.ps1'
        app.tk_root.after(700, app.tk_root.quit)
        app.tk_root.mainloop()
        subprocess.run(['pwsh', '-NoProfile', '-File', str(helper), '-Path', str(screenshot),
                        '-WindowHandle', str(hwnd)], check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        report['screenshot'] = str(screenshot)
        (ROOT/'runtime'/(args.label+'.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))
    finally:
        app.close()


if __name__ == '__main__':
    main()
