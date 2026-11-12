using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Local UI host. Task execution remains owned by the existing loopback service.
internal sealed class AutoStoryWindow : Form {
    readonly WebView2 view = new WebView2();
    readonly Uri endpoint;
    readonly string projectRoot, reportPath, capturePath;
    readonly Timer watchdog = new Timer();
    bool auditing;
    [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr window, int attr, ref int value, int length);
    [DllImport("user32.dll")] static extern uint GetDpiForSystem();

    public AutoStoryWindow(string[] args) {
        if (args.Length < 2) throw new ArgumentException("Missing local service URL or project root.");
        endpoint = new Uri(args[0]);
        if (endpoint.Scheme != "http" || endpoint.Host != "127.0.0.1" || endpoint.AbsolutePath != "/")
            throw new ArgumentException("Only the local AutoStory endpoint is supported.");
        projectRoot = Path.GetFullPath(args[1]);
        int width = 1152, height = 768;
        for (int i=2; i<args.Length; i++) {
            if (args[i] == "--smoke-report" && i+1<args.Length) reportPath = Path.GetFullPath(args[++i]);
            else if (args[i] == "--capture" && i+1<args.Length) capturePath = Path.GetFullPath(args[++i]);
            else if (args[i] == "--width" && i+1<args.Length) width = int.Parse(args[++i]);
            else if (args[i] == "--height" && i+1<args.Length) height = int.Parse(args[++i]);
            else throw new ArgumentException("Unknown host argument: " + args[i]);
        }
        Text = "MAA 自动推图助手";
        BackColor = Color.FromArgb(17,20,23);
        AutoScaleMode = AutoScaleMode.None;
        double scale = GetDpiForSystem()/96.0;
        Rectangle area = Screen.PrimaryScreen.WorkingArea;
        ClientSize = new Size(Math.Min((int)(width*scale), area.Width-80), Math.Min((int)(height*scale),area.Height-100));
        MinimumSize = new Size((int)(980*scale),(int)(680*scale));
        StartPosition = FormStartPosition.CenterScreen;
        string icon = Path.Combine(projectRoot,"assets","shu.ico");
        if (File.Exists(icon)) Icon = new Icon(icon);
        view.Dock = DockStyle.Fill;
        view.DefaultBackgroundColor = BackColor;
        Controls.Add(view);
        Shown += async delegate { await InitializeView(); };
        FormClosed += delegate { watchdog.Stop(); view.Dispose(); };
        watchdog.Interval = 30000;
        watchdog.Tick += delegate { if (reportPath != null) Fail(new TimeoutException("WebView startup timed out.")); };
    }

    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        int dark = 1, caption = 0x171411, foreground = 0xE8ECE8;
        DwmSetWindowAttribute(Handle,20,ref dark,4);
        DwmSetWindowAttribute(Handle,35,ref caption,4);
        DwmSetWindowAttribute(Handle,36,ref foreground,4);
    }

    async Task InitializeView() {
        try {
            if (reportPath != null) watchdog.Start();
            string profile = Path.Combine(projectRoot,"runtime","webview-profile");
            var environment = await CoreWebView2Environment.CreateAsync(null,profile);
            await view.EnsureCoreWebView2Async(environment);
            var core = view.CoreWebView2;
            core.Settings.AreDevToolsEnabled = false;
            core.Settings.AreDefaultContextMenusEnabled = false;
            core.Settings.IsStatusBarEnabled = false;
            core.Settings.IsZoomControlEnabled = false;
            core.Settings.AreBrowserAcceleratorKeysEnabled = false;
            core.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) {
                Uri target;
                if (!Uri.TryCreate(e.Uri,UriKind.Absolute,out target) || target.Scheme!=endpoint.Scheme || target.Host!=endpoint.Host || target.Port!=endpoint.Port) e.Cancel=true;
            };
            core.NewWindowRequested += delegate(object sender,CoreWebView2NewWindowRequestedEventArgs e) { e.Handled=true; };
            core.ProcessFailed += delegate(object sender,CoreWebView2ProcessFailedEventArgs e) { Fail(new InvalidOperationException("WebView process failed: "+e.ProcessFailedKind)); };
            core.NavigationCompleted += async delegate(object sender,CoreWebView2NavigationCompletedEventArgs e) {
                if (!e.IsSuccess) { Fail(new InvalidOperationException("Local page failed: "+e.WebErrorStatus)); return; }
                if (reportPath!=null && !auditing) { auditing=true; await Audit(); }
            };
            core.Navigate(endpoint.AbsoluteUri);
        } catch (Exception ex) { Fail(ex); }
    }

    async Task Audit() {
        try {
            // Allow the local read-only dashboard fetch and fonts to settle.
            await Task.Delay(2200);
            string result = await view.CoreWebView2.ExecuteScriptAsync(@"(()=>({ok:document.readyState==='complete',title:document.title,viewport:[innerWidth,innerHeight],devicePixelRatio,bodyWidth:document.body.scrollWidth,buttons:document.querySelectorAll('button').length,dashboardLoaded:!!(window.AutoStoryUI&&AutoStoryUI.getCoverage()),text:document.body.innerText.slice(0,2500)}))()");
            if (capturePath!=null) {
                Directory.CreateDirectory(Path.GetDirectoryName(capturePath));
                using(var stream = File.Create(capturePath)) await view.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,stream);
            }
            Directory.CreateDirectory(Path.GetDirectoryName(reportPath));
            File.WriteAllText(reportPath,result,new System.Text.UTF8Encoding(false));
            watchdog.Stop();
            Close();
        } catch (Exception ex) { Fail(ex); }
    }

    void Fail(Exception ex) {
        Environment.ExitCode=1;
        watchdog.Stop();
        string message=ex.ToString();
        try { Directory.CreateDirectory(Path.Combine(projectRoot,"runtime")); File.AppendAllText(Path.Combine(projectRoot,"runtime","webview-host.log"),DateTimeOffset.Now+"\n"+message+"\n"); } catch {}
        if(reportPath!=null) { try { File.WriteAllText(reportPath,"{\"ok\":false,\"error\":\"WebView startup failed; see webview-host.log\"}"); } catch {} }
        else MessageBox.Show("界面启动失败。请重新打开助手。\n\n"+ex.Message,Text,MessageBoxButtons.OK,MessageBoxIcon.Error);
        Close();
    }
}

internal static class Program {
    [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    [STAThread] static int Main(string[] args) {
        SetProcessDpiAwarenessContext(new IntPtr(-4));
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try { Application.Run(new AutoStoryWindow(args)); return Environment.ExitCode; }
        catch(Exception ex) { MessageBox.Show(ex.Message,"MAA 自动推图助手",MessageBoxButtons.OK,MessageBoxIcon.Error); return 1; }
    }
}
